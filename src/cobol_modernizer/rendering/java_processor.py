"""Render a Spring Batch `ItemProcessor` around an LLM-authored method body.

This is where the deterministic/model split becomes physical. Everything structural -- the package,
the imports, the `@Component`, the `implements ItemProcessor<I, O>`, the method signature, the
Javadoc, the provenance -- is rendered here from `BatchStepDesign`, which is structured data. The
model is asked for one thing only: the statements inside `process`. It never writes a file, never
chooses a class name, and never decides what the class implements.

**Why the boundary is marked in the generated source.** A reviewer's time is the dominant cost of a
migration at scale -- far above inference -- so the artifact tells them exactly which lines need
review. Everything outside the BEGIN/END markers is a pure function of `design.json` and is
reviewable once, by reading this module. Everything inside was written by a model and is reviewable
every time. Hiding that distinction would mean a reviewer either re-reads generated boilerplate
forever or skims the one part that actually needs them.

**The markers are load-bearing, so they are checked.** A body containing the END marker could
smuggle text that reads as deterministic scaffolding past a reviewer skimming for the boundary --
the same forgery concern `core/guardrails.DelimiterForgeryError` exists for on the way in, now on
the way out. `GeneratedBodyForgeryError` is the symmetric refusal: a body that forges the boundary
is rejected rather than escaped, because a body that tries is not one to trust the rest of.
"""

from __future__ import annotations

import logging
import re
import textwrap
from collections.abc import Sequence

from cobol_modernizer.core.contracts import BatchStepDesign
from cobol_modernizer.rendering.java_names import require_java_identifier

logger = logging.getLogger(__name__)

#: A fully-qualified Java type name, optionally a `static` member import. The model supplies the
#: imports its body needs -- the renderer cannot know them, since it never reads the body -- so
#: they are validated as a shape rather than accepted verbatim. Anything else in an import
#: statement is either a mistake or an attempt to put arbitrary text at the top of the file.
_IMPORT = re.compile(r"^(?:static\s+)?[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+(?:\.\*)?$")

#: Opens the model-authored region. `authored_by` and the source paragraphs are interpolated in.
BEGIN_MARKER = "// --- BEGIN model-authored logic"

#: Closes it. Deliberately a fixed string with no interpolation, so the check below is exact.
END_MARKER = "// --- END model-authored logic ---"

_BODY_INDENT = " " * 8


class UnrenderableImportError(Exception):
    """A model-supplied import is not a fully-qualified Java type name.

    The model is asked for the imports its body needs, because the renderer never reads the body
    and so cannot derive them. That makes the import list model-authored text placed *outside* the
    reviewed region, which is exactly why it is validated as a shape: an unchecked string here
    would be arbitrary text at the top of a file whose whole design is that everything outside the
    markers is deterministic.
    """


class GeneratedBodyForgeryError(Exception):
    """A model-authored body contained one of the region markers.

    The mirror of `core.guardrails.DelimiterForgeryError`: that one refuses untrusted COBOL that
    forges a prompt delimiter on the way in, this one refuses a model body that forges the
    review boundary on the way out. Both fail loudly rather than escaping the offending text --
    a body that tries to close its own region is not one whose remainder should be trusted into
    a file a human is about to approve.
    """


def _require_no_forged_marker(body: str, *, step_name: str) -> None:
    for marker in (BEGIN_MARKER, END_MARKER):
        if marker in body:
            raise GeneratedBodyForgeryError(
                f"model-authored body for step {step_name!r} contains the region marker "
                f"{marker!r}; refusing to render it"
            )


def _indent_body(body: str) -> list[str]:
    """Re-indent the model's statements to the method body's level, preserving relative structure.

    Deliberately not a per-line `strip()`: that flattens a wrapped expression's continuation lines
    onto the same column as the statement that owns them, which still compiles and is exactly the
    kind of formatting damage that makes generated code harder to review than it needed to be.
    The common leading indent is removed and the block re-indented as a unit, so whatever internal
    shape the model produced survives.
    """
    dedented = textwrap.dedent(body.strip("\n")).strip("\n")
    return [
        f"{_BODY_INDENT}{line}".rstrip() if line.strip() else ""
        for line in dedented.splitlines()
    ]


