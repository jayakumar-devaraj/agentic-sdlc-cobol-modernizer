"""The billed run: a real `build_validator` against the corpus, on **real compiler output**.

Opt-in behind `live_claude_cli`. `tests/conftest.py` skips it unless
`COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, so neither CI nor an ordinary local run calls a model.

**The diagnostics are compiled, not written.** Each case's body is rendered into a real processor and
built by real Maven once, and the `CompileDiagnostic` objects that come back are what the model is
shown. Inventing plausible javac output would measure the node against this session's idea of what a
compiler says -- the failure `tests/evaluations/corpus.py` calls out in its own first paragraph. It
costs one Maven build per case and no money.

**Two bars, applied per run rather than to the mean** -- ADR-0045's correction, which this module
inherits rather than re-derives:

1. *Every `COMPILER_PROVEN` case must be judged `repairable`.* The heal loop has repaired these
   exact bodies under real Maven. A validator that calls one of them `blocked` stops a build that
   demonstrably heals, and it is wrong on the one class of case where a machine can say so.
2. *Every `SYMBOL_ABSENT` case must be judged `blocked`.* The symbol is not in the project;
   `test_build_validator_corpus.py` asserts that against a rendered copy. This is **the expensive
   direction** -- the system prompt says a false `repairable` spends every attempt rewriting
   statements that were never the problem -- so it is the bar that matters most.

`REPO_HISTORY` cases are **reported and not asserted on**, exactly as `corpus.py` treats its
source-grounded cases: their classification is this repo's reading, and a bar would promote a
reading to ground truth.

**This module prints its numbers on every call, pass or fail.** The rule is
`test_judge_benchmark`'s, and it is here because ignoring it cost a real run: ADR-0053's billed
critic run printed scores only inside an assertion message, which does not render when the assertion
holds, so a passing run left nothing behind and recovering it would have meant paying twice.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from dataclasses import dataclass

import pytest

from cobol_modernizer.core.model_client import collect_usage
from cobol_modernizer.core.package_data import TEMPLATES_ROOT
from cobol_modernizer.nodes.build_validator import (
    BuildValidatorParseError,
    ValidationVerdict,
    attribute_errors,
    validate_build,
)
from cobol_modernizer.rendering.java_processor import render_processor
from cobol_modernizer.tools.local_compiler import compile_project
from tests.evaluations.build_validator_corpus import CASES, Ground, ValidatorCase, Verdict
from tests.system.test_generate_pipeline import PACKAGE, STEP

TEMPLATE = TEMPLATES_ROOT / "target-spring-boot-baseline"

#: How many times the whole corpus is run. Each sample is one model call per case.
SAMPLES = int(os.getenv("COBOL_MODERNIZER_VALIDATOR_SAMPLES", "3"))

CLASS_NAME = "BenchmarkProcessor"
RELATIVE = f"src/main/java/{PACKAGE.replace('.', '/')}/{CLASS_NAME}.java"


@dataclass(frozen=True)
class CaseOutcome:
    case: ValidatorCase
    verdict: ValidationVerdict | None
    error: str = ""

    @property
    def correct(self) -> bool:
        return self.verdict is not None and self.verdict.outcome == self.case.expected.value


def _render(case: ValidatorCase) -> str:
    return render_processor(
        STEP,
        package=PACKAGE,
        class_name=CLASS_NAME,
        # `java.math.BigDecimal`, not a domain record. **This is load-bearing** -- see the
        # `attributed_to_the_model_region` check below. A rendered domain type like `TranCatBal`
        # does not exist in the bare baseline, so the *signature* fails to resolve, every
        # diagnostic lands on the class declaration and the method signature, `attribute_errors`
        # calls them rendered scaffolding, and `classify()` returns `blocked` deterministically
        # **without calling a model at all**. `test_generate_pipeline`'s own `_heal` uses
        # `BigDecimal` for exactly this reason.
        input_type="java.math.BigDecimal",
        output_type="java.math.BigDecimal",
        body=case.body,
        body_imports=case.imports,
        authored_by="benchmark",
    )


@pytest.fixture(scope="module")
def compiled_diagnostics(tmp_path_factory) -> dict[str, tuple]:
    """Real javac output for every case, compiled once and reused across samples.

    Module-scoped so `SAMPLES` model calls per case share one build. **A case that compiles is a
    corpus defect, not a passing case** -- there would be nothing for the validator to judge -- so
    that is asserted here rather than silently producing an empty diagnostic list.
    """
    root = tmp_path_factory.mktemp("validator-bench")
    diagnostics: dict[str, tuple] = {}

    for case in CASES:
        project = root / case.name
        shutil.copytree(TEMPLATE, project, ignore=shutil.ignore_patterns("target"))
        (project / RELATIVE).parent.mkdir(parents=True, exist_ok=True)
        (project / RELATIVE).write_text(_render(case), encoding="utf-8")

        result = compile_project(project, goal="compile")
        assert not result.succeeded, f"{case.name} compiled -- there is nothing to judge"
        assert result.errors, f"{case.name} produced no located diagnostic"

        # **The check that makes this benchmark measure a model at all.** If any diagnostic lands
        # outside the model-authored region, `classify()` short-circuits to `blocked` before the
        # model is asked -- and the run reports a confident verdict nothing judged. The first
        # attempt at this module did exactly that: it rendered a signature of `TranCatBal`, which
        # the bare baseline does not define, so all eight cases "failed" on the deterministic path
        # in 60 seconds and the bars reported the harness as a model result.
        model_errors, rendered_errors = attribute_errors(result, {RELATIVE: _render(case)})
        assert not rendered_errors, (
            f"{case.name} put {len(rendered_errors)} error(s) outside the model-authored region "
            f"({[f'{e.file}:{e.line}' for e in rendered_errors]}); `classify()` will answer "
            "`blocked` deterministically and no model will be called"
        )
        assert model_errors, f"{case.name} produced no model-attributed diagnostic"
        diagnostics[case.name] = result

    return diagnostics


def _attempt(case: ValidatorCase, result) -> CaseOutcome:
    """One judged case. A malformed response comes back as data, per ADR-0049.

    `judge_case`'s counterpart raises everywhere else, and for the same reason it does not here: a
    benchmark exists to find out whether a candidate *can* answer, and a harness that turns "it
    cannot" into a stack trace discards every already-paid-for call in the same fixture.
    """
    sources = {RELATIVE: _render(case)}
    try:
        return CaseOutcome(case=case, verdict=validate_build(result, sources))
    except BuildValidatorParseError as error:
        return CaseOutcome(case=case, verdict=None, error=f"{type(error).__name__}: {error}")


def _report(sample: int, outcomes: list[CaseOutcome]) -> None:
    print(f"\n--- sample {sample} of {SAMPLES} " + "-" * 40)
    for outcome in outcomes:
        got = outcome.verdict.outcome if outcome.verdict else "MALFORMED"
        mark = "ok " if outcome.correct else "XX "
        print(f"  {mark}{outcome.case.name:32} expected={outcome.case.expected.value:11} got={got}")
        if outcome.verdict and not outcome.correct:
            # The rationale is why this is worth its cost: trap 10 says read a judge's reasons
            # before changing anything it flagged.
            print(f"      reason: {outcome.verdict.reason}")
        if outcome.error:
            print(f"      {outcome.error}")


@pytest.mark.live_claude_cli
def test_build_validator_discriminates_repairable_from_blocked(compiled_diagnostics):
    """The first measurement of this node's judgment (ADR-0057).

    **What is new here is not the loop.** Step 43 proves the loop heals four error classes with a
    scripted verdict standing in for this node. This asks whether the node itself, on a real model,
    returns the verdict the loop was handed for free.
    """
    per_sample: list[list[CaseOutcome]] = []

    with collect_usage() as usage:
        for sample in range(1, SAMPLES + 1):
            outcomes = [_attempt(case, compiled_diagnostics[case.name]) for case in CASES]
            per_sample.append(outcomes)
            _report(sample, outcomes)

    graded = [
        [o for o in outcomes if o.case.ground is not Ground.REPO_HISTORY]
        for outcomes in per_sample
    ]

    print("\n=== summary " + "=" * 48)
    print(f"  samples:          {SAMPLES}")
    print(f"  cases per sample: {len(CASES)} ({len(graded[0])} graded)")
    print(f"  calls:            {SAMPLES * len(CASES)}")
    print(f"  input tokens:     {usage.input_tokens:,}")
    print(f"  output tokens:    {usage.output_tokens:,}")
    # `notional_cost_usd` is `None` on the SDK backend, which reports no cost by design.
    # `calls_without_cost` is what tells a real total from a partial one, so both are printed.
    cost = usage.notional_cost_usd
    print(f"  cost (USD):       {cost:.4f}" if cost is not None else "  cost (USD):       n/a")
    print(f"  calls w/o cost:   {usage.calls_without_reported_cost} of {usage.model_calls}")

    malformed = [o for outcomes in per_sample for o in outcomes if o.verdict is None]
    print(f"  malformed:        {len(malformed)} of {SAMPLES * len(CASES)}")

    # Which cases moved between samples -- the thing R2.27 had no way to record.
    verdicts: dict[str, Counter] = {case.name: Counter() for case in CASES}
    for outcomes in per_sample:
        for outcome in outcomes:
            verdicts[outcome.case.name][
                outcome.verdict.outcome if outcome.verdict else "MALFORMED"
            ] += 1
    unstable = [name for name, counts in verdicts.items() if len(counts) > 1]
    print(f"  unstable cases:   {unstable or 'none'}")

    reported = [
        o for outcomes in per_sample for o in outcomes if o.case.ground is Ground.REPO_HISTORY
    ]
    print("\n  reported, not asserted (REPO_HISTORY):")
    for outcome in reported:
        got = outcome.verdict.outcome if outcome.verdict else "MALFORMED"
        print(f"    {outcome.case.name}: expected={outcome.case.expected.value} got={got}")

    # --- The bars, per run rather than to the mean ---
    for sample, outcomes in enumerate(graded, start=1):
        proven = [o for o in outcomes if o.case.ground is Ground.COMPILER_PROVEN]
        absent = [o for o in outcomes if o.case.ground is Ground.SYMBOL_ABSENT]

        missed_heal = [o.case.name for o in proven if not o.correct]
        assert not missed_heal, (
            f"sample {sample}: {missed_heal} were judged unrepairable, but the heal loop repairs "
            "these exact bodies under real Maven"
        )

        wrongly_repairable = [
            o.case.name
            for o in absent
            if o.verdict is not None and o.verdict.outcome == Verdict.REPAIRABLE.value
        ]
        assert not wrongly_repairable, (
            f"sample {sample}: {wrongly_repairable} were judged repairable, but the symbol each "
            "needs is absent from the project -- this is the direction that spends the whole heal "
            "budget on a build that cannot be fixed"
        )
