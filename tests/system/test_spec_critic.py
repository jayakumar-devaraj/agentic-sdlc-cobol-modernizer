"""Tests for nodes/spec_critic.py against real spec_extractor output for CBACT04C.

Every test below builds on a *real* `SpecExtractionResult` produced by `extract_spec` against the
real `CBACT04C` fixture (see `test_spec_extractor.py`'s own docstring for that fixture's
provenance) -- not a hand-built `SpecExtractionResult` with invented field names, so a fidelity
check is proven against the same real precision/scale values `test_spec_extractor.py` already
verifies. A `faithful_narrate` fake reproduces the real Known Facts block verbatim (as the real
prompt instructs the model to); `_corrupt` helpers mutate a copy of that faithful text in one
targeted way per test, so each negative test proves exactly one failure mode.

As with `test_spec_extractor.py`, the one thing not exercised here is a live Anthropic API call
-- every test injects a fake `critique` callable instead. See `nodes/spec_critic.py`'s module
docstring and `docs/qa/verification-report.md` for that gap.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_critic import (
    SpecCritiqueParseError,
    SpecCritiqueResult,
    build_critique_prompt,
    check_field_reference_fidelity,
    check_paragraph_coverage,
    check_unsupported_constructs_carried_forward,
    compute_fidelity_issues,
    critique_spec,
)
from cobol_modernizer.nodes.spec_extractor import UnsupportedField, extract_spec

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"


def _faithful_narrate(model: str, system_prompt: str, user_content: str) -> str:
    """A narration that faithfully restates the Known Facts block it was given, verbatim."""
    return user_content.split('<untrusted-cobol-source label="CBACT04C">')[0]


@pytest.fixture(scope="module")
def faithful_extraction():
    return extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=_faithful_narrate)


def _corrupt_precision(spec_markdown: str) -> str:
    return spec_markdown.replace(
        "| ACCT-CURR-BAL | S9(10)V99 | BigDecimal | 12 | 2 | True |",
        "| ACCT-CURR-BAL | S9(10)V99 | BigDecimal | 99 | 2 | True |",
    )


def _drop_field_row(spec_markdown: str) -> str:
    return spec_markdown.replace(
        "| DIS-INT-RATE | S9(04)V99 | BigDecimal | 6 | 2 | True |\n", ""
    )


def _drop_paragraph_mention(spec_markdown: str) -> str:
    return spec_markdown.replace("- 1400-COMPUTE-FEES\n", "")




# --- check_paragraph_coverage -----------------------------------------------------------------


def test_check_paragraph_coverage_detects_a_real_missing_paragraph(faithful_extraction):
    corrupted = _drop_paragraph_mention(faithful_extraction.spec_markdown)
    missing = check_paragraph_coverage(corrupted, faithful_extraction.paragraph_names)
    assert missing == ["1400-COMPUTE-FEES"]


def test_check_paragraph_coverage_empty_for_faithful_narration(faithful_extraction):
    assert check_paragraph_coverage(
        faithful_extraction.spec_markdown, faithful_extraction.paragraph_names
    ) == []


# --- check_field_reference_fidelity ------------------------------------------------------------


def test_check_field_reference_fidelity_detects_real_precision_drift(faithful_extraction):
    corrupted = _corrupt_precision(faithful_extraction.spec_markdown)
    issues = check_field_reference_fidelity(corrupted, faithful_extraction.field_mappings)
    assert len(issues) == 1
    assert "ACCT-CURR-BAL" in issues[0]
    assert "precision='99'" in issues[0]
    assert "precision='12'" in issues[0]


def test_check_field_reference_fidelity_detects_a_dropped_field(faithful_extraction):
    corrupted = _drop_field_row(faithful_extraction.spec_markdown)
    issues = check_field_reference_fidelity(corrupted, faithful_extraction.field_mappings)
    assert any("DIS-INT-RATE" in issue and "missing" in issue for issue in issues)


def test_check_field_reference_fidelity_empty_for_faithful_narration(faithful_extraction):
    assert (
        check_field_reference_fidelity(faithful_extraction.spec_markdown, faithful_extraction.field_mappings)
        == []
    )


def test_check_field_reference_fidelity_skips_filler_fields_by_name(faithful_extraction):
    # Real CBACT04C data has five distinct FILLER fields across its copybooks (CVTRA01Y,
    # CVACT03Y, CVTRA02Y, CVACT01Y, CVTRA05Y) -- pic_mapper names all five literally "FILLER",
    # so comparing them by name would silently collide. Confirms field_mappings really does
    # contain more than one FILLER entry (the condition this check exists for) and that none of
    # them produce a false-positive mismatch.
    filler_mappings = [m for m in faithful_extraction.field_mappings if m.field_name == "FILLER"]
    assert len(filler_mappings) == 5
    assert check_field_reference_fidelity(faithful_extraction.spec_markdown, filler_mappings) == []


# --- check_unsupported_constructs_carried_forward --------------------------------------------


def test_check_unsupported_constructs_carried_forward_detects_a_dropped_flag(faithful_extraction):
    # Every real unsupported field in CBACT04C's own REDEFINES groups happens to cross-reference
    # its siblings' names inside its own `reason` text (cobol_parser's `sibling_text` embeds every
    # other field's raw declaration line) -- so no real field name here is ever fully absent from
    # the faithful narration to begin with (see the "empty for faithful narration" test below,
    # which is the honest counterpart of that fact). To isolate this check's actual behavior, a
    # fabricated entry naming a field the real narration never mentions at all is used instead.
    fabricated = UnsupportedField(
        source_label="CBACT04C",
        field_name="TOTALLY-UNMENTIONED-FIELD",
        raw_text="05 TOTALLY-UNMENTIONED-FIELD PIC X.",
        reason="Unsupported construct 'REDEFINES' detected in field declaration",
    )
    missing = check_unsupported_constructs_carried_forward(faithful_extraction.spec_markdown, [fabricated])
    assert missing == ["TOTALLY-UNMENTIONED-FIELD"]


def test_check_unsupported_constructs_carried_forward_empty_for_faithful_narration(faithful_extraction):
    assert (
        check_unsupported_constructs_carried_forward(
            faithful_extraction.spec_markdown, faithful_extraction.unsupported_fields
        )
        == []
    )


# --- compute_fidelity_issues: composition of all three checks ---------------------------------


def test_compute_fidelity_issues_empty_for_faithful_narration(faithful_extraction):
    assert compute_fidelity_issues(faithful_extraction) == []


def test_compute_fidelity_issues_flags_every_real_discrepancy(faithful_extraction):
    corrupted = _corrupt_precision(faithful_extraction.spec_markdown)
    corrupted = _drop_paragraph_mention(corrupted)
    tampered = faithful_extraction.model_copy(update={"spec_markdown": corrupted})

    issues = compute_fidelity_issues(tampered)
    assert any("1400-COMPUTE-FEES" in issue for issue in issues)
    assert any("ACCT-CURR-BAL" in issue for issue in issues)
    assert len(issues) == 2


# --- build_critique_prompt ----------------------------------------------------------------------


def test_build_critique_prompt_includes_narration_known_facts_and_real_source(faithful_extraction):
    prompt = build_critique_prompt(FIXTURE_ROOT, faithful_extraction)
    assert "spec.md under review for CBACT04C" in prompt
    assert "ACCT-CURR-BAL" in prompt
    assert '<untrusted-cobol-source label="CBACT04C">' in prompt
    assert '<untrusted-cobol-source label="CVACT01Y">' in prompt
    # The interest formula, present in the real wrapped source the critic must judge against.
    assert "COMPUTE WS-MONTHLY-INT" in prompt


def test_build_critique_prompt_puts_the_stable_blocks_first_and_the_narration_last(
    faithful_extraction,
):
    """Order is the contract here, not an implementation detail -- see ADR-0017.

    This assertion exists because the *previous* order (narration first) made the span shared with
    `spec_extractor` a suffix rather than a prefix, which no cache configuration could work with.
    `in prompt` checks pass under either order, so without this the property could regress silently
    the next time someone edits the f-string.
    """
    prompt = build_critique_prompt(FIXTURE_ROOT, faithful_extraction)

    known_facts_at = prompt.index("# Known Facts")
    first_source_at = prompt.index('<untrusted-cobol-source label="CBACT04C">')
    narration_at = prompt.index("# spec.md under review for CBACT04C")

    assert known_facts_at < first_source_at < narration_at
    # The narration is the tail: nothing follows it, so everything before it is the stable span.
    assert prompt.endswith(faithful_extraction.spec_markdown)


def test_build_critique_prompt_shares_a_real_prefix_with_the_extractor_prompt():
    """The shared span must be a genuine leading substring, not merely present somewhere.

    Pins the measurement behind ADR-0017 (74,230 of 74,303 chars for `CBACT04C`, 99.9%) as a
    *property* rather than as the numbers, so it survives a fixture regeneration that moves the
    exact totals. Captures the extractor's real prompt through `extract_spec` with a fake
    `narrate` -- the same technique the rest of this suite uses -- rather than reassembling
    `build_prompt`'s inputs by hand, which would duplicate `extract_spec`'s internals and could
    drift from what the node really sends.
    """
    captured: dict[str, str] = {}

    def capture(routing, system_prompt, user_content):
        captured["user_content"] = user_content
        return "# CBACT04C\n\n## Business rules\n\nplaceholder narration\n"

    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=capture)
    critique_prompt = build_critique_prompt(FIXTURE_ROOT, extraction)

    assert critique_prompt.startswith(captured["user_content"]), (
        "the critic prompt must open with byte-identical extractor content; if this fails the "
        "shared span has stopped being a prefix and ADR-0017's premise no longer holds"
    )
    # Over half the critic's prompt is that shared span -- the reason the ordering matters at all.
    assert len(captured["user_content"]) / len(critique_prompt) > 0.5


# --- _parse_rule_confidence (via critique_spec, its only real entry point) --------------------


def test_critique_spec_parses_valid_json_response(faithful_extraction):
    def fake_critique(model, system_prompt, user_content):
        return json.dumps(
            [
                {"rule": "monthly interest formula", "confidence": 0.9, "rationale": "matches source"},
                {"rule": "fee computation is a stub", "confidence": 0.6, "rationale": "paragraph is empty"},
            ]
        )

    result = critique_spec(FIXTURE_ROOT, faithful_extraction, critique=fake_critique)
    assert isinstance(result, SpecCritiqueResult)
    assert len(result.rule_confidence) == 2
    assert result.rule_confidence[0].rule == "monthly interest formula"
    # No fidelity issues in the faithful extraction -> overall is the minimum per-rule score.
    assert result.overall_confidence == pytest.approx(0.6)


def test_critique_spec_strips_a_markdown_code_fence_the_prompt_forbids(faithful_extraction):
    def fenced_critique(model, system_prompt, user_content):
        payload = json.dumps([{"rule": "x", "confidence": 0.5, "rationale": "y"}])
        return f"```json\n{payload}\n```"

    result = critique_spec(FIXTURE_ROOT, faithful_extraction, critique=fenced_critique)
    assert result.rule_confidence[0].confidence == pytest.approx(0.5)


def test_critique_spec_raises_on_non_json_response(faithful_extraction):
    def bad_critique(model, system_prompt, user_content):
        return "this is not json"

    with pytest.raises(SpecCritiqueParseError, match="not valid JSON"):
        critique_spec(FIXTURE_ROOT, faithful_extraction, critique=bad_critique)


def test_critique_spec_raises_on_non_array_response(faithful_extraction):
    def bad_critique(model, system_prompt, user_content):
        return json.dumps({"rule": "x", "confidence": 0.5, "rationale": "y"})

    with pytest.raises(SpecCritiqueParseError, match="must be a JSON array"):
        critique_spec(FIXTURE_ROOT, faithful_extraction, critique=bad_critique)


def test_critique_spec_raises_on_missing_required_field(faithful_extraction):
    def bad_critique(model, system_prompt, user_content):
        return json.dumps([{"rule": "x", "confidence": 0.5}])  # no rationale

    with pytest.raises(SpecCritiqueParseError, match="missing rule/confidence/rationale"):
        critique_spec(FIXTURE_ROOT, faithful_extraction, critique=bad_critique)


def test_critique_spec_raises_on_out_of_range_confidence(faithful_extraction):
    def bad_critique(model, system_prompt, user_content):
        return json.dumps([{"rule": "x", "confidence": 1.5, "rationale": "y"}])

    with pytest.raises(SpecCritiqueParseError, match="out-of-range confidence"):
        critique_spec(FIXTURE_ROOT, faithful_extraction, critique=bad_critique)


# --- critique_spec: overall_confidence composition ---------------------------------------------


def test_critique_spec_forces_zero_confidence_when_fidelity_issues_exist(faithful_extraction):
    corrupted = _corrupt_precision(faithful_extraction.spec_markdown)
    tampered = faithful_extraction.model_copy(update={"spec_markdown": corrupted})

    def high_confidence_critique(model, system_prompt, user_content):
        return json.dumps([{"rule": "everything looks fine", "confidence": 0.99, "rationale": "..."}])

    result = critique_spec(FIXTURE_ROOT, tampered, critique=high_confidence_critique)
    # A confident-sounding critique must not override a mechanically-proven narration defect.
    assert result.fidelity_issues != []
    assert result.overall_confidence == 0.0


def test_critique_spec_defaults_to_full_confidence_with_no_rules_and_no_issues(faithful_extraction):
    def no_rules_critique(model, system_prompt, user_content):
        return "[]"

    result = critique_spec(FIXTURE_ROOT, faithful_extraction, critique=no_rules_critique)
    assert result.fidelity_issues == []
    assert result.rule_confidence == []
    assert result.overall_confidence == 1.0


# --- critique_spec: model routing + error propagation ------------------------------------------


def test_critique_spec_resolves_real_model_and_real_system_prompt(faithful_extraction):
    captured: dict[str, str] = {}

    def capturing_critique(model, system_prompt, user_content):
        captured["model"] = model
        captured["system_prompt"] = system_prompt
        return "[]"

    critique_spec(FIXTURE_ROOT, faithful_extraction, critique=capturing_critique)
    assert captured["model"]
    assert "spec_critic" in captured["system_prompt"]
    assert "TODO" not in captured["system_prompt"]


def test_critique_spec_propagates_missing_program_error(faithful_extraction):
    from cobol_modernizer.tools.tenant_repo import TenantRepoFileNotFoundError

    broken = faithful_extraction.model_copy(update={"program_name": "NOSUCHPROGRAM"})
    with pytest.raises(TenantRepoFileNotFoundError):
        critique_spec(FIXTURE_ROOT, broken, critique=lambda *_: "[]")
