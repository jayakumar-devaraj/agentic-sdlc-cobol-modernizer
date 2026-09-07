"""Rendering the test that actually runs the generated job (ADR-0075).

**What was missing was never the comparison.** `harness.compare_project_output` has been wired since
ADR-0064 and the oracle ships in the wheel; `generate` rendered a job, compiled it, and stopped. The
only thing that had ever *run* a generated job was `tests/integration/test_hand_written_round_trip.py`,
and it got there by copying in a hand-written `InterestJobRunTest` alongside a hand-written
`HandWrittenRemainder` that supplied the file paths -- so what it proved was that the hand-written
wiring runs. This renders the equivalent for the wiring the pipeline itself produces, which is the
first thing that can say anything about that.

**It asserts the run, never the values** (ADR-0029). Whether the generated arithmetic matches COBOL is
decided by the Python differential against `transact.dat`; duplicating that judgement here would give
it a second place to disagree with itself. What this asserts is that the job reached `COMPLETED` and
wrote something -- because "the job did not run" and "the job ran and was wrong" are different
findings, and a differential handed no file cannot tell them apart.

**A plain `AnnotationConfigApplicationContext`, not `@SpringBootTest`.** Boot's autoconfiguration
would want a `DataSource` for the JPA starter the baseline ships and a container to put it in,
neither of which is what is being measured. The rendered configuration carries its own
`ResourcelessJobRepository` and `ResourcelessTransactionManager`, so the context it needs is exactly
the two rendered classes and a placeholder configurer.

**The staged paths are baked into the source rather than passed as `-D`.** Surefire does not forward
command-line system properties into the forked JVM without being configured to, and configuring the
generated project's POM to make this one test work would change the artifact the tenant ships. Baked
in, the rendered test is also readable after the fact: it names the four files the run actually used,
which is what an audit trail wants.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path

from cobol_modernizer.core.contracts import BatchJobDesign
from cobol_modernizer.rendering.java_file_bindings import bindings_class_name
from cobol_modernizer.rendering.java_job import configuration_class_name

logger = logging.getLogger(__name__)


class UnrenderableJobRunError(Exception):
    """The job cannot be launched by a rendered test, and the reason is stated rather than guessed.

    Refused rather than rendered, per this repository's standing rule: a test rendered without the
    job's parameters compiles, starts the job, and fails inside it -- which reports as "the generated
    code is wrong" when nothing about the generated code is in question.
    """


def run_test_class_name(job: BatchJobDesign) -> str:
    """`monthlyInterestCalculationJob` -> `MonthlyInterestCalculationJobRunTest`."""
    configuration = configuration_class_name(job)
    return configuration.removesuffix("Configuration") + "RunTest"


def render_job_run_test(
    job: BatchJobDesign,
    *,
    package: str,
    staged: Mapping[str, str],
    output_path: Path,
) -> str:
    """Render the JUnit test that starts this job and asserts it completed.

    `staged` is what `equivalence.staging.stage_oracle_inputs` returned -- the property overrides it
    made true. Taken as an argument rather than recomputed so that the file the test points at is
    the file staging actually wrote; deriving it twice is how the two start disagreeing.

    Raises:
        UnrenderableJobRunError: the job declares parameters this cannot supply values for.
    """
    if job.job_parameters:
        raise UnrenderableJobRunError(
            f"job {job.job_name!r} declares parameter(s) "
            f"{', '.join(sorted(p.name for p in job.job_parameters))}, and nothing here knows what "
            f"to launch it with. A run started without them fails inside the job, which reads as a "
            f"defect in the generated code"
        )

    test_class = run_test_class_name(job)
    configuration = configuration_class_name(job)
    bindings = bindings_class_name(job)

    entries = ",\n".join(
        f'            "{name}", "{_java_string(value)}"' for name, value in staged.items()
    )
    return f"""\
package {package};

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.JobExecution;
import org.springframework.batch.core.job.parameters.JobParametersBuilder;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.support.PropertySourcesPlaceholderConfigurer;
import org.springframework.core.env.MapPropertySource;

/**
 * Runs job "{job.job_name}", rendered from design.json for {job.program_name} by
 * cobol-modernizer (ADR-0075).
 *
 * <p>The assertions here are about the <em>run</em>, never about the values: whether the generated
 * logic matches COBOL is decided by the Python differential against the written records (ADR-0029),
 * and duplicating that judgement here would give it a second place to disagree with itself.
 *
 * <p>The paths below are the ones the harness staged for this run. They are absolute because the
 * working directory of a surefire fork is not the one that staged them.
 */
class {test_class} {{

    /** Where this run's inputs were staged, overriding the rendered {{@code cobol.file.*}} defaults. */
    private static final Map<String, Object> STAGED = Map.of(
{entries});

    /** What the job must have written by the time it reports {{@code COMPLETED}}. */
    private static final Path OUTPUT = Path.of("{_java_string(str(output_path))}");

    @Test
    void runsTheJobToCompletionAndWritesOutput() throws Exception {{
        JobExecution execution;
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {{
            // Ahead of every other source, so the staged paths win over the rendered defaults in
            // application.properties without that file having to know this run happened.
            context.getEnvironment()
                    .getPropertySources()
                    .addFirst(new MapPropertySource("cobol-modernizer-staged-run", STAGED));
            // The configuration and its file bindings are separate classes and neither imports the
            // other -- in the generated service they meet through component scanning, which is not
            // running here.
            context.register(
                    {configuration}.class,
                    {bindings}.class,
                    PropertySourcesPlaceholderConfigurer.class);
            context.refresh();

            Job job = context.getBean(Job.class);
            JobOperator operator = context.getBean(JobOperator.class);
            execution =
                    operator.start(
                            job,
                            new JobParametersBuilder()
                                    .addString("source", "cobol-modernizer-round-trip")
                                    .toJobParameters());
        }}

        assertEquals(
                "COMPLETED",
                execution.getExitStatus().getExitCode(),
                execution.getAllFailureExceptions().toString());
        // Asserted here rather than left to the differential so that "the job ran and wrote
        // nothing" cannot present as a comparison failure.
        assertTrue(Files.exists(OUTPUT), "the job completed and wrote nothing to " + OUTPUT);
    }}
}}
"""


def _java_string(value: str) -> str:
    r"""Escape a path for a Java string literal. Windows paths arrive full of `\`."""
    return value.replace("\\", "\\\\").replace('"', '\\"')
