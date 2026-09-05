"""Render the beans that bind a rendered reader and writer to actual files (ADR-0067).

**This is the one thing the hand-written stopgap existed to supply.** ADR-0030 wrote the wiring by
hand precisely to find out what the contract was missing, and after four renderers landed the answer
was three beans doing one job:

    ItemReader<TranCatBalWithRate> tranCatBalWithRateItemReader()  // new ...ItemReader(paths...)
    ItemWriter<Tran>               tranItemWriter()
    ItemWriter<Account>            accountItemWriter()

Everything else -- layout, keys, joins, ordering, the control break -- is rendered from `design.json`
already. What is left is a path, and a path is not in the COBOL: `ASSIGN TO TCATBALF` names a DD
binding resolved by JCL on a machine this program will never run on again.

**So the path comes from a Spring property, and the property name comes from `ASSIGN TO`.**
`cobol.file.tcatbalf`. Derived from a declared fact rather than chosen, which is the same rule the
rendered constructor parameters already follow -- they are `_camel(assign_to)` -- so the property and
the argument it fills trace to one source and cannot drift apart.

**Nothing here decides a location.** The rendered defaults resolve against `cobol.file.base`, which
exists so the project starts and so every file the job touches is visible in one list. A default is a
convention, never a claim about where a tenant's data is; a wrong one surfaces as a missing file at
read time with the path in the message.

**`@Lazy`, and it is load-bearing rather than a performance choice.** A rendered reader opens its
files *in its constructor*, and `BatchApplication` component-scans this package -- so an eagerly
instantiated binding reads from disk while the Spring context is still starting, and every
`@SpringBootTest` in the generated project dies looking for a file that has nothing to do with it.
That is not hypothetical: the baseline template ships `BaselineStackTest`, and the first ungated
version of this renderer broke it in CI. The hand-written stopgap avoided the same collision with a
`@Profile`, and said so in its own docstring.

A profile would work and was rejected: a job that runs only under a non-default profile is not the
program (ADR-0066). Deferring construction achieves the same isolation without that cost, and moves
a missing file from a context-startup failure to a job-start failure -- which is both later and more
honest, because it fails when something actually wanted the data.
"""

from __future__ import annotations

import logging

from cobol_modernizer.core.contracts import BatchJobDesign, BatchStepDesign, UnifiedDesign
from cobol_modernizer.rendering.java_job import (
    UnrenderableJobError,
    _bean_name,
    _has_file_sink,
    _has_file_source,
    aggregation_source,
    plan_steps,
)
from cobol_modernizer.rendering.java_reader import (
    _camel,
    reader_class_name,
    reader_path_parameters,
)
from cobol_modernizer.rendering.java_writer import writer_class_name, writer_path_parameters

logger = logging.getLogger(__name__)

#: Prefix for every rendered file-path property. One namespace so `--cobol.file.base=/data` and a
#: per-file override read as the same family, and so a property dump shows the job's whole file
#: surface together.
PROPERTY_PREFIX = "cobol.file"

#: The property the rendered defaults resolve against. Named rather than a bare relative path: a job
#: whose default is "whatever directory it was launched from" is the failure mode where a test run
#: overwrites something real.
BASE_PROPERTY = f"{PROPERTY_PREFIX}.base"

DEFAULT_BASE = "data"


def bindings_class_name(job: BatchJobDesign) -> str:
    """`interestJob` -> `InterestJobFileBindings`."""
    base = job.job_name[:1].upper() + job.job_name[1:]
    return f"{base}FileBindings"


def property_name(assign_to: str) -> str:
    """`TCATBALF` -> `cobol.file.tcatbalf`."""
    return f"{PROPERTY_PREFIX}.{assign_to.lower()}"


