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
    locate_item_field,
)
from cobol_modernizer.rendering.java_working_set import working_set_class_name

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


def aggregating_reader_class_name(step: BatchStepDesign) -> str:
    """`postAccountInterest` -> `PostAccountInterestItemReader`.

    Duplicated from `java_aggregation` deliberately: that module imports this one for
    `staging_class_name`, and importing it back would make the pair circular. Two lines of naming
    is a smaller cost than a lazy import, and a test asserts the two agree.
    """
    base = step.step_name[:1].upper() + step.step_name[1:]
    return f"{base}ItemReader"


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
    """Whether the step's input can be read from declared files.

    Every entity it carries needs an access path -- and **it must carry nothing but entities.** A
    `computed_fields` entry is a working-storage value some step computes (ADR-0062), and no file
    holds one, so an item type declaring any is obtainable only from the step that computes it. Left
    unasked, this reported `AccruedCategoryInterest` as file-readable and `render_item_reader`
    rendered a constructor missing that argument.
    """
    composite = next((c for c in design.composite_types if c.name == step.input_type), None)
    if composite is not None and composite.computed_fields:
        return False

    try:
        from cobol_modernizer.rendering.java_reader import _component_entities

        for _field, entity in _component_entities(step, design):
            _paths_for(design, program_name, entity)
    except UnrenderableReaderError:
        return False
    return True


def _has_file_sink(step: BatchStepDesign, design: UnifiedDesign, program_name: str) -> bool:
    """Whether the step's output is an entity some declared file is written from.

    **A sequential step's output is a composite, and every one of its components is written** --
    `CBTRN02C` produces a transaction, a balance and an account from one daily record, and
    `java_writer` routes each by its own access path (ADR-0041). Without this the step would be
    planned as if its output crossed a step boundary and staged in memory, which is where the
    records would stay.
    """
    written = {
        path.written_entity_name
        for path in design.file_access_paths
        if path.program_name == program_name and path.written_entity_name
    }
    if step.output_type in written:
        return True

    composite = next((c for c in design.composite_types if c.name == step.output_type), None)
    if composite is None or not step.reads_own_writes:
        return False
    return all(component.entity_name in written for component in composite.components)


def aggregation_blockers(
    step: BatchStepDesign, upstream_type: str | None, design: UnifiedDesign
) -> list[str]:
    """Which of a control break's fields the upstream item type cannot reach.

    A rendered aggregation needs two things from the records it groups: the **break key**, to know
    where a group ends, and **the value to sum**. The first is a record field. The second has two
    declared forms, and asking for only one of them is what refused a live design:

    - the value **itself**, carried as a `computed_fields` entry (ADR-0062). `AccruedCategoryInterest`
      returns `WS-MONTHLY-INT` this way, which is the row-grain shape ADR-0063 requires.
    - the record field it **lands in**, when a `MOVE` puts it in one. `WS-MONTHLY-INT` also reaches
      `TRAN-AMT`, and summing that column was the only form this function knew.

    Either will do, because they are the same number. Checked in that order so a design carrying it
    both ways resolves as it always has, and so the nearer observation wins when only one type
    carries each -- which is exactly the live case: `AccruedCategoryInterest` has the break key and
    the computed value, `Tran` has the landing column and no account id.

    Returns the COBOL field names that are missing, empty when the aggregation is renderable, and
    the break's own names when there is no upstream type at all.
    """
    break_design = step.control_break
    if break_design is None:
        return []

    if upstream_type is None:
        wanted = [break_design.break_key_field]
        if break_design.landing_field:
            wanted.append(break_design.landing_field)
        return wanted

    def reaches(cobol_field: str) -> bool:
        return locate_item_field(design, upstream_type, cobol_field) is not None

    missing: list[str] = []
    if not reaches(break_design.break_key_field):
        missing.append(break_design.break_key_field)
    if not reaches(break_design.accumulated_from_field):
        if break_design.landing_field is None:
            # Neither carried nor landed: the value exists only in working storage, and no stream
            # this step could read has it. Named as the accumulated field rather than as a missing
            # `landing_field`, because "TRAN-AMT is missing" is not true when there is no TRAN-AMT.
            missing.append(break_design.accumulated_from_field)
        elif not reaches(break_design.landing_field):
            missing.append(break_design.landing_field)
    return missing


def aggregation_source(
    job: BatchJobDesign, step: BatchStepDesign, design: UnifiedDesign
) -> BatchStepDesign | None:
    """Which earlier step's output an aggregating step groups over.

    **A chain says each step consumes its predecessor's output, and an aggregate does not.**
    `postAccountInterest` groups the interest transactions by account, and the account id is not on
    a `Tran` -- it reaches the stream on the `TranWithContext` the step *before* that produced. So
    the source is found by walking backwards to the nearest step whose output type carries both the
    break key and the field the accumulated value lands in.

    Derived rather than declared, for the same reason as everything else here: the two fields come
    from the COBOL, and which stream carries them is then a property of the types, not a judgment.
    `None` when no earlier step's output carries them, which is a refusal with a shape.
    """
    if step.control_break is None:
        return None
    index = next((i for i, other in enumerate(job.steps) if other.step_name == step.step_name), None)
    if index is None:
        return None
    for candidate in reversed(job.steps[:index]):
        if not aggregation_blockers(step, candidate.output_type, design):
            return candidate
    return None


