"""The judge: prompt, response contract, and how a run is scored.

**The judge model is pinned here, not routed.** Every other model call in this repo resolves through
`core/model_routing.py`, and that is right for a pipeline node -- routing is where cost and capability
get traded off per node and per complexity tier. It is wrong for a measuring instrument. A judge
whose model follows the routing config is a yardstick that silently changes length whenever someone
retunes routing for an unrelated node, and the resulting score movement would be indistinguishable
from a real change in generator quality. So the model id is a constant in this module, and changing
it is an edit a reviewer sees.

**The judge never learns the answer.** `EvalCase` carries `failing_criterion` and `evidence`, and
neither goes anywhere near the prompt -- `test_the_prompt_never_leaks_what_the_case_expects` is the
guard, and it is the single most important test in this package. A prompt that leaks the expected
verdict produces a perfect score and measures nothing.

**COBOL reaches the judge wrapped, like everywhere else.** `wrap_untrusted_cobol` is not a formality
here: the source under judgement is tenant data, and a judge is a model reading it. The rule that
COBOL is data and never instructions (comments included) does not stop applying because the caller is
a test.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Callable
from dataclasses import dataclass

from cobol_modernizer.core.guardrails import wrap_untrusted_cobol
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.structured_output import strip_code_fence
from cobol_modernizer.rendering.java_processor import render_processor
from tests.evaluations.corpus import (
    CRITERIA,
    CRITERIA_BY_ID,
    DOWNSTREAM_BY_STEP,
    EvalCase,
    Ground,
    Verdict,
)

#: The judge, pinned. See the module docstring on why this is not a routing decision.
#:
#: Opus 5 rather than the cheaper tier, and unlike `spec_critic` that choice is *not* yet backed by a
#: benchmark -- `spec_critic` earned its Haiku pin by being measured against both. Doing the same here
#: needs a billed run per model on this corpus, which is exactly what this harness makes possible and
#: has not yet been spent. Recorded as an open question rather than presented as a decision.
JUDGE_MODEL = "claude-opus-5"

#: `low` deliberately: the judgement is a close reading of about thirty lines against four stated
#: criteria, not a search problem. High effort here buys tokens rather than accuracy, and an
#: instrument that costs more than the thing it measures does not get run.
JUDGE_EFFORT = "low"

_NODE_NAME = "eval_judge"

SYSTEM_PROMPT = """\
You are reviewing one generated Java method body against the COBOL paragraph it was translated from.

Your job is fidelity, not style. Do not comment on naming, formatting, efficiency, or whether the
Java is idiomatic. The only question is whether this body does what the COBOL does.

You will be given a rubric of named criteria. Answer each one independently with exactly one of:

  "pass"           -- the body satisfies this criterion
  "fail"           -- the body violates this criterion
  "not_applicable" -- this criterion has nothing to say about this body

Use "not_applicable" honestly. A body that performs no arithmetic is not passing an arithmetic
criterion, and recording it as a pass makes the measurement noisier. Equally, do not reach for
"not_applicable" to avoid a hard call on a criterion that clearly does apply.

Only the region between the BEGIN and END model-authored markers is under review. Everything outside
those markers is generated deterministically from a design document and is not the subject of this
judgement.

Respond with a JSON array and nothing else. One object per criterion, in the order given:

[{"criterion": "<id>", "verdict": "pass|fail|not_applicable", "rationale": "<one or two sentences>"}]
"""


class JudgeResponseParseError(Exception):
    """The judge broke its response contract.

    Distinct from a `FAIL` verdict for the same reason `BuildValidatorParseError` is distinct from a
    blocked one: a malformed response means the measurement did not happen, and scoring it as though
    the judge had an opinion would put noise into the number instead of a gap.
    """


AdjudicateFn = Callable[[str, str], str]


def render_rubric() -> str:
    """The criteria, with the reason each one exists.

    The rationale is included rather than trimmed for tokens. A criterion stated bare -- *"check the
    rounding mode"* -- gets checked literally; the same criterion with *"truncation and rounding
    agree on most inputs and disagree by a cent on the rest"* beside it gets checked for the thing
    that actually goes wrong. That is the same reasoning `render_step_facts` applies to the guard.
    """
    lines = ["## Rubric", ""]
    for criterion in CRITERIA:
        lines += [
            f"### {criterion.id}",
            "",
            criterion.question,
            "",
            f"Why this is on the list: {criterion.rationale}",
            "",
        ]
    return "\n".join(lines)


def render_cobol_facts(cobol_source: str, *, program: str) -> str:
    """The program's source, wrapped. Identical for every case of one program, so it goes early."""
    return wrap_untrusted_cobol(cobol_source, source_label=program)


