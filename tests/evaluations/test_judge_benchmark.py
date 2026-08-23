"""The billed run: a real judge against the real corpus, **sampled rather than measured once**.

Opt-in behind `live_claude_cli`, like every other module here that spends money. `tests/conftest.py`
skips it unless `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, so neither CI nor an ordinary local run ever
calls a model.

**Why this module runs the benchmark n times** (ADR-0045). Pillar 22 crossed to green at audit R2.23
on a run scoring 6 of 6 at a 0.00 false-positive rate, and was withdrawn at R2.27 when the same
judge, over the same corpus, with the same prompt, scored 4 of 6 at 0.50. Neither run was wrong. The
*summary* was, because a single sample of a non-deterministic instrument cannot say how much of what
it reports is the instrument moving.

**The bars below are applied to every run, not to the mean**, and that is the whole correction. A
judge that catches every defect in two runs of three has a mean of 0.67 and an eligibility of none:
the run that matters is the one nobody is watching. `SampledBenchmark.unstable_cases` names which
case moved, which is what R2.27 had no way to record and therefore could not act on.

**Cost scales linearly with `n`.** Each sample is one judge call per case per candidate model --
one call per case per candidate, per sample -- seven cases, so 21 calls at the default n=3.
Override with `COBOL_MODERNIZER_JUDGE_SAMPLES`.

**The thresholds below are derived, not picked**, and only one of them is a real bar:

*Every oracle-grounded defect must be caught.* Those two bodies are the ones `test_interest_equivalence`
already fails under real Maven, for free, on every run. The entire argument for a judge is that the
oracle covers one `COMPUTE` of one program (ADR-0021's stated ceiling) and cannot be written for the
other forty-three -- so the judge is proposed as the instrument that generalises. A judge that misses
a defect *where an oracle exists to check it* has no claim on the cases where none does. That is the
minimum bar for the harness to be worth its cost, and it is derived from what the judge is for rather
than chosen to be passable.

*No faithful body may be failed.* Section 4b of the feasibility assessment puts human review three to
four orders of magnitude above inference cost, so a judge that flags correct output is expensive in
the term that decides funding. Two faithful cases is a thin sample and this is a floor, not a
measured false-positive rate -- what it rules out is a judge that flags everything.

The source-grounded cases are **reported and not asserted on**. They are graded against this repo's
reading of the COBOL rather than against a machine, and turning that reading into a pass/fail bar
would quietly promote an interpretation to ground truth -- which is the check-that-cannot-fail
pattern this package was built to avoid.

**Every candidate faces the same bars, which is the point of parametrising rather than reporting.**
`verified_for` is a gate, not a scoreboard: a model that misses a defect a real JVM catches is not
eligible to be this judge, so a failing candidate fails the suite and is then removed from
`CANDIDATE_JUDGES` with the evidence written down -- exactly as both Sonnets were struck off
`spec_extractor`. A benchmark that merely printed scores would let an ineligible model drift into use.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pytest

from cobol_modernizer.core.model_client import collect_usage
from cobol_modernizer.tools.tenant_repo import resolve_program
from tests.evaluations.corpus import CASES, Ground
from tests.evaluations.judge import (
    CANDIDATE_JUDGES,
    DEFAULT_SAMPLES,
    BenchmarkSummary,
    CaseResult,
    SampledBenchmark,
    adjudicator_for,
    attempt_case,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"

#: Runs per candidate. Env-overridable so a first billed run can be cheap and a decisive one can be
#: wider, without editing the module -- but never below 2, because one sample is the defect ADR-0045
#: exists to fix and a benchmark silently configured back into it would be worse than none.
SAMPLES = max(2, int(os.environ.get("COBOL_MODERNIZER_JUDGE_SAMPLES", DEFAULT_SAMPLES)))

pytestmark = pytest.mark.live_claude_cli


@pytest.fixture(scope="module", params=CANDIDATE_JUDGES, ids=lambda m: m.split("-")[1])
def sampled(request) -> SampledBenchmark:
    """`SAMPLES` runs of one judge call per case, per candidate model.

    Module-scoped so a whole benchmark costs `len(CASES) * SAMPLES` calls rather than that per
    assertion.
    That is not only about money: a per-test call would let two assertions disagree because they
    scored different responses, and a flaky benchmark is one nobody trusts enough to act on.

    **Parametrised over `CANDIDATE_JUDGES`, and every candidate faces the same bars.** That is the
    `verified_for` discipline rather than a report card: a model that misses a defect a real JVM
    catches is not eligible to be this judge, and the test failing is the finding -- not a broken
    suite. A candidate that fails is removed from the list with the evidence recorded, exactly as
    both Sonnets were for `spec_critic`.
    """
    model = request.param
    # **Per case, not once.** The corpus spans two programs since ADR-0050, and a case graded against
    # the wrong program's COBOL would read as a judge error when it is a harness error. Resolved once
    # per program and reused, because the source is ~25k tokens and the prompt puts it behind the
    # cache prefix.
    sources = {
        program: resolve_program(FIXTURE_ROOT, program).source_text
        for program in sorted({case.program for case in CASES})
    }
    adjudicate = adjudicator_for(model)
    # `collect_usage` so the instrument reports what it cost to run. An evaluation harness whose own
    # price is unknown is awkward in a repo whose § 4b argument is about cost per unit of review --
    # and the first two runs of this benchmark could not say, because nothing bound an accumulator.
    with collect_usage() as usage:
        samples = []
        for _ in range(SAMPLES):
            # `attempt_case` rather than `judge_case`: a candidate that cannot hold the response
            # contract is the most decisive thing this comparison can find, and aborting on it would
            # turn that finding into a stack trace and discard the calls already paid for.
            outcomes = [
                attempt_case(case, sources[case.program], adjudicate=adjudicate) for case in CASES
            ]
            samples.append(
                BenchmarkSummary(
                    results=tuple(o for o in outcomes if isinstance(o, CaseResult)),
                    model=model,
                    malformed=tuple(o for o in outcomes if not isinstance(o, CaseResult)),
                )
            )
    rendered = SampledBenchmark(samples=tuple(samples), model=model)

    print(f"\n\n===== {model}, {SAMPLES} runs =====")
    print(
        f"{usage.model_calls} calls  in={usage.input_tokens}  out={usage.output_tokens}  "
        f"cache_read={usage.cache_read_input_tokens}  "
        f"notional=${usage.notional_cost_usd if usage.notional_cost_usd is not None else 0.0:.4f}"
    )
    # Printed so a real run leaves the artifact the verification report needs, whether or not the
    # assertions below pass. A benchmark that fails and prints nothing has to be run twice.
    print(f"\n{rendered.render()}\n")
    # Per-run detail under the distribution: the aggregate says the instrument moved, and these say
    # what it looked like each time it did.
    for index, sample in enumerate(samples, start=1):
        print(f"--- run {index} of {SAMPLES} ---")
        if sample.malformed:
            print(f"malformed: {[bad.case_name for bad in sample.malformed]}")
        print(sample.render())
        print(
            f"oracle detection {sample.detection_rate(ground=Ground.ORACLE):.2f}  "
            f"source detection {sample.detection_rate(ground=Ground.SOURCE):.2f}  "
            f"false positives {sample.false_positive_rate():.2f}"
        )
        print(f"{sample.render_disagreements()}\n")
    return rendered


def test_the_judge_answers_in_the_contracted_format_every_time(sampled):
    """**The first bar, because every number below is computed over the cases that were answered.**

    The prompt says *"Respond with a JSON array and nothing else."* A candidate that prepends prose,
    truncates, or invents a criterion id is not a judge that scored badly — it is a judge whose
    score cannot be computed, and the rates would quietly be taken over the subset it managed. A
    candidate failing to answer its hardest cases would then report a *better* detection rate than
    one that answered them all and got one wrong.

    Recorded rather than raised (`attempt_case`) so a failing candidate still produces a measured
    rate instead of a stack trace on its first call. A candidate that fails this is struck from
    `CANDIDATE_JUDGES` with the evidence written down, exactly as both Sonnets were for
    `spec_extractor`.
    """
    assert sampled.answered_everything, (
        f"{sampled.model} returned {len(sampled.malformed)} unparseable response(s) across "
        f"{len(sampled.samples)} runs: "
        f"{[(bad.case_name, bad.error) for bad in sampled.malformed]}. First excerpt: "
        f"{sampled.malformed[0].excerpt if sampled.malformed else ''!r}. A model that cannot hold "
        f"the response contract is not eligible to be this judge, and the rates reported for it are "
        f"computed over the subset it answered rather than over the corpus"
    )


def test_the_judge_catches_every_defect_a_real_jvm_already_catches(sampled):
    """The bar the harness has to clear to be worth running. See the module docstring.

    **Every run, not the mean.** This is the assertion R2.27 showed was being made too weakly: the
    judge cleared it once, was believed, and did not clear it the next time. A mean over three runs
    would let one miss hide behind two catches, and the miss is the run a real migration would have
    shipped on.

    Reported per case rather than as a rate, because with two cases a rate hides which one failed --
    and *which* matters: missing the rounding mode and missing the dropped guard are different
    failures with different consequences for the other forty-three programs.
    """
    missed = {
        index: [
            result.case.name
            for result in sample.results
            if result.case.ground is Ground.ORACLE and result.caught is False
        ]
        for index, sample in enumerate(sampled.samples, start=1)
    }
    failures = {index: names for index, names in missed.items() if names}
    assert not failures, (
        f"{sampled.model} missed {failures} (run -> cases), which real Maven catches against "
        f"ADR-0021's oracle. A judge that misses a defect where an oracle exists has no claim on "
        f"the programs where none does -- and missing it in {len(failures)} of "
        f"{len(sampled.samples)} runs is the reproducibility defect, not a fluke"
    )


def test_the_judge_does_not_flag_a_body_that_is_correct(sampled):
    """Also per run: a false positive in one run of three still sends a correct body to review."""
    flagged = {
        index: {
            result.case.name: result.false_positives
            for result in sample.results
            if result.case.failing_criterion is None and result.false_positives
        }
        for index, sample in enumerate(sampled.samples, start=1)
    }
    failures = {index: cases for index, cases in flagged.items() if cases}
    assert not failures, (
        f"{sampled.model} failed criteria on bodies with no defect: {failures} (run -> cases). "
        f"Every spurious flag is a body routed to human review, which section 4b puts three to four "
        f"orders of magnitude above inference cost"
    )


def test_the_judge_answered_every_criterion_for_every_case(sampled):
    # A vacuous benchmark would be one where parsing silently produced empty verdicts. `judge.py`
    # raises rather than allowing that; this asserts the runs really covered what they claim to.
    assert len(sampled.samples) >= 2, "one sample is the defect ADR-0045 fixed"
    for sample in sampled.samples:
        for result in sample.results:
            assert len(result.verdicts) == 4, result.case.name


def test_the_source_grounded_result_is_recorded_rather_than_asserted(sampled):
    """Deliberately not a bar -- see the module docstring.

    What is asserted is only that the numbers exist and are numbers, so a run cannot report a
    source-grounded score of `nan` and have it read as agreement.
    """
    for rate in sampled.detection(ground=Ground.SOURCE).values:
        assert not math.isnan(rate), "no source-grounded case was scored"


def test_the_judge_scores_the_same_corpus_the_same_way_every_time(sampled):
    """**The criterion R2.27 said pillar 22 should have had, stated as a test.**

    Not *"the judge is good"* -- the two tests above are that. This is *"the judge is an
    instrument"*: the same inputs produce the same verdicts, so a number it reports means something
    beyond the run that produced it. A judge failing only this one is not wrong, it is unrepeatable,
    and the difference decides whether its score can be quoted.

    The failure message names the cases that moved, because a rate that wobbles says the instrument
    is noisy and only the per-case detail says where -- which is exactly what the original finding
    lacked.
    """
    unstable = sampled.unstable_cases()
    assert sampled.is_reproducible, (
        f"{sampled.model} scored the same corpus differently across {len(sampled.samples)} runs: "
        f"{unstable} (case -> correct runs of total). Detection "
        f"{sampled.detection(ground=Ground.ORACLE).render()}, false positives "
        f"{sampled.false_positives().render()}. A score from an instrument that does not repeat "
        f"cannot be quoted as a property of the generator"
    )