def _validated_imports(imports: Sequence[str]) -> list[str]:
    """Deduplicate and sort the model's imports, rejecting anything that is not one.

    Sorted so the rendered file is byte-identical for the same design regardless of the order the
    model happened to emit them in -- determinism outside the marked region is the property this
    whole module exists to hold.
    """
    for candidate in imports:
        if not _IMPORT.match(candidate.strip()):
            raise UnrenderableImportError(
                f"{candidate!r} is not a fully-qualified Java import"
            )
    return sorted({candidate.strip() for candidate in imports})


def model_authored_line_range(java_source: str) -> tuple[int, int] | None:
    """The 1-based line range a model actually wrote, exclusive of the markers themselves.

    The reason the markers are worth having beyond documentation: a compile error can be attributed
    to the model or to the renderer. An error in rendered scaffolding is a defect in *this* module,
    and asking a model to repair it would invite it to rewrite deterministic code to make a symptom
    go away. `build_validator` uses this to refuse that.

    Returns `None` when the file carries no marked region -- a hand-written file, or one rendered
    before the markers existed. Callers must treat that as "attribution unavailable" rather than as
    "nothing is model-authored", since the two justify opposite decisions.
    """
    begin = end = None
    for number, line in enumerate(java_source.splitlines(), start=1):
        if begin is None and BEGIN_MARKER in line:
            begin = number
        elif END_MARKER in line:
            end = number
            break
    if begin is None or end is None or end <= begin:
        return None
    # Exclusive of both marker lines: the markers are rendered, not authored.
    return (begin + 1, end - 1)


def render_processor(
    step: BatchStepDesign,
    *,
    package: str,
    class_name: str,
    input_type: str,
    output_type: str,
    body: str,
    body_imports: Sequence[str] = (),
    authored_by: str,
) -> str:
    """Render an `ItemProcessor` for `step`, wrapping the model-authored `body`.

    `body_imports` are the imports the body needs. They come from the model rather than from here
    because this renderer never reads the body -- that is the point of the split -- so it cannot
    derive them. They are validated as fully-qualified names and sorted, so the file stays
    byte-identical for one design.

    `authored_by` is the model id that produced `body` and is recorded in the generated file: which
    model wrote a given method is part of the provenance a reviewer and an audit both need, and it
    is not recoverable from the code afterwards.

    Raises `UnrenderableJavaNameError` for a name Java would reject, `UnrenderableImportError` for
    an import that is not one, and `GeneratedBodyForgeryError` for a body that forges the
    review-region markers.
    """
    require_java_identifier(class_name, source_name=step.step_name, kind="Processor class name")
    _require_no_forged_marker(body, step_name=step.step_name)

    # `...batch.infrastructure.item...`, not `...batch.item...`. **Spring Batch 6 moved the
    # package**, and this renderer had the pre-6 name -- so every processor it produced carried an
    # import that does not resolve, and would have failed to compile the moment step 42 tried to
    # build one. Found by compiling a rendered processor rather than by reading the pom, and it is
    # the same shape as PR #27's finding that Spring Boot 4 deleted `spring.batch.jdbc.*`: the
    # framework moved, and every pre-6 example still says otherwise. A model's training data
    # overwhelmingly says otherwise too, which is exactly why this import is rendered rather than
    # left for the model to supply.
    framework_imports = [
        "org.springframework.batch.infrastructure.item.ItemProcessor",
        "org.springframework.stereotype.Component",
    ]
    all_imports = sorted({*framework_imports, *_validated_imports(body_imports)})
    import_block = "\n".join(f"import {name};" for name in all_imports)

    paragraphs = ", ".join(step.source_paragraphs) or "(none recorded)"
    source = f"""\
package {package};

{import_block}

/**
 * {class_name} -- batch step "{step.step_name}" ({step.role}).
 *
 * <p>{step.description}
 *
 * <p>Derived from COBOL paragraph(s): {paragraphs}.
 *
 * <p>Everything in this file outside the marked region is rendered deterministically from
 * design.json and is identical for identical input. Only the marked region was written by a
 * model ({authored_by}) and needs review as generated code.
 */
@Component
public class {class_name} implements ItemProcessor<{input_type}, {output_type}> {{

    @Override
    public {output_type} process({input_type} item) throws Exception {{
        {BEGIN_MARKER} ({authored_by}; from {paragraphs}) ---
{{body}}
        {END_MARKER}
    }}
}}
"""
    rendered = source.replace("{body}", "\n".join(_indent_body(body)))

    logger.debug(
        "rendered processor %s for step %s (%d body line(s), authored_by=%s)",
        class_name,
        step.step_name,
        len(body.strip().splitlines()),
        authored_by,
    )
    return rendered
