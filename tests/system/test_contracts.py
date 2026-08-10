"""Tests for core/contracts.py against real spec_extractor/spec_critic output for CBACT04C.

Uses the hand-verified golden fixture (`tests/fixtures/golden/CBACT04C/spec.md`) as the real
extraction to build gate items from -- not synthetic data -- so `build_gate_items`'s real counts
(9 unsupported-construct items, from the two real REDEFINES groups) are checked against a fixture
already independently verified in `test_golden_fixture.py`, not invented for this test alone.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from cobol_modernizer.core.complexity import classify_prompt
from cobol_modernizer.core.contracts import (
    LOW_CONFIDENCE_THRESHOLD,
    SCHEMA_VERSION,
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    DesignCliResult,
    DesignDocument,
    DomainEntity,
    DomainField,
    GateItem,
    GenerateCliResult,
    ProgramDesignEntry,
    RestEndpointDesign,
    UnifiedDesign,
    build_design_document,
    build_gate_items,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import SpecExtractionResult, extract_field_mappings
from cobol_modernizer.parsing.cobol_parser import extract_paragraphs
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
GOLDEN_SPEC_MD = Path(__file__).parent.parent / "fixtures" / "golden" / "CBACT04C" / "spec.md"


@pytest.fixture(scope="module")
def golden_extraction() -> SpecExtractionResult:
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    paragraphs = extract_paragraphs(resolved.source_text)
    field_mappings, unsupported_fields = extract_field_mappings(resolved)
    return SpecExtractionResult(
        program_name="CBACT04C",
        paragraph_names=[p.name for p in paragraphs],
        field_mappings=field_mappings,
        unsupported_fields=unsupported_fields,
        injection_flags=[],
        spec_markdown=GOLDEN_SPEC_MD.read_text(encoding="utf-8"),
        complexity=classify_prompt(
            "CBACT04C", prompt_chars=74_230, paragraph_count=len(paragraphs)
        ),
    )


def _entry_with_rule_scores(extraction: SpecExtractionResult, scores: list[float]) -> ProgramDesignEntry:
    def fake_critique(model, system_prompt, user_content):
        return json.dumps(
            [{"rule": f"rule {i}", "confidence": score, "rationale": f"r{i}"} for i, score in enumerate(scores)]
        )

    critique = critique_spec(FIXTURE_ROOT, extraction, critique=fake_critique)
    return ProgramDesignEntry(program_name="CBACT04C", spec_extraction=extraction, critique=critique)


# --- build_gate_items: real unsupported-construct and injection-flag counts -------------------


def test_build_gate_items_surfaces_every_real_unsupported_field(golden_extraction):
    entry = _entry_with_rule_scores(golden_extraction, [0.95])
    items = build_gate_items([entry])

    unsupported_items = [i for i in items if i.category == "unsupported_construct"]
    assert len(unsupported_items) == 9
    assert all(i.program_name == "CBACT04C" for i in unsupported_items)
    field_names = {i.summary for i in unsupported_items}
    assert any("TWO-BYTES-LEFT" in name for name in field_names)


def test_build_gate_items_finds_no_injection_flags_in_real_source(golden_extraction):
    entry = _entry_with_rule_scores(golden_extraction, [0.95])
    items = build_gate_items([entry])
    assert [i for i in items if i.category == "injection_flag"] == []


def test_build_gate_items_surfaces_a_present_injection_flag(golden_extraction):
    # Real CBACT04C source triggers zero injection flags (test above) -- this exercises the
    # injection_flag branch directly with a fabricated flag, the same style as
    # test_guardrails.py's own adversarial synthetic cases for the heuristic scan itself.
    from cobol_modernizer.core.guardrails import InjectionFlag

    flagged_extraction = golden_extraction.model_copy(
        update={
            "injection_flags": [
                InjectionFlag(pattern="ignore_instructions", matched_text="ignore all previous instructions", line_number=42)
            ]
        }
    )
    entry = _entry_with_rule_scores(flagged_extraction, [0.95])
    items = build_gate_items([entry])

    injection_items = [i for i in items if i.category == "injection_flag"]
    assert len(injection_items) == 1
    assert injection_items[0].program_name == "CBACT04C"
    assert "ignore_instructions" in injection_items[0].summary
    assert "line 42" in injection_items[0].detail


def test_build_gate_items_has_no_fidelity_issues_for_the_fidelity_clean_golden_fixture(golden_extraction):
    entry = _entry_with_rule_scores(golden_extraction, [0.95])
    items = build_gate_items([entry])
    assert [i for i in items if i.category == "fidelity_issue"] == []


# --- build_gate_items: low-confidence rule threshold -------------------------------------------


def test_build_gate_items_flags_rules_below_threshold_only(golden_extraction):
    scores = [0.95, 0.5, LOW_CONFIDENCE_THRESHOLD, LOW_CONFIDENCE_THRESHOLD - 0.01, 0.1]
    entry = _entry_with_rule_scores(golden_extraction, scores)
    items = build_gate_items([entry])

    low_confidence_items = [i for i in items if i.category == "low_confidence_rule"]
    # 0.5, 0.1, and (threshold - 0.01) are strictly below the threshold; 0.95 and the threshold
    # value itself are not (a score exactly at the threshold is not "below" it).
    assert len(low_confidence_items) == 3


def test_build_gate_items_has_no_fidelity_or_low_confidence_items_when_clean_and_confident(
    golden_extraction,
):
    # CBACT04C's own two real REDEFINES groups always produce 9 real unsupported_construct
    # items, regardless of confidence -- a REDEFINES field genuinely always needs human review.
    # "Clean and confident" here means no fidelity_issue/low_confidence_rule/injection_flag
    # items, not zero items overall.
    entry = _entry_with_rule_scores(golden_extraction, [0.9, 0.95, 1.0])
    items = build_gate_items([entry])
    assert [i for i in items if i.category != "unsupported_construct"] == []
    assert len(items) == 9


def test_build_gate_items_surfaces_a_real_fidelity_issue():
    # A deliberately corrupted narration (dropped paragraph mention) produces a real
    # fidelity_issue via spec_critic, which must show up as its own GateItem, not just a
    # zeroed-out overall_confidence a reviewer could miss. "0200-DISCGRP-OPEN" appears exactly
    # once in the golden fixture (its own heading, confirmed via `grep -c`), unlike
    # "1400-COMPUTE-FEES" which is also mentioned in prose elsewhere -- so removing this one
    # heading genuinely makes the paragraph name absent from the whole document, the real
    # condition check_paragraph_coverage's substring check looks for.
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    paragraphs = extract_paragraphs(resolved.source_text)
    field_mappings, unsupported_fields = extract_field_mappings(resolved)
    corrupted_markdown = GOLDEN_SPEC_MD.read_text(encoding="utf-8").replace(
        "### 0200-DISCGRP-OPEN", "### DROPPED"
    )
    extraction = SpecExtractionResult(
        program_name="CBACT04C",
        paragraph_names=[p.name for p in paragraphs],
        field_mappings=field_mappings,
        unsupported_fields=unsupported_fields,
        injection_flags=[],
        spec_markdown=corrupted_markdown,
        complexity=classify_prompt(
            "CBACT04C", prompt_chars=74_230, paragraph_count=len(paragraphs)
        ),
    )

    def fake_critique(model, system_prompt, user_content):
        return json.dumps([{"rule": "x", "confidence": 0.99, "rationale": "y"}])

    critique = critique_spec(FIXTURE_ROOT, extraction, critique=fake_critique)
    assert critique.fidelity_issues != []  # sanity: the corruption really was caught
    entry = ProgramDesignEntry(program_name="CBACT04C", spec_extraction=extraction, critique=critique)

    items = build_gate_items([entry])
    fidelity_items = [i for i in items if i.category == "fidelity_issue"]
    assert len(fidelity_items) == len(critique.fidelity_issues)
    assert any("0200-DISCGRP-OPEN" in i.detail for i in fidelity_items)


# --- build_design_document: gate_items always derived, never stale ----------------------------


def test_build_design_document_derives_gate_items_from_programs(golden_extraction):
    entry = _entry_with_rule_scores(golden_extraction, [0.1])
    document = build_design_document([entry])

    assert document.schema_version == SCHEMA_VERSION
    assert document.programs == [entry]
    assert document.gate_items == build_gate_items([entry])
    assert document.unified_design is None


def test_design_document_round_trips_through_json(golden_extraction):
    entry = _entry_with_rule_scores(golden_extraction, [0.95])
    document = build_design_document([entry])

    raw = document.model_dump_json()
    restored = DesignDocument.model_validate_json(raw)
    assert restored == document


def test_design_document_carries_a_real_typed_unified_design(golden_extraction):
    # ADR-0010: unified_design is a real UnifiedDesign, not the placeholder dict ADR-0008 left it
    # as -- confirms build_design_document accepts one and DesignDocument round-trips it intact.
    entry = _entry_with_rule_scores(golden_extraction, [0.95])
    unified = UnifiedDesign(
        domain_entities=[
            DomainEntity(
                name="Account",
                source_copybook="CVACT01Y",
                used_by_programs=["CBACT04C"],
                fields=[
                    DomainField(
                        java_field_name="acctCurrBal",
                        cobol_field_name="ACCT-CURR-BAL",
                        java_type="BigDecimal",
                        precision=12,
                        scale=2,
                        signed=True,
                    )
                ],
            )
        ],
        batch_jobs=[
            BatchJobDesign(
                program_name="CBACT04C",
                job_name="interestCalculationJob",
                domain_entities=["Account"],
                steps=[
                    BatchStepDesign(
                        step_name="readTransactionCategoryBalances",
                        source_paragraphs=["1000-TCATBALF-GET-NEXT"],
                        input_type="TranCatBal",
                        output_type="TranCatBal",
                        role="reader",
                        description="Reads each transaction category balance record.",
                    )
                ],
            )
        ],
        rest_endpoints=[
            RestEndpointDesign(
                method="GET",
                path="/accounts/{id}",
                domain_entity="Account",
                description="Look up an account's current balance.",
            )
        ],
    )

    document = build_design_document([entry], unified_design=unified)
    assert document.unified_design == unified

    restored = DesignDocument.model_validate_json(document.model_dump_json())
    assert restored.unified_design == unified


# --- CLI result contracts -----------------------------------------------------------------------


def test_design_cli_result_reports_facts_not_gate_policy():
    result = DesignCliResult(
        status="ok",
        run_id="cp-run-42",
        programs=["CBACT04C"],
        output_path="/tmp/design.json",
        gate_item_count=9,
        detail="wrote design.json",
    )
    assert result.phase == "design"
    # No "gate_required"/"blocked" status exists on this model at all -- see ADR-0008 decision 3.
    assert result.status in ("ok", "error")


def test_design_cli_result_requires_a_run_id():
    # run_id is required, not optional-with-a-default (ADR-0012): a result that could silently
    # omit its correlation id would be useless in exactly the status="error" case where someone
    # needs to find the matching stderr lines.
    with pytest.raises(ValidationError):
        DesignCliResult(
            status="ok",
            programs=["CBACT04C"],
            output_path="/tmp/design.json",
            gate_item_count=9,
            detail="wrote design.json",
        )


def test_generate_cli_result_minimal_shape():
    result = GenerateCliResult(
        status="ok", run_id="r-1", output_path="/tmp/out", detail="done"
    )
    assert result.phase == "generate"


def test_generate_cli_result_requires_a_run_id():
    # The mirror of test_design_cli_result_requires_a_run_id, and required for the same reason.
    # It is a separate test rather than a parametrization of that one because the two models have
    # genuinely different required fields; what must not differ is whether run_id is among them,
    # which is the asymmetry that let `generate` go 27 PRs without a correlation id at all.
    with pytest.raises(ValidationError):
        GenerateCliResult(status="ok", output_path="/tmp/out", detail="done")


def test_gate_item_requires_a_known_category():
    with pytest.raises(ValueError, match="category"):
        GateItem(
            category="not_a_real_category",
            program_name="CBACT04C",
            summary="x",
            detail="y",
        )


# --- Type resolution (ADR-0020) ------------------------------------------------------------------


def _design_with_composite() -> UnifiedDesign:
    entity = DomainEntity(
        name="TranCatBal", source_copybook="CVTRA01Y", used_by_programs=["CBACT04C"], fields=[]
    )
    return UnifiedDesign(
        domain_entities=[entity],
        batch_jobs=[],
        rest_endpoints=[],
        composite_types=[
            CompositeType(
                name="TranCatBalWithAccount",
                components=[CompositeComponent(field_name="balance", entity_name="TranCatBal")],
            )
        ],
    )


def test_a_type_name_resolves_against_entities_and_composites_alike():
    design = _design_with_composite()
    assert design.resolve_type("TranCatBal") is not None
    assert design.resolve_type("TranCatBalWithAccount") is not None


def test_an_unknown_type_name_resolves_to_nothing_rather_than_raising():
    # `generate` turns None into a blocked step naming the type. Raising here would make the
    # caller's error message worse, not better.
    assert _design_with_composite().resolve_type("NoSuchType") is None


def test_unresolvable_names_are_reported_so_a_design_fails_before_the_gate():
    design = UnifiedDesign(
        domain_entities=[],
        batch_jobs=[
            BatchJobDesign(
                program_name="CBACT04C", job_name="j", domain_entities=[],
                steps=[
                    BatchStepDesign(
                        step_name="s", source_paragraphs=[], role="processor", description="d",
                        input_type="Ghost", output_type="AlsoGhost",
                    )
                ],
            )
        ],
        rest_endpoints=[],
    )
    assert design.unresolvable_type_names() == ["Ghost", "AlsoGhost"]


def test_a_composite_component_naming_an_unknown_entity_is_reported_too():
    design = UnifiedDesign(
        domain_entities=[], batch_jobs=[], rest_endpoints=[],
        composite_types=[
            CompositeType(
                name="C", components=[CompositeComponent(field_name="x", entity_name="Ghost")]
            )
        ],
    )
    assert design.unresolvable_type_names() == ["Ghost"]


def test_a_fully_resolvable_design_reports_nothing():
    assert _design_with_composite().unresolvable_type_names() == []
