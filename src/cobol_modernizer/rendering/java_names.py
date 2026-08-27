"""Java identifier validation, shared by every renderer.

Extracted from `java_records.py`'s private helper once `java_processor.py` needed the same check --
the same trigger that moved `core/source_units.py` out of `spec_extractor` when `spec_critic`
became a second real caller. Two copies of a reserved-word list is exactly the kind of thing that
drifts apart silently.
"""

from __future__ import annotations

from cobol_modernizer.core.java_lexicon import why_java_rejects

#: Every Java reserved word, plus the three literals that are not technically keywords but are
#: equally illegal as identifiers. A COBOL name is transformed mechanically into a Java one, and
#: nothing in that transform knows what Java forbids -- a COBOL field named `CLASS` becomes
#: `class` and would not compile. Rendering it anyway produces a `javac` error pointing at
#: generated code rather than at the real cause.


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
    reason = why_java_rejects(name)
    if reason is not None:
        raise UnrenderableJavaNameError(f"{kind} {name!r} (from {source_name!r}) {reason}")
    return name