def _refuse_working_set(step: BatchStepDesign) -> None:
    """A `reads_own_writes` step's reader and writer take a working set, and nothing renders one.

    Refused rather than rendered without it, per this repository's standing rule. `render_step_bean`
    injects a working set by type and no `@Bean` produces one anywhere -- so a binding rendered for
    such a step would compile and fail to start, which is a worse answer than this one. `CBTRN02C` is
    the program that needs it (ADR-0041), and ADR-0066 scopes this work to `CBACT04C` explicitly.
    """
    if step.reads_own_writes:
        raise UnrenderableJobError(
            f"step {step.step_name!r} reads state it writes, so its reader and writer take a "
            f"working set as well as paths -- and nothing renders a working-set bean yet. Binding "
            f"only its paths would produce a context that cannot start"
        )


def _reads_a_file(
    job: BatchJobDesign, step: BatchStepDesign, design: UnifiedDesign, program_name: str
) -> bool:
    """Whether this step's *reader* is a file reader that needs paths bound to it.

    **A control-break step is fed by a rendered aggregation over an earlier step's staged output,
    not by a file**, and that aggregation needs no path: everything it groups and sums is already in
    memory. `render_job_configuration` constructs it directly rather than injecting a bean, so a
    binding rendered here would be supplying an argument nothing asks for -- and, worse, would
    refuse first, because a step whose input is an in-memory aggregate resolves to no driving
    stream at all.

    `render_job_wiring` has always asked `aggregation_source` before `_has_file_source`. This module
    did not, and a live design put a control break on `postAccountInterest` and refused the whole
    job's wiring for it.
    """
    return aggregation_source(job, step, design) is None and _has_file_source(
        step, design, program_name
    )


def file_binding_properties(
    job: BatchJobDesign, design: UnifiedDesign, program_name: str
) -> dict[str, str]:
    """Every file property this job's rendered wiring reads, mapped to its rendered default.

    Ordered by first appearance so the rendered properties file reads in the job's own step order,
    which is the order an operator debugging a run encounters the files in.
    """
    renderable, _skipped, _staged = plan_steps(job, design, program_name)
    properties: dict[str, str] = {}
    for step in renderable:
        assigns: list[str] = []
        if _reads_a_file(job, step, design, program_name):
            _refuse_working_set(step)
            assigns += reader_path_parameters(step, design, program_name)
        if _has_file_sink(step, design, program_name):
            _refuse_working_set(step)
            assigns += writer_path_parameters(step, design, program_name)
        for assign_to in assigns:
            properties.setdefault(
                property_name(assign_to), f"${{{BASE_PROPERTY}}}/{assign_to}"
            )
    return properties


def render_application_properties(
    job: BatchJobDesign, design: UnifiedDesign, program_name: str
) -> str:
    """The generated project's `application.properties`, listing every file the job touches.

    **Every path is listed even though the base alone would cover the default case**, because this
    file is the documentation of the deployment surface `generate` now produces. An operator seeing
    four lines knows there are four files; one seeing a base directory knows only that there is a
    directory.
    """
    properties = file_binding_properties(job, design, program_name)
    lines = [
        f"# Where {program_name}'s files live. Rendered by cobol-modernizer (ADR-0067).",
        "#",
        "# Each property is named from the COBOL's own ASSIGN TO, which is the name the JCL bound",
        "# and the one an operator already knows. The defaults below are a convention and assert",
        "# nothing about where this data actually is: override the base, or any single path.",
        "#",
        f"#   --{BASE_PROPERTY}=/srv/carddemo",
        "",
        f"{BASE_PROPERTY}={DEFAULT_BASE}",
        "",
    ]
    lines += [f"{name}={default}" for name, default in properties.items()]
    return "\n".join(lines) + "\n"


def _bean(
    *,
    bean_type: str,
    bean_name: str,
    constructed: str,
    assigns: list[str],
    javadoc: str,
) -> str:
    parameters = ",\n            ".join(
        f'@Value("${{{property_name(assign_to)}}}") Path {_camel(assign_to)}'
        for assign_to in assigns
    )
    arguments = ", ".join(_camel(assign_to) for assign_to in assigns)
    return f"""    /**
     * {javadoc}
     *
     * <p>The arguments are the program's own {{@code ASSIGN TO}} names in declaration order, so
     * what this supplies is paths -- not layout, not keys, not joins.
     */
    @Bean
    {bean_type} {bean_name}(
            {parameters}) throws Exception {{
        return new {constructed}({arguments});
    }}"""