def render_step_facts(case: EvalCase) -> str:
    """The step under judgement -- short, varies per case, and therefore late in the prompt."""
    step = case.step
    guard = (
        f"`{step.guard_condition}` (the COBOL performs this step only when it holds)"
        if step.guard_condition
        else "none -- the COBOL performs this step for every input record"
    )
    lines = [
        "## The step",
        "",
        f"- Step: {step.step_name} ({step.role})",
        f"- Description: {step.description}",
        f"- Source COBOL paragraph(s): {', '.join(step.source_paragraphs) or '(none recorded)'}",
        f"- Signature: {step.output_type} process({step.input_type} item)",
        f"- Guard condition: {guard}",
    ]
    downstream = DOWNSTREAM_BY_STEP.get(step.step_name)
    if downstream:
        # Keyed by step, never by case -- see `DOWNSTREAM_BY_STEP`. Without it the judge cannot tell a
        # placeholder in a carried record from a short value in a written one, and the first billed
        # run showed it calling the first the second.
        lines += ["", f"- **What happens to this step's output**: {downstream}"]
    return "\n".join(lines)


def render_generated_java(case: EvalCase) -> str:
    """The body as it actually ships -- rendered, with its markers.

    Judged as a rendered file rather than as a bare body so the judge sees the signature it must
    satisfy and the marker boundary it must stay inside. Both change what a correct answer looks
    like: `return null` reads as a bug in isolation and is the required translation of a guard.
    """
    rendered = render_processor(
        case.step,
        package="com.modernized.batch.processor",
        class_name="GeneratedProcessor",
        input_type=case.step.input_type,
        output_type=case.step.output_type,
        body=case.body,
        body_imports=case.imports,
        authored_by="under-evaluation",
    )
    return f"## The generated Java\n\n```java\n{rendered.strip()}\n```"


def build_judge_prompt(case: EvalCase, cobol_source: str, *, program: str = "CBACT04C") -> str:
    """Stable content first, the varying part last -- exactly as in `build_engineer_prompt`.

    **The ordering was wrong on the first pass and it is worth recording why.** The step facts sat
    ahead of the COBOL, which put a ~25k-token source file -- identical for every case -- behind a
    block that changes with the step. That is G13's shape and ADR-0017's correction, reintroduced in
    a new module: the shared span stops being a *prefix* and a cache cannot see it. Rubric and source
    now lead, so every case of one program shares everything up to a few hundred characters of step
    facts and the Java under judgement.
    """
    return (
        f"{render_rubric()}\n\n{render_cobol_facts(cobol_source, program=program)}\n\n"
        f"{render_step_facts(case)}\n\n{render_generated_java(case)}"
    )


@dataclass(frozen=True)
class JudgeAnswer:
    """One judge response: what it decided, **and why**.

    The rationales exist because the first billed run needed them and they were not there. The
    original `parse_judge_response` returned verdicts and dropped the reasoning on the floor, so when
    the judge disagreed with the corpus there was no way to tell a judge error from a corpus error
    without paying for another run. For an instrument whose entire output is a disagreement, the
    reasoning *is* the finding -- the verdict is just its index.
    """

    verdicts: dict[str, Verdict]
    rationales: dict[str, str]


