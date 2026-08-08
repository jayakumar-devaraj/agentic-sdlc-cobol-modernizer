"""Deterministic mapping from a COBOL `PIC` clause to a Java `BigDecimal`/`String` shape.

This is the platform's zero-data-drift claim in code form. Precision, scale, and signedness
are computed from the clause text by regex and arithmetic -- never inferred by a model call.
See `.claude/agents/development.md`, "`pic_mapper` is deterministic, not a model call": a
non-deterministic path here (an LLM guessing at precision) would be the one place a wrong
answer looks exactly like a right one.

Two failure modes are handled by refusing to guess, not by producing a plausible answer:

- A field declaration that mixes `REDEFINES`, `OCCURS ... DEPENDING ON`, or a fixed `OCCURS`
  into the same or an adjacent record structure raises `UnsupportedPicConstructError`. Per
  ADR-0002 (docs/adr/0002-a-hand-rolled-parser-for-a-deliberately-bounded-grammar.md), the first
  two require resolving which of several overlapping interpretations of the same bytes is active
  -- a real alias-analysis problem this hand-rolled parser does not attempt. A fixed `OCCURS` is
  different and is rejected for its own reason (ADR-0011): it is perfectly unambiguous, but this
  module's `PicMapping` has no way to express "there are N of these", so mapping such a field
  would return a correct precision and scale attached to a silently wrong cardinality -- one
  scalar where the record really holds an array. Callers must catch this and route it to a
  human-in-the-loop gate, not swallow it and fall back to a best guess.
- A `PIC` clause this module cannot resolve deterministically (no recognized tokens, or a mix
  of numeric and alphanumeric tokens) raises `ValueError` rather than guessing.

`USAGE` clauses (including `COMP-3`) are always detected and recorded, even though no Track C
program's copybook currently declares one (verified in
docs/cobol-construct-support-matrix.md against the real CVACT01Y/CVTRA02Y/CVTRA05Y source) --
this module must not silently ignore a construct just because today's fixtures don't exercise it.
"""

from __future__ import annotations

import re
from enum import Enum

from pydantic import BaseModel


class PicFieldType(str, Enum):
    """What kind of Java shape a PIC clause maps to."""

    NUMERIC = "numeric"
    ALPHANUMERIC = "alphanumeric"


class UsageClause(str, Enum):
    """COBOL `USAGE` clauses this module can identify.

    `DISPLAY` is COBOL's implicit default when no `USAGE` clause is present (zoned decimal for
    numeric fields) -- it is not itself evidence that a `USAGE` clause was written.
    """

    DISPLAY = "DISPLAY"
    COMP = "COMP"
    COMP_1 = "COMP-1"
    COMP_2 = "COMP-2"
    COMP_3 = "COMP-3"
    BINARY = "BINARY"


class UnsupportedPicConstructError(Exception):
    """A field declaration involves `REDEFINES`, `OCCURS ... DEPENDING ON`, or a fixed `OCCURS`.

    Per ADR-0002 (the first two) and ADR-0011 (the third), these must be detected and routed to a
    human-in-the-loop gate, never partially parsed or guessed at. Catch this at the call site and
    surface it as a gate item in the JSON this repo's CLI returns to control-plane -- do not
    catch-and-continue with a best-effort mapping.

    `construct` names which one was found (`"REDEFINES"`, `"OCCURS ... DEPENDING ON"`, or
    `"OCCURS (fixed)"`) so a reviewer reading a gate item knows what kind of ambiguity they are
    being asked about without re-reading the source.
    """

    def __init__(self, construct: str, declaration_text: str) -> None:
        self.construct = construct
        self.declaration_text = declaration_text
        super().__init__(
            f"Unsupported construct {construct!r} detected in field declaration; per "
            f"ADR-0002 this must route to a human gate, not be parsed: {declaration_text!r}"
        )


