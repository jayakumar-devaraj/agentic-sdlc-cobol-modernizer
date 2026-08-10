"""Java identifier validation, shared by every renderer.

Extracted from `java_records.py`'s private helper once `java_processor.py` needed the same check --
the same trigger that moved `core/source_units.py` out of `spec_extractor` when `spec_critic`
became a second real caller. Two copies of a reserved-word list is exactly the kind of thing that
drifts apart silently.
"""

from __future__ import annotations

import re

#: Every Java reserved word, plus the three literals that are not technically keywords but are
#: equally illegal as identifiers. A COBOL name is transformed mechanically into a Java one, and
#: nothing in that transform knows what Java forbids -- a COBOL field named `CLASS` becomes
#: `class` and would not compile. Rendering it anyway produces a `javac` error pointing at
#: generated code rather than at the real cause.
JAVA_RESERVED = frozenset(
    (
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
        "const", "continue", "default", "do", "double", "else", "enum", "extends", "final",
        "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private", "protected", "public",
        "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
        "throw", "throws", "transient", "try", "void", "volatile", "while",
        # `true`/`false`/`null` are literals rather than keywords in the JLS, but are equally
        # illegal as identifiers, so they belong in the same check.
        "true", "false", "null",
    )
)

#: A legal Java identifier, restricted to the ASCII subset a mechanical COBOL-name transform can
#: actually produce. Deliberately narrower than the JLS (which allows most Unicode letters): a name
#: outside this set did not come from the transform these renderers are fed by, and silently
#: accepting it would mean rendering something no one checked.
JAVA_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


class UnrenderableJavaNameError(Exception):
    """A name cannot be rendered as a legal Java identifier.

    Joins the `UnsupportedPicConstructError`/`UnsupportedCopyConstructError` family: an unambiguous
    case that must fail loudly rather than be guessed at or silently mangled. Renaming to dodge a
    collision (`class` -> `class_`) is the kind of quiet fix that makes generated code stop
    matching the COBOL it claims to implement, so the caller is told instead.
    """


def require_java_identifier(name: str, *, source_name: str, kind: str) -> str:
    """Return `name` unchanged, or raise if Java could not accept it as an identifier.

    `source_name` is the COBOL name (or design element) the identifier came from, and goes into the
    error message so a report points at the source rather than at the generated file.
    """
    if not JAVA_IDENTIFIER.match(name):
        raise UnrenderableJavaNameError(
            f"{kind} {name!r} (from {source_name!r}) is not a legal Java identifier"
        )
    if name in JAVA_RESERVED:
        raise UnrenderableJavaNameError(
            f"{kind} {name!r} (from {source_name!r}) is a Java reserved word"
        )
    return name
