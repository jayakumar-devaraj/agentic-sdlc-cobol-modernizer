"""Which COBOL record fields a step's paragraphs actually touch.

**Why this exists** (gap G26's systemic half). ADR-0020 made a step declare its `input_type` and
`output_type`, and `solution_architect` checks those names *resolve* — that each is a declared
entity or composite. Nothing checked they were **populatable**: whether the data the step's COBOL
actually reads is reachable from the type it is handed.

It was not, and a model found it rather than a test. Asked to build a `Tran` from a balance and a
rate, it could not reach `ACCT-ID` or `XREF-CARD-NUM`, left both `null`, and named the paragraph
that produces them. Resolution had passed: every type name was real. The design was still
ungeneratable, and the only signal was a model refusing to invent values.

**The analysis is deliberately narrow.** It answers one question — *which declared record fields
does this step's COBOL mention* — by matching against a known vocabulary of field names taken from
the entities themselves. It does not parse expressions, infer dataflow, or decide what a field is
used for. A name that appears is reported; anything cleverer would be inference, and inference is
what this repo removes.

**It follows `PERFORM`, and that is the load-bearing part.** G26's fields are not referenced by the
paragraph the step names: `1300-COMPUTE-INTEREST` performs `1300-B-WRITE-TX`, and the moves live
there. A check that read only the named paragraphs would have found nothing wrong with the design
that produced the defect.
"""

from __future__ import annotations

import re

from cobol_modernizer.parsing.cobol_parser import extract_paragraphs

#: `PERFORM 1300-B-WRITE-TX`, `PERFORM 1050-UPDATE-ACCOUNT.` -- the plain form. `PERFORM UNTIL`,
#: `PERFORM VARYING` and inline `PERFORM ... END-PERFORM` are deliberately not matched: they name
#: no paragraph, so there is nothing to follow, and matching them would produce phantom names.
_PERFORM = re.compile(r"\bPERFORM\s+(?P<name>[A-Z0-9][A-Z0-9-]*)\b(?!\s+(?:UNTIL|VARYING|TIMES))")

#: A COBOL word as it appears in a statement. Matching whole tokens matters: `TRAN-CAT-BAL` is a
#: substring of nothing here, but `ACCT-ID` is a substring of `FD-ACCT-ID`, and a naive `in` test
#: would count the file-section alias as a reference to the record field.
_WORD = re.compile(r"[A-Z0-9][A-Z0-9-]*")


def reachable_paragraphs(source_text: str, names: list[str]) -> dict[str, str]:
    """The named paragraphs plus every paragraph they reach through `PERFORM`, transitively.

    Returns name -> body. Unknown names are skipped rather than raised on: a design may name a
    paragraph this parser did not recognise, and that is a separate problem from this one.
    """
    bodies = {paragraph.name: paragraph.body for paragraph in extract_paragraphs(source_text)}

    collected: dict[str, str] = {}
    pending = list(names)
    while pending:
        name = pending.pop()
        if name in collected or name not in bodies:
            continue
        body = bodies[name]
        collected[name] = body
        pending.extend(match.group("name") for match in _PERFORM.finditer(body))
    return collected


def referenced_fields(source_text: str, names: list[str], vocabulary: set[str]) -> set[str]:
    """Every name in `vocabulary` mentioned by the step's paragraphs or anything they `PERFORM`.

    `vocabulary` is the set of COBOL field names the design knows about, so this reports references
    to *declared record data* and ignores working-storage locals, verbs and literals without having
    to understand any of them.
    """
    seen: set[str] = set()
    for body in reachable_paragraphs(source_text, names).values():
        for word in _WORD.findall(body.upper()):
            if word in vocabulary:
                seen.add(word)
    return seen
