"""The billed run: a real judge against the real corpus.

Opt-in behind `live_claude_cli`, like every other module here that spends money. `tests/conftest.py`
skips it unless `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, so neither CI nor an ordinary local run ever
calls a model.

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
from pathlib import Path

import pytest

from cobol_modernizer.core.model_client import collect_usage
from cobol_modernizer.tools.tenant_repo import resolve_program
from tests.evaluations.corpus import CASES, Ground
from tests.evaluations.judge import (
    CANDIDATE_JUDGES,
    BenchmarkSummary,
    adjudicator_for,
    judge_case,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"

pytestmark = pytest.mark.live_claude_cli


@pytest.fixture(scope="module", params=CANDIDATE_JUDGES, ids=lambda m: m.split("-")[1])
def summary(request) -> BenchmarkSummary:
    """One judge call per case, per candidate model -- six calls each.

    Module-scoped so a whole benchmark costs six calls rather than six per assertion. That is not
    only about money: a per-test call would let two assertions disagree because they scored different
    responses, and a flaky benchmark is one nobody trusts enough to act on.

    **Parametrised over `CANDIDATE_JUDGES`, and every candidate faces the same bars.** That is the
    `verified_for` discipline rather than a report card: a model that misses a defect a real JVM
    catches is not eligible to be this judge, and the test failing is the finding — not a broken
    suite. A candidate that fails is removed from the list with the evidence recorded, exactly as
    both Sonnets were for `spec_extractor`.
    """
    model = request.param
    source = resolve_program(FIXTURE_ROOT, "CBACT04C").source_text
    # `collect_usage` so the instrument reports what it cost to run. An evaluation harness whose own
    # price is unknown is awkward in a repo whose § 4b argument is about cost per unit of review --
    # and the first two runs of this benchmark could not say, because nothing bound an accumulator.
    with collect_usage() as usage:
        results = tuple(
            judge_case(case, source, adjudicate=adjudicator_for(model)) for case in CASES
        )
    rendered = BenchmarkSummary(results=results, model=model)
    print(f"\n\n===== {model} =====")
    print(
        f"{usage.model_calls} calls  in={usage.input_tokens}  out={usage.output_tokens}  "
        f"cache_read={usage.cache_read_input_tokens}  "
        f"notional=${usage.notional_cost_usd if usage.notional_cost_usd is not None else 0.0:.4f}"
    )
    # Printed so a real run leaves the artifact the verification report needs, whether or not the
    # assertions below pass. A benchmark that fails and prints nothing has to be run twice.
    print(f"\n{rendered.render()}\n")
    print(
        f"oracle-grounded detection: {rendered.detection_rate(ground=Ground.ORACLE):.2f}  "
        f"source-grounded detection: {rendered.detection_rate(ground=Ground.SOURCE):.2f}  "
        f"false-positive rate: {rendered.false_positive_rate():.2f}"
    )
    # The judge's own reasoning for anything it got wrong. Added after the first billed run, which
    # failed and left no way to tell a judge error from a corpus error without paying again.
    print(f"\n{rendered.render_disagreements()}\n")
    return rendered


def test_the_judge_catches_every_defect_a_real_jvm_already_catches(summary):
    """The bar the harness has to clear to be worth running. See the module docstring.

    Reported per case rather than as a rate, because with two cases a rate hides which one failed --
    and *which* matters: missing the rounding mode and missing the dropped guard are different
    failures with different consequences for the other forty-three programs.
    """
    missed = [
        result.case.name
        for result in summary.results
        if result.case.ground is Ground.ORACLE and result.caught is False
    ]
    assert not missed, (
        f"{summary.model} missed {missed}, which real Maven catches against ADR-0021's oracle. A judge "
        f"that misses a defect where an oracle exists has no claim on the programs where none does"
    )


def test_the_judge_does_not_flag_a_body_that_is_correct(summary):
    flagged = {
        result.case.name: result.false_positives
        for result in summary.results
        if result.case.failing_criterion is None and result.false_positives
    }
    assert not flagged, (
        f"{summary.model} failed criteria on bodies with no defect: {flagged}. Every spurious flag is "
        f"a body routed to human review, which section 4b puts three to four orders of magnitude "
        f"above inference cost"
    )


def test_the_judge_answered_every_criterion_for_every_case(summary):
    # A vacuous benchmark would be one where parsing silently produced empty verdicts. `judge.py`
    # raises rather than allowing that; this asserts the run really covered what it claims to.
    for result in summary.results:
        assert len(result.verdicts) == 4, result.case.name


def test_the_source_grounded_result_is_recorded_rather_than_asserted(summary):
    """Deliberately not a bar -- see the module docstring.

    What is asserted is only that the number exists and is a number, so a run cannot report a
    source-grounded score of `nan` and have it read as agreement.
    """
    rate = summary.detection_rate(ground=Ground.SOURCE)
    assert not math.isnan(rate), "no source-grounded case was scored"
