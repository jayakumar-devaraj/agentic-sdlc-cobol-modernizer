"""What a keyed read is looked up *by* -- the join predicate, read from the COBOL that states it.

**The last fact a rendered reader is missing.** `FILE-CONTROL` says `ACCOUNT-FILE` is read by
`FD-ACCT-ID`; it does not say what value goes in there. The COBOL does, immediately before the read:

    MOVE TRANCAT-ACCT-ID TO FD-ACCT-ID
    PERFORM 1100-GET-ACCT-DATA

So the join is `account.acctId == tranCatBal.trancatAcctId`, stated outright. ADR-0030 refused an
LLM-declared join on the grounds that a wrong one produces plausible rows and a silently wrong
comparison; this is the same reasoning one step further -- the join does not have to be declared by
anyone, because it is already written down.

**Composite keys work the same way.** `DISCGRP-FILE`'s `RECORD KEY` is the group `FD-DISCGRP-KEY`,
and the program fills its three components separately. Resolving a group key into its components is
therefore part of the job, and is done from the `FD` record's own level numbers rather than by
guessing from names.

**The `'DEFAULT'` fallback falls out of this rather than needing special handling.**
`1200-GET-INTEREST-RATE` re-reads under `MOVE 'DEFAULT' TO FD-DIS-ACCT-GROUP-ID` on file status 23,
and that assignment is a literal rather than a field -- so it appears here as one, and finding F4's
"business logic living in wiring" becomes a fact a renderer can carry instead of something a human
has to remember.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from cobol_modernizer.parsing.cobol_parser import _iter_code_lines, extract_record_fields

#: `MOVE <source> TO <target>`. The source is either a field name or a quoted literal; both matter,
#: because a literal is how the fallback key is set.
_MOVE_RE = re.compile(
    r"\bMOVE\s+(?:'([^']*)'|\"([^\"]*)\"|([A-Z0-9-]+))\s+TO\s+([A-Z0-9-]+)",
    re.IGNORECASE,
)


class KeyAssignment(BaseModel):
    """One `MOVE ... TO <key field>`: what a lookup key is filled from.

    `source_field` is the COBOL name the value comes from -- normally a field of the driving record
    or of an earlier lookup's record. `literal` is set instead when the program moves a constant,
    which is how `CBACT04C` retries the disclosure-group read under `'DEFAULT'`.
    """

    key_field: str
    source_field: str | None = None
    literal: str | None = None
    source_line: int

    @property
    def is_literal(self) -> bool:
        return self.literal is not None


def key_components(source_text: str, key_field: str) -> list[str]:
    """The elementary fields a `RECORD KEY` is made of, in record order.

    An elementary key is itself; a group key is its children. Resolved from the `FD` record's level
    numbers rather than from name prefixes, because a name that merely looks like a member of a
    group is not evidence that it is one -- and a key assembled out of the wrong fields would look
    entirely plausible while finding nothing.
    """
    declarations = extract_record_fields(source_text)
    for index, declaration in enumerate(declarations):
        if declaration.name != key_field:
            continue
        if "PIC" in declaration.raw_text.upper():
            return [key_field]

        level = int(declaration.level)
        components: list[str] = []
        for child in declarations[index + 1 :]:
            if int(child.level) <= level:
                break
            if "PIC" in child.raw_text.upper() and child.name:
                components.append(child.name)
        return components or [key_field]
    return [key_field]


def extract_key_assignments(source_text: str, key_fields: set[str]) -> list[KeyAssignment]:
    """Every `MOVE` into one of `key_fields`, in source order.

    Restricted to the key fields a caller already knows about rather than collecting every `MOVE` in
    the program: the point is what fills a lookup key, and a general move-graph would be a much
    larger claim than this needs to make.

    Repeats are kept. `FD-DIS-ACCT-GROUP-ID` is assigned twice -- from the account's group, then
    from the `'DEFAULT'` literal on the retry -- and collapsing them would delete the fallback.
    """
    wanted = {field.upper() for field in key_fields}
    assignments: list[KeyAssignment] = []
    for line_no, text in _iter_code_lines(source_text):
        for match in _MOVE_RE.finditer(text):
            single, double, field, target = match.groups()
            if target.upper() not in wanted:
                continue
            literal = single if single is not None else double
            assignments.append(
                KeyAssignment(
                    key_field=target.upper(),
                    source_field=field.upper() if literal is None and field else None,
                    literal=literal,
                    source_line=line_no,
                )
            )
    return assignments
