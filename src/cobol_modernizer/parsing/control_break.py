"""The control break: what a batch program groups by, and what it accumulates across a group.

**Why this is the last blocker on G31.** Everything else a rendered job needs is a declaration --
a `PIC` clause, a `SELECT`, a `READ ... INTO`. A control break is not declared anywhere: it is an
*idiom*, four statements spread across a loop, and it is the reason `postAccountInterest` cannot be
rendered. ADR-0027 moved the accumulation into the reader; nothing said what to group by or what to
sum.

**The idiom, in `CBACT04C`:**

    IF TRANCAT-ACCT-ID NOT= WS-LAST-ACCT-NUM        <- the break test
        ...
        PERFORM 1050-UPDATE-ACCOUNT                 <- what runs at the break
        MOVE 0 TO WS-TOTAL-INT                      <- the accumulator resets
        MOVE TRANCAT-ACCT-ID TO WS-LAST-ACCT-NUM    <- the saved key advances
    END-IF
    ...
    ADD WS-MONTHLY-INT TO WS-TOTAL-INT              <- the accumulation

**Recognition requires all five, and that conjunction is the safety.** An inequality test alone is
not a control break -- `CBACT04C` has `IF DIS-INT-RATE NOT = 0` three lines further on. What
distinguishes one is that the *same field* being tested is then moved into the *same field* it was
tested against, while an accumulator is zeroed beside it. A program that does all of that is
grouping; anything less is refused by returning nothing, because a wrong grouping key produces
plausible totals against the wrong accounts, which is `pic_mapper`'s objection in a new place.

**This module reports; it does not decide.** Whether a recognised break can be *rendered* depends on
whether its key and its accumulated value are reachable from the step's declared types, which is a
question about the design rather than about the source.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from cobol_modernizer.parsing.cobol_parser import _iter_code_lines

_NAME = r"[A-Z0-9-]+"
_IF_NOT_EQUAL_RE = re.compile(rf"^IF\s+({_NAME})\s+NOT\s*=\s*({_NAME})\s*$", re.IGNORECASE)
_PERFORM_RE = re.compile(rf"^PERFORM\s+({_NAME})\s*\.?$", re.IGNORECASE)
_MOVE_ZERO_RE = re.compile(rf"^MOVE\s+(?:0|ZERO|ZEROS|ZEROES)\s+TO\s+({_NAME})", re.IGNORECASE)
_MOVE_RE = re.compile(rf"^MOVE\s+({_NAME})\s+TO\s+({_NAME})", re.IGNORECASE)
_ADD_RE = re.compile(rf"^ADD\s+({_NAME})\s+TO\s+({_NAME})", re.IGNORECASE)
_IF_RE = re.compile(r"^IF\b", re.IGNORECASE)
_END_IF_RE = re.compile(r"^END-IF\b", re.IGNORECASE)


class ControlBreak(BaseModel):
    """One control break: the group boundary, what accumulates across it, and what runs at it.

    Every field is read from the source. `accumulated_from_field` is what is *added* to the
    accumulator, not the accumulator itself -- for a rendered aggregation the first is the value to
    sum per group and the second is a variable that will not exist.
    """

    #: The field whose change ends a group -- `TRANCAT-ACCT-ID`.
    break_key_field: str
    #: Where the previous record's key is held so the next one can be compared to it.
    saved_key_field: str
    #: The running total, reset at each break -- `WS-TOTAL-INT`.
    accumulator_field: str
    #: What is added to it -- `WS-MONTHLY-INT`. This is what a rendered aggregation sums.
    accumulated_from_field: str
    #: The paragraph performed at the break, which is how this attaches to a declared step.
    performed_paragraph: str
    test_line: int
    reset_line: int
    add_line: int


def _block(lines: list[tuple[int, str]], start: int) -> list[tuple[int, str]]:
    """The statements between an `IF` and its matching `END-IF`, nesting counted.

    Counted rather than taken to the first `END-IF`: the break block in `CBACT04C` contains a whole
    `IF/ELSE/END-IF` of its own, and stopping at the first terminator would cut the block before the
    accumulator reset -- leaving a real control break unrecognised.
    """
    depth = 0
    collected: list[tuple[int, str]] = []
    for line_no, text in lines[start:]:
        stripped = text.strip()
        if _IF_RE.match(stripped):
            depth += 1
            if depth == 1:
                continue
        if _END_IF_RE.match(stripped):
            depth -= 1
            if depth == 0:
                return collected
        if depth >= 1:
            collected.append((line_no, stripped))
    return collected


def extract_control_breaks(source_text: str) -> list[ControlBreak]:
    """Every recognised control break in `source_text`, in source order.

    Returns an empty list when the program has none -- which is a fact about the program, not a
    failure. A partially-matching idiom is likewise not reported: see the module docstring on why
    the conjunction is the safety.
    """
    lines = _iter_code_lines(source_text)
    adds = {}
    for line_no, text in lines:
        match = _ADD_RE.match(text.strip())
        if match:
            adds.setdefault(match.group(2).upper(), (match.group(1).upper(), line_no))

    breaks: list[ControlBreak] = []
    for index, (line_no, text) in enumerate(lines):
        test = _IF_NOT_EQUAL_RE.match(text.strip())
        if test is None:
            continue
        key, saved = test.group(1).upper(), test.group(2).upper()

        block = _block(lines, index)
        advances = any(
            (moved := _MOVE_RE.match(statement))
            and moved.group(1).upper() == key
            and moved.group(2).upper() == saved
            for _, statement in block
        )
        if not advances:
            # The test compares two fields and never advances the saved one, so it is an ordinary
            # comparison rather than a group boundary.
            continue

        reset = next(
            (
                (match.group(1).upper(), statement_line)
                for statement_line, statement in block
                if (match := _MOVE_ZERO_RE.match(statement))
            ),
            None,
        )
        performed = next(
            (
                match.group(1).upper()
                for _, statement in block
                if (match := _PERFORM_RE.match(statement))
            ),
            None,
        )
        if reset is None or performed is None:
            continue

        accumulator, reset_line = reset
        if accumulator not in adds:
            # Something is zeroed at the break and never added to: a flag or a counter this module
            # has no reason to call an accumulator.
            continue

        accumulated_from, add_line = adds[accumulator]
        breaks.append(
            ControlBreak(
                break_key_field=key,
                saved_key_field=saved,
                accumulator_field=accumulator,
                accumulated_from_field=accumulated_from,
                performed_paragraph=performed,
                test_line=line_no,
                reset_line=reset_line,
                add_line=add_line,
            )
        )
    return breaks


def landing_field(source_text: str, accumulated_from: str) -> str | None:
    """Which record field the accumulated value is also moved into, if any.

    `WS-MONTHLY-INT` is added to `WS-TOTAL-INT` *and* moved to `TRAN-AMT`. That second statement is
    what lets an aggregation over already-written records sum the right column: the accumulator is a
    program variable that no generated type has, and `TRAN-AMT` is a field of a record that does.

    Returns `None` when the value is never moved anywhere, which makes the group total unreachable
    from the records a step actually sees.
    """
    for _line_no, text in _iter_code_lines(source_text):
        match = _MOVE_RE.match(text.strip())
        if match and match.group(1).upper() == accumulated_from.upper():
            return match.group(2).upper()
    return None
