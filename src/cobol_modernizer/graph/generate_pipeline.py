"""The self-healing generate loop: write, compile, judge, repair -- at most three times.

**Sequential, deliberately, where `design` fans out.** `design_graph` runs one branch per program
because programs are independent reads. Generation is not: every step writes into **one** project
directory and every compile builds **all** of it, so two concurrent attempts would see each other's
half-written files and attribute each other's errors. The fan-out that makes `design` fast would
make this incorrect, which is a better reason to serialise than performance.

**The loop's hard job is refusing to retry.** Retrying is easy and cheap to write; knowing when not
to is what keeps a bounded budget from being spent on a problem no rewrite can reach. Three
conditions stop it immediately, none of which is "attempts exhausted":

- a `blocked` verdict from `build_validator` -- the design is wrong, or the error is in rendered
  scaffolding, and no rewrite of a method body reaches either;
- a `ToolchainNotFoundError` -- there is no JDK or no Maven, so nothing here is about the code;
- a `CompileTimeoutError` -- a build that never finished says nothing about whether it would have
  compiled, and asking a model to fix source that may be perfectly valid is worse than stopping.

Only a `repairable` verdict costs an attempt. That is why `build_validator` exists at all: without
it, every one of those three would look like "it failed, try again".

**`MAX_HEAL_ATTEMPTS` is defined here and nowhere else.** `model_client.MAX_TRANSPORT_ATTEMPTS`
bounds retries of a single HTTP call against a 429; this bounds how many times a model is asked to
rewrite code. They are unrelated quantities that multiply if anything ever confuses them -- the
failure ADR-0013 describes for SDK retry stacking on this module's own loop, invisible in both
layers' logs and quadratic in cost.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.contracts import BatchStepDesign, DomainEntity, ProgramDesignEntry
from cobol_modernizer.nodes.build_validator import AdviseFn, ValidationVerdict, validate_build
from cobol_modernizer.nodes.modernization_engineer import (
    AuthorFn,
    GeneratedProcessor,
    RepairContext,
    generate_processor,
)
from cobol_modernizer.rendering.java_processor import model_authored_line_range
from cobol_modernizer.tools.local_compiler import (
    CompileResult,
    compile_project,
)

logger = logging.getLogger(__name__)

#: How many times a model may be asked to rewrite statements that did not compile. Three, per the
#: plan. **Not** `model_client.MAX_TRANSPORT_ATTEMPTS`, which bounds something else entirely --
#: see the module docstring for why keeping them apart matters.
MAX_HEAL_ATTEMPTS = 3


@dataclass(frozen=True)
class StepOutcome:
    """What happened to one batch step, including everything a gate would want to see."""

    program_name: str
    step_name: str
    class_name: str
    #: `compiled` -- the project built with this step's file in it.
    #: `blocked` -- something no rewrite reaches; `reason` says what.
    #: `exhausted` -- every heal attempt was spent and it still does not compile.
    status: str
    attempts: int
    reason: str
    java_source: str = ""
    relative_path: str = ""
    #: Whatever the generator flagged it could not translate faithfully, across every attempt.
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def succeeded(self) -> bool:
        return self.status == "compiled"


def processor_relative_path(package: str, class_name: str) -> str:
    """Where a rendered processor goes, in the same project-relative POSIX form the compiler reports.

    Kept in one place because `build_validator` keys its attribution on exactly this string: if the
    path written and the path reported ever diverge, every diagnostic silently becomes "a file this
    run did not generate" and the loop stops attributing anything to the model.
    """
    return f"src/main/java/{package.replace('.', '/')}/{class_name}.java"


def _extract_body(java_source: str) -> str:
    """The model-authored statements back out of a rendered file, for the next repair prompt."""
    span = model_authored_line_range(java_source)
    if span is None:
        return ""
    lines = java_source.splitlines()[span[0] - 1 : span[1]]
    return "\n".join(line.strip() for line in lines)


def heal_step(
    worktree_root: Path,
    project_dir: Path,
    program_entry: ProgramDesignEntry,
    step: BatchStepDesign,
    entities: list[DomainEntity],
    *,
    package: str,
    input_type: str,
    output_type: str,
    tier: ComplexityTier = ComplexityTier.COMPLEX,
    max_attempts: int = MAX_HEAL_ATTEMPTS,
    author: AuthorFn | None = None,
    advise: AdviseFn | None = None,
) -> StepOutcome:
    """Generate one step's processor, compiling after each attempt and repairing while it is worth it.

    Writes the rendered file into `project_dir` on every attempt, including the last failed one:
    a human reviewing a failure needs the code that failed, not the absence of it.

    Raises:
        tools.local_compiler.ToolchainNotFoundError: no JDK or no Maven. Deliberately propagated
            rather than converted into a failed outcome -- it is not a fact about the generated code.
        tools.local_compiler.CompileTimeoutError: a build exceeded its ceiling. Same reasoning.
        nodes.modernization_engineer.ModernizationEngineerParseError: the generator broke its
            response contract, which is not something a compile diagnostic can repair.
    """
    # `None` means "use the node's own default", so this module never reaches into another
    # module's privates to name a live model call it does not own.
    author_kwargs = {"author": author} if author is not None else {}
    advise_kwargs = {"advise": advise} if advise is not None else {}

    class_name = ""
    relative_path = ""
    java_source = ""
    notes: list[str] = []
    repair: RepairContext | None = None
    verdict: ValidationVerdict | None = None
    result: CompileResult | None = None

    for attempt in range(1, max_attempts + 1):
        generated: GeneratedProcessor = generate_processor(
            worktree_root,
            program_entry,
            step,
            entities,
            package=package,
            input_type=input_type,
            output_type=output_type,
            tier=tier,
            repair=repair,
            **author_kwargs,
        )
        class_name = generated.class_name
        java_source = generated.java_source
        relative_path = processor_relative_path(package, class_name)
        if generated.notes:
            notes.append(generated.notes)

        destination = project_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(java_source, encoding="utf-8")

        result = compile_project(project_dir, goal="compile")
        if result.succeeded:
            logger.info(
                "generate: %s/%s compiled on attempt %d", program_entry.program_name,
                step.step_name, attempt,
            )
            return StepOutcome(
                program_name=program_entry.program_name,
                step_name=step.step_name,
                class_name=class_name,
                status="compiled",
                attempts=attempt,
                reason=f"compiled on attempt {attempt}",
                java_source=java_source,
                relative_path=relative_path,
                notes=tuple(notes),
            )

        verdict = validate_build(result, {relative_path: java_source}, **advise_kwargs)
        if verdict.outcome != "repairable":
            logger.warning(
                "generate: %s/%s blocked after attempt %d -- %s",
                program_entry.program_name, step.step_name, attempt, verdict.reason,
            )
            return StepOutcome(
                program_name=program_entry.program_name,
                step_name=step.step_name,
                class_name=class_name,
                status="blocked",
                attempts=attempt,
                reason=verdict.reason,
                java_source=java_source,
                relative_path=relative_path,
                notes=tuple(notes),
            )

        repair = RepairContext(
            previous_body=_extract_body(java_source),
            diagnostics=verdict.model_region_errors,
            instruction=verdict.instruction,
            attempt=attempt + 1,
        )

    reason = (
        f"still failing after {max_attempts} attempt(s); last verdict: "
        f"{verdict.reason if verdict else 'none'}"
    )
    logger.warning(
        "generate: %s/%s exhausted -- %s", program_entry.program_name, step.step_name, reason
    )
    return StepOutcome(
        program_name=program_entry.program_name,
        step_name=step.step_name,
        class_name=class_name,
        status="exhausted",
        attempts=max_attempts,
        reason=reason,
        java_source=java_source,
        relative_path=relative_path,
        notes=tuple(notes),
    )
