"""Render a job, its steps and the handoff between them -- the last of G31's wiring.

**What this completes.** `generate` rendered processors; the reader and writers followed once the
COBOL's access paths, keys and layouts were parsed into `design.json`. This renders what is left:
the `JobRepository` and transaction manager, one `Step` per declared step, the staging that carries
a value across a step boundary, and the `Job` that chains them.

**Two things it deliberately does not do**, both from ADR-0032:

1. A step whose input is an *aggregate* of an earlier step's output is **not rendered**. Nothing in
   the design carries the grouping key, the summed field or the ordering -- those live in the COBOL
   as a control break -- and a renderer that picked them would be choosing business semantics. The
   job still names the step, so a missing bean is a startup failure that names it.
2. A declared chain with no store between its steps renders as an **in-memory staging bean**, and
   the generated class says in its own Javadoc that it is not restartable. Fusing the two steps into
   one would have removed the question and also the step boundary a human approved at the gate.

**The job looks its steps up by name.** That is what lets a rendered job contain a hand-written step
without this renderer knowing anything about it, and what makes an unrendered step impossible to
forget.
"""

from __future__ import annotations

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    UnifiedDesign,
)
from cobol_modernizer.nodes.modernization_engineer import processor_class_name
from cobol_modernizer.rendering.java_names import require_java_identifier
from cobol_modernizer.rendering.java_reader import (
    UnrenderableReaderError,
    _paths_for,
)

_INDENT = " " * 4

#: How many items a rendered step processes per transaction.
#:
#: **Not a COBOL fact.** Nothing in the source implies a batch size -- the original processes one
#: record at a time inside one unit of work -- so this is a performance decision for whoever runs the
#: job, rendered as a named constant rather than buried in a builder call (ADR-0032).
DEFAULT_CHUNK_SIZE = 10


class UnrenderableJobError(Exception):
    """A job cannot be rendered from this design without inventing something.

    Raised when a step's input can be neither read from a file nor produced by the step before it,
    and when a job declares no steps at all. A step that is merely *unrendered* is not an error --
    it is named in the job and supplied by a human (ADR-0032).
    """


def configuration_class_name(job: BatchJobDesign) -> str:
    """`interestJob` -> `InterestJobConfiguration`."""
    base = job.job_name[:1].upper() + job.job_name[1:]
    return f"{base}Configuration"


def _bean_name(class_name: str) -> str:
    """`TranWithContextStaging` -> `tranWithContextStaging`.

    Not `_camel`, which splits on hyphens and would flatten an already-PascalCase Java name into one
    lowercase run -- readable enough to miss in review and wrong as a bean name convention.
    """
    return class_name[:1].lower() + class_name[1:]


def staging_class_name(type_name: str) -> str:
    """`TranWithContext` -> `TranWithContextStaging`."""
    return f"{type_name}Staging"


def _has_file_source(step: BatchStepDesign, design: UnifiedDesign, program_name: str) -> bool:
    """Whether every entity the step's input carries can be read from a declared file."""
    try:
        from cobol_modernizer.rendering.java_reader import _component_entities

        for _field, entity in _component_entities(step, design):
            _paths_for(design, program_name, entity)
    except UnrenderableReaderError:
        return False
    return True


def _has_file_sink(step: BatchStepDesign, design: UnifiedDesign, program_name: str) -> bool:
    """Whether the step's output is an entity some declared file is written from."""
    return any(
        path.program_name == program_name and path.written_entity_name == step.output_type
        for path in design.file_access_paths
    )


