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
    MODEL_IMPORT_MARKER,
    GeneratedBodyForgeryError,
    NonDeterministicBodyError,
    UnrenderableImportError,
    model_authored_line_numbers,
    model_authored_line_range,
    render_processor,
)

PACKAGE = "com.modernized.batch.processor"

# Real CBACT04C paragraph names -- the interest calculation this step would really come from.
STEP = BatchStepDesign(
    step_name="computeMonthlyInterest",
    source_paragraphs=["1300-COMPUTE-INTEREST", "1400-COMPUTE-FEES"],
    input_type="TranCatBal",
    output_type="TranCatBal",
    role="processor",
    description="Computes monthly interest for one transaction-category balance.",
        guard_condition=None)

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


# --- G30: the file states which imports the model supplied ----------------------------------------


def test_a_model_supplied_import_is_marked_as_model_authored():
    """The artifact reports what the model wrote, in both regions rather than one.

    Before this, the BEGIN/END markers covered the body and nothing covered the imports, so a
    reviewer skimming for "what did a model produce here" got an incomplete answer -- and
    `build_validator`, which had only the file to go on, got the same incomplete answer and
    misattributed a model's bad import to this renderer.
    """
    source = _render(body_imports=["com.modernized.batch.cobol.CobolArithmetic"])
    assert f"import com.modernized.batch.cobol.CobolArithmetic;  {MODEL_IMPORT_MARKER}" in source


def test_the_framework_imports_this_renderer_adds_are_not_marked():
    # The other half of the claim: marking everything would be the same defect inverted, handing a
    # model responsibility for lines it never wrote.
    source = _render(body_imports=["java.math.BigDecimal"])
    for framework in (
        "org.springframework.batch.infrastructure.item.ItemProcessor",
        "org.springframework.stereotype.Component",
    ):
        assert f"import {framework};\n" in source + "\n"
        assert f"import {framework};  {MODEL_IMPORT_MARKER}" not in source


def test_an_import_the_renderer_would_have_emitted_anyway_is_not_marked():
    """A model naming a framework import does not become answerable for it.

    It is rendered unconditionally, so it would be in the file whether the model asked or not, and
    a diagnostic on that line is this repo's to fix. Marking it would hand a model a defect it
    could not have caused -- the exact error G30 was, pointing the other way.
    """
    source = _render(body_imports=["org.springframework.stereotype.Component"])
    assert "import org.springframework.stereotype.Component;  " not in source


def test_model_authored_line_numbers_covers_the_body_and_the_supplied_imports():
    source = _render(body_imports=["java.math.BigDecimal"])
    numbers = model_authored_line_numbers(source)
    span = model_authored_line_range(source)

    assert span is not None
    assert set(range(span[0], span[1] + 1)) <= numbers, "the body must still be attributed"

    lines = source.splitlines()
    import_line = next(
        n for n, text in enumerate(lines, start=1) if text.startswith("import java.math.BigDecimal;")
    )
    assert import_line in numbers, "the model's own import is not attributed to it"
    # And the renderer's own lines are still this repo's.
    package_line = next(n for n, text in enumerate(lines, start=1) if text.startswith("package "))
    assert package_line not in numbers


def test_attribution_is_unavailable_rather_than_empty_for_an_unmarked_file():
    # Same posture as `model_authored_line_range`: a hand-written or pre-marker file means nobody
    # knows, and the conservative reading of "nobody knows" is that nothing is the model's to rewrite.
    assert model_authored_line_numbers("class Foo {}") == frozenset()


def test_a_body_cannot_forge_import_attribution():
    """The marker only means something on a line that really is an import statement.

    A body is already entirely model-authored, so writing the marker inside one changes nothing --
    but the check is written so that it *cannot* matter, rather than relying on that argument
    holding after the next change. `_validated_imports` closes the other route: an import carrying a
    comment is not a bare qualified name and is refused outright.
    """
    source = _render(body=f'String s = "x"; {MODEL_IMPORT_MARKER}\nreturn item;')
    numbers = model_authored_line_numbers(source)
    package_line = next(
        n for n, text in enumerate(source.splitlines(), start=1) if text.startswith("package ")
    )
    assert package_line not in numbers

    with pytest.raises(UnrenderableImportError):
        _render(body_imports=[f"java.math.BigDecimal;  {MODEL_IMPORT_MARKER}"])