#: Why a step of this role is never planned as a chunk step, keyed by the role.
#:
#: **`role` is a fact the design has carried since the first version of this contract, and this
#: module never consulted it.** `unobtainable_inputs` states both limits already, in the same
#: sentence -- *"a reader's and a writer's outputs are bound by `READ ... INTO` and `WRITE ... FROM`,
#: and a tasklet has no item at all"* -- and names this exact shape, the open/close tasklets of
#: `CBACT01C` and `CBCUS01C`.
#:
#: **What planning one anyway cost.** A live `CBACT04C` design decomposes the program the way the
#: COBOL is written: a tasklet of five file OPENs and a reader of `1000-TCATBALF-GET-NEXT`, both
#: typed `TranCatBal -> TranCatBal`, around the steps that do the work. Planned as chunk steps, each
#: demanded its own `ItemReader<TranCatBal>` bean beside the step that actually drives the file --
#: an ambiguity `render_file_bindings` refuses by refusing the whole job's wiring. Its message named
#: the two colliding steps and could not name the reason they were both there.
#:
#: Skipped rather than refused, because nothing is lost and the design is not wrong. Opening a file
#: and reading its next record are real COBOL; in Spring Batch they are the item reader's own
#: lifecycle rather than steps, so the step that consumes the records drives the file directly.
_NOT_A_CHUNK_STEP = {
    "tasklet": (
        "its role is 'tasklet', which has no item -- a chunk-oriented step reads one and writes "
        "one, and opening or closing a file is the item reader's lifecycle rather than a step of "
        "its own"
    ),
    "reader": (
        "its role is 'reader', whose output is bound to what it read -- in Spring Batch that is "
        "the ItemReader itself rather than a step, and the step consuming these records reads the "
        "file directly"
    ),
}