def plan_steps(
    job: BatchJobDesign, design: UnifiedDesign, program_name: str
) -> tuple[list[BatchStepDesign], list[tuple[BatchStepDesign, str]], list[str]]:
    """Split a job's steps into what can be rendered, what cannot, and the types needing staging.

    Returns `(renderable, [(step, why not)], staged type names)`.

    A step is renderable when its input can be obtained -- from a file, or from the step before it --
    and its output can be put somewhere: a file, or the step after it. Anything else is reported with
    the reason rather than rendered, because the alternative is a step bean wired to nothing.
    """
    if not job.steps:
        raise UnrenderableJobError(f"job {job.job_name!r} declares no steps")

    renderable: list[BatchStepDesign] = []
    skipped: list[tuple[BatchStepDesign, str]] = []
    staged: list[str] = []

    for index, step in enumerate(job.steps):
        previous = job.steps[index - 1] if index else None
        following = job.steps[index + 1] if index + 1 < len(job.steps) else None

        from_file = _has_file_source(step, design, program_name)
        from_chain = previous is not None and previous.output_type == step.input_type
        to_file = _has_file_sink(step, design, program_name)
        to_chain = following is not None and following.input_type == step.output_type

        if not (from_file or from_chain):
            reason = (
                    f"its input {step.input_type!r} is neither readable from a declared file nor "
                    f"the output of the step before it. If it is an aggregate of earlier output, "
                    f"the design carries no grouping key, summed field or ordering -- those are a "
                    f"control break in the COBOL, and choosing them here would be choosing business "
                    f"semantics (ADR-0032)"
            )
            skipped.append((step, reason))
            continue
        if not (to_file or to_chain):
            reason = (
                    f"its output {step.output_type!r} is written to no declared file and consumed by "
                    "no following step, so a rendered step would have nowhere to put it"
            )
            skipped.append((step, reason))
            continue

        renderable.append(step)
        if to_chain and not to_file and step.output_type not in staged:
            staged.append(step.output_type)

    return renderable, skipped, staged


def render_staging(type_name: str, *, package: str, domain_package: str) -> str:
    """The in-memory handoff for a chain the design declares no store for (ADR-0032, finding F3)."""
    class_name = staging_class_name(type_name)
    qualified = f"{domain_package}.{type_name}"
    return f"""package {package};

import java.util.ArrayList;
import java.util.List;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.item.ItemWriter;

/**
 * Carries {type_name} from the step that produces it to the step that consumes it.
 *
 * <p>The design declares this chain and no store for it: {type_name} corresponds to no copybook and
 * no file, because in the original it is working storage between two PERFORMs. Rendered as an
 * in-memory handoff per ADR-0032.
 *
 * <p><b>This is not restartable.</b> A job that fails after the producing step restarts with this
 * empty, and the consuming step processes nothing. The restartable answer is a staging table, which
 * needs a schema for a type that has no copybook -- a decision the design does not carry. That
 * sentence is here rather than only in the ADR because this is where it applies.
 */
public class {class_name} implements ItemWriter<{qualified}>, ItemReader<{qualified}> {{

{_INDENT}private final List<{qualified}> staged = new ArrayList<>();
{_INDENT}private int next;

{_INDENT}@Override
{_INDENT}public void write(Chunk<? extends {qualified}> chunk) {{
{_INDENT * 2}staged.addAll(chunk.getItems());
{_INDENT}}}

{_INDENT}@Override
{_INDENT}public {qualified} read() {{
{_INDENT * 2}return next < staged.size() ? staged.get(next++) : null;
{_INDENT}}}

{_INDENT}/** What the producing step wrote, for a consumer that needs all of it at once. */
{_INDENT}public List<{qualified}> items() {{
{_INDENT * 2}return List.copyOf(staged);
{_INDENT}}}
}}
"""


