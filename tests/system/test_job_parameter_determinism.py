"""ADR-0026's real test: a job parameter makes a record reproducible, and it actually reaches the body.

**Why compiling is not enough here.** The whole point of G29 is behavioural — the same input must
produce the same record on two runs, including a restart that reprocesses a chunk. A rendered
processor that compiles proves the `@StepScope`/`@Value` shape is legal, and says nothing about
whether the body can read the value or whether the output is stable. So this renders a real
processor, renders a JUnit test around it, and runs both through real Maven.

**And the harness is shown to discriminate before it is trusted**, the discipline step 45 established
with its `divideRounded` body. Two instances built with the *same* parameter must agree; two built
with *different* parameters must disagree. Without the second assertion the first is vacuous — a body
that ignored the parameter entirely, or a renderer that dropped it, would pass a same-input equality
check perfectly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import BatchStepDesign, JobParameter
from cobol_modernizer.core.package_data import TEMPLATES_ROOT
from cobol_modernizer.rendering.java_processor import render_processor
from cobol_modernizer.tools.local_compiler import compile_project

TEMPLATE = TEMPLATES_ROOT / "target-spring-boot-baseline"
PACKAGE = "com.modernized.batch.processor"

STEP = BatchStepDesign(
    step_name="stampTransaction",
    source_paragraphs=["1300-B-WRITE-TX"],
    role="processor",
    description="Stamps a record with the run's timestamp -- CBACT04C's TRAN-ORIG-TS/TRAN-PROC-TS.",
    input_type="java.math.BigDecimal",
    output_type="String",
    guard_condition=None,
    job_parameters=["runTimestamp"],
)

RUN_TIMESTAMP = JobParameter(
    name="runTimestamp",
    java_type="String",
    description="The run's DB2-format instant, supplied once per invocation.",
    # No `source_cobol`: ADR-0026 records this as a deliberate divergence from `FUNCTION
    # CURRENT-DATE`, which COBOL reads per record, rather than as an equivalent of it.
    source_cobol=None,
)

#: Reads the injected field, exactly as the real `1300-B-WRITE-TX` body would for its two timestamps.
BODY = 'return runTimestamp + "|" + item.toPlainString();'

DETERMINISM_TEST = f"""\
package {PACKAGE};

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;

import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/** Rendered by tests/system/test_job_parameter_determinism.py -- see that module for why. */
class StampTransactionDeterminismTest {{

    private static final BigDecimal ITEM = new BigDecimal("194.00");

    @Test
    void sameJobParameterProducesTheSameRecord() throws Exception {{
        var first = new StampTransactionProcessor("2026-08-11-12.00.00.000000");
        var second = new StampTransactionProcessor("2026-08-11-12.00.00.000000");
        assertEquals(first.process(ITEM), second.process(ITEM));
    }}

    @Test
    void twoProcessingsWithinOneInstanceAgreeToo() throws Exception {{
        var processor = new StampTransactionProcessor("2026-08-11-12.00.00.000000");
        assertEquals(processor.process(ITEM), processor.process(ITEM));
    }}

    @Test
    void aDifferentJobParameterProducesADifferentRecord() throws Exception {{
        // The teeth. Without this the equality checks above would pass for a body that ignored the
        // parameter, or for a renderer that never wired it in.
        var first = new StampTransactionProcessor("2026-08-11-12.00.00.000000");
        var second = new StampTransactionProcessor("2026-08-11-13.30.00.000000");
        assertNotEquals(first.process(ITEM), second.process(ITEM));
    }}
}}
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("jobparam-project") / "proj"
    shutil.copytree(TEMPLATE, destination, ignore=shutil.ignore_patterns("target"))

    source = render_processor(
        STEP,
        package=PACKAGE,
        class_name="StampTransactionProcessor",
        input_type=STEP.input_type,
        output_type=STEP.output_type,
        body=BODY,
        authored_by="test",
        job_parameters=[RUN_TIMESTAMP],
    )
    package_dir = destination / "src" / "main" / "java" / Path(PACKAGE.replace(".", "/"))
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "StampTransactionProcessor.java").write_text(source, encoding="utf-8")

    test_dir = destination / "src" / "test" / "java" / Path(PACKAGE.replace(".", "/"))
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "StampTransactionDeterminismTest.java").write_text(
        DETERMINISM_TEST, encoding="utf-8"
    )
    return destination


def test_the_generated_processor_is_reproducible_under_real_maven(project):
    """The claim G29 exists for, run rather than argued.

    `mvn verify` compiles the rendered processor *and* executes the three JUnit cases above. A green
    result means: the `@StepScope`/`@Value` shape is legal Java, the injected value is readable from
    a model-authored body, the output is stable across instances and across calls, and the parameter
    genuinely determines the record.
    """
    result = compile_project(project, goal="verify")
    assert result.succeeded, "\n".join(e.render() for e in result.errors) or result.raw_output[-2000:]


def test_the_processor_is_constructible_without_a_spring_context(project):
    """Why ADR-0026 chose constructor injection over field injection.

    The determinism test above builds the processor with `new`. That is only possible because the
    parameters arrive through the constructor -- with field injection the class would need a Spring
    context to be exercised at all, and the property that matters would be checkable only by
    launching a job.
    """
    source = (
        project / "src" / "main" / "java" / Path(PACKAGE.replace(".", "/"))
        / "StampTransactionProcessor.java"
    ).read_text(encoding="utf-8")

    assert "public StampTransactionProcessor(" in source
    assert "private final String runTimestamp;" in source
    assert "@StepScope" in source, "without @StepScope the jobParameters expression cannot resolve"
    assert "this.runTimestamp = runTimestamp;" in source
