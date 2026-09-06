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

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    DesignDocument,
    ProgramDesignEntry,
)
from cobol_modernizer.nodes.solution_architect import (
    SolutionArchitectParseError,
    _a_move_that_strands_no_step,
    _derive_entity_name,
    _refuse_a_step_ordered_before_its_input_exists,
    _to_camel_case,
    attach_control_breaks,
    build_architect_prompt,
    build_computed_values,
    build_domain_entities,
    build_file_access_paths,
    design_solution,
    unreachable_entities,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_job import is_chunk_step, plan_steps
from cobol_modernizer.tools.tenant_repo import resolve_program

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
                            "guard_condition": None,
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


def test_design_solution_repairs_a_prose_preamble(all_program_entries):
    """The wiring to `parse_with_repair`, pinned (ADR-0054).

    Written after a damage probe: setting `max_attempts=1` here left all 35 tests in this file
    green, so the repair path was wired and entirely uncovered. A test that only passes on the
    happy path cannot tell a wired loop from an unwired one.
    """
    responses: list[str] = []
    sent: list[str] = []

    def preamble_then_comply(model, system_prompt, user_content):
        sent.append(user_content)
        if not responses:
            entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
            good = _fake_architect_response(entities, all_program_entries)
            responses.append(good)
            return f"Certainly! Here is the unified design:\n\n{good}"
        return responses.pop()

    design = design_solution(
        FIXTURE_ROOT, all_program_entries, architect=preamble_then_comply
    )

    assert len(design.domain_entities) == 7
    assert len(sent) == 2, "the node re-asked exactly once"
    assert "could not be parsed" in sent[1]


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
                                "guard_condition": None,
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


def _typed(all_program_entries, role: str):
    """The valid response with its one step turned into a transform carrying `role`."""

    def architect(model, system_prompt, user_content):
        entities = build_domain_entities(FIXTURE_ROOT, all_program_entries)
        response = json.loads(_fake_architect_response(entities, all_program_entries))
        for job in response["batch_jobs"]:
            job["domain_entities"] = [entity.name for entity in entities]
            job["steps"][0]["role"] = role
            job["steps"][0]["output_type"] = entities[1].name
        return json.dumps(response)

    return architect


def test_design_solution_rejects_a_transform_that_is_not_a_processor(all_program_entries):
    """ADR-0070. A live design typed two type-changing steps `writer`, and the project did not
    compile: `generate` renders a body for a processor and nothing else, so the wiring injected two
    classes the pipeline would never produce -- after a human had approved the design.

    The message has to name the role to use. A model told only that `writer` is wrong has three
    options left and two of them are also wrong.
    """
    with pytest.raises(SolutionArchitectParseError, match="Only a processor transforms an item"):
        design_solution(
            FIXTURE_ROOT, all_program_entries, architect=_typed(all_program_entries, "writer")
        )


def test_the_same_step_typed_processor_is_accepted(all_program_entries):
    """The other half, so this is the role being checked rather than the type change.

    Without it the refusal above would keep passing if the rule had become "a step may not change
    its item's type", which would refuse every processor in every design.
    """
    design = design_solution(
        FIXTURE_ROOT, all_program_entries, architect=_typed(all_program_entries, "processor")
    )
    step = design.batch_jobs[0].steps[0]
    assert step.role == "processor"
    assert step.input_type != step.output_type


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
            "guard_condition": None,
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


# --- G26's systemic half: types that resolve but cannot be populated -------------------------------


def _interest_step(**overrides):
    kwargs = {
        "step_name": "computeInterest",
        "source_paragraphs": ["1300-COMPUTE-INTEREST"],
        "role": "processor",
        "description": "Computes monthly interest.",
        "input_type": "TranCatBalWithRate",
        "output_type": "Tran",
        "guard_condition": "IF DIS-INT-RATE NOT = 0",
    }
    kwargs.update(overrides)
    return BatchStepDesign(**kwargs)


def _composite(*components):
    return CompositeType(
        name="TranCatBalWithRate",
        components=[CompositeComponent(field_name=f, entity_name=e) for f, e in components],
    )


_BALANCE_AND_RATE = (("balance", "TranCatBal"), ("disclosureGroup", "DisGroup"))
_WITH_CONTEXT = (*_BALANCE_AND_RATE, ("account", "Account"), ("cardXref", "CardXref"))


@pytest.fixture(scope="module")
def cbact04c_source():
    return resolve_program(FIXTURE_ROOT, "CBACT04C").source_text


@pytest.fixture(scope="module")
def entities(all_program_entries):
    """The real merged entities, so the vocabulary below is the one a real design carries."""
    return build_domain_entities(FIXTURE_ROOT, all_program_entries)


def test_the_check_flags_exactly_what_the_model_could_not_reach(entities, cbact04c_source):
    """G26 as it actually occurred, reproduced against the real COBOL.

    This is the composite the design had when a real model was asked to build a `Tran` from it. It
    resolved -- every type name was declared -- and the model still could not reach `ACCT-ID` or
    `XREF-CARD-NUM`, left both `null`, and named the paragraph that produces them. Resolution
    passing while this fails is the whole gap.
    """
    missing = unreachable_entities(
        _interest_step(),
        source_text=cbact04c_source,
        entities=entities,
        composites=[_composite(*_BALANCE_AND_RATE)],
    )
    assert missing == ["Account", "CardXref"]


def test_widening_the_composite_clears_it(entities, cbact04c_source):
    # The fix PR #40 made, now checkable rather than argued: with those two components the step's
    # COBOL reads nothing its types cannot reach.
    missing = unreachable_entities(
        _interest_step(),
        source_text=cbact04c_source,
        entities=entities,
        composites=[_composite(*_WITH_CONTEXT)],
    )
    assert missing == []


def test_the_check_follows_perform_which_is_where_the_evidence_is(entities, cbact04c_source):
    """The load-bearing detail: the fields are not in the paragraph the step names.

    `1300-COMPUTE-INTEREST` performs `1300-B-WRITE-TX`, and the moves live there. A check reading
    only the named paragraph finds nothing wrong with the design that produced the defect -- so
    this asserts the difference directly rather than trusting that following PERFORM mattered.
    """
    from cobol_modernizer.parsing.field_references import reachable_paragraphs

    reached = reachable_paragraphs(cbact04c_source, ["1300-COMPUTE-INTEREST"])
    assert "1300-B-WRITE-TX" in reached, "PERFORM was not followed"
    assert "XREF-CARD-NUM" in reached["1300-B-WRITE-TX"]
    assert "XREF-CARD-NUM" not in reached["1300-COMPUTE-INTEREST"]


def test_a_plain_entity_step_is_handled_without_a_composite(entities, cbact04c_source):
    # Most steps take an entity, not a composite. Reaching for `composites_by_name` first must not
    # make those unanalysable.
    missing = unreachable_entities(
        _interest_step(input_type="TranCatBal", output_type="TranCatBal"),
        source_text=cbact04c_source,
        entities=entities,
        composites=[],
    )
    assert "DisGroup" in missing, "a balance-only step cannot reach the rate it multiplies by"


def test_a_paragraph_name_the_parser_does_not_know_is_skipped_not_raised(cbact04c_source):
    # A design may name a paragraph this parser did not recognise. That is a different problem from
    # this one, and turning it into an exception here would make an unrelated defect look like an
    # unpopulatable step.
    from cobol_modernizer.parsing.field_references import reachable_paragraphs

    reached = reachable_paragraphs(cbact04c_source, ["1300-COMPUTE-INTEREST", "NO-SUCH-PARAGRAPH"])
    assert "1300-COMPUTE-INTEREST" in reached
    assert "NO-SUCH-PARAGRAPH" not in reached


def test_splitting_a_paragraph_chain_moves_responsibility_rather_than_erasing_it(
    entities, cbact04c_source
):
    """`1300-B-WRITE-TX` as its own step, and what that does to the check.

    A `PERFORM` is a call, and a design may legitimately split one into two steps. Once the write
    paragraph is owned by a step of its own, the paragraph that performs it is no longer answerable
    for the data it reads -- otherwise every chained design reports as broken.

    The half that matters is the third assertion: the finding does not vanish, it lands on the step
    that now owns the work. A boundary that made a real gap disappear would be worse than no
    boundary at all.
    """
    compute = _interest_step(output_type="Tran")
    write = BatchStepDesign(
        step_name="writeTransaction",
        source_paragraphs=["1300-B-WRITE-TX"],
        role="writer",
        description="Writes the interest transaction record.",
        input_type="Tran",
        output_type="Tran",
        guard_condition=None,
    )
    narrow = [_composite(*_BALANCE_AND_RATE)]
    owned = frozenset(p for s in (compute, write) for p in s.source_paragraphs)

    def check(step, **kwargs):
        return unreachable_entities(
            step, source_text=cbact04c_source, entities=entities, composites=narrow, **kwargs
        )

    # Undivided, the caller is charged with the callee's data.
    assert check(compute) == ["Account", "CardXref"]
    # Split, it is answerable only for what it reads itself.
    assert check(compute, owned_elsewhere=owned) == []
    # And the finding relocates rather than disappearing.
    assert check(write, owned_elsewhere=owned) == ["Account", "CardXref"]


def test_a_step_is_never_excluded_from_its_own_paragraphs(entities, cbact04c_source):
    # `owned_elsewhere` is every paragraph the job claims, this step's included, so the subtraction
    # matters: without it a step would stop at its own entry paragraph and analyse nothing.
    step = _interest_step(output_type="Tran")
    owned = frozenset({"1300-COMPUTE-INTEREST"})
    assert unreachable_entities(
        step,
        source_text=cbact04c_source,
        entities=entities,
        composites=[_composite(*_BALANCE_AND_RATE)],
        owned_elsewhere=owned,
    ) == ["Account", "CardXref"], "a step must still read its own paragraphs"


# --- ADR-0072: a step is ordered where its input exists ------------------------------------------
#
# Anchored on the design a model actually wrote, for ADR-0069's reason: the fault is one no fixture
# this repository designs would contain, because a fixture is written to the rule it is testing.

LIVE_DESIGN = (
    Path(__file__).parent.parent / "fixtures" / "live_designs" / "cbact04c-design.json"
)


def _live_job_as_validated():
    """`CBACT04C`'s live job in the state `_parse_unified_design_response` sees it.

    **Control breaks stripped**, which is not a contrivance: `attach_control_breaks` runs *after*
    `parse_with_repair`, so at validation time no step carries one. The saved `design.json` is the
    post-attachment artifact, so using it as-is would test the refusal against a design it never
    meets. `contracts.py`'s `accumulator_owners` records a first version of another rule making
    exactly this mistake, its unit tests passing over it.
    """
    document = DesignDocument.model_validate_json(LIVE_DESIGN.read_text(encoding="utf-8"))
    job = document.unified_design.batch_jobs[0]
    return document.unified_design, job.model_copy(
        update={"steps": [step.model_copy(update={"control_break": None}) for step in job.steps]}
    )


def _moved_before(job, step_name: str, before: str):
    """`job` with `step_name` lifted out and reinserted immediately ahead of `before`."""
    others = [step for step in job.steps if step.step_name != step_name]
    step = next(s for s in job.steps if s.step_name == step_name)
    at = [s.step_name for s in others].index(before)
    return job.model_copy(update={"steps": others[:at] + [step] + others[at:]})


def _refuse_live(job, design, all_program_entries):
    _refuse_a_step_ordered_before_its_input_exists(
        [job],
        design.composite_types,
        design.domain_entities,
        build_computed_values(FIXTURE_ROOT, all_program_entries),
        build_file_access_paths(FIXTURE_ROOT, all_program_entries),
        FIXTURE_ROOT,
        all_program_entries,
    )


def test_a_step_ordered_before_its_input_exists_is_refused(all_program_entries):
    """ADR-0072, against the design that produced the defect.

    `CBACT04C` performs `1300-COMPUTE-INTEREST` then `1400-COMPUTE-FEES` under one guard, and
    `1300-B-WRITE-TX` is a nested PERFORM inside the first. Flattened into three sibling steps in
    paragraph order, the step turning an `AccruedCategoryInterest` into a `Tran` lands between the
    step producing that accrued interest and the second step consuming it -- and that last step
    cannot be wired. The job named it in `STEP_NAMES` (ADR-0032, correctly) with no bean behind it
    and could not start, an approval and a `generate` after a human signed the design off.
    """
    design, job = _live_job_as_validated()
    with pytest.raises(SolutionArchitectParseError) as raised:
        _refuse_live(job, design, all_program_entries)

    message = str(raised.value)
    # The move, not only the fault. A model told a step is misplaced and not told where it goes has
    # every other position left to choose from -- the same reason ADR-0070's message names the role.
    assert "Move 'computeCategoryFees' so it runs before 'writeInterestTransaction'" in message
    assert "AccruedCategoryInterest" in message


def test_the_same_design_reordered_is_accepted(all_program_entries):
    """The other half. Without it the refusal above would keep passing if the rule had become
    "a step whose input differs from its predecessor's output is refused", which is every
    aggregating step in every design -- including `postAccountInterest` in this very job."""
    design, job = _live_job_as_validated()
    _refuse_live(
        _moved_before(job, "computeCategoryFees", "writeInterestTransaction"),
        design,
        all_program_entries,
    )


def test_the_move_the_refusal_names_is_the_one_that_renders(all_program_entries):
    """The instruction is checked against `generate`, not only against the refusal that emitted it.

    A message naming a move that does not actually wire the job would read as correct and cost a
    run. `plan_steps` is the same oracle the refusal consults, so this asserts the loop closes:
    every chunk step planned, and nothing left named in `STEP_NAMES` without a bean.
    """
    design, job = _live_job_as_validated()
    moved = _moved_before(job, "computeCategoryFees", "writeInterestTransaction")
    attached = attach_control_breaks(FIXTURE_ROOT, [moved], all_program_entries)
    resolved = design.model_copy(
        update={
            "batch_jobs": attached,
            "computed_values": build_computed_values(FIXTURE_ROOT, all_program_entries),
            "file_access_paths": build_file_access_paths(FIXTURE_ROOT, all_program_entries),
        }
    )
    renderable, skipped, _staged = plan_steps(attached[0], resolved, "CBACT04C")

    assert skipped == []
    named = [step.step_name for step in attached[0].steps if is_chunk_step(step)]
    assert named == [step.step_name for step in renderable]
    assert "computeCategoryFees" in named


def test_the_refusal_is_silent_when_no_single_move_would_wire_the_job(all_program_entries):
    """Restraint, and it is load-bearing rather than defensive.

    A design that cannot be ordered has a real fan-out the chain cannot express, and ADR-0054 gives
    one repair attempt: spending it on "reorder this" that no reordering satisfies buys nothing and
    loses the attempt. `postAccountInterest` aggregates, so no position in the chain supplies its
    input -- with `computeCategoryFees` *also* stranded, no single move empties the skip list.
    """
    design, job = _live_job_as_validated()
    resolved = design.model_copy(
        update={
            "batch_jobs": [job],
            "computed_values": build_computed_values(FIXTURE_ROOT, all_program_entries),
            "file_access_paths": build_file_access_paths(FIXTURE_ROOT, all_program_entries),
        }
    )
    _renderable, skipped, _staged = plan_steps(job, resolved, "CBACT04C")

    # Both, precisely because the control breaks are stripped here.
    assert [step.step_name for step, _why in skipped] == [
        "computeCategoryFees",
        "postAccountInterest",
    ]
    assert _a_move_that_strands_no_step(job, resolved, [s for s, _ in skipped]) is None


def test_attaching_control_breaks_is_what_lets_the_refusal_fire(all_program_entries):
    """The guard above the refusal, asserted as the thing it is: without it, nothing ever fires.

    `aggregation_source` reads `step.control_break`, and at validation time no step carries one, so
    an aggregating step reads as stranded. The test above shows the pair of stranded steps admits no
    single move -- so a refusal checked against the unattached design would return `None` for every
    live design ever written and never raise. That is a check that cannot fail, which this
    repository has shipped once already (verification 18) and does not intend to ship again.

    The two assertions are the same design one function apart, which is the whole claim.
    """
    design, job = _live_job_as_validated()
    assert all(step.control_break is None for step in job.steps)

    attached = attach_control_breaks(FIXTURE_ROOT, [job], all_program_entries)
    assert [step.step_name for step in attached[0].steps if step.control_break] == [
        "postAccountInterest"
    ]

    with pytest.raises(SolutionArchitectParseError):
        _refuse_live(job, design, all_program_entries)