def _step_bean(
    step: BatchStepDesign,
    design: UnifiedDesign,
    program_name: str,
    *,
    domain_package: str,
    processor_package: str,
) -> str:
    """One `@Bean Step`, wired to whichever reader and writer this step's data comes from and to.

    **It takes them as beans rather than constructing them**, and that is a boundary rather than
    laziness: a reader needs file paths, and a path is a deployment fact -- the COBOL says
    `ASSIGN TO TCATBALF`, an environment name, and nothing anywhere says what that resolves to. The
    rendered reader and writer *classes* come from `java_reader` and `java_writer`; binding them to
    locations is the job of whoever runs the job.

    A step whose input or output crosses a step boundary takes the staging bean instead, which this
    configuration does render, because it corresponds to no file and therefore to no path.
    """
    input_type = f"{domain_package}.{step.input_type}"
    output_type = f"{domain_package}.{step.output_type}"

    if _has_file_source(step, design, program_name):
        reader_parameter = f"ItemReader<{input_type}> reader"
        reader_expression = "reader"
    else:
        staging = staging_class_name(step.input_type)
        reader_parameter = f"{staging} {_bean_name(staging)}"
        reader_expression = _bean_name(staging)

    if _has_file_sink(step, design, program_name):
        writer_parameter = f"ItemWriter<{output_type}> writer"
        writer_expression = "writer"
    else:
        staging = staging_class_name(step.output_type)
        writer_parameter = f"{staging} {_bean_name(staging)}"
        writer_expression = _bean_name(staging)

    return f"""{_INDENT}/** Step "{step.step_name}": {step.description} */
{_INDENT}@Bean
{_INDENT}Step {step.step_name}Step(
{_INDENT * 3}JobRepository jobRepository,
{_INDENT * 3}PlatformTransactionManager transactionManager,
{_INDENT * 3}{reader_parameter},
{_INDENT * 3}{writer_parameter}) {{
{_INDENT * 2}return new StepBuilder("{step.step_name}", jobRepository)
{_INDENT * 4}.<{input_type}, {output_type}>chunk(CHUNK_SIZE)
{_INDENT * 4}.reader({reader_expression})
{_INDENT * 4}.processor(new {processor_package}.{processor_class_name(step)}())
{_INDENT * 4}.writer({writer_expression})
{_INDENT * 4}.transactionManager(transactionManager)
{_INDENT * 4}.build();
{_INDENT}}}"""


