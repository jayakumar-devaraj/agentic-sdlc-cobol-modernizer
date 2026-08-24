"""Does `spec_critic` actually catch a wrong narration -- and is the cheap model enough?

ADR-0004 assigned `spec_critic` a cheaper tier and flagged that choice to "revisit empirically once
Milestone C2's golden fixtures exist". This module is that revisit, and it answers two questions
that had been open since Milestone C2:

1. **Is the critic load-bearing at all?** ADR-0001 calls its confidence score "the only independent
   check on extraction quality" a human gate sees. Everything verified before this ran was the
   *deterministic* half (`compute_fidelity_issues`); the model's own contribution had never been
   tested against a narration that was actually wrong.
2. **Is Haiku enough for it?** Answered by running the same corrupted narration past both the
   configured cheap model and the strongest one and comparing what each catches.

The corruptions are chosen so the deterministic checks **cannot** catch them: every paragraph name,
field name, and Known-Facts row is left intact, and only prose semantics change. That is asserted
rather than assumed by `test_the_deterministic_checks_do_not_catch_these_corruptions` -- if those
checks ever start catching them, this module is silently measuring the wrong thing and says so.

The live-model half is opt-in (`live_claude_cli`, see `tests/conftest.py`); the deterministic half
runs always and is what keeps the fixture honest between billed runs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.core import model_client
from cobol_modernizer.core.complexity import classify_prompt
from cobol_modernizer.core.contracts import LOW_CONFIDENCE_THRESHOLD
from cobol_modernizer.nodes.spec_critic import (
    build_critique_prompt,
    compute_fidelity_issues,
)
from cobol_modernizer.nodes.spec_extractor import SpecExtractionResult, extract_field_mappings
from cobol_modernizer.parsing.cobol_parser import extract_paragraphs
from cobol_modernizer.prompts_registry_client.loader import node_prompt_version, prompt_path
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
NARRATION = Path(__file__).parent.parent / "fixtures" / "narrations" / "CBCUS01C" / "spec.md"

PROGRAM = "CBCUS01C"

#: Each entry is (anchor in the real narration, the false replacement, what makes it false).
#: All three are checkable against `CBCUS01C.cbl` by line number, so a reviewer can confirm the
#: benchmark is testing real errors rather than debatable ones.
CORRUPTIONS = [
    (
        "`APPL-AOK` = 0, `APPL-EOF` = 16",
        "`APPL-AOK` = 0, `APPL-EOF` = 99",
        "source line 63 declares `88 APPL-EOF VALUE 16.`",
    ),
    (
        "abend with code 999",
        "abend with code 16",
        "source line 157 is `MOVE 999 TO ABCODE`",
    ),
    (
        "`'10'` means normal end of file and is **not** an error",
        "`'10'` is a fatal error and aborts the run",
        "source lines 98/107-108 treat status '10' as clean end-of-file",
    ),
]


def _extraction(markdown: str) -> SpecExtractionResult:
    resolved = resolve_program(FIXTURE_ROOT, PROGRAM)
    paragraphs = extract_paragraphs(resolved.source_text)
    mappings, unsupported = extract_field_mappings(resolved)
    return SpecExtractionResult(
        program_name=PROGRAM,
        paragraph_names=[p.name for p in paragraphs],
        field_mappings=mappings,
        unsupported_fields=unsupported,
        injection_flags=[],
        spec_markdown=markdown,
        complexity=classify_prompt(PROGRAM, prompt_chars=1, paragraph_count=len(paragraphs)),
    )


def real_narration() -> str:
    return NARRATION.read_text(encoding="utf-8")


def corrupted_narration() -> str:
    text = real_narration()
    for anchor, replacement, _why in CORRUPTIONS:
        assert anchor in text, (
            f"corruption anchor missing from the narration fixture, so the corruption would be a "
            f"silent no-op and the benchmark would pass for the wrong reason: {anchor!r}"
        )
        text = text.replace(anchor, replacement)
    return text


# --- Free: keeps the fixture honest between billed runs ------------------------------------------


def test_the_narration_fixture_is_real_prose_not_a_known_facts_echo():
    # The failure this module exists because of: `faithful_narrate` returns the Known Facts block
    # as the narration, and a real critic rejects that prompt as having no narration in it.
    text = real_narration()
    assert not text.startswith("# Known Facts")
    assert "## Business rules" in text
    assert "## Paragraph flow" in text


def test_every_corruption_anchor_still_matches_the_fixture():
    # If a regenerated narration phrases these rules differently, the corruptions silently become
    # no-ops and the live benchmark below would "pass" against an uncorrupted narration.
    text = real_narration()
    for anchor, _replacement, _why in CORRUPTIONS:
        assert anchor in text, anchor


def test_the_deterministic_checks_do_not_catch_these_corruptions():
    """The load-bearing precondition of this whole benchmark.

    If `compute_fidelity_issues` catches these, then the live test below proves nothing about the
    *model* -- the deterministic layer would already have failed the narration on its own.
    """
    assert compute_fidelity_issues(_extraction(corrupted_narration())) == []


def test_the_deterministic_checks_also_pass_the_uncorrupted_narration():
    # Rules out the opposite reading of the test above: that the checks are simply inert here.
    assert compute_fidelity_issues(_extraction(real_narration())) == []


# --- Billed: the actual discrimination benchmark --------------------------------------------------


def _parsed_and_printed(result, model: str, narration_kind: str) -> list[dict]:
    """Parse the critic's JSON, and print the scores this run actually produced.

    `test_judge_benchmark` states the rule in its own words -- printed so a real run leaves the
    artifact the verification report needs, **whether or not the assertions pass**. This module had
    the inverse defect and paid for it: the ADR-0053 run passed, printed nothing (scores appear only
    in an assertion message, which does not render when the assertion holds), and recovering the
    numbers would have meant buying the run a second time.
    """
    text = result.text.strip()
    for fence in ("```json", "```"):
        text = text.removeprefix(fence)
    rules = json.loads(text.removesuffix("```").strip())

    flagged = [r for r in rules if r["confidence"] < LOW_CONFIDENCE_THRESHOLD]
    print(f"\n===== {model}, {narration_kind} narration =====")
    print(
        f"{len(rules)} rules scored, {len(flagged)} below {LOW_CONFIDENCE_THRESHOLD}  "
        f"in={result.input_tokens} out={result.output_tokens}  "
        f"notional=${result.notional_cost_usd if result.notional_cost_usd is not None else 0.0:.4f}"
    )
    print(f"scores: {sorted(r['confidence'] for r in rules)}")
    # The rationales, for the flagged ones only. ADR-0024 keeps these precisely so a disagreement is
    # diagnosable without paying for another run, and trap 10 records what skipping them cost.
    for rule in flagged:
        print(f"  [{rule['confidence']}] {rule['rule']}\n      {rule['rationale']}")
    return rules


def _score_with(model: str) -> list[dict]:
    extraction = _extraction(corrupted_narration())
    result = model_client.call_model(
        "spec_critic",
        model,
        prompt_path("spec_critic", node_prompt_version("spec_critic")).read_text(
            encoding="utf-8"
        ),
        build_critique_prompt(FIXTURE_ROOT, extraction),
        effort="medium",
        max_output_tokens=28_000,
        backend="claude_cli",
    )
    return _parsed_and_printed(result, model, "corrupted")


@pytest.mark.live_claude_cli
@pytest.mark.parametrize("model", ["claude-haiku-4-5-20251001", "claude-opus-5"])
def test_both_tiers_catch_every_planted_error(model):
    """Measured 2026-08-08: Haiku scored 0.00/0.20/0.40, Opus 0.30/0.15/0.35 -- three flagged each.

    Haiku was *more* decisive on the `APPL-EOF` constant (0.00 vs 0.30) at 2.3x lower cost
    ($0.1058 vs $0.2388), which is the evidence ADR-0004 asked for and did not have: the cheap
    tier is not a compromise for this node.

    **Re-run 2026-08-24 on prompt v1_1_0** (ADR-0053), after the narration moved inside the untrusted
    block: both tiers still flag at least one rule per planted error, and the threshold still
    separates a corrupted narration from a clean one. Four calls, 9m16s.

    **That run left no scores, and this is why the printing exists now.** The numbers appeared only
    inside the assertion message below, which does not render when the assertion holds -- so a
    passing run recorded nothing, and recovering it would have meant buying the run a second time.
    `test_judge_benchmark` had stated the rule in its own comment since it was written.
    """
    rules = _score_with(model)
    flagged = [r for r in rules if r["confidence"] < LOW_CONFIDENCE_THRESHOLD]
    assert len(flagged) >= len(CORRUPTIONS), (
        f"{model} flagged {len(flagged)} rules below {LOW_CONFIDENCE_THRESHOLD}; expected at least "
        f"{len(CORRUPTIONS)}, one per planted error. Scores: "
        f"{sorted(r['confidence'] for r in rules)}"
    )


@pytest.mark.live_claude_cli
def test_the_low_confidence_threshold_separates_good_from_bad():
    """Calibrates `LOW_CONFIDENCE_THRESHOLD`, which ADR-0008 shipped as an admitted guess.

    Corrects an earlier reading of the live run, where the lowest score across four *genuine*
    narrations was exactly 0.70 and did not flag -- which looked like the threshold being too
    permissive. Measured against a narration that is actually wrong, real defects land at
    0.00-0.40, far below 0.7. The 0.70 case was a borderline claim, not a missed defect, and the
    threshold has ample margin.
    """
    clean_min = min(r["confidence"] for r in _score_with_real())
    bad_min = min(r["confidence"] for r in _score_with("claude-haiku-4-5-20251001"))
    assert bad_min < LOW_CONFIDENCE_THRESHOLD < clean_min, (
        f"threshold {LOW_CONFIDENCE_THRESHOLD} does not separate a corrupted narration "
        f"(min {bad_min}) from a clean one (min {clean_min})"
    )


def _score_with_real() -> list[dict]:
    extraction = _extraction(real_narration())
    result = model_client.call_model(
        "spec_critic",
        "claude-haiku-4-5-20251001",
        prompt_path("spec_critic", node_prompt_version("spec_critic")).read_text(
            encoding="utf-8"
        ),
        build_critique_prompt(FIXTURE_ROOT, extraction),
        effort="medium",
        max_output_tokens=28_000,
        backend="claude_cli",
    )
    return _parsed_and_printed(result, "claude-haiku-4-5-20251001", "real")
