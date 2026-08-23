"""The harness itself, at no cost -- and shown to discriminate before anything is billed.

`test_interest_equivalence` established the rule this module follows: a harness is demonstrated to
fail before its passing result is believed. There it was a `divideRounded` body failing exactly the
six rows the oracle predicted. Here it is three scripted judges -- one perfect, one that passes
everything, one that fails everything -- run through the real scoring code, so the numbers this
package reports are known to move in the right direction before a real model produces one.
"""

from __future__ import annotations

import json
import os
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
    MalformedResponse,
    SampledBenchmark,
    Spread,
    attempt_case,
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


def test_the_large_shared_span_really_is_a_prefix(cobol_source):
    """G13/ADR-0017's shape, which this module reintroduced once before this test existed.

    The COBOL source is ~25k tokens and identical for all six cases; the step facts are a few hundred
    characters and vary. Ordered the other way round, the big shared span sits behind a variable
    block and no cache can see it. Asserted by position rather than by measuring a cache, because the
    ordering is the thing under this module's control.
    """
    prompt = build_judge_prompt(CASES[0], cobol_source)
    assert (
        prompt.index("## Rubric")
        < prompt.index("<untrusted-cobol-source")
        < prompt.index("## The step")
        < prompt.index("## The generated Java")
    )


def test_all_six_cases_share_the_rubric_and_the_source_as_a_common_prefix(cobol_source):
    prompts = [build_judge_prompt(case, cobol_source) for case in CASES]
    common = len(os.path.commonprefix(prompts))
    # The shared prefix must cover the whole source block, not merely the rubric.
    assert common > prompts[0].index("</untrusted-cobol-source>")


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
    assert parsed.verdicts["arithmetic_mode"] is Verdict.FAIL
    assert parsed.verdicts["guard_applied"] is Verdict.PASS
    assert set(parsed.verdicts) == {c.id for c in CRITERIA}


def test_the_rationale_is_kept_for_every_criterion():
    """What the first billed run needed and did not have.

    Verdicts alone cannot distinguish a judge that misread the code from a corpus that mislabelled
    it, and that was the exact question left open when the benchmark first failed. Paying for a
    second run to recover reasoning the first run already produced is the waste this prevents.
    """
    parsed = parse_judge_response(_response(arithmetic_mode="fail"))
    assert set(parsed.rationales) == {c.id for c in CRITERIA}
    assert parsed.rationales["arithmetic_mode"] == "because"


def test_a_verdict_without_a_rationale_raises():
    raw = json.dumps([{"criterion": c.id, "verdict": "pass"} for c in CRITERIA])
    with pytest.raises(JudgeResponseParseError, match="no rationale"):
        parse_judge_response(raw)


def test_a_blank_rationale_is_not_a_rationale():
    raw = json.dumps(
        [{"criterion": c.id, "verdict": "pass", "rationale": "   "} for c in CRITERIA]
    )
    with pytest.raises(JudgeResponseParseError, match="no rationale"):
        parse_judge_response(raw)


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
    raw = json.dumps(
        [{"criterion": c.id, "verdict": "maybe", "rationale": "ok"} for c in CRITERIA]
    )
    with pytest.raises(JudgeResponseParseError, match="unrecognised verdict"):
        parse_judge_response(raw)


def test_a_partial_answer_raises_instead_of_counting_as_passes():
    """The failure mode worth being strict about.

    A judge that answers three criteria and skips the fourth, scored leniently, reports a clean run
    over a criterion nobody evaluated -- and the skipped one would tend to be the hardest.
    """
    raw = json.dumps([{"criterion": CRITERIA[0].id, "verdict": "pass", "rationale": "ok"}])
    with pytest.raises(JudgeResponseParseError, match="did not answer"):
        parse_judge_response(raw)


