"""Tests for core/complexity.py against the real Track C programs (ADR-0014).

The bands were set against measurements, so the tests assert against those same real programs
rather than synthetic numbers -- a threshold that only works on invented inputs is not a
calibration, it is a coincidence.

Measured, by running the real pipeline (see ADR-0014 for the table):

    CBCUS01C   11,346 chars,  5 paragraphs
    CBACT04C   74,230 chars, 22 paragraphs
    CBACT01C   78,647 chars, 16 paragraphs
    CBTRN02C   81,902 chars, 26 paragraphs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.complexity import (
    COMPLEX_MIN_PARAGRAPHS,
    COMPLEX_MIN_PROMPT_CHARS,
    SIMPLE_MAX_PARAGRAPHS,
    SIMPLE_MAX_PROMPT_CHARS,
    ComplexityTier,
    classify_prompt,
    critic_tier,
)
from cobol_modernizer.nodes.spec_extractor import build_prompt, extract_field_mappings
from cobol_modernizer.parsing.cobol_parser import extract_paragraphs
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"

#: The tier each real program must land in. CBCUS01C is the only one that changes routing today;
#: the other three stay on the model their output was actually verified against (ADR-0014).
EXPECTED_TIERS = {
    "CBCUS01C": ComplexityTier.SIMPLE,
    "CBACT04C": ComplexityTier.COMPLEX,
    "CBACT01C": ComplexityTier.COMPLEX,
    "CBTRN02C": ComplexityTier.COMPLEX,
}


def measure(program_name: str):
    resolved = resolve_program(FIXTURE_ROOT, program_name)
    paragraphs = extract_paragraphs(resolved.source_text)
    mappings, unsupported = extract_field_mappings(resolved)
    user_content, _ = build_prompt(resolved, paragraphs, mappings, unsupported)
    return classify_prompt(
        program_name=program_name,
        prompt_chars=len(user_content),
        paragraph_count=len(paragraphs),
        mapped_field_count=len(mappings),
        unsupported_field_count=len(unsupported),
        copybook_count=len(resolved.copybook_sources),
    )


# --- Against the real programs -----------------------------------------------------------------


@pytest.mark.parametrize("program_name,expected", sorted(EXPECTED_TIERS.items()))
def test_each_real_program_lands_in_its_measured_tier(program_name, expected):
    assert measure(program_name).tier is expected


def test_the_bands_are_not_borderline_for_any_real_program():
    """A calibration that a one-line source edit could flip is not a calibration.

    CBCUS01C measures 11,346 characters against a 25,000 ceiling, and the next smallest program
    measures 74,230 against a 60,000 floor -- both sit clear of their boundary by a wide margin.
    This asserts that headroom directly, so a future threshold tweak that quietly puts a real
    program on a knife edge fails here rather than in production.
    """
    simple = measure("CBCUS01C")
    assert simple.prompt_chars < SIMPLE_MAX_PROMPT_CHARS * 0.6
    assert simple.paragraph_count < SIMPLE_MAX_PARAGRAPHS

    for name in ["CBACT04C", "CBACT01C", "CBTRN02C"]:
        assert measure(name).prompt_chars > COMPLEX_MIN_PROMPT_CHARS * 1.2, name


def test_classification_explains_itself():
    # `tier` alone cannot answer "why was this routed to the cheap model?" at a review gate.
    result = measure("CBCUS01C")
    assert result.tier.value in result.rationale
    assert "11,346" in result.rationale
    assert result.copybook_count == 1
    assert result.paragraph_count == 5


def test_classification_needs_no_model_call():
    # The signals come from the deterministic pipeline, so classification is free. Guarded by
    # making any model call explode: if this ever regresses to probing a model to decide which
    # model to use, the test fails rather than the bill growing quietly.
    from cobol_modernizer.core import model_client

    original = model_client.call_model
    model_client.call_model = lambda *a, **k: pytest.fail("complexity must not call a model")
    try:
        assert measure("CBTRN02C").tier is ComplexityTier.COMPLEX
    finally:
        model_client.call_model = original


# --- Band boundaries ----------------------------------------------------------------------------


def test_a_structurally_involved_but_small_program_is_not_simple():
    # Paragraph count is the second signal precisely so a short source file with heavy control
    # flow doesn't get routed cheap on size alone.
    result = classify_prompt("SYNTHETIC", prompt_chars=5_000, paragraph_count=COMPLEX_MIN_PARAGRAPHS)
    assert result.tier is ComplexityTier.COMPLEX


def test_the_moderate_band_exists_and_is_reachable():
    # Empty for Track C today, but a real estate has mid-size programs; an unreachable band would
    # mean the config's `moderate` entries were dead weight.
    result = classify_prompt("SYNTHETIC", prompt_chars=40_000, paragraph_count=12)
    assert result.tier is ComplexityTier.MODERATE


def test_a_large_prompt_with_few_paragraphs_is_still_complex():
    # Either signal alone escalates -- a program can be huge without being branchy.
    result = classify_prompt("SYNTHETIC", prompt_chars=COMPLEX_MIN_PROMPT_CHARS, paragraph_count=1)
    assert result.tier is ComplexityTier.COMPLEX


# --- The critic's cheap path --------------------------------------------------------------------


@pytest.mark.parametrize("program_tier", list(ComplexityTier))
def test_a_proven_fidelity_issue_routes_the_critic_cheap(program_tier):
    """Not a heuristic -- a consequence of ADR-0007.

    Once `compute_fidelity_issues` has proven a defect, `overall_confidence` is forced to 0.0
    regardless of what the critic scores. Paying the top tier to produce numbers that cannot
    change the gate's verdict is waste with no capability argument behind it.
    """
    assert critic_tier(program_tier, has_deterministic_fidelity_issues=True) is ComplexityTier.SIMPLE


@pytest.mark.parametrize("program_tier", list(ComplexityTier))
def test_a_clean_narration_gets_a_critic_matching_the_programs_tier(program_tier):
    assert critic_tier(program_tier, has_deterministic_fidelity_issues=False) is program_tier
