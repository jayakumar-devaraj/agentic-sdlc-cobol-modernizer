"""Regression tests for the hand-verified golden fixture: tests/fixtures/golden/CBACT04C/spec.md.

Per `.claude/agents/qa.md`, "a golden fixture nobody checked is just the first output the
pipeline happened to produce, elevated to a standard." `tests/fixtures/golden/CBACT04C/spec.md`'s
own header documents how it was verified: its Overview/Paragraph flow/Business rules sections
were hand-written and checked paragraph by paragraph against the real `CBACT04C.cbl` source and
its five real copybooks; its Field reference and Flagged-for-human-review sections were generated
verbatim by `render_known_facts` against that same real fixture, never retyped by hand.

The tests below are what makes that claim falsifiable rather than asserted once and forgotten:
every run re-derives the real deterministic facts from the real fixture via
`extract_field_mappings`/`extract_paragraphs` and checks the committed golden file against them.
If `cobol_parser.py` or `pic_mapper.py` ever changes in a way that would silently invalidate this
fixture, these tests fail here, not only once a future spec_extractor prompt change happens to
surface it. This is also the concrete, falsifiable form of Milestone C2's gate: "golden fixture
matches exactly."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_critic import compute_fidelity_issues
from cobol_modernizer.nodes.spec_extractor import SpecExtractionResult, extract_field_mappings
from cobol_modernizer.parsing.cobol_parser import extract_paragraphs
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
GOLDEN_SPEC_MD = Path(__file__).parent.parent / "fixtures" / "golden" / "CBACT04C" / "spec.md"


@pytest.fixture(scope="module")
def golden_extraction():
    """A `SpecExtractionResult` pairing the real, live-derived deterministic facts for CBACT04C
    with the committed golden `spec.md` text -- not a fake narration built ad hoc in the test."""
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    paragraphs = extract_paragraphs(resolved.source_text)
    field_mappings, unsupported_fields = extract_field_mappings(resolved)
    spec_markdown = GOLDEN_SPEC_MD.read_text(encoding="utf-8")
    return SpecExtractionResult(
        program_name="CBACT04C",
        paragraph_names=[p.name for p in paragraphs],
        field_mappings=field_mappings,
        unsupported_fields=unsupported_fields,
        injection_flags=[],
        spec_markdown=spec_markdown,
    )


def test_golden_fixture_file_exists_and_is_nonempty():
    assert GOLDEN_SPEC_MD.exists()
    assert GOLDEN_SPEC_MD.read_text(encoding="utf-8").strip()


def test_golden_extraction_matches_the_plan_s_verified_real_targets(golden_extraction):
    # The plan's own verified-real numbers for CBACT04C (re-derived from the live fixture, not
    # hardcoded in this test in isolation -- see the module docstring).
    assert len(golden_extraction.paragraph_names) == 22
    assert len(golden_extraction.field_mappings) == 93  # 75 before ADR-0011's FILE/LINKAGE fix
    assert len(golden_extraction.unsupported_fields) == 9


def test_golden_fixture_is_fidelity_clean(golden_extraction):
    # The falsifiable form of "golden fixture matches exactly": every deterministic fact the real
    # pipeline computes right now is correctly restated in the committed golden spec.md.
    assert compute_fidelity_issues(golden_extraction) == []


def test_golden_fixture_has_all_five_prompt_sections():
    text = GOLDEN_SPEC_MD.read_text(encoding="utf-8")
    for heading in [
        "## Overview",
        "## Paragraph flow",
        "## Business rules",
        "## Field reference",
        "## Flagged for human review",
    ]:
        assert heading in text


def test_golden_fixture_preserves_the_interest_formula_verbatim():
    text = GOLDEN_SPEC_MD.read_text(encoding="utf-8")
    assert "COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200" in text


def test_golden_fixture_documents_the_1400_compute_fees_stub():
    # A real, easy-to-miss detail: 1400-COMPUTE-FEES is called but does nothing. A golden fixture
    # that narrated fee computation as if it were implemented would itself be a hand-verification
    # failure -- this asserts the stub is actually called out, not glossed over.
    text = GOLDEN_SPEC_MD.read_text(encoding="utf-8")
    assert "1400-COMPUTE-FEES" in text
    assert "stub" in text.lower() or "not yet implemented" in text.lower()


# --- critique_spec against the golden fixture: deterministic layer only ----------------------


def test_critique_spec_finds_the_golden_fixture_fidelity_clean(golden_extraction):
    def fake_critique(model, system_prompt, user_content):
        import json

        return json.dumps(
            [{"rule": "monthly interest formula", "confidence": 0.95, "rationale": "matches source"}]
        )

    from cobol_modernizer.nodes.spec_critic import critique_spec

    result = critique_spec(FIXTURE_ROOT, golden_extraction, critique=fake_critique)
    assert result.fidelity_issues == []
    assert result.overall_confidence == pytest.approx(0.95)
