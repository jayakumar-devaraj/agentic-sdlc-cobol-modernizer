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

import json
import logging
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeType,
    ComputedValue,
    DesignDocument,
    DomainEntity,
    EquivalenceTestVerdict,
    JobParameter,
    ProgramDesignEntry,
    UnifiedDesign,
    WiringVerdict,
)
from cobol_modernizer.core.package_data import ORACLE_ROOT, TEMPLATES_ROOT
from cobol_modernizer.nodes.build_validator import AdviseFn, ValidationVerdict, validate_build
from cobol_modernizer.nodes.modernization_engineer import (
    AuthorFn,
    GeneratedProcessor,
    RepairContext,
    generate_processor,
)
from cobol_modernizer.rendering.java_aggregation import (
    aggregating_reader_class_name,
    render_aggregating_reader,
)
from cobol_modernizer.rendering.java_equivalence_test import (
    UnrenderableOracleError,
    render_equivalence_test,
)
from cobol_modernizer.rendering.java_file_bindings import (
    bindings_class_name,
    render_application_properties,
    render_file_bindings,
)
from cobol_modernizer.rendering.java_job import (
    UnrenderableJobError,
    _has_file_sink,
    aggregation_source,
    configuration_class_name,
    plan_steps,
    reads_a_file,
    render_job_configuration,
    render_staging,
    staging_class_name,
)
from cobol_modernizer.rendering.java_processor import (
    model_authored_line_numbers,
    model_authored_line_range,
)
from cobol_modernizer.rendering.java_reader import (
    UnrenderableReaderError,
    reader_class_name,
    render_item_reader,
)
from cobol_modernizer.rendering.java_records import render_composite, render_record
from cobol_modernizer.rendering.java_writer import (
    UnrenderableWriterError,
    render_item_writer,
    writer_class_name,
)
from cobol_modernizer.tools.local_compiler import (
    CompileResult,
    compile_project,
)

logger = logging.getLogger(__name__)

#: How many times a model may be asked to rewrite statements that did not compile. Three, per the
#: plan. **Not** `model_client.MAX_TRANSPORT_ATTEMPTS`, which bounds something else entirely --
#: see the module docstring for why keeping them apart matters.
MAX_HEAL_ATTEMPTS = 3

#: The baseline Maven project `generate` copies into an empty target repo (ADR-0009, ADR-0034).
TEMPLATE_DIR = TEMPLATES_ROOT / "target-spring-boot-baseline"

#: Where generated processors are declared. `card-service`'s own package, not this repo's.
DEFAULT_PACKAGE = "com.modernized.batch.processor"

#: Where rendered domain records and composites are declared. A separate package from the
#: processors so a reviewer can tell computed data shapes from model-authored logic by path
#: alone.
DEFAULT_DOMAIN_PACKAGE = "com.modernized.batch.domain"

#: Where the rendered wiring is declared (ADR-0066). Split by role for the same reason the domain
#: package is split from the processors: a reviewer can tell rendered infrastructure from
#: model-authored logic by path alone, and only the processor package holds anything a model wrote.
DEFAULT_READER_PACKAGE = "com.modernized.batch.reader"
DEFAULT_WRITER_PACKAGE = "com.modernized.batch.writer"
DEFAULT_JOB_PACKAGE = "com.modernized.batch.job"


@dataclass(frozen=True)
class StepOutcome:
    """What happened to one batch step, including everything a gate would want to see."""

    program_name: str
    step_name: str
    class_name: str
    #: `compiled` -- the project built with this step's file in it.
    #: `blocked` -- something no rewrite reaches; `reason` says what.
    #: `exhausted` -- every heal attempt was spent and it still does not compile.
    #: `not_generated` -- a non-processor step this pipeline does not render (G27). Reported
    #: rather than dropped: a reader or writer really is Spring Batch wiring, but
    #: `1050-UPDATE-ACCOUNT` is a balance mutation that cannot be an ItemProcessor, and the two
    #: are indistinguishable from a role alone.
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


