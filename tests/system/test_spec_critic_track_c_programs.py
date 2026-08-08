"""Tests for spec_critic against the other three real Track C programs.

`test_spec_critic.py` already verifies `CBACT04C` exhaustively, built on its hand-verified golden
`spec.md`. The other three programs don't have hand-verified golden fixtures (yet -- see
`docs/qa/verification-report.md`'s "Not yet covered"), so this file uses the same `faithful
narrate` technique `test_spec_extractor.py` uses: a narration that reproduces the real Known Facts
block verbatim via `render_known_facts`. This is enough to exercise `spec_critic`'s deterministic
fidelity-check machinery against three more real, structurally different program/copybook
combinations -- it is not a substitute for full hand-verified narrative prose.

`CBACT01C` is the most valuable case here: its `CODATECN` copybook contributes 28 real unsupported
fields (four `REDEFINES` groups plus a standalone elementary `REDEFINES`), the largest
unsupported-field set of any Track C program -- a real stress case for
`check_unsupported_constructs_carried_forward` at a scale `CBACT04C`'s 9 never exercised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_critic import (
    check_unsupported_constructs_carried_forward,
    compute_fidelity_issues,
    critique_spec,
)
from cobol_modernizer.nodes.spec_extractor import (
    SpecExtractionResult,
    UnsupportedField,
    extract_spec,
)

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"


def _faithful_extraction(program_name: str) -> SpecExtractionResult:
    """A real `SpecExtractionResult` whose narration is the real Known Facts block, verbatim."""

    def faithful_narrate(model: str, system_prompt: str, user_content: str) -> str:
        return user_content.split(f'<untrusted-cobol-source label="{program_name}">')[0]

    return extract_spec(FIXTURE_ROOT, program_name, narrate=faithful_narrate)


@pytest.fixture(scope="module", params=["CBCUS01C", "CBACT01C", "CBTRN02C"])
def faithful_extraction(request) -> SpecExtractionResult:
    return _faithful_extraction(request.param)


# --- Faithful narrations are fidelity-clean for every new program -----------------------------


def test_faithful_narration_is_fidelity_clean(faithful_extraction):
    assert compute_fidelity_issues(faithful_extraction) == []


def test_critique_spec_confidence_is_not_forced_to_zero_for_a_faithful_narration(faithful_extraction):
    def high_confidence_critique(model, system_prompt, user_content):
        return '[{"rule": "representative rule", "confidence": 0.9, "rationale": "matches source"}]'

    result = critique_spec(FIXTURE_ROOT, faithful_extraction, critique=high_confidence_critique)
    assert result.fidelity_issues == []
    assert result.overall_confidence == pytest.approx(0.9)


# --- CBACT01C: a real stress case for the unsupported-construct carry-forward check -----------


def test_cbact01c_carries_forward_all_28_real_unsupported_fields():
    extraction = _faithful_extraction("CBACT01C")
    assert len(extraction.unsupported_fields) == 28
    assert compute_fidelity_issues(extraction) == []


def test_cbact01c_unsupported_field_carry_forward_detects_a_dropped_flag():
    # Same real limitation ADR-0006/test_spec_critic.py document for CBACT04C: CODATECN's own
    # REDEFINES groups embed every sibling field's raw declaration line in each other's `reason`
    # text (cobol_parser's sibling_text), so a real field name here is never fully absent from a
    # faithful narration to begin with -- a fabricated entry isolates the check's actual behavior.
    fabricated = UnsupportedField(
        source_label="CBACT01C",
        field_name="TOTALLY-UNMENTIONED-CODATECN-FIELD",
        raw_text="10 TOTALLY-UNMENTIONED-CODATECN-FIELD PIC X.",
        reason="Unsupported construct 'REDEFINES' detected in field declaration",
    )
    extraction = _faithful_extraction("CBACT01C")
    missing = check_unsupported_constructs_carried_forward(extraction.spec_markdown, [fabricated])
    assert missing == ["TOTALLY-UNMENTIONED-CODATECN-FIELD"]


# --- A real corrupted field precision is detected for each new program ------------------------


@pytest.mark.parametrize(
    ("program_name", "field_name", "real_row", "corrupted_row"),
    [
        (
            "CBCUS01C",
            "CUST-ID",
            "| CUST-ID | 9(09) | BigDecimal | 9 | 0 | False |",
            "| CUST-ID | 9(09) | BigDecimal | 99 | 0 | False |",
        ),
        (
            "CBACT01C",
            "ACCT-CURR-BAL",
            "| ACCT-CURR-BAL | S9(10)V99 | BigDecimal | 12 | 2 | True |",
            "| ACCT-CURR-BAL | S9(10)V99 | BigDecimal | 99 | 2 | True |",
        ),
        (
            "CBTRN02C",
            "DALYTRAN-AMT",
            "| DALYTRAN-AMT | S9(09)V99 | BigDecimal | 11 | 2 | True |",
            "| DALYTRAN-AMT | S9(09)V99 | BigDecimal | 99 | 2 | True |",
        ),
    ],
)
def test_corrupted_field_precision_is_detected(program_name, field_name, real_row, corrupted_row):
    extraction = _faithful_extraction(program_name)
    assert real_row in extraction.spec_markdown  # sanity: the real row is exactly this text

    corrupted_markdown = extraction.spec_markdown.replace(real_row, corrupted_row)
    tampered = extraction.model_copy(update={"spec_markdown": corrupted_markdown})

    issues = compute_fidelity_issues(tampered)
    assert any(field_name in issue for issue in issues)


# --- critique_spec end-to-end wiring for a new program -----------------------------------------


def test_critique_spec_resolves_real_model_for_a_new_program():
    extraction = _faithful_extraction("CBTRN02C")
    captured = {}

    def capturing_critique(model, system_prompt, user_content):
        captured["model"] = model
        return "[]"

    critique_spec(FIXTURE_ROOT, extraction, critique=capturing_critique)
    assert captured["model"]