def test_answering_twice_for_one_criterion_raises():
    raw = json.dumps(
        [{"criterion": c.id, "verdict": "pass", "rationale": "ok"} for c in CRITERIA]
        + [{"criterion": CRITERIA[0].id, "verdict": "fail", "rationale": "ok"}]
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
    # `guard_applied` deliberately: `interest_rounds` applies its guard correctly and does not list
    # that criterion as impure, so a `fail` on it is a genuine false positive. Using
    # `fixed_width_text` here would not be -- the carrier record really is short, which is what
    # `impure_criteria` records.
    case = CASES_BY_NAME["interest_rounds"]
    result = score_case(
        case, parse_judge_response(_response(arithmetic_mode="fail", guard_applied="fail"))
    )
    assert result.caught is True
    assert result.false_positives == ("guard_applied",)
    assert not result.correct


def test_a_criterion_a_case_genuinely_violates_is_not_counted_against_the_judge():
    """The correction the second billed run forced, in one assertion.

    The interest bodies carry an intermediate `Tran` with `""` in a `PIC X(16)` field. A judge that
    flags that is **right about the code**, and counting it as a false positive would report the
    corpus's impurity as the judge's error -- which is what the second Opus run did.
    """
    case = CASES_BY_NAME["interest_rounds"]
    assert "fixed_width_text" in case.impure_criteria
    result = score_case(
        case, parse_judge_response(_response(arithmetic_mode="fail", fixed_width_text="fail"))
    )
    assert result.false_positives == ()
    assert result.correct


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_a_case_can_never_declare_its_own_defect_impure(case):
    """The guard that stops `impure_criteria` becoming a way to hide a miss.

    Excluding a criterion from false-positive counting is a statement about the *specimen*. Allowing
    a case to exclude the criterion it exists to fail would let a defect be marked unscoreable, which
    is the one thing this field must never be able to do.
    """
    assert case.failing_criterion not in case.impure_criteria


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


# --- The opt-in guard, which this package broke and then fixed ----------------------------------


class _FakeItem:
    """The two methods `pytest_collection_modifyitems` uses, and nothing else."""

    def __init__(self, live: bool) -> None:
        self._live = live
        self.markers: list = []

    def get_closest_marker(self, name: str):
        return pytest.mark.live_claude_cli if (self._live and name == "live_claude_cli") else None

    def add_marker(self, marker) -> None:
        self.markers.append(marker)


@pytest.mark.parametrize(
    ("env_value", "live", "expect_skipped"),
    [
        (None, True, True),  # opted out: a live test must be skipped
        ("0", True, True),  # anything but "1" is opted out
        ("1", True, False),  # opted in: the live test must actually run
        (None, False, False),  # an ordinary test is never touched
        ("1", False, False),
    ],
)
def test_the_live_opt_in_guard_skips_and_unskips_correctly(
    monkeypatch, env_value, live, expect_skipped
):
    """Both directions, because only one of them was ever exercised by running the suite.

    The skip direction is what stops a `pytest tests/` from spending money -- and it failed here
    once: this package's benchmark put its model calls in a *module-scoped* fixture, which pytest
    sets up before any function-scoped autouse guard, so an ordinary run made six real calls with
    the opt-in unset. The guard moved to collection time, which has no such hole.

    The un-skip direction has no such evidence, because verifying it by running the suite would cost
    exactly what the guard exists to prevent. So it is checked here against the hook directly: a
    guard that skips unconditionally would look identical in CI and only fail when someone tried to
    opt in.
    """
    from tests.conftest import LIVE_CLI_ENV_VAR, pytest_collection_modifyitems

    monkeypatch.delenv(LIVE_CLI_ENV_VAR, raising=False)
    if env_value is not None:
        monkeypatch.setenv(LIVE_CLI_ENV_VAR, env_value)

    item = _FakeItem(live=live)
    pytest_collection_modifyitems(config=None, items=[item])

    assert bool(item.markers) is expect_skipped


def test_judge_case_uses_the_injected_adjudicator(cobol_source):
    seen: list[str] = []

    def adjudicate(system_prompt: str, user_content: str) -> str:
        seen.append(user_content)
        return _perfect("interest_rounds")

    result = judge_case(CASES_BY_NAME["interest_rounds"], cobol_source, adjudicate=adjudicate)
    assert result.caught is True
    assert seen and "## Rubric" in seen[0]
# --- Sampling: the fix ADR-0045 decided, and the defect it exists to prevent ----------------------
#
# **The defect in one line.** Pillar 22 crossed to green at audit R2.23 on a run scoring 6 of 6 at a
# 0.00 false-positive rate, and was withdrawn at R2.27 when the same judge, same corpus, same prompt
# scored 4 of 6 at 0.50. Nothing in the harness could say which of those two runs to believe, because
# it reported one sample of a non-deterministic instrument as a measurement.
#
# Everything below is exercised with **synthetic verdicts** and costs nothing. What a real run adds
# is real judge variance; what these establish is that the harness would *report* it.


def _sampled(*responders) -> SampledBenchmark:
    """A sampled benchmark from one responder per run, so instability is scripted rather than hoped."""
    return SampledBenchmark(
        samples=tuple(_summary(responder) for responder in responders), model="test-model"
    )


def test_a_spread_over_identical_runs_reports_no_variance():
    spread = Spread((1.0, 1.0, 1.0))
    assert (spread.mean, spread.stdev, spread.lowest, spread.highest) == (1.0, 0.0, 1.0, 1.0)
    assert spread.is_constant


def test_a_spread_reports_the_variance_that_hid_the_defect():
    """The actual R2.27 numbers: detection 1.00 then 0.67, and the summary must not average them away."""
    spread = Spread((1.0, 0.67))
    assert not spread.is_constant
    assert spread.lowest == 0.67 and spread.highest == 1.0
    assert spread.stdev > 0
    assert "min 0.67" in spread.render(), "the run that failed has to survive into the report"


def test_a_single_run_is_never_reported_as_constant():
    """**The guard on the original defect.** One run cannot be evidence of reproducibility.

    Its `stdev` is `0.0` because a lone run genuinely has no observed spread, and reporting `nan`
    would make an un-sampled benchmark look like a broken one. `is_constant` is what refuses, and it
    refuses on the count rather than on the arithmetic.
    """
    spread = Spread((1.0,))
    assert spread.stdev == 0.0
    assert not spread.is_constant, "n=1 is the defect, not a passing case"


def test_a_stable_judge_is_reported_reproducible():
    sampled = _sampled(_perfect, _perfect, _perfect)
    assert sampled.detection().is_constant
    assert sampled.false_positives().is_constant
    assert sampled.unstable_cases() == {}
    assert sampled.is_reproducible


def test_one_flipped_case_makes_the_whole_run_not_reproducible():
    """**The check that would have caught R2.27 the first time.**

    Two runs perfect, one where the judge passes everything -- so detection collapses on that run
    only. A harness reporting the mean would call this 0.67 and leave a reader to guess whether that
    is a weak judge or an unstable one.
    """
    sampled = _sampled(_perfect, _perfect, lambda _name: _all_pass())
    assert not sampled.is_reproducible
    assert not sampled.detection().is_constant
    assert sampled.detection().lowest == 0.0
    assert sampled.detection().highest == 1.0

    unstable = sampled.unstable_cases()
    assert unstable, "the flip has to be attributable to cases, not just to a moving rate"
    for correct, total in unstable.values():
        assert total == 3 and 0 < correct < 3


def test_a_consistently_wrong_judge_is_reproducible_and_still_not_eligible():
    """**Reproducible and eligible are different questions, and this pins the difference.**

    A judge that passes everything gets the same answer every run, so the instrument is stable --
    `is_reproducible` is `True` and saying otherwise would make the word mean "good". What
    disqualifies it is the detection rate, which is `0.0` on every one of those stable runs.

    The distinction matters because the bars are applied *per run* by the benchmark: stability says
    the number can be trusted, and the number says whether the judge can be. Folding them together
    would produce a metric that cannot tell a noisy judge from a bad one -- which is the pair R2.27
    could not separate.
    """
    sampled = _sampled(*(lambda _name: _all_pass() for _ in range(3)))
    assert sampled.unstable_cases() == {}, "consistent is not unstable, even when consistently wrong"
    assert sampled.detection().is_constant
    assert sampled.is_reproducible, "stable is stable; eligibility is the detection rate's job"
    assert sampled.detection().mean == 0.0, "and this is what makes it ineligible"


def test_the_unstable_cases_are_named_in_the_rendered_report():
    """A report that says *"not reproducible"* and not *which* case moved cannot be acted on.

    That is precisely what R2.27 had: two numbers, four revisions apart, and no way to tell whether
    one case flipped twice or two cases flipped once.
    """
    rendered = _sampled(_perfect, _perfect, lambda _name: _all_pass()).render()
    assert "reproducible | NO" in rendered
    assert "Unstable across runs" in rendered
    assert any(case.name in rendered for case in CASES)


def test_the_report_says_reproducible_only_when_it_is():
    rendered = _sampled(_perfect, _perfect, _perfect).render()
    assert "reproducible | yes" in rendered
    assert "Unstable across runs" not in rendered
def test_the_billed_fixtures_assembly_works_before_anything_is_billed():
    """**The plumbing of the billed benchmark, exercised for free.**

    `test_judge_benchmark`'s fixture is the one piece of this package that cannot be run without
    spending, and everything it does apart from calling a model is ordinary code: loop `SAMPLES`
    times, score six cases per run, wrap them in `BenchmarkSummary`, wrap those in
    `SampledBenchmark`, render. A mistake anywhere in that chain would surface only after the money
    was gone -- which is how the first two billed runs of this benchmark were discovered to have
    recorded no usage and kept no rationales.

    So the same construction runs here against the stub seam every other test uses. What a real run
    adds is real judge variance; what this establishes is that the harness assembles and reports.
    """
    source = "       IDENTIFICATION DIVISION.\n"
    samples = tuple(
        BenchmarkSummary(
            results=tuple(
                judge_case(case, source, adjudicate=lambda _s, _u, name=case.name: _perfect(name))
                for case in CASES
            ),
            model="stub-model",
        )
        for _ in range(3)
    )
    sampled = SampledBenchmark(samples=samples, model="stub-model")

    assert len(sampled.samples) == 3
    assert all(len(sample.results) == len(CASES) for sample in sampled.samples)
    assert sampled.detection(ground=Ground.ORACLE).values == (1.0, 1.0, 1.0)
    assert sampled.is_reproducible
    rendered = sampled.render()
    assert "reproducible | yes" in rendered and "oracle-grounded detection" in rendered

    # Every per-run block the fixture prints has to render too -- a benchmark that fails its bars
    # and then raises while reporting why is a benchmark that has to be paid for twice.
    for sample in sampled.samples:
        assert sample.render()
        assert sample.render_disagreements() == "(no disagreements)"
# --- A candidate that cannot hold the response contract ------------------------------------------
#
# **Measured from a real billed run.** `claude-haiku-4-5` answered the first case with a prose
# preamble -- *"Looking at this Java processor against the COBOL paragraph 1300-B-WRITE-TX, I need to
# examine fidelity against each..."* -- ahead of its fenced JSON, where the prompt says *"Respond
# with a JSON array and nothing else."* That aborted the whole benchmark on its first call and
# discarded the seventeen it had already been paid for.
#
# The pipeline still refuses such a response. The *benchmark* records it, because "this candidate
# cannot answer" is the most decisive thing a candidate comparison can discover.

_PROSE_THEN_JSON = (
    "Looking at this Java processor against the COBOL paragraph, I need to examine each "
    "criterion in turn.\n\n```json\n[]\n```"
)


def test_a_response_with_a_prose_preamble_is_recorded_rather_than_raised():
    """The exact shape a real candidate produced, and the reason `attempt_case` exists."""
    case = CASES[0]
    outcome = attempt_case(case, "source", adjudicate=lambda _s, _u: _PROSE_THEN_JSON)

    assert isinstance(outcome, MalformedResponse)
    assert outcome.case_name == case.name
    assert outcome.error == "JudgeResponseParseError"
    assert "Looking at this Java processor" in outcome.excerpt, (
        "the excerpt has to show *how* the contract was broken, or a report of it is unactionable"
    )


def test_judge_case_still_raises_on_the_same_response():
    """`attempt_case` is a benchmark affordance and must not soften the pipeline's refusal.

    Everywhere outside a candidate comparison there is one judge, and a broken answer from it is a
    broken run -- ADR-0024's whole argument for pinning the model is that the instrument must not
    drift quietly.
    """
    with pytest.raises(JudgeResponseParseError):
        judge_case(CASES[0], "source", adjudicate=lambda _s, _u: _PROSE_THEN_JSON)


def test_a_run_with_a_malformed_response_is_not_reported_as_answered():
    summary = BenchmarkSummary(
        results=(),
        model="stub",
        malformed=(MalformedResponse("some_case", "JudgeResponseParseError", "prose..."),),
    )
    assert not summary.answered_everything
    assert not SampledBenchmark(samples=(summary, summary)).answered_everything


def test_rates_over_a_partial_answer_set_are_flagged_in_the_report():
    """**The trap this guards.** Every rate is computed over the cases the judge answered.

    So a candidate that fails to answer its *hardest* cases and answers the rest correctly reports a
    better detection rate than one that answered them all and got a single verdict wrong. The number
    is not wrong arithmetically; it is a measurement of a different, easier corpus. The report has to
    say so on its face, because a table of rates is what gets quoted.
    """
    perfect = _summary(_perfect)
    crippled = BenchmarkSummary(
        results=perfect.results,
        model="stub",
        malformed=(MalformedResponse("hardest_case", "JudgeResponseParseError", "prose..."),),
    )
    sampled = SampledBenchmark(samples=(crippled, crippled), model="stub")

    # The arithmetic still says 1.00 -- which is exactly why the caveat has to be printed with it.
    assert sampled.detection().mean == 1.0
    rendered = sampled.render()
    assert "Malformed responses" in rendered
    assert "not a measurement of this candidate" in rendered
    assert "hardest_case" in rendered
# --- Verdict churn: measured from the first real sampled run --------------------------------------


def test_a_stable_score_can_hide_an_unstable_judge():
    """**The finding the first billed sampled run produced, pinned as a test.**

    Opus 5 returned `1.00 / 0.67 / 0.00` on all three runs with no unstable case -- and its raw
    verdicts moved anyway. It flagged `fixed_width_text` on `interest_rounds` in run 1, not in
    run 2, and on `interest_faithful` in run 3. Every one of those runs scored identically, because a
    case is `correct` as long as its defect was caught and a criterion the case genuinely violates
    is excluded from its false positives.

    So *"the numbers repeat"* and *"the judge says the same thing"* are different claims. The first
    is what pillar 22's criterion needs and what this harness bars on. The second is the leading
    indicator: churn is what crosses a threshold later and moves a rate, which is the most likely
    mechanism behind R2.23's 6-of-6 becoming R2.27's 4-of-6.
    """
    case = CASES[1]
    extra = next(c.id for c in CRITERIA if c.id != case.failing_criterion)

    def _run(with_extra: bool) -> BenchmarkSummary:
        flags = {case.failing_criterion: "fail"}
        if with_extra:
            flags[extra] = "fail"
        return BenchmarkSummary(
            results=(
                score_case(case, parse_judge_response(_response(**flags))),
            ),
            model="stub",
        )

    sampled = SampledBenchmark(samples=(_run(True), _run(False)), model="stub")

    churn = sampled.unstable_verdicts()
    assert case.name in churn, "the judge answered differently and the harness has to notice"
    assert len(set(churn[case.name])) == 2

    rendered = sampled.render()
    assert "Verdict churn" in rendered
    assert "recorded rather than barred" in rendered.lower()


def test_verdict_churn_is_reported_but_does_not_fail_reproducibility():
    """Barring on churn would fail a judge whose reported numbers are perfectly stable.

    That is the wrong trade: the rates are what get quoted, and a harness that refused a judge for
    varying on a criterion its scoring already accounts for would be unusable. The churn is recorded
    so the *next* surprise is diagnosable, not so this one is punished.
    """
    case = CASES[1]
    extra = next(c.id for c in CRITERIA if c.id != case.failing_criterion)

    def _run(with_extra: bool) -> BenchmarkSummary:
        flags = {case.failing_criterion: "fail"}
        if with_extra:
            flags[extra] = "fail"
        return BenchmarkSummary(
            results=(score_case(case, parse_judge_response(_response(**flags))),), model="stub"
        )

    sampled = SampledBenchmark(samples=(_run(True), _run(True)), model="stub")
    assert sampled.unstable_verdicts() == {}, "identical verdicts are not churn"
    assert sampled.is_reproducible