class UnresolvedJobParameterError(Exception):
    """A step names a job parameter its job does not declare (ADR-0026).

    Joins the `UnsupportedPicConstructError` family: an unambiguous case that fails loudly rather
    than being guessed past. Rendering the constructor argument anyway would produce a class Spring
    instantiates with `null` -- a processor that compiles, starts, and writes a record with a hole
    in it, which is the shape of defect this repo exists to refuse.
    """


def _resolve_job_parameters(
    job: BatchJobDesign, step: BatchStepDesign
) -> list[JobParameter]:
    """The parameters `step` declares, looked up in `job`'s declarations."""
    declared = {parameter.name: parameter for parameter in job.job_parameters}
    missing = [name for name in step.job_parameters if name not in declared]
    if missing:
        raise UnresolvedJobParameterError(
            f"step {step.step_name!r} consumes job parameter(s) {missing} that job "
            f"{job.job_name!r} does not declare; declared: {sorted(declared)}"
        )
    return [declared[name] for name in step.job_parameters]


def _extract_model_imports(java_source: str) -> tuple[str, ...]:
    """The imports the model supplied, read back out of the rendered file (G30).

    Recovered from the artifact rather than threaded through the loop as state, for the same reason
    `_extract_body` is: the file is what compiled, so the file is what a repair should be shown. Two
    copies of "what the model wrote" -- one in the source, one carried alongside -- is a pair that can
    disagree, and the disagreement would surface as a repair aimed at a line that no longer says what
    the prompt claims.
    """
    span = model_authored_line_range(java_source)
    if span is None:
        return ()
    lines = java_source.splitlines()
    return tuple(
        lines[number - 1].strip().removeprefix("import ").split(";")[0].strip()
        for number in sorted(model_authored_line_numbers(java_source))
        if not span[0] <= number <= span[1]
    )


def materialize_target_project(output_dir: Path, template_dir: Path = TEMPLATE_DIR) -> bool:
    """Ensure `output_dir` is a Maven project, copying the baseline template in if it is not.

    Returns True when the template was copied, False when a project was already there.

    **Never overwrites an existing project.** `card-service` is a real repository (ADR-0009), and a
    second run that clobbered a reviewed scaffold would destroy work between the gate and the
    merge. The presence of a `pom.xml` is the test, because that is what `local_compiler` requires
    and what makes the directory buildable at all.
    """
    if (output_dir / "pom.xml").is_file():
        return False

    output_dir.mkdir(parents=True, exist_ok=True)
    for entry in template_dir.iterdir():
        target = output_dir / entry.name
        if target.exists():
            continue
        if entry.is_dir():
            shutil.copytree(entry, target, ignore=shutil.ignore_patterns("target"))
        else:
            shutil.copy2(entry, target)
    logger.info("generate: materialized the baseline template into %s", output_dir)
    return True


def render_domain_types(design: UnifiedDesign, output_dir: Path, *, package: str) -> list[str]:
    """Write every domain entity and composite into the target project. Returns the paths written.

    **Processors are generated against these types, so they have to exist before anything compiles.**
    Both are rendered rather than generated -- a record from a `DomainEntity` is a mechanical
    transform of `pic_mapper` output, and a composite is a mechanical transform of its declaration
    (ADR-0010's line, ADR-0020's composites).
    """
    written: list[str] = []
    for entity in design.domain_entities:
        relative = f"src/main/java/{package.replace('.', '/')}/{entity.name}.java"
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(render_record(entity, package=package), encoding="utf-8")
        written.append(relative)

    for composite in design.composite_types:
        relative = f"src/main/java/{package.replace('.', '/')}/{composite.name}.java"
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            render_composite(
                composite, package=package, computed_values=design.computed_values
            ),
            encoding="utf-8",
        )
        written.append(relative)

    logger.info("generate: rendered %d domain type(s) into %s", len(written), output_dir)
    return written