def parse_judge_response(raw_response: str) -> JudgeAnswer:
    """Parse the judge's array into verdicts and rationales, or raise.

    Strict on every axis a silent partial answer could take: unknown criterion ids, missing ones,
    duplicates, and unrecognised verdict strings all raise. A judge that answers three of four
    criteria and is scored as though the fourth passed would report a miss as a clean run.

    **A missing or empty `rationale` raises too**, which is the `modernization_engineer` precedent --
    `notes` is mandatory there so that having nothing to say is a statement rather than an omission.
    The same argument applies with more force here, since a rationale is the only thing that makes a
    surprising verdict diagnosable without spending money again.
    """
    try:
        payload = json.loads(strip_code_fence(raw_response))
    except json.JSONDecodeError as exc:
        raise JudgeResponseParseError(f"judge response is not valid JSON: {exc}") from exc

    if not isinstance(payload, list):
        raise JudgeResponseParseError(
            f"judge response is not a JSON array: got {type(payload).__name__}"
        )

    verdicts: dict[str, Verdict] = {}
    rationales: dict[str, str] = {}
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict) or "criterion" not in entry or "verdict" not in entry:
            raise JudgeResponseParseError(
                f"judge response entry {index} is missing `criterion` or `verdict`: {entry!r}"
            )
        criterion_id = entry["criterion"]
        if criterion_id not in CRITERIA_BY_ID:
            raise JudgeResponseParseError(
                f"judge answered for an unknown criterion {criterion_id!r}"
            )
        if criterion_id in verdicts:
            raise JudgeResponseParseError(
                f"judge answered twice for criterion {criterion_id!r}"
            )
        try:
            verdicts[criterion_id] = Verdict(entry["verdict"])
        except ValueError as exc:
            raise JudgeResponseParseError(
                f"judge returned an unrecognised verdict for {criterion_id!r}: "
                f"{entry['verdict']!r}"
            ) from exc

        rationale = entry.get("rationale")
        if not isinstance(rationale, str) or not rationale.strip():
            raise JudgeResponseParseError(
                f"judge gave no rationale for {criterion_id!r}; a verdict whose reasoning is not "
                f"recorded cannot be diagnosed without paying for another run"
            )
        rationales[criterion_id] = rationale.strip()

    missing = sorted(set(CRITERIA_BY_ID) - set(verdicts))
    if missing:
        raise JudgeResponseParseError(f"judge did not answer for criterion(s): {missing}")
    return JudgeAnswer(verdicts=verdicts, rationales=rationales)


@dataclass(frozen=True)
class CaseResult:
    """What the judge said about one case, and whether it was right."""

    case: EvalCase
    answer: JudgeAnswer

    @property
    def verdicts(self) -> dict[str, Verdict]:
        return self.answer.verdicts

    @property
    def rationales(self) -> dict[str, str]:
        return self.answer.rationales

    @property
    def caught(self) -> bool | None:
        """Did the judge fail the criterion this case is defective on?

        `None` for a faithful case, which has no defect to catch. Deliberately not `False`: a
        faithful case counted as a miss would drag the detection rate down for being correct.
        """
        if self.case.failing_criterion is None:
            return None
        return self.verdicts[self.case.failing_criterion] is Verdict.FAIL

    @property
    def false_positives(self) -> tuple[str, ...]:
        """Criteria the judge failed that this case does not violate.

        That is the number § 4b of the feasibility assessment says decides the economics: human
        review is three to four orders of magnitude above inference cost, so a judge that cries wolf
        sends work to the expensive place. A harness that only counted misses would rate such a judge
        perfect.

        **`impure_criteria` are excluded, and that is a correction rather than an excuse.** This
        metric scores the judge against the corpus, so a criterion a case genuinely violates would
        otherwise be counted as the judge's error when it is the corpus's. Twice the judge failed a
        body labelled faithful and was right about the code. A case may never list the criterion it
        is supposed to fail, so this cannot hide a miss -- only a known impurity.
        """
        excluded = {self.case.failing_criterion, *self.case.impure_criteria}
        return tuple(
            criterion_id
            for criterion_id, verdict in sorted(self.verdicts.items())
            if verdict is Verdict.FAIL and criterion_id not in excluded
        )

    @property
    def correct(self) -> bool:
        """Found the defect if there was one, and invented none either way.

        `is not False` rather than `in (True, None)`: the three-state `caught` reads badly through a
        membership test, and `1 in (True, None)` being true is the kind of thing that survives review
        and bites later.
        """
        return self.caught is not False and not self.false_positives


def score_case(case: EvalCase, answer: JudgeAnswer) -> CaseResult:
    return CaseResult(case=case, answer=answer)