def render_job_configuration(
    job: BatchJobDesign,
    design: UnifiedDesign,
    program_name: str,
    *,
    package: str,
    domain_package: str,
    processor_package: str,
    profile: str | None = None,
) -> str:
    """Render the job, its steps and its infrastructure beans.

    `profile` gates the whole configuration behind a Spring profile. Off by default, because in a
    generated service the job configuration *is* the application and gating it would be strange. It
    exists for a context that also runs something else -- this repo's own round-trip harness builds
    the baseline project's Spring Boot test in the same classpath, and an always-active job
    configuration would join that context and fail it looking for file paths nobody supplied.

    Raises:
        UnrenderableJobError: the job declares no steps.
    """
    class_name = configuration_class_name(job)
    require_java_identifier(class_name, source_name=job.job_name, kind="Configuration class name")

    renderable, skipped, staged = plan_steps(job, design, program_name)
    step_beans = "\n\n".join(
        _step_bean(
            step,
            design,
            program_name,
            domain_package=domain_package,
            processor_package=processor_package,
        )
        for step in renderable
    )
    staging_beans = "\n\n".join(
        f"{_INDENT}@Bean\n"
        f"{_INDENT}{staging_class_name(name)} {_bean_name(staging_class_name(name))}() {{\n"
        f"{_INDENT * 2}return new {staging_class_name(name)}();\n"
        f"{_INDENT}}}"
        for name in staged
    )
    names = ", ".join(f'"{step.step_name}"' for step in job.steps)
    profile_annotation = f'\n@Profile("{profile}")' if profile else ""
    profile_import = (
        "import org.springframework.context.annotation.Profile;\n" if profile else ""
    )
    unrendered = (
        "\n".join(
            f" *   <li><b>{step.step_name}</b> -- {reason}</li>" for step, reason in skipped
        )
        or " *   <li>none</li>"
    )

    return f"""package {package};

import java.util.List;
import java.util.Map;
import org.springframework.batch.core.configuration.JobRegistry;
import org.springframework.batch.core.configuration.support.MapJobRegistry;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.builder.JobBuilder;
import org.springframework.batch.core.job.builder.SimpleJobBuilder;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.batch.core.launch.support.TaskExecutorJobOperator;
import org.springframework.batch.core.repository.JobRepository;
import org.springframework.batch.core.repository.support.ResourcelessJobRepository;
import org.springframework.batch.core.step.Step;
import org.springframework.batch.core.step.builder.StepBuilder;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.item.ItemWriter;
import org.springframework.batch.infrastructure.support.transaction.ResourcelessTransactionManager;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
{profile_import}import org.springframework.transaction.PlatformTransactionManager;

/**
 * {class_name} -- job "{job.job_name}", rendered from design.json for {program_name}.
 *
 * <p>{len(job.steps)} declared step(s), of which this configuration renders {len(renderable)}.
 *
 * <p>Steps this configuration does <b>not</b> render, and which must be supplied as {{@code Step}}
 * beans elsewhere (ADR-0032):
 * <ul>
{unrendered}
 * </ul>
 *
 * <p>The job looks every declared step up by name, so a missing one is a startup failure naming it
 * rather than a step that silently does not run.
 */
@Configuration{profile_annotation}
public class {class_name} {{

{_INDENT}/**
{_INDENT}  * Not a COBOL fact: a batch size is a performance decision for whoever runs the job.
{_INDENT}  *
{_INDENT}  * <p>Public because a step this configuration does not render still belongs to this
{_INDENT}  * job, and it should be chunked the same way rather than picking its own number.
{_INDENT}  */
{_INDENT}public static final int CHUNK_SIZE = {DEFAULT_CHUNK_SIZE};

{_INDENT}/** Every step of this job, in declared order. */
{_INDENT}public static final List<String> STEP_NAMES = List.of({names});

{_INDENT}@Bean
{_INDENT}JobRepository jobRepository() {{
{_INDENT * 2}return new ResourcelessJobRepository();
{_INDENT}}}

{_INDENT}@Bean
{_INDENT}PlatformTransactionManager transactionManager() {{
{_INDENT * 2}return new ResourcelessTransactionManager();
{_INDENT}}}

{_INDENT}@Bean
{_INDENT}JobRegistry jobRegistry() {{
{_INDENT * 2}return new MapJobRegistry();
{_INDENT}}}

{_INDENT}@Bean
{_INDENT}JobOperator jobOperator(JobRepository jobRepository, JobRegistry jobRegistry)
{_INDENT * 3}throws Exception {{
{_INDENT * 2}TaskExecutorJobOperator operator = new TaskExecutorJobOperator();
{_INDENT * 2}operator.setJobRepository(jobRepository);
{_INDENT * 2}operator.setJobRegistry(jobRegistry);
{_INDENT * 2}operator.afterPropertiesSet();
{_INDENT * 2}return operator;
{_INDENT}}}

{staging_beans}

{step_beans}

{_INDENT}/**
{_INDENT}  * The job, chaining every declared step in order.
{_INDENT}  *
{_INDENT}  * <p>Steps arrive by name rather than by type so that a step this renderer did not produce
{_INDENT}  * can still be part of the job -- and so that a missing one fails here, naming itself,
{_INDENT}  * instead of leaving a shorter job that looks like it ran.
{_INDENT}  */
{_INDENT}@Bean
{_INDENT}Job {job.job_name}(JobRepository jobRepository, Map<String, Step> steps) {{
{_INDENT * 2}SimpleJobBuilder builder =
{_INDENT * 3}new JobBuilder("{job.job_name}", jobRepository).start(step(steps, STEP_NAMES.get(0)));
{_INDENT * 2}for (String name : STEP_NAMES.subList(1, STEP_NAMES.size())) {{
{_INDENT * 3}builder = builder.next(step(steps, name));
{_INDENT * 2}}}
{_INDENT * 2}return builder.build();
{_INDENT}}}

{_INDENT}private static Step step(Map<String, Step> steps, String name) {{
{_INDENT * 2}Step found = steps.get(name + "Step");
{_INDENT * 2}if (found == null) {{
{_INDENT * 3}throw new IllegalStateException(
{_INDENT * 4}"job \\"{job.job_name}\\" declares step \\"" + name + "\\" and no bean named \\""
{_INDENT * 5}+ name + "Step\\" supplies it");
{_INDENT * 2}}}
{_INDENT * 2}return found;
{_INDENT}}}
}}
"""