class PicMapping(BaseModel):
    """Deterministic result of mapping one COBOL field declaration's PIC clause.

    For `field_type == ALPHANUMERIC`, `precision`/`scale` are `None` and `java_type` is
    `"String"` -- callers must not treat an alphanumeric field as a `BigDecimal` candidate.
    """

    field_name: str | None = None
    raw_pic: str
    field_type: PicFieldType
    usage: UsageClause = UsageClause.DISPLAY
    signed: bool = False
    precision: int | None = None
    scale: int | None = None
    string_length: int | None = None
    java_type: str
    source_text: str


_LEVEL_NAME_RE = re.compile(r"^\s*(\d+)\s+([A-Za-z0-9\-]+)")
_PIC_RE = re.compile(r"PIC(?:TURE)?\s+(?:IS\s+)?([S9VXA0-9()]+)", re.IGNORECASE)
_PIC_TOKEN_RE = re.compile(r"([S9VXA])(?:\((\d+)\))?", re.IGNORECASE)

_REDEFINES_RE = re.compile(r"\bREDEFINES\b", re.IGNORECASE)
_OCCURS_DEPENDING_RE = re.compile(r"\bOCCURS\b.*?\bDEPENDING\s+ON\b", re.IGNORECASE | re.DOTALL)
_OCCURS_RE = re.compile(r"\bOCCURS\b", re.IGNORECASE)

# Order matters: more specific patterns (COMP-3) must be checked before their prefix (COMP).
_USAGE_PATTERNS: list[tuple[re.Pattern[str], UsageClause]] = [
    (re.compile(r"\bCOMP(?:UTATIONAL)?-3\b", re.IGNORECASE), UsageClause.COMP_3),
    (re.compile(r"\bPACKED-DECIMAL\b", re.IGNORECASE), UsageClause.COMP_3),
    (re.compile(r"\bCOMP(?:UTATIONAL)?-2\b", re.IGNORECASE), UsageClause.COMP_2),
    (re.compile(r"\bCOMP(?:UTATIONAL)?-1\b", re.IGNORECASE), UsageClause.COMP_1),
    (re.compile(r"\bBINARY\b", re.IGNORECASE), UsageClause.BINARY),
    (re.compile(r"\bCOMP(?:UTATIONAL)?\b", re.IGNORECASE), UsageClause.COMP),
]


def _check_unsupported_constructs(*texts: str) -> None:
    """Raise if `REDEFINES`, `OCCURS ... DEPENDING ON`, or a fixed `OCCURS` appears in the texts.

    Callers pass both the field's own declaration text and any adjacent record-structure text
    they have available, so a construct on a sibling field in the same 01-level group is still
    caught -- the ADR-0002 boundary applies to "the same or an adjacent record structure", not
    just the single field line being mapped.

    `OCCURS ... DEPENDING ON` is checked before the plain `OCCURS` fallback so the more specific
    construct keeps its own, more informative name in the raised error -- the same
    specific-before-general ordering `_USAGE_PATTERNS` uses for `COMP-3` before `COMP`.

    Scoping note, unchanged in spirit from ADR-0006: because the parser hands over a whole
    01-level group as `adjacent_text` and carries no nesting information, a group containing one
    of these constructs has *all* of its fields isolated, not only the fields genuinely inside
    it. That over-flags -- `ARR-ACCT-ID` sits beside `CBACT01C`'s real `OCCURS 5 TIMES` group
    without being part of it -- and over-flagging routes an unambiguous field to a human gate,
    which is the safe direction to be wrong in. See ADR-0011.
    """
    combined = "\n".join(texts)
    if _REDEFINES_RE.search(combined):
        raise UnsupportedPicConstructError("REDEFINES", combined)
    if _OCCURS_DEPENDING_RE.search(combined):
        raise UnsupportedPicConstructError("OCCURS ... DEPENDING ON", combined)
    if _OCCURS_RE.search(combined):
        raise UnsupportedPicConstructError("OCCURS (fixed)", combined)