def plan_steps(
    job: BatchJobDesign, design: UnifiedDesign, program_name: str
) -> tuple[list[BatchStepDesign], list[tuple[BatchStepDesign, str]], list[str]]:
    """Split a job's steps into what can be rendered, what cannot, and the types needing staging.

    Returns `(renderable, [(step, why not)], staged type names)`.

    A step is renderable when its input can be obtained -- from a file, or from the step before it --
    and its output can be put somewhere: a file, or the step after it. Anything else is reported with
    the reason rather than rendered, because the alternative is a step bean wired to nothing.

    **A reader and a tasklet are none of those questions**, and asking them of one is what this
    function did until a live design refused. See `_NOT_A_CHUNK_STEP`.
    """
    if not job.steps:
        raise UnrenderableJobError(f"job {job.job_name!r} declares no steps")

    renderable: list[BatchStepDesign] = []
    #: Reported before anything else, so a reader of the skip list meets the structural answer
    #: ("this is not a chunk step") before the per-step ones.
    skipped: list[tuple[BatchStepDesign, str]] = [
        (step, _NOT_A_CHUNK_STEP[step.role])
        for step in job.steps
        if step.role in _NOT_A_CHUNK_STEP
    ]
    staged: list[str] = []

    #: The steps that carry an item. The others are dropped from the chain as well as from the plan:
    #: the step after a file open takes its input from the step before it, not from the open.
    items = [step for step in job.steps if step.role not in _NOT_A_CHUNK_STEP]

    for index, step in enumerate(items):
        previous = items[index - 1] if index else None
        following = items[index + 1] if index + 1 < len(items) else None

        from_file = _has_file_source(step, design, program_name)
        from_chain = previous is not None and previous.output_type == step.input_type
        to_file = _has_file_sink(step, design, program_name)
        to_chain = following is not None and following.input_type == step.output_type

        if not (from_file or from_chain):
            source = aggregation_source(job, step, design)
            if source is not None:
                # It aggregates an earlier step's output, and that output carries what it groups by
                # and what it sums. Renderable, and staged from the step it reads rather than from
                # the one that happens to precede it in the chain.
                renderable.append(step)
                if source.output_type not in staged:
                    staged.append(source.output_type)
                continue

            upstream = previous.output_type if previous else None
            blockers = aggregation_blockers(step, upstream, design)
            if step.control_break is not None:
                control = step.control_break
                reason = (
                    f"it aggregates: {control.performed_paragraph} runs at a control break on "
                    f"{control.break_key_field} (line {control.test_line}), summing "
                    f"{control.accumulated_from_field} which lands in "
                    f"{control.landing_field or '(nowhere)'}. Rendering that needs both readable "
                    f"from {upstream or 'the step before it'}, and "
                    f"{', '.join(blockers)} {'is' if len(blockers) == 1 else 'are'} not -- widen "
                    "that type to carry it, or give this step an input the design can supply"
                )
                skipped.append((step, reason))
                continue
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
    reader_package: str,
    job: BatchJobDesign | None = None,
    working_set_package: str | None = None,
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

    aggregates_from = aggregation_source(job, step, design) if job is not None else None
    if aggregates_from is not None:
        # A control-break step reads a *rendered aggregation* over an earlier step's staged output,
        # not the stream that happens to precede it. Constructed here rather than injected because
        # it needs no path: everything it groups and sums is already in memory.
        staging = staging_class_name(aggregates_from.output_type)
        reader_parameter = f"{staging} {_bean_name(staging)}"
        reader_expression = (
            f"new {reader_package}.{aggregating_reader_class_name(step)}"
            f"({_bean_name(staging)})"
        )
    elif _has_file_source(step, design, program_name):
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

    # **A sequential step is chunked at 1, and that is correctness rather than tuning.** Every
    # other step chunks at `CHUNK_SIZE`, which the constant's own comment calls a performance
    # decision because it is one. Here each item's write has to be visible to the next item's read
    # -- the writer puts into the working set and the reader takes its lookups from it -- so any
    # size above 1 would let a chunk decide several items against the state as it stood before any
    # of them (ADR-0041). Rendered as a literal beside a named constant on purpose: the two are
    # different kinds of number and should not look alike.
    chunk = "1" if step.reads_own_writes else "CHUNK_SIZE"
    if step.reads_own_writes and working_set_package is None:
        raise UnrenderableJobError(
            f"step {step.step_name!r} reads state it writes, so its bean takes a working set -- "
            "and nothing said which package that class is in"
        )
    state_parameter = (
        f",\n{_INDENT * 3}{working_set_package}.{working_set_class_name(step)} state"
        if step.reads_own_writes
        else ""
    )
    flush = (
        f"\n{_INDENT * 4}.listener(new StepExecutionListener() {{\n"
        f"{_INDENT * 5}@Override\n"
        f"{_INDENT * 5}public ExitStatus afterStep(StepExecution stepExecution) {{\n"
        f"{_INDENT * 6}// The working set is the step's output for these files; nothing has been\n"
        f"{_INDENT * 6}// written to disk until this runs.\n"
        f"{_INDENT * 6}try {{\n"
        f"{_INDENT * 7}state.flush();\n"
        f"{_INDENT * 6}}} catch (java.io.IOException e) {{\n"
        f"{_INDENT * 7}throw new java.io.UncheckedIOException(e);\n"
        f"{_INDENT * 6}}}\n"
        f"{_INDENT * 6}return stepExecution.getExitStatus();\n"
        f"{_INDENT * 5}}}\n"
        f"{_INDENT * 4}}})"
        if step.reads_own_writes
        else ""
    )

    return f"""{_INDENT}/** Step "{step.step_name}": {step.description} */
{_INDENT}@Bean
{_INDENT}Step {step.step_name}Step(
{_INDENT * 3}JobRepository jobRepository,
{_INDENT * 3}PlatformTransactionManager transactionManager,
{_INDENT * 3}{reader_parameter},
{_INDENT * 3}{writer_parameter}{state_parameter}) {{
{_INDENT * 2}return new StepBuilder("{step.step_name}", jobRepository)
{_INDENT * 4}.<{input_type}, {output_type}>chunk({chunk})
{_INDENT * 4}.reader({reader_expression})
{_INDENT * 4}.processor(new {processor_package}.{processor_class_name(step)}())
{_INDENT * 4}.writer({writer_expression})
{_INDENT * 4}.transactionManager(transactionManager){flush}
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
    reader_package: str,
    profile: str | None = None,
    working_set_package: str | None = None,
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
            reader_package=reader_package,
            job=job,
            working_set_package=working_set_package,
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

    # Only when a sequential step is present, and every package below was read out of
    # spring-batch-core-6.0.4.jar rather than recalled: PR #32's trap was a pre-6 `ItemProcessor`
    # package that compiled in every example on the internet and not here.
    sequential_imports = (
        "import org.springframework.batch.core.ExitStatus;\n"
        "import org.springframework.batch.core.listener.StepExecutionListener;\n"
        "import org.springframework.batch.core.step.StepExecution;\n"
        if any(step.reads_own_writes for step in renderable)
        else ""
    )

    return f"""package {package};

import java.util.List;
import java.util.Map;
{sequential_imports}import org.springframework.batch.core.configuration.JobRegistry;
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
import org.springframework.context.annotation.Lazy;
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
@Configuration
@Lazy{profile_annotation}
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
