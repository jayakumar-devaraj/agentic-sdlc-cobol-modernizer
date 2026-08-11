"""The harness itself, at no cost -- and shown to discriminate before anything is billed.

`test_interest_equivalence` established the rule this module follows: a harness is demonstrated to
fail before its passing result is believed. There it was a `divideRounded` body failing exactly the
six rows the oracle predicted. Here it is three scripted judges -- one perfect, one that passes
everything, one that fails everything -- run through the real scoring code, so the numbers this
package reports are known to move in the right direction before a real model produces one.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.tools.tenant_repo import resolve_program
from tests.evaluations.corpus import (
    CASES,
    CASES_BY_NAME,
    CRITERIA,
    FAITHFUL_CASES,
    UNFAITHFUL_CASES,
    Ground,
    Verdict,
)
from tests.evaluations.judge import (
    SYSTEM_PROMPT,
    BenchmarkSummary,
    JudgeResponseParseError,
    build_judge_prompt,
    judge_case,
    parse_judge_response,
    render_rubric,
    score_case,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"


@pytest.fixture(scope="module")
def cobol_source() -> str:
    return resolve_program(FIXTURE_ROOT, "CBACT04C").source_text


# --- The prompt --------------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_the_prompt_never_leaks_what_the_case_expects(case, cobol_source):
    """The single most important test in this package.

    `EvalCase` carries the answer -- which criterion must fail, and the evidence for it. If any of
    that reaches the prompt, the judge scores perfectly, the benchmark reports a perfect judge, and
    the number means nothing at all. It would also be entirely invisible: a leaked prompt produces a
    *better*-looking result, so nothing downstream would ever prompt anyone to look.
    """
    prompt = build_judge_prompt(case, cobol_source)

    assert case.evidence not in prompt, "the prompt states the evidence for the expected verdict"
    # The case's own name encodes the answer (`interest_rounds`, `completion_empty_string`).
    assert case.name not in prompt, "the prompt names the case, and the name gives the answer away"


def test_the_rubric_is_identical_for_every_case(cobol_source):
    """Why naming a criterion in the prompt is not itself a leak -- and the condition for that.

    Every criterion id appears in every prompt, because the judge is asked all four every time. That
    carries no information about *this* case only so long as the rubric does not vary with the case.
    A rubric narrowed to the criterion a case violates would be the leak in its most natural form:
    it would look like a helpful token saving, and it would hand over the answer.
    """
    rubrics = {build_judge_prompt(case, cobol_source).split("## The step")[0] for case in CASES}
    assert len(rubrics) == 1, "the rubric varies by case, so its content is a signal about the case"


def test_the_only_thing_that_varies_between_two_cases_of_one_step_is_the_java(cobol_source):
    """The corollary: for two cases sharing a step, the prompts differ only in the generated code.

    `interest_faithful` and `interest_rounds` are the same paragraph, the same guard, the same COBOL.
    If anything else differed, the judge could tell them apart without reading the Java -- and would
    then be scored on having noticed a fixture artefact.
    """
    faithful = build_judge_prompt(CASES_BY_NAME["interest_faithful"], cobol_source)
    rounds = build_judge_prompt(CASES_BY_NAME["interest_rounds"], cobol_source)

    assert faithful.split("## The generated Java")[0] == rounds.split("## The generated Java")[0]
    assert faithful != rounds


def test_the_rubric_asks_about_every_criterion():
    rubric = render_rubric()
    for criterion in CRITERIA:
        assert criterion.id in rubric
        assert criterion.question in rubric
        # The rationale is what stops the criterion being checked literally -- see `render_rubric`.
        assert criterion.rationale in rubric


def test_the_cobol_reaches_the_judge_wrapped_as_untrusted(cobol_source):
    """Same rule as every other prompt builder here: COBOL is data, never instructions.

    A judge is a model reading tenant source, so the guardrail applies unchanged. Easy to skip on the
    reasoning that this one only runs in tests -- which is exactly how a rule acquires an exception.
    """
    prompt = build_judge_prompt(CASES[0], cobol_source)
    assert "<untrusted-cobol-source" in prompt
    assert "</untrusted-cobol-source>" in prompt


def test_the_prompt_carries_the_rendered_java_with_its_markers(cobol_source):
    prompt = build_judge_prompt(CASES_BY_NAME["interest_faithful"], cobol_source)
    assert "BEGIN model-authored logic" in prompt
    assert "END model-authored logic" in prompt
    assert "CobolArithmetic.divide" in prompt


def test_the_prompt_states_the_guard_when_the_step_has_one(cobol_source):
    guarded = build_judge_prompt(CASES_BY_NAME["interest_faithful"], cobol_source)
    unguarded = build_judge_prompt(CASES_BY_NAME["completion_faithful"], cobol_source)
    assert "IF DIS-INT-RATE NOT = 0" in guarded
    # And "no guard" must be stated rather than absent, for ADR-0022's reason: a step with no guard
    # and a step whose guard nobody recorded must not look the same from inside the prompt.
    assert "every input record" in unguarded


def test_the_rubric_leads_so_the_shared_prefix_is_a_prefix(cobol_source):
    # G13/ADR-0017's shape: six cases re-sending an identical rubric behind a variable prefix.
    prompt = build_judge_prompt(CASES[0], cobol_source)
    assert prompt.index("## Rubric") < prompt.index("## The step") < prompt.index(
        "## The generated Java"
    )


def test_the_system_prompt_rules_out_a_style_review():
    # A judge that comments on naming produces findings that are all true and none actionable, which
    # is how a quality signal stops being read.
    assert "not style" in SYSTEM_PROMPT or "fidelity, not style" in SYSTEM_PROMPT
    assert "not_applicable" in SYSTEM_PROMPT


# --- The response contract ---------------------------------------------------------------------


def _response(**verdicts: str) -> str:
    return json.dumps(
        [
            {"criterion": c.id, "verdict": verdicts.get(c.id, "pass"), "rationale": "because"}
            for c in CRITERIA
        ]
    )


def test_a_well_formed_response_parses_to_every_criterion():
    parsed = parse_judge_response(_response(arithmetic_mode="fail"))
    assert parsed["arithmetic_mode"] is Verdict.FAIL
    assert parsed["guard_applied"] is Verdict.PASS
    assert set(parsed) == {c.id for c in CRITERIA}


def test_a_fenced_response_parses():
    assert parse_judge_response(f"```json\n{_response()}\n```")


@pytest.mark.parametrize(
    ("raw", "match"),
    [
        ("not json at all", "not valid JSON"),
        ('{"criterion": "arithmetic_mode"}', "not a JSON array"),
        ('[{"verdict": "pass"}]', "missing"),
        ('[{"criterion": "made_up", "verdict": "pass"}]', "unknown criterion"),
    ],
)
def test_a_broken_response_raises_rather_than_scoring(raw, match):
    with pytest.raises(JudgeResponseParseError, match=match):
        parse_judge_response(raw)


def test_an_unrecognised_verdict_raises():
    raw = json.dumps([{"criterion": c.id, "verdict": "maybe"} for c in CRITERIA])
    with pytest.raises(JudgeResponseParseError, match="unrecognised verdict"):
        parse_judge_response(raw)


def test_a_partial_answer_raises_instead_of_counting_as_passes():
    """The failure mode worth being strict about.

    A judge that answers three criteria and skips the fourth, scored leniently, reports a clean run
    over a criterion nobody evaluated -- and the skipped one would tend to be the hardest.
    """
    raw = json.dumps([{"criterion": CRITERIA[0].id, "verdict": "pass"}])
    with pytest.raises(JudgeResponseParseError, match="did not answer"):
        parse_judge_response(raw)


def test_answering_twice_for_one_criterion_raises():
    raw = json.dumps(
        [{"criterion": c.id, "verdict": "pass"} for c in CRITERIA]
        + [{"criterion": CRITERIA[0].id, "verdict": "fail"}]
    )
    with pytest.raises(JudgeResponseParseError, match="twice"):
        parse_judge_response(raw)


# --- Scoring, and the demonstration that it discriminates ---------------------------------------


def _perfect(case_name: str) -> str:
    case = CASES_BY_NAME[case_name]
    return _response(**({case.failing_criterion: "fail"} if case.failing_criterion else {}))


def _all_pass() -> str:
    return _response()


def _all_fail() -> str:
    return _response(**{c.id: "fail" for c in CRITERIA})


def _summary(responder) -> BenchmarkSummary:
    return BenchmarkSummary(
        results=tuple(
            score_case(case, parse_judge_response(responder(case.name))) for case in CASES
        )
    )


def test_a_perfect_judge_scores_full_detection_and_no_false_positives():
    summary = _summary(_perfect)
    assert summary.detection_rate() == 1.0
    assert summary.false_positive_rate() == 0.0
    assert all(result.correct for result in summary.results)


def test_a_judge_that_passes_everything_detects_nothing():
    """The harness's own `divideRounded`: proof the detection number can reach zero.

    A judge that says "looks fine" to all six cases is the exact failure an LLM-as-judge harness
    tends towards, and it must score 0.0 rather than 4-out-of-6-for-agreeing-with-the-faithful-ones.
    """
    summary = _summary(lambda _name: _all_pass())
    assert summary.detection_rate() == 0.0
    assert summary.false_positive_rate() == 0.0
    assert not any(result.caught for result in summary.results if result.caught is not None)


def test_a_judge_that_fails_everything_scores_perfect_detection_and_is_still_wrong():
    """Why detection alone is not the metric.

    Failing every criterion of every case catches all four defects -- a detection rate of 1.0 -- and
    is worthless, because it also condemns both faithful bodies and flags three spurious defects on
    each real one. The false-positive rate is what separates the two, and § 4b is why it matters more
    than it looks: every spurious flag is a body routed to human review, which is the dominant cost.
    """
    summary = _summary(lambda _name: _all_fail())
    assert summary.detection_rate() == 1.0
    assert summary.false_positive_rate() == 1.0
    assert not any(result.correct for result in summary.results)


def test_a_judge_that_finds_the_defect_and_invents_another_is_not_correct():
    case = CASES_BY_NAME["interest_rounds"]
    result = score_case(
        case, parse_judge_response(_response(arithmetic_mode="fail", fixed_width_text="fail"))
    )
    assert result.caught is True
    assert result.false_positives == ("fixed_width_text",)
    assert not result.correct


def test_a_faithful_case_is_never_counted_as_a_miss():
    """`caught` is `None`, not `False`, for a body with no defect.

    Collapsing the two would let the two faithful cases drag the detection rate to 4/6 for a judge
    that got everything right -- a metric that punishes correctness.
    """
    for case in FAITHFUL_CASES:
        assert score_case(case, parse_judge_response(_all_pass())).caught is None


@pytest.mark.parametrize("case", UNFAITHFUL_CASES, ids=[c.name for c in UNFAITHFUL_CASES])
def test_not_applicable_is_neither_a_catch_nor_a_false_positive(case):
    result = score_case(
        case, parse_judge_response(_response(**{c.id: "not_applicable" for c in CRITERIA}))
    )
    assert result.caught is False
    assert result.false_positives == ()


def test_the_two_grounds_are_reported_separately():
    """A disagreement with a real JVM must not be averaged away by agreements with a reading."""
    summary = _summary(
        lambda name: _all_pass() if CASES_BY_NAME[name].ground is Ground.ORACLE else _perfect(name)
    )
    assert summary.detection_rate(ground=Ground.ORACLE) == 0.0
    assert summary.detection_rate(ground=Ground.SOURCE) == 1.0
    # And the blended number hides it, which is the argument for not reporting only that.
    assert 0.0 < summary.detection_rate() < 1.0


def test_the_rendered_table_names_every_case_and_its_verdict():
    rendered = _summary(_perfect).render()
    for case in CASES:
        assert case.name in rendered
    assert "NO" not in rendered, "a perfect run should mark no case incorrect"


def test_judge_case_uses_the_injected_adjudicator(cobol_source):
    seen: list[str] = []

    def adjudicate(system_prompt: str, user_content: str) -> str:
        seen.append(user_content)
        return _perfect("interest_rounds")

    result = judge_case(CASES_BY_NAME["interest_rounds"], cobol_source, adjudicate=adjudicate)
    assert result.caught is True
    assert seen and "## Rubric" in seen[0]