def test_marking_keeps_the_file_deterministic():
    forward = _render(body_imports=["java.math.BigDecimal", "com.modernized.batch.cobol.CobolText"])
    reverse = _render(body_imports=["com.modernized.batch.cobol.CobolText", "java.math.BigDecimal"])
    assert forward == reverse


def test_the_framework_imports_are_always_present_even_with_no_body_imports():
    # `...batch.infrastructure.item...`, not `...batch.item...`. This assertion carried the pre-6
    # package and so codified the bug: Spring Batch 6 moved it, and every processor rendered until
    # then had an import that does not resolve. A string assertion cannot catch that on its own --
    # `test_build_validator.py::test_a_rendered_processor_actually_compiles` is what pins it, by
    # compiling one. This test only guards against the imports going missing entirely.
    source = _render(body_imports=[])
    assert "import org.springframework.batch.infrastructure.item.ItemProcessor;" in source
    assert "import org.springframework.stereotype.Component;" in source


# --- Provenance is in the file ------------------------------------------------------------------


def test_the_source_paragraphs_and_model_are_recorded_in_the_file():
    source = _render()
    assert "1300-COMPUTE-INTEREST, 1400-COMPUTE-FEES" in source
    # Which model wrote a method is not recoverable from the code afterwards.
    assert "claude-opus-5" in source


def test_a_step_with_no_recorded_paragraphs_says_so_rather_than_rendering_an_empty_list():
    bare = BatchStepDesign(
        step_name="passThrough", source_paragraphs=[], input_type="TranCatBal", output_type="TranCatBal", role="processor", description="d",
        guard_condition=None)
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


# --- Reproducibility: a generated body may not read a clock -----------------------------------------


def test_the_real_body_that_reached_for_a_clock_is_refused():
    """The exact line a real model wrote, now rejected before it reaches a compiler.

    Asked to translate a paragraph performing `Z-GET-DB2-FORMAT-TIMESTAMP`, a real Opus 5 call
    reconstructed the timestamp layout from the `REDEFINES` sub-fields and filled it with
    `LocalDateTime.now()`. It compiled, it looked right, and it makes the same input produce a
    different record on every run -- including a restart that reprocesses one chunk.

    It flagged the choice in its notes, which is the only reason it was caught. The next one may
    not, so this is a refusal rather than a note.
    """
    body = (
        'String db2FormatTs = LocalDateTime.now()\n'
        '        .format(DateTimeFormatter.ofPattern("yyyy-MM-dd-HH.mm.ss.SS")) + "0000";\n'
        "return new Tran(db2FormatTs);"
    )
    with pytest.raises(NonDeterministicBodyError, match="ambient state"):
        render_processor(
            STEP,
            package=PACKAGE,
            class_name="CompleteTransactionProcessor",
            input_type="TranWithContext",
            output_type="Tran",
            body=body,
            authored_by="test",
        )


@pytest.mark.parametrize(
    "snippet",
    [
        "Instant.now()",
        "System.currentTimeMillis()",
        "new Random().nextInt()",
        "Math.random()",
        "UUID.randomUUID()",
        'System.getenv("RUN_DATE")',
    ],
)
def test_every_ambient_source_is_refused(snippet):
    with pytest.raises(NonDeterministicBodyError):
        render_processor(
            STEP,
            package=PACKAGE,
            class_name="P",
            input_type="A",
            output_type="B",
            body=f"return {snippet};",
            authored_by="test",
        )


@pytest.mark.parametrize(
    "snippet",
    [
        "item.tran().tranAmt()",
        "item.nowField()",
        "CobolText.spaces(50)",
        "// the COBOL reads the clock here; see notes",
    ],
)
def test_a_body_that_merely_mentions_time_is_not_refused(snippet):
    """The check matches call sites, not words.

    A field named `nowField`, a helper call, or a comment explaining why the clock was *not* used
    must all pass -- a guard that fired on the word would push a model into writing worse notes.
    """
    source = render_processor(
        STEP,
        package=PACKAGE,
        class_name="P",
        input_type="A",
        output_type="B",
        body=f"return {snippet};" if not snippet.startswith("//") else f"{snippet}\nreturn null;",
        authored_by="test",
    )
    assert snippet.strip("/ ") in source or snippet in source