#: Models this corpus is used to compare. The pin above is the one in force; these are the ones a
#: benchmark run measures against it.
#:
#: **`spec_critic`'s precedent is the whole reason this list exists.** ADR-0004 assigned it a cheaper
#: tier and flagged the choice to revisit empirically; the revisit ran the same corrupted narration
#: past both models and found Haiku matched Opus's detection at 2.3x lower cost, which is what turned
#: an assumption into a pin. ADR-0024 said outright that this judge's Opus pin was scaffolding until
#: the same measurement existed here.
#:
#: **That measurement now exists, and it came out the other way** (ADR-0049, 2026-08-23). Sampled
#: three runs each over this corpus, `claude-haiku-4-5-20251001` was ineligible on three independent
#: grounds and was struck from this list:
#:
#: 1. **5 of 21 calls did not hold the response contract** -- prose preambles ahead of the JSON,
#:    where the prompt says *"Respond with a JSON array and nothing else."* Its rates are therefore
#:    computed over the subset it answered.
#: 2. **Not reproducible.** `completion_short_source` scored correct in 1 of 3 runs and
#:    `completion_invented_tran_id` in 1 of 2 -- the exact defect ADR-0045's sampling was built to
#:    surface, found on the first sampled run.
#: 3. **False-positive rate 0.33 ± 0.29 (max 0.50)**, against Opus's 0.00 ± 0.00. It failed criteria
#:    on `completion_faithful`, a body with no defect. § 4b puts human review three to four orders of
#:    magnitude above inference cost, so that is the expensive direction to be wrong in.
#:
#: **And it was not cheaper.** 259,591 output tokens against Opus's 15,981 for the same work --
#: $1.93 against $2.18. The cost case that justified Haiku for `spec_critic` simply is not present
#: here, which is why the two decisions differ rather than one of them being inconsistent.
#:
#: Struck rather than reported, following exactly the precedent the paragraph above cites: both
#: Sonnets were removed from `spec_extractor` the same way, with the evidence written down.
CANDIDATE_JUDGES = ("claude-opus-5",)


def adjudicator_for(model: str) -> AdjudicateFn:
    """A live adjudicator bound to `model` -- the seam a benchmark parametrises over.

    The model is passed rather than read from `JUDGE_MODEL` so that comparing candidates does not
    require mutating the pin. A benchmark that had to reassign the module constant would leave the
    instrument in a different state depending on which test ran last, which is exactly the drift the
    pin exists to prevent.
    """

    def adjudicate(system_prompt: str, user_content: str) -> str:
        return call_model(
            _NODE_NAME,
            model,
            system_prompt,
            user_content,
            effort=JUDGE_EFFORT,
            backend="claude_cli",
        ).text

    return adjudicate


_default_adjudicate = adjudicator_for(JUDGE_MODEL)


def judge_case(
    case: EvalCase,
    cobol_source: str,
    *,
    adjudicate: AdjudicateFn = _default_adjudicate,
) -> CaseResult:
    """Score one case. `adjudicate` is the seam every test in this package uses instead of a model."""
    raw = adjudicate(SYSTEM_PROMPT, build_judge_prompt(case, cobol_source))
    return score_case(case, parse_judge_response(raw))


def attempt_case(
    case: EvalCase,
    cobol_source: str,
    *,
    adjudicate: AdjudicateFn = _default_adjudicate,
) -> CaseResult | MalformedResponse:
    """`judge_case`, but a response the contract forbids comes back as data.

    **Only the candidate benchmark uses this.** Everywhere else a malformed judge response should
    raise, because everywhere else there is one judge and a broken answer from it is a broken run.
    A benchmark exists precisely to find out whether a candidate *can* answer, and that question has
    an answer worth recording rather than an exception worth propagating.
    """
    raw = adjudicate(SYSTEM_PROMPT, build_judge_prompt(case, cobol_source))
    try:
        return score_case(case, parse_judge_response(raw))
    except JudgeResponseParseError as error:
        return MalformedResponse(
            case_name=case.name,
            error=type(error).__name__,
            # Enough of the response to see *how* it broke the contract -- prose preamble, prose
            # epilogue, truncation -- without pasting a page of it into a report.
            excerpt=" ".join(raw.split())[:160],
        )


