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
from collections.abc import Callable
from dataclasses import dataclass

from cobol_modernizer.core.guardrails import wrap_untrusted_cobol
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.structured_output import strip_code_fence
from cobol_modernizer.rendering.java_processor import render_processor
from tests.evaluations.corpus import CRITERIA, CRITERIA_BY_ID, EvalCase, Verdict

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


def render_case_facts(case: EvalCase, cobol_source: str) -> str:
    """The step, the COBOL it came from, and the rendered Java -- and nothing about the answer."""
    step = case.step
    guard = (
        f"`{step.guard_condition}` (the COBOL performs this step only when it holds)"
        if step.guard_condition
        else "none -- the COBOL performs this step for every input record"
    )
    return "\n".join(
        [
            "## The step",
            "",
            f"- Step: {step.step_name} ({step.role})",
            f"- Description: {step.description}",
            f"- Source COBOL paragraph(s): {', '.join(step.source_paragraphs) or '(none recorded)'}",
            f"- Signature: {step.output_type} process({step.input_type} item)",
            f"- Guard condition: {guard}",
            "",
            wrap_untrusted_cobol(cobol_source, source_label=case.step.step_name),
        ]
    )


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


def build_judge_prompt(case: EvalCase, cobol_source: str) -> str:
    """Rubric first, then the case. Stable content leads, exactly as in `build_engineer_prompt`.

    The rubric is identical for every case in a run, so it is the cached prefix; the COBOL source is
    identical for every case of one program; the Java is what varies. Ordering them this way is worth
    the thought for the same reason ADR-0017 was: six cases re-sending a rubric behind a variable
    prefix is G13's shape in miniature.
    """
    return f"{render_rubric()}\n\n{render_case_facts(case, cobol_source)}\n\n{render_generated_java(case)}"


def parse_judge_response(raw_response: str) -> dict[str, Verdict]:
    """Parse the judge's array into `{criterion_id: Verdict}`, or raise.

    Strict on every axis a silent partial answer could take: unknown criterion ids, missing ones, and
    unrecognised verdict strings all raise. A judge that answers three of four criteria and is scored
    as though the fourth passed would report a miss as a clean run.
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

    missing = sorted(set(CRITERIA_BY_ID) - set(verdicts))
    if missing:
        raise JudgeResponseParseError(f"judge did not answer for criterion(s): {missing}")
    return verdicts


@dataclass(frozen=True)
class CaseResult:
    """What the judge said about one case, and whether it was right."""

    case: EvalCase
    verdicts: dict[str, Verdict]

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

        Every case in the corpus isolates exactly one defect, so any *other* `FAIL` is the judge
        flagging correct code. That is the number § 4b of the feasibility assessment says decides the
        economics: human review is three to four orders of magnitude above inference cost, so a judge
        that cries wolf sends work to the expensive place. A harness that only counted misses would
        rate such a judge perfect.
        """
        return tuple(
            criterion_id
            for criterion_id, verdict in sorted(self.verdicts.items())
            if verdict is Verdict.FAIL and criterion_id != self.case.failing_criterion
        )

    @property
    def correct(self) -> bool:
        return (self.caught in (True, None)) and not self.false_positives


def score_case(case: EvalCase, verdicts: dict[str, Verdict]) -> CaseResult:
    return CaseResult(case=case, verdicts=verdicts)


def _default_adjudicate(system_prompt: str, user_content: str) -> str:
    return call_model(
        _NODE_NAME,
        JUDGE_MODEL,
        system_prompt,
        user_content,
        effort=JUDGE_EFFORT,
        backend="claude_cli",
    ).text


def judge_case(
    case: EvalCase,
    cobol_source: str,
    *,
    adjudicate: AdjudicateFn = _default_adjudicate,
) -> CaseResult:
    """Score one case. `adjudicate` is the seam every test in this package uses instead of a model."""
    raw = adjudicate(SYSTEM_PROMPT, build_judge_prompt(case, cobol_source))
    return score_case(case, parse_judge_response(raw))


@dataclass(frozen=True)
class BenchmarkSummary:
    """A whole run, reported so the two grounds cannot be quietly averaged together.

    Mixing them would let four agreements with this repo's own reading of the COBOL outvote a
    disagreement with a real JVM, which is the direction of error this package exists to avoid.
    """

    results: tuple[CaseResult, ...]

    def _subset(self, *, ground=None, defective: bool | None = None) -> tuple[CaseResult, ...]:
        return tuple(
            result
            for result in self.results
            if (ground is None or result.case.ground is ground)
            and (
                defective is None
                or (result.case.failing_criterion is not None) is defective
            )
        )

    def detection_rate(self, *, ground=None) -> float:
        """Fraction of defective cases whose defect the judge named. `nan` when there are none."""
        defective = self._subset(ground=ground, defective=True)
        if not defective:
            return float("nan")
        return sum(1 for result in defective if result.caught) / len(defective)

    def false_positive_rate(self, *, ground=None) -> float:
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
