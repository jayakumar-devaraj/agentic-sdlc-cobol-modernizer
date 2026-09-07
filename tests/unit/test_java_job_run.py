"""`java_job_run` renders the test that starts a generated job (ADR-0075).

**Why the class names are asserted against the renderers rather than spelled out.** The runner has
to compile against `configuration_class_name(job)` and `bindings_class_name(job)`, both of which are
derived from the design's own `job_name` -- and the two live designs produce different ones
(`InterestCalculationJobConfiguration` and `MonthlyInterestCalculationJobConfiguration`). A test
holding a literal would pass against the design it was written for and say nothing about the next.
That is what makes this a rendered runner rather than a copied one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import DesignDocument, JobParameter
from cobol_modernizer.rendering.java_file_bindings import bindings_class_name
from cobol_modernizer.rendering.java_job import configuration_class_name
from cobol_modernizer.rendering.java_job_run import (
    UnrenderableJobRunError,
    render_job_run_test,
    run_test_class_name,
)

LIVE_DESIGNS = Path(__file__).resolve().parents[1] / "fixtures" / "live_designs"
LIVE = ["cbact04c-design-step56.json", "cbact04c-design-step58.json"]

STAGED = {
    "cobol.file.tcatbalf": r"C:\tmp\p\roundtrip\input\tcatbal-posted.dat",
    "cobol.file.transact": r"C:\tmp\p\roundtrip\output\transact.dat",
}


def _job(name: str):
    document = DesignDocument.model_validate_json((LIVE_DESIGNS / name).read_text(encoding="utf-8"))
    design = document.unified_design
    assert design is not None
    return design.batch_jobs[0]


@pytest.mark.parametrize("name", LIVE)
def test_the_runner_registers_the_classes_this_design_actually_produced(name: str) -> None:
    """Both rendered classes are named from the design, and both are registered.

    Neither imports the other -- in a generated service they meet through component scanning, which
    is not running in an `AnnotationConfigApplicationContext`. A runner registering only the
    configuration compiles and fails at refresh with no reader bean.
    """
    job = _job(name)

    source = render_job_run_test(
        job, package="com.modernized.batch.job", staged=STAGED, output_path=Path("out.dat")
    )

    assert f"{configuration_class_name(job)}.class" in source
    assert f"{bindings_class_name(job)}.class" in source
    assert f"class {run_test_class_name(job)} " in source
    # Without this the `@Value` placeholders stay literal and every bound path becomes a file
    # named `${cobol.file.tcatbalf}`.
    assert "PropertySourcesPlaceholderConfigurer.class" in source


@pytest.mark.parametrize("name", LIVE)
def test_the_two_live_designs_produce_different_runner_names(name: str) -> None:
    """A guard on the reason this is rendered at all: the names are not a constant."""
    other = next(n for n in LIVE if n != name)
    assert run_test_class_name(_job(name)) != run_test_class_name(_job(other))


def test_windows_paths_survive_as_java_string_literals() -> None:
    """A staged path is full of backslashes, and an unescaped one is an invalid escape sequence.

    `\\t` in `C:\\tmp` is a tab to javac, so this is a compile failure rather than a wrong path --
    which would report as "the rendered runner does not compile" and hide the cause.
    """
    source = render_job_run_test(
        _job("cbact04c-design-step58.json"),
        package="com.modernized.batch.job",
        staged=STAGED,
        output_path=Path(r"C:\tmp\p\roundtrip\output\transact.dat"),
    )

    assert r"C:\\tmp\\p\\roundtrip\\input\\tcatbal-posted.dat" in source
    assert r"C:\tmp" not in source


def test_the_runner_asserts_the_run_and_never_the_values() -> None:
    """ADR-0029: the Python differential owns the judgement about values.

    Asserted because the tempting next change is to check a total here, which would give that
    judgement a second place to disagree with itself.
    """
    source = render_job_run_test(
        _job("cbact04c-design-step58.json"),
        package="com.modernized.batch.job",
        staged=STAGED,
        output_path=Path("out.dat"),
    )

    assert '"COMPLETED"' in source
    assert "Files.exists(OUTPUT)" in source
    # No arithmetic, no expected amounts, no oracle.
    assert "BigDecimal" not in source
    assert "oracle" not in source.lower().replace("cobol-modernizer", "")


def test_a_job_declaring_parameters_is_refused() -> None:
    """Refused rather than launched without them, so the failure names its own cause.

    A run started without a parameter its steps consume fails *inside* the job, which reads as a
    defect in the generated code -- the exact misattribution this repository keeps separating.
    """
    job = _job("cbact04c-design-step58.json")
    job.job_parameters = [
        JobParameter(name="parmDate", java_type="String", description="PARM-DATE")
    ]

    with pytest.raises(UnrenderableJobRunError, match="parmDate"):
        render_job_run_test(
            job, package="com.modernized.batch.job", staged=STAGED, output_path=Path("out.dat")
        )
