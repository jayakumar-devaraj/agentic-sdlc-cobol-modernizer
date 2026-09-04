"""Which working-storage fields a program's COBOL actually computes, and where.

**Why this exists** (the sixth sighting of the class `CLAUDE.md` names). `pic_mapper` computes
`WS-MONTHLY-INT`'s precision and scale, `group_field_mappings_by_source` groups it under the
program, and `build_domain_entities` drops the whole group one line later because it keeps
copybook-sourced fields only. So a value the program computes has no representation in the design
at all -- and a step that computes one has no declared place to put it.

That is not hypothetical. `CBACT04C`'s `computeMonthlyInterest` was designed `in=out=
RatedCategoryBalance`, and the generated processor computed the interest and discarded it, because
the type it returns has no field to carry it. Every component behaved correctly; the design
language could not express the value.

**The analysis is deliberately narrow**, in the same spirit as `field_references`. It answers one
syntactic question -- *which known field names appear in an arithmetic receiving position* -- and
nothing else. It does not evaluate expressions, decide whether a value matters, or infer where it
ought to go. A field that is computed is reported; weighing that is a gate's job.

**Stated limits**, rather than approximated:

- Only `COMPUTE`, `ADD`, `SUBTRACT` and the `GIVING` form are recognised. `MULTIPLY` and `DIVIDE`
  appear nowhere in this corpus; they are absent because they were not needed, not because they
  are hard, and the pattern table is where they go.
- A `MOVE` is not arithmetic and is not matched. Worth stating precisely, because probing showed
  the exclusion carries less weight than it looks: `CBACT04C`'s `MOVE 0 TO WS-TOTAL-INT` is
  already invisible for the reason below, so adding a `MOVE` pattern changes nothing in this
  corpus. The rule stands on its meaning -- a reset is not a computation -- rather than on a
  defect it is currently preventing.
- **Only arithmetic inside a paragraph is seen.** `extract_paragraphs` parses paragraph bodies, so
  statements in the `PROCEDURE DIVISION`'s main loop -- before the first paragraph header -- are
  attributed to nothing. `CBACT04C`'s `ADD 1 TO WS-RECORD-COUNT` is genuinely computed and is
  genuinely not reported. This is the right boundary for the question a caller asks, since a step
  declares `source_paragraphs` and a value computed outside every paragraph belongs to no step, but
  it is a limit rather than a judgment and callers should not read the absence as "not computed".
- Only the first receiving field of a multi-target statement is found (`ADD A TO B C`). No such
  statement exists in this corpus; the day one does, this under-reports rather than misreports,
  which is the direction a gate can survive.
"""

from __future__ import annotations

import re

from cobol_modernizer.parsing.cobol_parser import extract_paragraphs

#: A COBOL word as it appears in a statement -- the same shape `field_references` matches, and for
#: the same reason: whole tokens only, so `ACCT-ID` never matches inside `FD-ACCT-ID`.
_WORD = r"[A-Z0-9][A-Z0-9-]*"

#: `GIVING` names the receiving field of whatever precedes it, so the `TO`/`FROM` operand in that
#: form is an operand rather than a target: `ADD 8 TO ZERO GIVING APPL-RESULT` computes
#: `APPL-RESULT`, not `ZERO`. The `TO`/`FROM` patterns therefore refuse a statement containing
#: `GIVING`, and the `GIVING` pattern claims it instead -- otherwise a real program in this corpus
#: would report two targets for one statement, one of them wrong.
_NO_GIVING = r"(?![^.]*?\bGIVING\b)"

_RECEIVING = (
    # `COMPUTE WS-MONTHLY-INT` -- and the CBACT04C case where the `=` is on the next line.
    re.compile(rf"\bCOMPUTE\s+(?P<field>{_WORD})", re.IGNORECASE),
    re.compile(rf"\bADD\s+{_NO_GIVING}.+?\bTO\s+(?P<field>{_WORD})", re.IGNORECASE),
    re.compile(rf"\bSUBTRACT\s+{_NO_GIVING}.+?\bFROM\s+(?P<field>{_WORD})", re.IGNORECASE),
    re.compile(rf"\bGIVING\s+(?P<field>{_WORD})", re.IGNORECASE),
)


def computed_fields(source_text: str, vocabulary: set[str]) -> dict[str, set[str]]:
    """Every name in `vocabulary` this program computes, mapped to the paragraphs that compute it.

    `vocabulary` is the set of COBOL field names the caller knows about -- in practice the
    program's own working-storage fields, as `pic_mapper` named them. Passing it keeps this
    function from having to understand literals, verbs, figurative constants or record fields: a
    name it was not given is a name it does not report.

    Returns `{COBOL field name: {paragraph name, ...}}`, upper-cased on both sides so a caller can
    match it against `BatchStepDesign.source_paragraphs` without re-normalising. A field computed
    in two paragraphs carries both, because which step owns it is exactly the question a caller is
    asking.
    """
    known = {name.upper() for name in vocabulary}
    found: dict[str, set[str]] = {}

    for paragraph in extract_paragraphs(source_text):
        for pattern in _RECEIVING:
            for match in pattern.finditer(paragraph.body):
                field = match.group("field").upper()
                if field in known:
                    found.setdefault(field, set()).add(paragraph.name.upper())

    return found