def _detect_usage(declaration_text: str) -> UsageClause:
    for pattern, usage in _USAGE_PATTERNS:
        if pattern.search(declaration_text):
            return usage
    return UsageClause.DISPLAY


def _extract_field_name(declaration_text: str) -> str | None:
    match = _LEVEL_NAME_RE.search(declaration_text)
    return match.group(2).upper() if match else None


def map_pic_clause(declaration_text: str, *, adjacent_text: str = "") -> PicMapping:
    """Map one COBOL field declaration's `PIC` clause to a deterministic Java shape.

    Args:
        declaration_text: The raw field declaration, e.g.
            `"05  ACCT-CURR-BAL                     PIC S9(10)V99."`.
        adjacent_text: Optional surrounding record-structure text (sibling fields in the same
            01-level group) to also scan for `REDEFINES`/`OCCURS ... DEPENDING ON`/fixed
            `OCCURS`, per the ADR-0002 boundary that these constructs are rejected even when
            found in an adjacent structure, not only the field being mapped. A fixed `OCCURS`
            especially needs this: it is declared on the *parent* group line
            (`05 ARR-ACCT-BAL OCCURS 5 TIMES.`), never on the `PIC`-carrying children it
            actually multiplies, so checking the field's own line alone would always miss it.

    Returns:
        A `PicMapping` with precision/scale/signedness for numeric fields, or a string-length
        signal for alphanumeric fields -- never a guess.

    Raises:
        UnsupportedPicConstructError: `REDEFINES`, `OCCURS ... DEPENDING ON`, or a fixed
            `OCCURS` was detected.
        ValueError: No `PIC` clause was found, or its tokens can't be resolved deterministically
            (e.g. it mixes numeric and alphanumeric characters).
    """
    _check_unsupported_constructs(declaration_text, adjacent_text)

    pic_match = _PIC_RE.search(declaration_text)
    if not pic_match:
        raise ValueError(
            f"No PIC clause found in field declaration; cannot map deterministically: "
            f"{declaration_text!r}"
        )
    raw_pic = pic_match.group(1).upper()

    integer_digits = 0
    decimal_digits = 0
    alnum_length = 0
    signed = False
    seen_v = False
    has_numeric = False
    has_alnum = False

    for char, count_str in _PIC_TOKEN_RE.findall(raw_pic):
        count = int(count_str) if count_str else 1
        if char == "S":
            signed = True
        elif char == "V":
            seen_v = True
        elif char == "9":
            has_numeric = True
            if seen_v:
                decimal_digits += count
            else:
                integer_digits += count
        elif char in ("X", "A"):
            has_alnum = True
            alnum_length += count

    if has_numeric and has_alnum:
        raise ValueError(
            f"PIC clause mixes numeric and alphanumeric characters; cannot map "
            f"deterministically without guessing: {raw_pic!r} in {declaration_text!r}"
        )
    if not has_numeric and not has_alnum:
        raise ValueError(
            f"PIC clause has no recognized numeric or alphanumeric tokens; cannot map "
            f"deterministically: {raw_pic!r} in {declaration_text!r}"
        )

    usage = _detect_usage(declaration_text)
    field_name = _extract_field_name(declaration_text)

    if has_numeric:
        return PicMapping(
            field_name=field_name,
            raw_pic=raw_pic,
            field_type=PicFieldType.NUMERIC,
            usage=usage,
            signed=signed,
            precision=integer_digits + decimal_digits,
            scale=decimal_digits,
            string_length=None,
            java_type="BigDecimal",
            source_text=declaration_text,
        )

    return PicMapping(
        field_name=field_name,
        raw_pic=raw_pic,
        field_type=PicFieldType.ALPHANUMERIC,
        usage=usage,
        signed=False,
        precision=None,
        scale=None,
        string_length=alnum_length,
        java_type="String",
        source_text=declaration_text,
    )
