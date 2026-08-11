"""`build_validator` -- decide what a failed build means, and whether a model can fix it.

`local_compiler` answers "did it build, and where did it break". This node answers the question
step 42's loop actually needs: **is this something the generator can repair, or will asking it to
just burn attempts?** Getting that wrong is the expensive failure mode. A loop that treats an
unfixable input as fixable spends its whole budget rewriting code that was never the problem, and
ends by handing a human three worse versions of it.

**The deterministic half runs first, and one of its checks is only possible because of the
markers.** `rendering/java_processor.py` brackets the model-authored region in every generated
file, so a diagnostic's line number can be attributed: inside the region a model wrote it, outside
it the renderer did. **An error in rendered scaffolding is a defect in this repo, not in the
model's output**, and asking a model to repair it invites exactly the thing the split exists to
prevent -- a model rewriting deterministic code to make a symptom go away. That case is refused
here rather than passed to the loop.

**The model half is a triage, not a review.** For errors that really are in the model's own
statements, the judgment worth paying for is whether the body is wrong (`cannot find symbol:
method setScaleTypo` -- rewrite it) or whether the *inputs the body was given* are wrong (`cannot
find symbol: class TranCatBalWithRate` -- a type the design named and nothing generates, which no
rewrite of this method will conjure). The first is repairable; the second must stop. That
distinction is not hypothetical: the first real generate call in this repo failed for the second
reason, and a loop lacking this check would have retried it three times.

Same posture as every other node here: injectable model call, no repair-retry of its own (that is
step 42's whole job), and a malformed response raises rather than being guessed past.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.model_routing import RoutingDecision, resolve_routing
from cobol_modernizer.core.structured_output import strip_code_fence
from cobol_modernizer.prompts_registry_client.loader import prompt_path
from cobol_modernizer.rendering.java_processor import (
    model_authored_line_numbers,
    model_authored_line_range,
)
from cobol_modernizer.tools.local_compiler import CompileDiagnostic, CompileResult

logger = logging.getLogger(__name__)

_NODE_NAME = "build_validator"

_REQUIRED_KEYS = frozenset({"repairable", "reason", "instruction"})

#: `accepted` -- the build succeeded and nothing is owed.
#: `repairable` -- every error is in the model's own statements and a rewrite could plausibly fix
#: it; `instruction` says what to change.
#: `blocked` -- a rewrite cannot fix this. The design is wrong, the renderer is wrong, or the
#: failure has no location at all. **Step 42 must stop on this, not retry.**
Outcome = Literal["accepted", "repairable", "blocked"]

AdviseFn = Callable[[RoutingDecision, str, str], str]


class BuildValidatorParseError(Exception):
    """The validator model's response could not be parsed into a verdict.

    Distinct from a *blocked* verdict on purpose: one means the model broke its contract, the other
    means the model read the diagnostics correctly and concluded a rewrite cannot help. Collapsing
    them would let a malformed response silently look like a considered "stop".
    """


@dataclass(frozen=True)
class ValidationVerdict:
    """What step 42 needs to decide its next move."""

    outcome: Outcome
    reason: str
    #: What to tell the generator, when and only when `outcome == "repairable"`.
    instruction: str = ""
    #: Errors attributed to the model's own statements.
    model_region_errors: tuple[CompileDiagnostic, ...] = ()
    #: Errors in rendered scaffolding -- a defect in this repo, never the model's to repair.
    rendered_region_errors: tuple[CompileDiagnostic, ...] = ()

    @property
    def should_retry(self) -> bool:
        return self.outcome == "repairable"


def attribute_errors(
    result: CompileResult, generated_sources: dict[str, str]
) -> tuple[tuple[CompileDiagnostic, ...], tuple[CompileDiagnostic, ...]]:
    """Split errors into (model-authored, rendered), by line against each file's marked region.

    A diagnostic in a file this run did not generate -- a hand-written template class, say -- counts
    as rendered: it is certainly not something the model wrote in this attempt, and the conservative
    reading is the one that refuses to ask a model to change it.

    `generated_sources` maps the same project-relative path `local_compiler` reports to the file's
    rendered text. Keying on the compiler's own path form is deliberate: reconstructing it here
    would be a second place for the `/C:/` handling to be got wrong.
    """
    model_authored: list[CompileDiagnostic] = []
    rendered: list[CompileDiagnostic] = []

    for error in result.errors:
        source = generated_sources.get(error.file)
        authored = model_authored_line_numbers(source) if source is not None else frozenset()
        if error.line is not None and error.line in authored:
            model_authored.append(error)
        else:
            rendered.append(error)

    return tuple(model_authored), tuple(rendered)


def classify(
    result: CompileResult, generated_sources: dict[str, str]
) -> ValidationVerdict | None:
    """The deterministic verdict, or `None` when a model's judgment is genuinely needed.

    Everything decidable without a model is decided here, so the only calls made are the ones that
    buy something.
    """
    if result.succeeded:
        return ValidationVerdict(outcome="accepted", reason="the build succeeded")

    if result.has_unparsed_failure:
        return ValidationVerdict(
            outcome="blocked",
            reason=(
                "the build failed with no located diagnostic, so there is nothing to repair; "
                "raw_output needs a human"
            ),
        )

    model_errors, rendered_errors = attribute_errors(result, generated_sources)

    if rendered_errors:
        locations = ", ".join(f"{e.file}:{e.line}" for e in rendered_errors)
        return ValidationVerdict(
            outcome="blocked",
            reason=(
                f"{len(rendered_errors)} error(s) are in rendered scaffolding, not in "
                f"model-authored statements ({locations}). That is a defect in this repo's "
                "renderer; asking a model to repair it would let it rewrite deterministic code"
            ),
            model_region_errors=model_errors,
            rendered_region_errors=rendered_errors,
        )

    if not model_errors:
        return ValidationVerdict(
            outcome="blocked",
            reason=(
                "the build failed but no error could be attributed to a generated file; "
                "the failure is outside anything this run produced"
            ),
        )

    # Errors are genuinely in the model's own statements. Whether a rewrite can fix them depends on
    # what the diagnostics mean, which is the one judgment worth a model call.
    return None


def build_validator_prompt(
    errors: tuple[CompileDiagnostic, ...], generated_sources: dict[str, str]
) -> str:
    """The diagnostics, and the model-authored statements they point at.

    Only the marked regions are included, not whole files: the rendered scaffolding is not what is
    being judged, and sending it would invite a verdict about code the model is not allowed to
    change.
    """
    lines = ["## Compile errors in model-authored statements", ""]
    lines += [error.render() for error in errors]

    lines += ["", "## The statements those errors point at", ""]
    for path in sorted({error.file for error in errors}):
        source = generated_sources.get(path)
        span = model_authored_line_range(source) if source else None
        if source is None or span is None:
            continue
        source_lines = source.splitlines()

        # The imports the model supplied, when one of them is what broke (G30). Without these the
        # validator would be shown a body that is fine and asked why the build failed -- the error
        # points at a line the prompt does not contain, which is how a repairable failure gets
        # judged unrepairable.
        import_numbers = sorted(
            number
            for number in model_authored_line_numbers(source)
            if not span[0] <= number <= span[1]
        )
        if import_numbers:
            lines.append(f"### {path} -- imports this model supplied")
            lines += [f"{number}: {source_lines[number - 1].strip()}" for number in import_numbers]
            lines.append("")

        body = source_lines[span[0] - 1 : span[1]]
        lines.append(f"### {path} (lines {span[0]}-{span[1]})")
        lines += [f"{span[0] + offset}: {text}" for offset, text in enumerate(body)]
        lines.append("")
    return "\n".join(lines)


def _parse_verdict(raw_response: str) -> tuple[bool, str, str]:
    try:
        payload = json.loads(strip_code_fence(raw_response))
    except json.JSONDecodeError as exc:
        raise BuildValidatorParseError(f"{_NODE_NAME} response is not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or not _REQUIRED_KEYS <= set(payload):
        missing = sorted(_REQUIRED_KEYS - set(payload)) if isinstance(payload, dict) else ["all"]
        raise BuildValidatorParseError(f"{_NODE_NAME} response missing required key(s): {missing}")

    repairable, reason, instruction = (
        payload["repairable"], payload["reason"], payload["instruction"]
    )
    if not isinstance(repairable, bool):
        raise BuildValidatorParseError(
            f"{_NODE_NAME} returned a non-boolean `repairable`: {repairable!r}"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise BuildValidatorParseError(f"{_NODE_NAME} returned an empty or non-string `reason`")
    if not isinstance(instruction, str):
        raise BuildValidatorParseError(
            f"{_NODE_NAME} returned a non-string `instruction`: {instruction!r}"
        )
    if repairable and not instruction.strip():
        raise BuildValidatorParseError(
            f"{_NODE_NAME} called the failure repairable but gave no instruction to repair it"
        )
    return repairable, reason.strip(), instruction.strip()


def _default_advise(routing: RoutingDecision, system_prompt: str, user_content: str) -> str:
    return call_model(
        _NODE_NAME,
        routing.model,
        system_prompt,
        user_content,
        effort=routing.effort,
        max_output_tokens=routing.max_output_tokens,
    ).text


def _load_system_prompt() -> str:
    return prompt_path(_NODE_NAME).read_text(encoding="utf-8")


def validate_build(
    result: CompileResult,
    generated_sources: dict[str, str],
    *,
    tier: ComplexityTier = ComplexityTier.SIMPLE,
    model_routing_config: Path | None = None,
    advise: AdviseFn = _default_advise,
) -> ValidationVerdict:
    """Decide whether a failed build is worth another generation attempt.

    Args:
        result: What `local_compiler.compile_project` returned.
        generated_sources: Project-relative path -> rendered Java, for every file this run wrote.
            Keyed exactly as `local_compiler` reports paths.
        tier: Routing tier. Defaults to `SIMPLE` -- unlike generation, this is a short triage over
            a handful of diagnostics, and the routing config prices it accordingly.
        model_routing_config: Overrides the routing config path -- tests only.
        advise: Overrides the live model call -- tests only.

    Raises:
        core.model_routing.ModelRoutingConfigError: the routing config is missing or malformed.
        BuildValidatorParseError: the model broke its response contract.
    """
    deterministic = classify(result, generated_sources)
    if deterministic is not None:
        logger.info(
            "%s: %s (deterministic) -- %s",
            _NODE_NAME, deterministic.outcome, deterministic.reason,
        )
        return deterministic

    model_errors, rendered_errors = attribute_errors(result, generated_sources)
    routing_kwargs = {"config_path": model_routing_config} if model_routing_config else {}
    routing = resolve_routing(_NODE_NAME, tier, **routing_kwargs)

    logger.info(
        "%s: %d model-authored error(s), asking %s whether a rewrite can fix them",
        _NODE_NAME, len(model_errors), routing.model,
    )

    repairable, reason, instruction = _parse_verdict(
        advise(routing, _load_system_prompt(), build_validator_prompt(model_errors, generated_sources))
    )

    verdict = ValidationVerdict(
        outcome="repairable" if repairable else "blocked",
        reason=reason,
        instruction=instruction if repairable else "",
        model_region_errors=model_errors,
        rendered_region_errors=rendered_errors,
    )
    logger.info("%s: %s -- %s", _NODE_NAME, verdict.outcome, verdict.reason)
    return verdict