def processor_types(
    step: BatchStepDesign, design: UnifiedDesign, *, domain_package: str
) -> tuple[str, str] | None:
    """The `ItemProcessor<I, O>` type arguments for `step`, fully qualified, or `None`.

    `None` means a name resolved to neither a domain entity nor a declared composite, which is a
    design that cannot be generated from (ADR-0020). `run_generate` turns that into a blocked step
    naming the type rather than rendering Java against a class that will not exist.

    Fully qualified rather than imported: the processor lives in its own package, and a qualified
    name in the `implements` clause needs no import block to stay in sync with it.
    """
    if design.resolve_type(step.input_type) is None:
        return None
    if design.resolve_type(step.output_type) is None:
        return None
    return (f"{domain_package}.{step.input_type}", f"{domain_package}.{step.output_type}")


def _write_java(output_dir: Path, package: str, class_name: str, source: str) -> str:
    """Write one rendered class, and return its project-relative path."""
    relative = f"src/main/java/{package.replace('.', '/')}/{class_name}.java"
    destination = output_dir / Path(relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    return relative


def render_job_wiring(
    job: BatchJobDesign,
    design: UnifiedDesign,
    output_dir: Path,
    *,
    domain_package: str,
    processor_package: str,
    reader_package: str = DEFAULT_READER_PACKAGE,
    writer_package: str = DEFAULT_WRITER_PACKAGE,
    job_package: str = DEFAULT_JOB_PACKAGE,
) -> tuple[list[str], list[tuple[BatchStepDesign, str]]]:
    """Render everything that turns this job's processors into a job that runs (ADR-0066).

    Returns `(files written, steps plan_steps could not render)`. The second is returned rather than
    logged because a skipped step means business logic present in the COBOL is **absent from the
    generated project** -- ADR-0023's rule, applied to the wiring. A job rendered with three of nine
    steps runs, produces output, and is not the program.

    **Everything is rendered before anything is written**, and that is correctness rather than
    style. The bindings are rendered last and are the most likely to refuse -- a design with no
    declared file access paths reaches them after the job configuration is already on disk -- and a
    project left holding a configuration whose step beans have no reader is worse than one left
    holding nothing: it compiles, and then no Spring context in it can start. Caught in CI exactly
    that way.

    Nothing here is new rendering. Every renderer it calls but one has shipped in the package for
    weeks and was reachable only from an integration test; this is the call that was missing.
    """
    program_name = job.program_name
    renderable, skipped, staged = plan_steps(job, design, program_name)
    #: `(package, class name, source)`, accumulated and written only once all of it exists.
    pending: list[tuple[str, str, str]] = []

    for producer in staged:
        pending.append(
            (
                job_package,
                staging_class_name(producer),
                render_staging(producer, package=job_package, domain_package=domain_package),
            )
        )

    for step in renderable:
        # A control-break step reads a rendered aggregation over an earlier step's staged output.
        source = aggregation_source(job, step, design)
        if source is not None:
            pending.append(
                (
                    reader_package,
                    aggregating_reader_class_name(step),
                    render_aggregating_reader(
                        step,
                        source,
                        design,
                        package=reader_package,
                        domain_package=domain_package,
                        staging_package=job_package,
                    ),
                )
            )
        elif reads_a_file(step, design, program_name, job):
            pending.append(
                (
                    reader_package,
                    reader_class_name(step),
                    render_item_reader(
                        step,
                        design,
                        program_name,
                        package=reader_package,
                        domain_package=domain_package,
                    ),
                )
            )
        if _has_file_sink(step, design, program_name):
            pending.append(
                (
                    writer_package,
                    writer_class_name(step),
                    render_item_writer(
                        step,
                        design,
                        program_name,
                        package=writer_package,
                        domain_package=domain_package,
                    ),
                )
            )

    pending.append(
        (
            job_package,
            configuration_class_name(job),
            render_job_configuration(
                job,
                design,
                program_name,
                package=job_package,
                domain_package=domain_package,
                processor_package=processor_package,
                reader_package=reader_package,
            ),
        )
    )

    # The beans binding those readers and writers to files, and the properties that fill them
    # (ADR-0067). Rendered without a profile, unlike the hand-written fixture: a job that runs only
    # under a non-default profile is not the program. Rendered `@Lazy` instead, because a reader
    # opens its files in its constructor.
    pending.append(
        (
            job_package,
            bindings_class_name(job),
            render_file_bindings(
                job,
                design,
                program_name,
                package=job_package,
                domain_package=domain_package,
                reader_package=reader_package,
                writer_package=writer_package,
            ),
        )
    )
    properties = render_application_properties(job, design, program_name)

    written = [
        _write_java(output_dir, package, class_name, source)
        for package, class_name, source in pending
    ]
    destination = output_dir / "src" / "main" / "resources" / "application.properties"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(properties, encoding="utf-8")
    written.append("src/main/resources/application.properties")

    logger.info(
        "generate: rendered %d wiring file(s) for job %s -- %d step(s) renderable, %d skipped",
        len(written), job.job_name, len(renderable), len(skipped),
    )
    return written, skipped


#: The per-program oracle a rendered equivalence test reads its expected values from. Absent for
#: every program but `CBACT04C` today, and absence is not an error: a program with no hand-derived
#: oracle gets no rendered test, which `not_rendered` says.
EQUIVALENCE_ORACLE_NAME = "interest-oracle.json"


def load_equivalence_oracle(program_name: str) -> dict[str, Any] | None:
    """The parsed oracle for `program_name`, or `None` when the package ships none."""
    path = ORACLE_ROOT / program_name / EQUIVALENCE_ORACLE_NAME
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def render_step_equivalence_test(
    step: BatchStepDesign,
    design: UnifiedDesign,
    output_dir: Path,
    *,
    oracle: Mapping[str, Any],
    processor_class: str,
    package: str,
    domain_package: str,
) -> str:
    """Render the equivalence test for `step` into the target project, and return its class name.

    Raises `UnrenderableOracleError` when the design gives the computed value nowhere to land --
    which the caller reports as a `refused` verdict rather than swallowing, because that refusal
    *is* the finding (ADR-0065).
    """
    test_class = f"{processor_class}EquivalenceTest"
    composites = {c.name: c for c in design.composite_types}
    consumed = composites.get(step.input_type)
    if consumed is None:
        # The renderer builds the item it feeds the processor out of the composite's own components,
        # so a step consuming a bare entity is a shape it cannot construct. Refused rather than
        # approximated, per this module's standing rule.
        raise UnrenderableOracleError(
            f"step {step.step_name!r} consumes {step.input_type!r}, which is a domain entity "
            f"rather than a declared composite, so the balance and the rate cannot both be "
            f"supplied to one item"
        )
    rendered = render_equivalence_test(
        oracle,
        package=package,
        test_class_name=test_class,
        processor_class=processor_class,
        composite=consumed,
        entities=design.domain_entities,
        output_entity=step.output_type,
        domain_package=domain_package,
        output_composite=composites.get(step.output_type),
    )
    destination = output_dir.joinpath(
        "src", "test", "java", *package.split("."), f"{test_class}.java"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    logger.info("generate: rendered equivalence test %s for %s", test_class, processor_class)
    return test_class


def run_equivalence_test(
    project_dir: Path, test_class: str, *, paragraph: str
) -> EquivalenceTestVerdict:
    """Run the one rendered test through Maven and turn the build's exit into a verdict.

    **`-Dtest=` is not an optimisation.** The heal loop's own goal is `compile`, which never
    compiles test sources, so something here has to run tests or the rendered file is inert -- the
    ADR-0063 failure this record was written to avoid repeating. But the baseline template ships
    `BaselineStackTest`, which is `@SpringBootTest @Testcontainers`: an unfiltered `test` would
    demand Docker on the specialist host and fail for reasons that say nothing about the interest
    arithmetic. Narrowing to the rendered class runs exactly the check this verdict claims to
    report, and claims no more.
    """
    result = compile_project(
        project_dir,
        goal="test",
        extra_args=(f"-Dtest={test_class}", "-Dsurefire.failIfNoSpecifiedTests=false"),
    )
    if result.succeeded:
        return EquivalenceTestVerdict(
            status="passed",
            reason=(
                f"{test_class} passed: the per-row interest arithmetic matches the hand-derived "
                f"oracle and the value reaches the step's output. Covers one COMPUTE -- not the "
                f"account accumulator, and not the job's written records (ADR-0065)"
            ),
            test_class=test_class,
        )
    # **A build that did not compile is not a wrong answer**, and reporting it as one would tell a
    # reviewer the generated arithmetic diverges from COBOL when nothing has been compared at all.
    # The same distinction `build_validator` draws for the processor, drawn again here.
    if result.errors:
        return EquivalenceTestVerdict(
            status="failed",
            reason=(
                f"{test_class} did not compile, so nothing was compared: "
                f"{result.errors[0].render()}"
            ),
            test_class=test_class,
        )
    return EquivalenceTestVerdict(
        status="failed",
        reason=(
            f"{test_class} failed: generated code does not reproduce COBOL's own answers for "
            f"{paragraph}. Maven exit {result.exit_code}"
        ),
        test_class=test_class,
    )


def heal_step(
    worktree_root: Path,
    project_dir: Path,
    program_entry: ProgramDesignEntry,
    step: BatchStepDesign,
    entities: list[DomainEntity],
    *,
    package: str,
    composites: list[CompositeType] | None = None,
    computed_values: list[ComputedValue] | None = None,
    job_parameters: list[JobParameter] | None = None,
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
    # `dict[str, Any]`, not the inferred value type: a `**` splat of a narrowly-typed dict is checked
    # against *every* parameter it could name, so the inferred `dict[str, Callable[...]]` was
    # reported against `tier`, `project_dir` and the rest rather than against `author`/`advise`.
    author_kwargs: dict[str, Any] = {"author": author} if author is not None else {}
    advise_kwargs: dict[str, Any] = {"advise": advise} if advise is not None else {}

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
            composites=composites,
            computed_values=computed_values,
            job_parameters=job_parameters,
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
            previous_imports=_extract_model_imports(java_source),
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


@dataclass(frozen=True)
class GenerateOutcome:
    """Everything one `generate` invocation produced, for the CLI summary and a human gate."""

    outcomes: tuple[StepOutcome, ...]
    scaffolded: bool
    output_dir: str
    #: What the rendered JUnit equivalence test did (ADR-0065). Defaults to `not_rendered` so a run
    #: that checked nothing says so, rather than leaving the subject out.
    equivalence_test: EquivalenceTestVerdict = field(
        default_factory=lambda: EquivalenceTestVerdict(
            status="not_rendered", reason="no equivalence test was rendered for this design"
        )
    )
    #: Whether this run produced a job that can run, and what it left out (ADR-0066).
    wiring: WiringVerdict = field(
        default_factory=lambda: WiringVerdict(
            status="not_rendered", reason="no job wiring was rendered for this design"
        )
    )

    @property
    def compiled(self) -> tuple[StepOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "compiled")

    @property
    def blocked(self) -> tuple[StepOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "blocked")

    @property
    def exhausted(self) -> tuple[StepOutcome, ...]:
        return tuple(o for o in self.outcomes if o.status == "exhausted")

    @property
    def not_generated(self) -> tuple[StepOutcome, ...]:
        """Steps this pipeline does not render, reported so a gate can see them (G27)."""
        return tuple(o for o in self.outcomes if o.status == "not_generated")

    @property
    def generable(self) -> tuple[StepOutcome, ...]:
        """The steps this pipeline was ever going to produce: the processors."""
        return tuple(o for o in self.outcomes if o.status != "not_generated")

    @property
    def succeeded(self) -> bool:
        """Every processor step compiled, and there was at least one to compile.

        An empty run is not a success: a design that yielded no generable step means the pipeline
        produced nothing, and reporting that as `ok` would tell control-plane's gate that a
        migration happened when none did.

        Measured over **generable** steps only. A declared writer is not a failed processor, and
        counting it as one would make every realistic design report an error; it is surfaced
        separately instead (`not_generated`), because the honest answer to "did this succeed" is
        "every processor compiled, and here is what was never generated" -- not one number.
        """
        return bool(self.generable) and len(self.compiled) == len(self.generable)


def run_generate(
    design_path: Path,
    worktree_root: Path,
    output_dir: Path,
    *,
    package: str = DEFAULT_PACKAGE,
    domain_package: str = DEFAULT_DOMAIN_PACKAGE,
    max_attempts: int = MAX_HEAL_ATTEMPTS,
    author: AuthorFn | None = None,
    advise: AdviseFn | None = None,
) -> GenerateOutcome:
    """Run the whole `generate` phase from an approved `design.json`.

    Sequential across every processor step of every program -- see the module docstring for why
    concurrency would be incorrect here rather than merely unnecessary.

    Raises:
        FileNotFoundError: `design_path` does not exist.
        ValueError: the document has no `unified_design`, so there is nothing to generate from.
        tools.local_compiler.ToolchainNotFoundError: no JDK or no Maven.
        tools.local_compiler.CompileTimeoutError: a build exceeded its ceiling.
    """
    document = DesignDocument.model_validate_json(design_path.read_text(encoding="utf-8"))
    if document.unified_design is None:
        raise ValueError(
            f"{design_path} has no unified_design; `design` must run before `generate`"
        )

    design = document.unified_design
    entities = design.domain_entities
    entries = {entry.program_name: entry for entry in document.programs}
    scaffolded = materialize_target_project(output_dir)
    render_domain_types(design, output_dir, package=domain_package)

    outcomes: list[StepOutcome] = []
    wiring = WiringVerdict(
        status="not_rendered",
        reason="this design declares no batch job whose steps could be wired",
    )
    wiring_files: list[str] = []
    wiring_skipped: list[str] = []
    wiring_refusal: str | None = None
    rendered_test_class: str | None = None
    rendered_test_paragraph = ""
    equivalence_test = EquivalenceTestVerdict(
        status="not_rendered",
        reason=(
            "no step declares the paragraph this program's oracle covers, or the package ships no "
            "oracle for it"
        ),
    )
    for job in document.unified_design.batch_jobs:
        entry = entries.get(job.program_name)
        if entry is None:
            logger.warning(
                "generate: job %s names program %s, which is not in this design document",
                job.job_name, job.program_name,
            )
            continue

        for step in job.steps:
            if step.role != "processor":
                # `rendering/java_processor.py` renders an `ItemProcessor` and nothing else, so a
                # reader, writer or tasklet is genuinely not this renderer's to produce. That much
                # was always right. The `continue` was not: the step reached no outcome, no count
                # and no gate, so one processor plus one writer reported `steps_total: 1,
                # status: ok` and looked identical to a job with nothing else in it (gap G27).
                #
                # **Not every non-processor is wiring.** `CBACT04C`'s `1050-UPDATE-ACCOUNT` does
                # `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` -- a control-break accumulation that mutates
                # a balance, and it cannot be an `ItemProcessor`, because a stateless per-item
                # processor holds nothing across items. Dropping it silently loses money in the
                # one direction nothing downstream can detect: the arithmetic that *is* generated
                # stays correct, and only the total is wrong.
                #
                # Recorded rather than skipped, with the role and the paragraphs in the reason,
                # because that is what lets a reviewer tell wiring from lost business logic.
                reason = (
                    f"not generated: role is {step.role!r}, and this pipeline renders "
                    f"ItemProcessors only. Its COBOL is "
                    f"{', '.join(step.source_paragraphs) or '(none recorded)'} -- if that carries "
                    f"business logic, it is absent from the generated project and needs an owner."
                )
                logger.warning(
                    "generate: %s/%s not generated (role=%s, paragraphs=%s)",
                    job.program_name,
                    step.step_name,
                    step.role,
                    ", ".join(step.source_paragraphs) or "(none)",
                )
                outcomes.append(
                    StepOutcome(
                        program_name=job.program_name,
                        step_name=step.step_name,
                        class_name="",
                        status="not_generated",
                        attempts=0,
                        reason=reason,
                    )
                )
                continue

            # **`reads_own_writes` no longer refuses here.** ADR-0040 reported such a step
            # `not_generated` because nothing could run it correctly: a stateless processor decides
            # an item against state as the job found it, and `CBTRN02C` writes 287 records that way
            # where COBOL writes 262 (ADR-0039). ADR-0041 built what runs it -- a working set the
            # reader and writer share, at chunk 1 -- and the refusal is lifted now that a rendered
            # sequential job compiles and runs against a real Maven build rather than on the
            # argument that it should.
            #
            # The processor is rendered exactly as any other, which is the point: what makes the
            # step sequential is its *wiring*, so a body stays a pure function of the item and the
            # state as the step currently has it.
            types = processor_types(step, design, domain_package=domain_package)
            if types is None:
                outcomes.append(
                    StepOutcome(
                        program_name=job.program_name,
                        step_name=step.step_name,
                        class_name="",
                        status="blocked",
                        attempts=0,
                        reason=(
                            f"input_type {step.input_type!r} or output_type "
                            f"{step.output_type!r} resolves to neither a domain entity nor a "
                            "declared composite type, so an ItemProcessor cannot be generated "
                            "against it (ADR-0020). Rendering Java against a class that will not "
                            "exist would fail later and less clearly"
                        ),
                    )
                )
                continue

            step_outcome = heal_step(
                worktree_root,
                output_dir,
                entry,
                step,
                entities,
                package=package,
                composites=list(design.composite_types),
                computed_values=list(design.computed_values),
                # Only the parameters this step declares it consumes (ADR-0026), resolved
                # against the job's own declarations. A step naming one the job does not
                # declare is a design defect, and `_resolve_job_parameters` raises rather
                # than rendering a constructor argument nothing will ever bind.
                job_parameters=_resolve_job_parameters(job, step),
                input_type=types[0],
                output_type=types[1],
                max_attempts=max_attempts,
                author=author,
                advise=advise,
            )
            outcomes.append(step_outcome)

            # The equivalence test is rendered for the one step the oracle has expected values for,
            # selected by the paragraph the step *declares* (ADR-0065). Never by step name:
            # `computeMonthlyInterest` is a name a model chose and is not a fact about anything.
            oracle = load_equivalence_oracle(job.program_name)
            if oracle is None or oracle["source"]["paragraph"] not in step.source_paragraphs:
                # Nothing for this step to be checked against: the package ships no oracle for this
                # program, or this is not the step the oracle covers. Last statement of the loop
                # body, so `continue` skips nothing else.
                continue
            if not step_outcome.succeeded:
                # The step the oracle covers exists and did not compile. Distinguished from "no such
                # step" because they are different findings: one says this design was never in scope
                # for the check, the other says the check was in scope and could not be reached.
                equivalence_test = EquivalenceTestVerdict(
                    status="not_rendered",
                    reason=(
                        f"the step covering {oracle['source']['paragraph']} "
                        f"({job.program_name}/{step.step_name}) is {step_outcome.status}, so no "
                        f"test could be run against it"
                    ),
                )
            else:
                try:
                    rendered_test_paragraph = oracle["source"]["paragraph"]
                    rendered_test_class = render_step_equivalence_test(
                        step,
                        design,
                        output_dir,
                        oracle=oracle,
                        processor_class=step_outcome.class_name,
                        package=package,
                        domain_package=domain_package,
                    )
                except UnrenderableOracleError as exc:
                    # **The refusal is the finding, not a tooling failure.** A design whose interest
                    # step carries the computed value nowhere is step 49's defect exactly, and it is
                    # caught here before any Java is written.
                    logger.warning(
                        "generate: no equivalence test for %s/%s -- %s",
                        job.program_name, step.step_name, exc,
                    )
                    equivalence_test = EquivalenceTestVerdict(
                        status="refused",
                        reason=(
                            f"no equivalence test could be rendered for "
                            f"{step_outcome.class_name}: {exc}"
                        ),
                    )

    # **The wiring is rendered after every processor, not beside them** (ADR-0066). A step bean
    # names its processor class, so rendering it into a project mid-loop would break the heal loop's
    # own compiles on classes that do not exist yet -- and those failures would reach
    # `build_validator` as if a model had written them.
    if any(o.succeeded for o in outcomes):
        for job in document.unified_design.batch_jobs:
            try:
                files, skipped = render_job_wiring(
                    job,
                    design,
                    output_dir,
                    domain_package=domain_package,
                    processor_package=package,
                )
            except (UnrenderableJobError, UnrenderableReaderError, UnrenderableWriterError) as exc:
                logger.warning("generate: wiring refused for job %s -- %s", job.job_name, exc)
                wiring_refusal = f"{job.job_name}: {exc}"
                break
            wiring_files += files
            wiring_skipped += [f"{step.step_name}: {why}" for step, why in skipped]

    if wiring_refusal is not None:
        wiring = WiringVerdict(
            status="refused",
            reason=(
                f"the job wiring could not be rendered, so this run produced processors rather "
                f"than a program -- {wiring_refusal}"
            ),
            skipped_steps=wiring_skipped,
        )
    elif wiring_files:
        # Compiled once with the wiring in it, because until this runs nothing has built the
        # readers, writers, staging or job configuration at all: the heal loop's last compile
        # predates every one of them.
        built = compile_project(output_dir, goal="compile")
        if built.succeeded:
            left_out = (
                f"; {len(wiring_skipped)} step(s) left out, and their COBOL is absent from the "
                f"generated project"
                if wiring_skipped
                else " with every renderable step wired"
            )
            wiring = WiringVerdict(
                status="rendered",
                reason=f"{len(wiring_files)} wiring file(s) rendered and compiled{left_out}",
                files_rendered=wiring_files,
                skipped_steps=wiring_skipped,
            )
        else:
            first = built.errors[0].render() if built.errors else "no located diagnostic"
            wiring = WiringVerdict(
                status="refused",
                reason=f"the rendered wiring does not compile: {first}",
                files_rendered=wiring_files,
                skipped_steps=wiring_skipped,
            )

    if rendered_test_class is not None:
        equivalence_test = run_equivalence_test(
            output_dir, rendered_test_class, paragraph=rendered_test_paragraph
        )

    logger.info(
        "generate: %d step(s) -- %d compiled, %d blocked, %d exhausted",
        len(outcomes),
        len([o for o in outcomes if o.status == "compiled"]),
        len([o for o in outcomes if o.status == "blocked"]),
        len([o for o in outcomes if o.status == "exhausted"]),
    )
    return GenerateOutcome(
        outcomes=tuple(outcomes),
        scaffolded=scaffolded,
        output_dir=str(output_dir),
        equivalence_test=equivalence_test,
        wiring=wiring,
    )