@dataclass(frozen=True)
class MalformedResponse:
    """A case the judge answered in a shape the contract forbids, kept rather than raised.

    **Why this is data and not an exception.** `parse_judge_response` is deliberately strict, and it
    is right to be: a judge that answers three of four criteria and is scored as though the fourth
    passed would report a miss as a clean run. But a *benchmark comparing candidates* has a
    different job from a pipeline node. "This model cannot hold the response contract" is the single
    most decisive thing a candidate comparison can discover, and a harness that aborts on it turns
    its most useful finding into a stack trace and loses the other seventeen calls it had paid for.

    So the pipeline still refuses, and the benchmark records.
    """

    case_name: str
    error: str
    excerpt: str


@dataclass(frozen=True)
class BenchmarkSummary:
    """A whole run, reported so the two grounds cannot be quietly averaged together.

    Mixing them would let four agreements with this repo's own reading of the COBOL outvote a
    disagreement with a real JVM, which is the direction of error this package exists to avoid.

    **`malformed` is not cosmetic and the rates below are not valid without it.** Every rate here is
    computed over cases the judge actually answered, so a candidate that fails to answer the hard
    ones would score perfectly on the remainder. `answered_everything` is the guard, and the
    benchmark bars on it before it reads any rate.
    """

    results: tuple[CaseResult, ...]
    #: Which judge produced these. Carried on the summary rather than tracked by the caller, because
    #: a result without its model is not a measurement of anything -- and once the benchmark compares
    #: candidates, an assertion message naming the *pinned* model while reporting a candidate's score
    #: is worse than no message.
    model: str = ""
    #: Cases whose response could not be parsed at all. See the class docstring: the rates below
    #: mean nothing unless this is empty.
    malformed: tuple[MalformedResponse, ...] = ()

    @property
    def answered_everything(self) -> bool:
        return not self.malformed

    def _subset(
        self, *, ground: Ground | None = None, defective: bool | None = None
    ) -> tuple[CaseResult, ...]:
        return tuple(
            result
            for result in self.results
            if (ground is None or result.case.ground is ground)
            and (
                defective is None
                or (result.case.failing_criterion is not None) is defective
            )
        )

    def detection_rate(self, *, ground: Ground | None = None) -> float:
        """Fraction of defective cases whose defect the judge named. `nan` when there are none."""
        defective = self._subset(ground=ground, defective=True)
        if not defective:
            return float("nan")
        return sum(1 for result in defective if result.caught) / len(defective)

    def false_positive_rate(self, *, ground: Ground | None = None) -> float:
        """Fraction of *faithful* cases the judge failed on some criterion. `nan` when there are none."""
        faithful = self._subset(ground=ground, defective=False)
        if not faithful:
            return float("nan")
        return sum(1 for result in faithful if result.false_positives) / len(faithful)

    def render(self) -> str:
        """A table for the verification report -- the artifact this whole package exists to produce."""
        lines = ["| case | ground | expected | judge | correct |", "|---|---|---|---|---|"]
        for result in self.results:
            expected = result.case.failing_criterion or "(faithful)"
            failed = [c for c, v in sorted(result.verdicts.items()) if v is Verdict.FAIL]
            lines.append(
                f"| {result.case.name} | {result.case.ground.value} | {expected} | "
                f"{', '.join(failed) or '(none)'} | {'yes' if result.correct else 'NO'} |"
            )
        return "\n".join(lines)

    def render_disagreements(self) -> str:
        """Every verdict this run got wrong, **with the judge's own reasoning**.

        The part worth reading when a benchmark fails. A table of criterion names says a
        disagreement happened; only the rationale distinguishes a judge that misread the code from a
        corpus that mislabelled it -- which is exactly the question the first run could not answer,
        because rationales were not kept.
        """
        blocks: list[str] = []
        for result in self.results:
            if result.correct:
                continue
            wrong = list(result.false_positives)
            if result.caught is False and result.case.failing_criterion:
                wrong.append(f"{result.case.failing_criterion} (missed)")
            blocks.append(f"### {result.case.name}")
            for criterion_id in wrong:
                key = criterion_id.split(" ")[0]
                blocks.append(
                    f"- **{criterion_id}** -> {result.verdicts[key].value}: "
                    f"{result.rationales.get(key, '(no rationale)')}"
                )
        return "\n".join(blocks) if blocks else "(no disagreements)"
#: How many times a benchmark is run before its numbers are believed. See `SampledBenchmark`.
#:
#: Three rather than one because one is what produced the defect this exists to fix, and three is the
#: smallest *n* for which "every run cleared the bar" is a different statement from "the run cleared
#: the bar". Higher is better evidence and costs linearly -- each sample is one judge call per case,
#: per candidate model.
DEFAULT_SAMPLES = 3