def render_file_bindings(
    job: BatchJobDesign,
    design: UnifiedDesign,
    program_name: str,
    *,
    package: str,
    domain_package: str,
    reader_package: str,
    writer_package: str,
) -> str:
    """Render the `@Configuration` binding this job's readers and writers to files.

    Raises:
        UnrenderableJobError: two renderable steps would produce two beans of the same type, or a
            step needs a working set. Both are refusals rather than guesses -- see the module
            docstring and `_refuse_working_set`.
    """
    renderable, _skipped, _staged = plan_steps(job, design, program_name)

    beans: list[str] = []
    # **By type, because that is how `render_step_bean` injects them.** A step bean declares
    # `ItemReader<TranCatBalWithRate> reader` and Spring resolves it by type, so two beans of one
    # type is not a naming collision to work around -- it is an ambiguity the context cannot
    # resolve, and it fails at startup rather than here unless this refuses first.
    claimed: dict[str, str] = {}

    def claim(bean_type: str, step_name: str) -> None:
        if bean_type in claimed:
            raise UnrenderableJobError(
                f"steps {claimed[bean_type]!r} and {step_name!r} both need a {bean_type} bean, and "
                f"Spring resolves these by type -- so this job's wiring is ambiguous. Which file "
                f"each one reads or writes is a fact the design carries; which bean a step gets is "
                f"not"
            )
        claimed[bean_type] = step_name

    for step in renderable:
        if _reads_a_file(job, step, design, program_name):
            _refuse_working_set(step)
            bean_type = f"ItemReader<{domain_package}.{step.input_type}>"
            claim(bean_type, step.step_name)
            class_name = reader_class_name(step)
            beans.append(
                _bean(
                    bean_type=bean_type,
                    bean_name=_bean_name(class_name),
                    constructed=f"{reader_package}.{class_name}",
                    assigns=reader_path_parameters(step, design, program_name),
                    javadoc=f"The rendered reader for step \"{step.step_name}\", bound to files.",
                )
            )
        if _has_file_sink(step, design, program_name):
            _refuse_working_set(step)
            bean_type = f"ItemWriter<{domain_package}.{step.output_type}>"
            claim(bean_type, step.step_name)
            class_name = writer_class_name(step)
            beans.append(
                _bean(
                    bean_type=bean_type,
                    bean_name=_bean_name(class_name),
                    constructed=f"{writer_package}.{class_name}",
                    assigns=writer_path_parameters(step, design, program_name),
                    javadoc=f"The rendered writer for step \"{step.step_name}\", bound to a file.",
                )
            )

    if not beans:
        raise UnrenderableJobError(
            f"job {job.job_name!r} has no renderable step that reads or writes a declared file, so "
            f"there is nothing to bind. A job whose every step is staged in memory reads and writes "
            f"nothing at all"
        )

    class_name = bindings_class_name(job)
    body = "\n\n".join(beans)
    logger.debug("rendered %s with %d file-bound bean(s)", class_name, len(beans))
    return f"""package {package};

import java.nio.file.Path;
import org.springframework.batch.infrastructure.item.ItemReader;
import org.springframework.batch.infrastructure.item.ItemWriter;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Lazy;

/**
 * Binds {program_name}'s rendered readers and writers to files.
 *
 * <p><b>Paths, and nothing else.</b> The job, its infrastructure beans, the staging, the step beans,
 * the readers, the writers and any control-break aggregation are all rendered from design.json. A
 * path is not in the COBOL to be rendered from: {{@code ASSIGN TO}} names a DD binding resolved by
 * JCL, so it comes from configuration instead (ADR-0067).
 *
 * <p>Each property is named from the {{@code ASSIGN TO}} it fills. See application.properties for
 * the rendered defaults and how to override them.
 *
 * <p>This file is rendered from design.json. It is not model-authored.
 */
@Configuration
@Lazy
public class {class_name} {{

{body}
}}
"""
