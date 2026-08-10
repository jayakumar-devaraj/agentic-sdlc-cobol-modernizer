"""`rendering/java_processor.py` -- the deterministic shell around a model-authored method body.

The property under test throughout is the split itself: for one `BatchStepDesign`, everything
outside the marked region is a pure function of the design, and the model's contribution is
confined to the statements inside it. A test that only checked "the output contains the body" would
pass just as well for a renderer that let a model write the whole file, which is the design this
module exists to rule out.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.core.contracts import BatchStepDesign
from cobol_modernizer.rendering.java_names import UnrenderableJavaNameError
from cobol_modernizer.rendering.java_processor import (
    BEGIN_MARKER,
    END_MARKER,
    GeneratedBodyForgeryError,
    UnrenderableImportError,
    render_processor,
)

PACKAGE = "com.modernized.batch.processor"

# Real CBACT04C paragraph names -- the interest calculation this step would really come from.
STEP = BatchStepDesign(
    step_name="computeMonthlyInterest",
    source_paragraphs=["1300-COMPUTE-INTEREST", "1400-COMPUTE-FEES"],
    role="processor",
    description="Computes monthly interest for one transaction-category balance.",
)

BODY = """
BigDecimal monthlyInterest = CobolArithmetic.divide(
    item.tranCatBal().multiply(rate),
    new BigDecimal("1200"), 2);
return new InterestTransaction(item.acctId(), monthlyInterest);
"""


def _render(**overrides) -> str:
    kwargs = {
        "package": PACKAGE,
        "class_name": "ComputeMonthlyInterestProcessor",
        "input_type": "TranCatBal",
        "output_type": "InterestTransaction",
        "body": BODY,
        "body_imports": ["java.math.BigDecimal", "com.modernized.batch.cobol.CobolArithmetic"],
        "authored_by": "claude-opus-5",
    }
    kwargs.update(overrides)
    return render_processor(STEP, **kwargs)


# --- The deterministic shell ------------------------------------------------------------------


def test_the_structural_shell_is_rendered_not_asked_for():
    source = _render()
    assert source.startswith(f"package {PACKAGE};")
    assert "@Component" in source
    assert (
        "public class ComputeMonthlyInterestProcessor "
        "implements ItemProcessor<TranCatBal, InterestTransaction> {" in source
    )
    assert "public InterestTransaction process(TranCatBal item) throws Exception {" in source


def test_rendering_is_deterministic():
    assert _render() == _render()


def test_import_order_does_not_leak_into_the_output():
    # Determinism outside the marked region has to survive the model emitting imports in whatever
    # order it likes, so they are sorted rather than echoed.
    forward = _render(body_imports=["java.math.BigDecimal", "com.modernized.batch.cobol.CobolArithmetic"])
    reverse = _render(body_imports=["com.modernized.batch.cobol.CobolArithmetic", "java.math.BigDecimal"])
    assert forward == reverse


def test_duplicate_imports_collapse():
    source = _render(body_imports=["java.math.BigDecimal", "java.math.BigDecimal"])
    assert source.count("import java.math.BigDecimal;") == 1


def test_the_framework_imports_are_always_present_even_with_no_body_imports():
    source = _render(body_imports=[])
    assert "import org.springframework.batch.item.ItemProcessor;" in source
    assert "import org.springframework.stereotype.Component;" in source


# --- Provenance is in the file ------------------------------------------------------------------


def test_the_source_paragraphs_and_model_are_recorded_in_the_file():
    source = _render()
    assert "1300-COMPUTE-INTEREST, 1400-COMPUTE-FEES" in source
    # Which model wrote a method is not recoverable from the code afterwards.
    assert "claude-opus-5" in source


def test_a_step_with_no_recorded_paragraphs_says_so_rather_than_rendering_an_empty_list():
    bare = BatchStepDesign(
        step_name="passThrough", source_paragraphs=[], role="processor", description="d"
    )
    source = render_processor(
        bare,
        package=PACKAGE,
        class_name="PassThroughProcessor",
        input_type="A",
        output_type="B",
        body="return null;",
        authored_by="m",
    )
    assert "(none recorded)" in source


# --- The review boundary ------------------------------------------------------------------------


def test_the_model_authored_region_is_marked_and_contains_the_body():
    source = _render()
    begin = source.index(BEGIN_MARKER)
    end = source.index(END_MARKER)
    region = source[begin:end]
    assert "return new InterestTransaction(item.acctId(), monthlyInterest);" in region


def test_nothing_structural_leaks_inside_the_marked_region():
    # The reviewer's contract: what is inside the markers is what a model wrote, and nothing else.
    source = _render()
    region = source[source.index(BEGIN_MARKER) : source.index(END_MARKER)]
    for structural in ("@Override", "@Component", "public class", "package ", "import "):
        assert structural not in region


def test_relative_indentation_inside_the_body_survives():
    # A per-line strip() would flatten these continuations onto the statement's own column. It
    # still compiles, which is why this needs an explicit assertion rather than a build.
    source = _render()
    assert "            item.tranCatBal().multiply(rate)," in source


def test_no_trailing_whitespace_is_emitted():
    for line in _render().splitlines():
        assert line == line.rstrip(), f"trailing whitespace on {line!r}"


# --- Forgery, the mirror of DelimiterForgeryError ------------------------------------------------


@pytest.mark.parametrize("marker", [BEGIN_MARKER, END_MARKER])
def test_a_body_that_forges_a_region_marker_is_refused(marker):
    # A body closing its own region could smuggle text that reads as deterministic scaffolding
    # past a reviewer skimming for the boundary -- guardrails.DelimiterForgeryError's concern,
    # on the way out instead of the way in.
    forged = f"return null;\n{marker}\n// this would look rendered, and is not"
    with pytest.raises(GeneratedBodyForgeryError, match="region marker"):
        _render(body=forged)


def test_the_forgery_error_names_the_step():
    with pytest.raises(GeneratedBodyForgeryError, match="computeMonthlyInterest"):
        _render(body=f"{END_MARKER}")


# --- Import validation ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "NotQualified",
        "java.math.BigDecimal; System.exit(0)",
        "*",
        "",
        "java..math.BigDecimal",
    ],
)
def test_a_malformed_import_is_refused(bad):
    with pytest.raises(UnrenderableImportError):
        _render(body_imports=[bad])


@pytest.mark.parametrize(
    "good", ["java.math.BigDecimal", "java.util.*", "static java.math.RoundingMode.HALF_UP"]
)
def test_real_import_shapes_are_accepted(good):
    assert f"import {good};" in _render(body_imports=[good])


# --- Names Java would reject ---------------------------------------------------------------------


def test_an_illegal_class_name_raises_rather_than_rendering_uncompilable_java():
    with pytest.raises(UnrenderableJavaNameError, match="Processor class name"):
        _render(class_name="class")