@dataclass(frozen=True)
class Spread:
    """One metric observed across *n* runs, reported as a distribution rather than a number.

    **This type exists because a single float was the defect.** Pillar 22 crossed to ✅ at audit
    R2.23 on a run scoring 6 of 6 at a 0.00 false-positive rate, and was withdrawn at R2.27 when the
    same judge, same corpus, same prompt scored 4 of 6 at 0.50. Neither run was wrong; the *summary*
    was, because it had no way to say how much of what it reported was the instrument moving.
    """

    values: tuple[float, ...]

    @property
    def mean(self) -> float:
        return float("nan") if not self.values else statistics.fmean(self.values)

    @property
    def lowest(self) -> float:
        return float("nan") if not self.values else min(self.values)

    @property
    def highest(self) -> float:
        return float("nan") if not self.values else max(self.values)

    @property
    def stdev(self) -> float:
        """Sample standard deviation. `0.0` for a single run -- **not** `nan`.

        A lone run really does have no observed spread, and reporting `nan` there would make an
        un-sampled benchmark indistinguishable from one whose metric could not be computed. What
        stops a single run being read as *reproducible* is `is_constant` requiring more than one
        sample, not an arithmetic quirk.
        """
        if len(self.values) < 2:
            return 0.0
        if any(math.isnan(value) for value in self.values):
            return float("nan")
        return statistics.stdev(self.values)

    @property
    def is_constant(self) -> bool:
        """Every run agreed, **and there was more than one run.**

        The second clause is the whole point: `n=1` can never be evidence of reproducibility, and a
        property that returned `True` for it would let the original defect back in through the door
        this type was added to close.
        """
        return len(self.values) > 1 and len(set(self.values)) == 1

    def render(self) -> str:
        if not self.values:
            return "(no runs)"
        spread = "" if self.is_constant else f"  (min {self.lowest:.2f}, max {self.highest:.2f})"
        return f"{self.mean:.2f} ± {self.stdev:.2f}{spread}"


