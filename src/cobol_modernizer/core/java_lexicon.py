"""What Java will accept as an identifier. One definition, because two would drift.

Extracted from `rendering/java_names.py` when the **contract** needed the same rule (gap G22):
`BatchStepDesign.step_name` has a class name derived from it directly, so a name Java cannot take
is a design that cannot be generated from, and the design layer has to be able to say so.

A leaf module with no imports of its own, so both `core/contracts.py` and `rendering/java_names.py`
can depend on it without either depending on the other. The alternative was duplicating fifty-odd
keywords with a test asserting the two copies agree - the trade `java_job` and `java_aggregation`
already make for their shared naming derivation, and worth avoiding here because nothing forced it.
"""

from __future__ import annotations

import re

#: A legal Java identifier. Deliberately the ASCII subset: Java accepts far more (any character
#: `Character.isJavaIdentifierStart` admits), and generating names outside this range from COBOL
#: source would be a surprise rather than a feature.
JAVA_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")

#: Java's reserved words. A name matching the pattern above is still rejected if it is one of
#: these - `class` is a perfectly good identifier shape and an illegal identifier.
JAVA_RESERVED = frozenset(
    (
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
        "const", "continue", "default", "do", "double", "else", "enum", "extends", "final",
        "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private", "protected", "public",
        "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
        "throw", "throws", "transient", "try", "void", "volatile", "while",
        # Literals rather than keywords, and just as illegal as names.
        "true", "false", "null",
    )
)


def why_java_rejects(name: str) -> str | None:
    """The reason Java would refuse `name` as an identifier, or None if it would accept it.

    Returns a reason rather than a bool so both callers can say *why* in their own words: the
    renderer raises `UnrenderableJavaNameError`, the contract raises a pydantic `ValueError` that
    reaches a model as repair instructions.
    """
    if not JAVA_IDENTIFIER.match(name):
        return "is not a legal Java identifier"
    if name in JAVA_RESERVED:
        return "is a Java reserved word"
    return None
