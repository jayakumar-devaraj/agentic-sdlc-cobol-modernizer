"""Tests for nodes/solution_architect.py against real data for all four Track C programs.

`build_domain_entities` is exercised against real `extract_spec`/`critique_spec` output for
`CBACT04C`, `CBCUS01C`, `CBACT01C`, `CBTRN02C` -- a faithful `narrate` fake (the same technique
`test_spec_critic_track_c_programs.py` uses) stands in for the live model call `spec_extractor`
would otherwise need, but the domain-entity merging itself runs against real, byte-verified
tenant-repo fixtures, not synthetic data. As with `spec_extractor`/`spec_critic`, the one thing not
exercised here is a live Anthropic API call -- every test injects a fake `architect` instead. See
`nodes/solution_architect.py`'s module docstring and `docs/qa/verification-report.md` for that gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import ProgramDesignEntry
from cobol_modernizer.nodes.solution_architect import (
    SolutionArchitectParseError,
    _derive_entity_name,
    _to_camel_case,
    build_architect_prompt,
    build_domain_entities,
    design_solution,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
ALL_PROGRAMS = ["CBACT04C", "CBCUS01C", "CBACT01C", "CBTRN02C"]


def _faithful_narrate(program_name: str):
    def narrate(model: str, system_prompt: str, user_content: str) -> str:
        return user_content.split(f'<untrusted-cobol-source label="{program_name}">')[0]

    return narrate


def _no_op_critique(model: str, system_prompt: str, user_content: str) -> str:
    return "[]"


@pytest.fixture(scope="module")
def all_program_entries() -> list[ProgramDesignEntry]:
    entries = []
    for program_name in ALL_PROGRAMS:
        extraction = extract_spec(FIXTURE_ROOT, program_name, narrate=_faithful_narrate(program_name))
        critique = critique_spec(FIXTURE_ROOT, extraction, critique=_no_op_critique)
        entries.append(
            ProgramDesignEntry(program_name=program_name, spec_extraction=extraction, critique=critique)
        )
    return entries


# --- build_domain_entities: real cross-program merge --------------------------------------------


def test_build_domain_entities_produces_the_real_seven_entities(all_program_entries):
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    names = {entity.name for entity in entities}
    assert names == {"Account", "Tran", "CardXref", "TranCatBal", "DisGroup", "Customer", "Dalytran"}


def test_build_domain_entities_excludes_codatecn_entirely(all_program_entries):
    # CODATECN contributes zero successfully-mapped fields (all 28 are inside its four real
    # REDEFINES groups) -- it must produce no entity at all, not an empty or guessed-at one.
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    source_copybooks = {entity.source_copybook for entity in entities}
    assert "CODATECN" not in source_copybooks


def test_build_domain_entities_merges_account_across_three_real_programs(all_program_entries):
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    account = next(e for e in entities if e.name == "Account")
    assert account.source_copybook == "CVACT01Y"
    assert set(account.used_by_programs) == {"CBACT04C", "CBACT01C", "CBTRN02C"}
    # 13 real CVACT01Y fields minus 1 FILLER -- FILLER is excluded, not a real business field.
    assert len(account.fields) == 12
    assert "FILLER" not in {f.cobol_field_name for f in account.fields}


def test_build_domain_entities_keeps_structurally_similar_copybooks_separate(all_program_entries):
    # CVTRA06Y (DALYTRAN-RECORD) and CVTRA05Y (TRAN-RECORD) are both 350-byte transaction-shaped
    # records but are different real copybooks -- ADR-0010 decision 1: never merged by
    # resemblance, only by exact copybook name.
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    by_name = {e.name: e for e in entities}
    assert by_name["Dalytran"].source_copybook == "CVTRA06Y"
    assert by_name["Tran"].source_copybook == "CVTRA05Y"
    assert by_name["Dalytran"].source_copybook != by_name["Tran"].source_copybook


def test_build_domain_entities_field_data_matches_real_pic_mapper_output(all_program_entries):
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    account = next(e for e in entities if e.name == "Account")
    by_cobol_name = {f.cobol_field_name: f for f in account.fields}

    balance = by_cobol_name["ACCT-CURR-BAL"]
    assert balance.java_field_name == "acctCurrBal"
    assert (balance.java_type, balance.precision, balance.scale, balance.signed) == (
        "BigDecimal",
        12,
        2,
        True,
    )


def test_build_domain_entities_names_are_mechanical_not_semantic(all_program_entries):
    # ADR-0010 decision 2: TranCatBal, not "TransactionCategoryBalance" -- a direct transform of
    # TRAN-CAT-BAL-RECORD, never a business rename.
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    names = {e.name for e in entities}
    assert "TranCatBal" in names
    assert "TransactionCategoryBalance" not in names


# --- build_architect_prompt: guardrail wrapping of prior LLM output ----------------------------


def test_build_architect_prompt_wraps_every_program_narration_as_untrusted(all_program_entries):
    entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
    prompt = build_architect_prompt(entities, all_program_entries)

    for program_name in ALL_PROGRAMS:
        assert f'<untrusted-cobol-source label="{program_name}">' in prompt
    assert "Account" in prompt
    assert "acctCurrBal" in prompt


# --- design_solution: end-to-end wiring with a fake architect ----------------------------------


def _fake_architect_response(entities, programs) -> str:
    entity_name = entities[0].name
    return json.dumps(
        {
            "batch_jobs": [
                {
                    "program_name": entry.program_name,
                    "job_name": f"{entry.program_name.lower()}Job",
                    "domain_entities": [entity_name],
                    "steps": [
                        {
                            "step_name": "step1",
                            "source_paragraphs": [entry.spec_extraction.paragraph_names[0]],
                            "role": "reader",
                            "description": "Reads the first record.",
                            "input_type": entity_name,
                            "output_type": entity_name,
                        }
                    ],
                }
                for entry in programs
            ],
            "rest_endpoints": [
                {
                    "method": "GET",
                    "path": "/x",
                    "domain_entity": entity_name,
                    "description": "A point query.",
                }
            ],
        }
    )


def test_design_solution_end_to_end_with_a_fake_architect(all_program_entries):
    def fake_architect(model, system_prompt, user_content):
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        return _fake_architect_response(entities, all_program_entries)

    design = design_solution(FIXTURE_ROOT, all_program_entries, architect=fake_architect)

    assert len(design.domain_entities) == 7
    assert {job.program_name for job in design.batch_jobs} == set(ALL_PROGRAMS)
    assert len(design.rest_endpoints) == 1


def test_design_solution_resolves_real_model_and_real_system_prompt(all_program_entries):
    captured = {}

    def capturing_architect(model, system_prompt, user_content):
        captured["model"] = model
        captured["system_prompt"] = system_prompt
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        return _fake_architect_response(entities, all_program_entries)

    design_solution(FIXTURE_ROOT, all_program_entries, architect=capturing_architect)
    assert captured["model"]
    assert "solution_architect" in captured["system_prompt"]
    assert "TODO" not in captured["system_prompt"]


# --- design_solution: validation of the architect's structured output --------------------------


def test_design_solution_rejects_non_json_response(all_program_entries):
    with pytest.raises(SolutionArchitectParseError, match="not valid JSON"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=lambda *_: "not json")


def test_design_solution_rejects_missing_top_level_keys(all_program_entries):
    with pytest.raises(SolutionArchitectParseError, match="batch_jobs and"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=lambda *_: "{}")


def test_design_solution_rejects_an_unknown_domain_entity_reference(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        return json.dumps(
            {
                "batch_jobs": [
                    {
                        "program_name": "CBACT04C",
                        "job_name": "x",
                        "domain_entities": ["NotARealEntity"],
                        "steps": [],
                    }
                ],
                "rest_endpoints": [],
            }
        )

    with pytest.raises(SolutionArchitectParseError, match="unknown domain entity"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_an_unknown_program_reference(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        return json.dumps(
            {
                "batch_jobs": [
                    {"program_name": "NOTREAL", "job_name": "x", "domain_entities": [], "steps": []}
                ],
                "rest_endpoints": [],
            }
        )

    with pytest.raises(SolutionArchitectParseError, match="unknown program"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_a_missing_program(all_program_entries):
    def incomplete_architect(model, system_prompt, user_content):
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        response = json.loads(_fake_architect_response(entities, all_program_entries))
        response["batch_jobs"] = response["batch_jobs"][:-1]  # drop one program's job
        return json.dumps(response)

    with pytest.raises(SolutionArchitectParseError, match="missing batch_jobs"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=incomplete_architect)


def test_design_solution_rejects_an_unknown_step_role(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        return json.dumps(
            {
                "batch_jobs": [
                    {
                        "program_name": p.program_name,
                        "job_name": "x",
                        "domain_entities": [],
                        "steps": [
                            {
                                "step_name": "s",
                                "source_paragraphs": [],
                                "role": "not_a_real_role",
                                "description": "x",
                                "input_type": "Account",
                                "output_type": "Account",
                            }
                        ],
                    }
                    for p in all_program_entries
                ],
                "rest_endpoints": [],
            }
        )

    with pytest.raises(SolutionArchitectParseError, match="unknown role"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_an_unknown_rest_method(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        response = json.loads(_fake_architect_response(entities, all_program_entries))
        response["rest_endpoints"][0]["method"] = "PATCH"
        return json.dumps(response)

    with pytest.raises(SolutionArchitectParseError, match="unknown method"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_a_rest_endpoint_with_an_unknown_domain_entity(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        response = json.loads(_fake_architect_response(entities, all_program_entries))
        response["rest_endpoints"][0]["domain_entity"] = "NotARealEntity"
        return json.dumps(response)

    with pytest.raises(SolutionArchitectParseError, match="unknown domain entity"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_a_batch_job_missing_a_required_field(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        return json.dumps({"batch_jobs": [{"program_name": "CBACT04C"}], "rest_endpoints": []})

    with pytest.raises(SolutionArchitectParseError, match="missing required fields"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_a_step_missing_a_required_field(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        return json.dumps(
            {
                "batch_jobs": [
                    {
                        "program_name": "CBACT04C",
                        "job_name": "x",
                        "domain_entities": [],
                        "steps": [{"step_name": "s"}],
                    }
                ],
                "rest_endpoints": [],
            }
        )

    with pytest.raises(SolutionArchitectParseError, match="missing required fields"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


def test_design_solution_rejects_a_rest_endpoint_missing_a_required_field(all_program_entries):
    def bad_architect(model, system_prompt, user_content):
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        response = json.loads(_fake_architect_response(entities, all_program_entries))
        response["rest_endpoints"] = [{"method": "GET"}]  # missing path/domain_entity/description
        return json.dumps(response)

    with pytest.raises(SolutionArchitectParseError, match="missing required fields"):
        design_solution(FIXTURE_ROOT, all_program_entries, architect=bad_architect)


# --- Pure-function edge cases: no real data reaches these, tested directly ---------------------


def test_to_camel_case_handles_a_name_with_no_words():
    # Defensive fallback -- no real field name reaching this function is ever all-hyphens, since
    # pic_mapper's own field-name extraction already requires a real token to have found a PIC
    # clause in the first place.
    assert _to_camel_case("---") == "---"


def test_derive_entity_name_falls_back_to_the_copybook_name_with_no_01_level_record():
    # Defensive fallback -- every real copybook that contributes a mapped field has an 01-level
    # record by construction (pic_mapper needs a level-numbered field to map anything at all).
    assert _derive_entity_name("CVFAKE01Y", "no record header in this text") == "Cvfake01y"


# --- Composites and step types (ADR-0020) ---------------------------------------------------------


def _architect_with(composites, steps_extra=None, program="CBACT04C"):
    """A fake architect emitting one job for `program` with the given composites and step fields."""
    def architect(routing, system_prompt, user_content):
        step = {
            "step_name": "s", "source_paragraphs": [], "role": "processor", "description": "d",
            "input_type": "Account", "output_type": "Account",
        }
        step.update(steps_extra or {})
        return json.dumps({
            "composite_types": composites,
            "batch_jobs": [{
                "program_name": program, "job_name": "j", "domain_entities": [], "steps": [step],
            }],
            "rest_endpoints": [],
        })
    return architect


def test_a_declared_composite_survives_into_the_unified_design(all_program_entries):
    entry = next(e for e in all_program_entries if e.program_name == "CBACT04C")
    composites = [{
        "name": "AccountWithXref",
        "components": [{"field_name": "account", "entity_name": "Account"}],
    }]
    design = design_solution(FIXTURE_ROOT, [entry], architect=_architect_with(composites))

    assert [c.name for c in design.composite_types] == ["AccountWithXref"]
    assert design.composite_types[0].components[0].entity_name == "Account"


def test_a_step_may_be_typed_by_a_composite(all_program_entries):
    entry = next(e for e in all_program_entries if e.program_name == "CBACT04C")
    composites = [{
        "name": "AccountWithXref",
        "components": [{"field_name": "account", "entity_name": "Account"}],
    }]
    design = design_solution(
        FIXTURE_ROOT, [entry],
        architect=_architect_with(composites, {"input_type": "AccountWithXref"}),
    )
    assert design.batch_jobs[0].steps[0].input_type == "AccountWithXref"


def test_a_composite_referencing_an_unknown_entity_is_rejected(all_program_entries):
    entry = next(e for e in all_program_entries if e.program_name == "CBACT04C")
    composites = [{
        "name": "Bogus", "components": [{"field_name": "x", "entity_name": "NoSuchEntity"}],
    }]
    with pytest.raises(SolutionArchitectParseError, match="unknown domain entity"):
        design_solution(FIXTURE_ROOT, [entry], architect=_architect_with(composites))


def test_a_step_type_resolving_to_nothing_is_rejected_before_the_gate(all_program_entries):
    # ADR-0020 decision 5: a design that cannot be generated from must fail where it is produced,
    # not three layers down in `generate` after a human has already approved it.
    entry = next(e for e in all_program_entries if e.program_name == "CBACT04C")
    with pytest.raises(SolutionArchitectParseError, match="neither a domain entity nor"):
        design_solution(
            FIXTURE_ROOT, [entry], architect=_architect_with([], {"output_type": "Ghost"})
        )


def test_a_step_missing_its_types_entirely_is_rejected(all_program_entries):
    entry = next(e for e in all_program_entries if e.program_name == "CBACT04C")

    def architect(routing, system_prompt, user_content):
        return json.dumps({
            "batch_jobs": [{
                "program_name": "CBACT04C", "job_name": "j", "domain_entities": [],
                "steps": [{
                    "step_name": "s", "source_paragraphs": [], "role": "processor",
                    "description": "d",
                }],
            }],
            "rest_endpoints": [],
        })

    with pytest.raises(SolutionArchitectParseError, match="missing required fields"):
        design_solution(FIXTURE_ROOT, [entry], architect=architect)


def test_composites_are_optional(all_program_entries):
    # A design whose steps all operate on plain entities needs none.
    entry = next(e for e in all_program_entries if e.program_name == "CBACT04C")
    design = design_solution(FIXTURE_ROOT, [entry], architect=_architect_with([]))
    assert design.composite_types == []