@dataclass(frozen=True)
class SampledBenchmark:
    """*n* runs of the same benchmark over the same corpus, reported as a distribution.

    **What ADR-0045 decided and this implements.** The harness was never wrong about any single run;
    it reported one sample of a non-deterministic instrument as though it were a measurement. This
    turns a judge run into *n* runs and reports the spread, so "the judge detects defects" becomes a
    claim with an error bar on it.

    **`unstable_cases` is the part worth reading.** A rate that moves tells you the instrument is
    noisy; it does not tell you *where*. R2.27's 6-of-6-then-4-of-6 could have been two different
    cases flipping, or one case flipping twice, and nothing recorded which -- so the finding could
    not be acted on, only noted. Per-case stability is what makes the next such run diagnosable.
    """

    samples: tuple[BenchmarkSummary, ...]
    model: str = ""

    def detection(self, *, ground: Ground | None = None) -> Spread:
        return Spread(tuple(s.detection_rate(ground=ground) for s in self.samples))

    def false_positives(self, *, ground: Ground | None = None) -> Spread:
        return Spread(tuple(s.false_positive_rate(ground=ground) for s in self.samples))

    @property
    def malformed(self) -> tuple[MalformedResponse, ...]:
        """Every unparseable response across every run, flattened."""
        return tuple(bad for sample in self.samples for bad in sample.malformed)

    @property
    def answered_everything(self) -> bool:
        return all(sample.answered_everything for sample in self.samples)

    def unstable_verdicts(self) -> dict[str, tuple[frozenset[str], ...]]:
        """Cases whose **raw verdicts** moved between runs, even where the score did not.

        **Reported, never barred, and the distinction is the point.** `unstable_cases` asks whether
        the harness's *numbers* repeat, which is what pillar 22's criterion is about and what gets
        quoted. This asks the finer question underneath: did the judge actually say the same thing?

        The first real sampled run showed they are not the same question. Opus 5 returned
        `1.00 / 0.67 / 0.00` on all three runs with no unstable case — and flagged `fixed_width_text`
        on `interest_rounds` in run 1, not in run 2, and on `interest_faithful` in run 3. The scored
        outcome absorbed all of it, because a criterion a case genuinely violates is excluded from
        its false positives and a case is `correct` as long as the defect was caught.

        So this is a **leading indicator**: churn here is what eventually crosses a threshold and
        moves a rate, which is the most likely mechanism behind R2.23's 6-of-6 becoming R2.27's
        4-of-6. Barring on it would be wrong — it would fail a judge whose reported numbers are
        perfectly stable — but leaving it unmeasured is how the last surprise stayed a surprise.
        """
        if not self.samples:
            return {}
        churn: dict[str, tuple[frozenset[str], ...]] = {}
        for name in (result.case.name for result in self.samples[0].results):
            observed = [
                frozenset(
                    criterion
                    for criterion, verdict in result.verdicts.items()
                    if verdict is Verdict.FAIL
                )
                for sample in self.samples
                for result in sample.results
                if result.case.name == name
            ]
            if len(set(observed)) > 1:
                churn[name] = tuple(observed)
        return churn

    def unstable_cases(self) -> dict[str, tuple[int, int]]:
        """Cases the judge did not score the same way every time: name -> (correct runs, total).

        Only genuinely unstable cases appear. A case wrong in every run is *consistent* and belongs
        in `render_disagreements` -- it is a finding about the judge or the corpus, not about
        reproducibility, and mixing the two would hide a steady defect inside a noise report.
        """
        if not self.samples:
            return {}
        unstable: dict[str, tuple[int, int]] = {}
        for name in (result.case.name for result in self.samples[0].results):
            outcomes = [
                result.correct
                for sample in self.samples
                for result in sample.results
                if result.case.name == name
            ]
            correct = sum(1 for outcome in outcomes if outcome)
            if 0 < correct < len(outcomes):
                unstable[name] = (correct, len(outcomes))
        return unstable

    @property
    def is_reproducible(self) -> bool:
        """Every run agreed on every case. **Stability, not eligibility** -- they are not the same.

        A judge that passes everything is perfectly reproducible and completely useless; its
        detection rate is what disqualifies it, on every one of those identical runs. Folding the
        two together would give a metric that cannot tell a noisy judge from a bad one, and that
        pair is exactly what R2.27 could not separate.

        So this answers *"can the numbers be trusted?"* and the bars answer *"are they good enough?"*
        -- and the bars are applied per run rather than to the mean, because a judge that catches
        every defect two runs in three has a mean of 0.67 and an eligibility of none. The run that
        matters is the one nobody is watching.
        """
        return len(self.samples) > 1 and not self.unstable_cases()

    def render(self) -> str:
        """The distribution table a verification entry quotes, with the per-case detail under it."""
        lines = [
            f"| metric | across {len(self.samples)} run(s) |",
            "|---|---|",
            f"| oracle-grounded detection | {self.detection(ground=Ground.ORACLE).render()} |",
            f"| source-grounded detection | {self.detection(ground=Ground.SOURCE).render()} |",
            f"| false-positive rate | {self.false_positives().render()} |",
            f"| reproducible | {'yes' if self.is_reproducible else 'NO'} |",
        ]
        if self.malformed:
            lines.append("")
            lines.append(
                f"**Malformed responses — {len(self.malformed)} of "
                f"{sum(len(s.results) + len(s.malformed) for s in self.samples)} calls did not hold "
                f"the response contract.** Every rate above is computed over the answered cases "
                f"only and is not a measurement of this candidate:"
            )
            for bad in self.malformed:
                lines.append(f"- `{bad.case_name}`: {bad.error} — {bad.excerpt}")
        unstable = self.unstable_cases()
        if unstable:
            lines.append("")
            lines.append("**Unstable across runs** — the same input scored differently:")
            for name, (correct, total) in sorted(unstable.items()):
                lines.append(f"- `{name}`: correct in {correct} of {total} runs")
        churn = self.unstable_verdicts()
        if churn:
            lines.append("")
            lines.append(
                "**Verdict churn** — the score was stable, the judge's answer was not. Recorded "
                "rather than barred: this is what crosses a threshold later and moves a rate."
            )
            for name, observed in sorted(churn.items()):
                seen = " | ".join(
                    ", ".join(sorted(verdicts)) or "(none)" for verdicts in observed
                )
                lines.append(f"- `{name}`: {seen}")
        return "\n".join(lines)
