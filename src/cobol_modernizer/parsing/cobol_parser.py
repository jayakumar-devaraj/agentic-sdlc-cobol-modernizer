"""Hand-rolled structural parser for Track C's deliberately bounded COBOL grammar.

Per ADR-0002 (docs/adr/0002-a-hand-rolled-parser-for-a-deliberately-bounded-grammar.md), this
module walks `WORKING-STORAGE` for field declarations, `PROCEDURE DIVISION` for paragraph
boundaries, and resolves straight `COPY` by name -- it does not attempt `REDEFINES`,
`OCCURS ... DEPENDING ON`, or `COPY ... REPLACING`. Those constructs, when found, are reported as
unsupported so a caller can route them to a human-in-the-loop gate, never guessed at.

Everything this module returns is *structure*, not interpretation: it hands `tools/pic_mapper.py`
enough text (a field's own declaration plus its 01-level group siblings) to make its own
deterministic `REDEFINES`/`OCCURS ... DEPENDING ON` determination, and it hands the future
`nodes/spec_extractor.py` paragraph boundaries so that module narrates COBOL structure an LLM
already has grounded, rather than reading raw unparsed text (and, per this repo's guardrail
posture, never treating any of that text -- including comments -- as instructions to that LLM).
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# --- Fixed-format COBOL column conventions -------------------------------------------------
#
# Comment lines mark column 7 (0-indexed 6) with `*`. Paragraph names and 01/level-1 group
# headers live in Area A (columns 8-11, 0-indexed leading whitespace of 7-10); ordinary
# statements are indented into Area B (column 12+, 0-indexed leading whitespace of 11+). This is
# the real distinguishing signal fixed-format COBOL itself uses between a paragraph header and a
# statement inside one -- not a heuristic word list.

_COMMENT_INDICATOR_COLUMN = 6  # 0-indexed column 7
_AREA_A_MAX_INDENT = 10  # 0-indexed leading whitespace; Area A ends at column 11

_LEVEL_LINE_RE = re.compile(r"^\s*(\d{1,2})\s+(\S+)")
_COPY_RE = re.compile(r"\bCOPY\s+([A-Za-z0-9\-]+)\b(.*?)\.", re.IGNORECASE | re.DOTALL)
_REPLACING_RE = re.compile(r"\bREPLACING\b", re.IGNORECASE)
_WORKING_STORAGE_RE = re.compile(r"^\s*WORKING-STORAGE\s+SECTION\b", re.IGNORECASE | re.MULTILINE)
_SECTION_OR_DIVISION_RE = re.compile(
    r"^\s*[A-Z0-9\-]+\s+(?:SECTION|DIVISION)\b", re.IGNORECASE | re.MULTILINE
)
_PROCEDURE_DIVISION_RE = re.compile(r"^\s*PROCEDURE\s+DIVISION\b.*?\.", re.IGNORECASE | re.MULTILINE)
_PARAGRAPH_HEADER_RE = re.compile(
    rf"^([ ]{{0,{_AREA_A_MAX_INDENT}}})([A-Za-z0-9][A-Za-z0-9\-]*)\.[ \t]*$"
)

# Reserved single-word COBOL statements that could otherwise be mistaken for a paragraph name if
# a source file's indentation doesn't cleanly separate Area A from Area B. Indentation is the
# primary signal; this is a defensive backstop, not the primary mechanism.
_RESERVED_PARAGRAPH_LOOKALIKES = {"EXIT", "GOBACK", "CONTINUE", "STOP"}


class UnsupportedCopyConstructError(Exception):
    """A `COPY` statement uses `REPLACING`.

    Per ADR-0002 and the COBOL construct support matrix, `COPY ... REPLACING` is out of scope
    for Track C -- it requires resolving pseudo-text substitution, which this parser does not
    attempt. Catch this at the call site and route it to a human-in-the-loop gate, the same as
    `pic_mapper.UnsupportedPicConstructError` -- do not catch-and-continue by treating the
    statement as a straight `COPY` and ignoring the `REPLACING` clause.
    """

    def __init__(self, copybook_name: str, statement_text: str) -> None:
        self.copybook_name = copybook_name
        self.statement_text = statement_text
        super().__init__(
            f"Unsupported construct 'COPY ... REPLACING' detected for copybook "
            f"{copybook_name!r}; per ADR-0002 this must route to a human gate, not be parsed: "
            f"{statement_text!r}"
        )


class CopyStatement(BaseModel):
    """One straight `COPY <name>.` statement found in the source."""

    copybook_name: str
    raw_text: str


class FieldDeclaration(BaseModel):
    """One `WORKING-STORAGE` (or copybook top-level record) field declaration.

    `sibling_text` is every other field's `raw_text` within the same 01-level group -- including
    the group's own 01-level header line -- joined by newlines. It exists so a caller can do:

        pic_mapper.map_pic_clause(declaration.raw_text, adjacent_text=declaration.sibling_text)

    and get correct `REDEFINES`-on-a-sibling detection, per the ADR-0002 boundary that these
    constructs are rejected even when found on an adjacent line in the same record structure,
    not only on the field's own declaration line.
    """

    level: str
    name: str | None
    raw_text: str
    sibling_text: str
    is_filler: bool = False


class Paragraph(BaseModel):
    """One `PROCEDURE DIVISION` paragraph, in source order."""

    name: str
    body: str


def _is_comment_line(line: str) -> bool:
    return len(line) > _COMMENT_INDICATOR_COLUMN and line[_COMMENT_INDICATOR_COLUMN] == "*"


def _iter_code_lines(source_text: str) -> list[tuple[int, str]]:
    """Return `(1-indexed line number, line text)` for every non-comment, non-blank line."""
    result = []
    for line_no, line in enumerate(source_text.splitlines(), start=1):
        if _is_comment_line(line):
            continue
        if not line.strip():
            continue
        result.append((line_no, line))
    return result


def extract_copy_statements(source_text: str) -> list[CopyStatement]:
    """Find every straight `COPY <name>.` statement in `source_text`.

    Raises:
        UnsupportedCopyConstructError: a `COPY ... REPLACING` statement was found. Per ADR-0002
            this is a hard boundary -- detected and rejected, never parsed with the `REPLACING`
            clause silently dropped and treated as a straight copy.
    """
    # Work over comment-stripped text so a COPY statement mentioned only inside a comment (e.g.
    # a commented-out example) is never mistaken for a live statement -- and, per this repo's
    # guardrail posture, COBOL comment text is never treated as anything but inert data.
    code_only = "\n".join(line for _, line in _iter_code_lines(source_text))

    statements: list[CopyStatement] = []
    for match in _COPY_RE.finditer(code_only):
        copybook_name = match.group(1).upper()
        statement_text = match.group(0).strip()
        if _REPLACING_RE.search(match.group(2)):
            raise UnsupportedCopyConstructError(copybook_name, statement_text)
        statements.append(CopyStatement(copybook_name=copybook_name, raw_text=statement_text))
    return statements


def _working_storage_region(source_text: str) -> str:
    """Slice out the `WORKING-STORAGE SECTION` body, or the whole text if there's no header.

    Copybooks like `CVACT01Y.cpy` have no `WORKING-STORAGE SECTION` header of their own -- they
    are `COPY`-included directly into a program's own `WORKING-STORAGE SECTION` -- so their own
    top-level `01`-level record structure is used as-is.
    """
    header_match = _WORKING_STORAGE_RE.search(source_text)
    if header_match is None:
        return source_text

    region_start = header_match.end()
    next_header_match = _SECTION_OR_DIVISION_RE.search(source_text, pos=region_start)
    region_end = next_header_match.start() if next_header_match else len(source_text)
    return source_text[region_start:region_end]


def _iter_field_sentences(region_text: str) -> list[tuple[str, str]]:
    """Group the region's code lines into logical field-declaration sentences.

    A sentence is one or more consecutive physical lines up to (and including) the line whose
    stripped text ends with `.` -- this lets a wrapped declaration span multiple physical lines
    while the common case (one field per line, as in every Track C fixture) still works.
    """
    sentences: list[tuple[str, str]] = []
    buffer: list[str] = []
    for _, line in _iter_code_lines(region_text):
        buffer.append(line)
        if line.rstrip().endswith("."):
            sentences.append((line, "\n".join(buffer)))
            buffer = []
    if buffer:
        sentences.append((buffer[-1], "\n".join(buffer)))
    return sentences


def extract_working_storage_fields(source_text: str) -> list[FieldDeclaration]:
    """Parse a `WORKING-STORAGE SECTION` (or a bare copybook record) into field declarations.

    Fields are grouped by their enclosing top-level (`01`-level) record; every field's
    `sibling_text` is the raw text of every other field in the same group, so a caller can feed
    it straight into `pic_mapper.map_pic_clause(..., adjacent_text=...)`.

    `FILLER` fields are still returned (structurally -- a real copybook like `CVACT01Y.cpy` ends
    with one), but with `name=None` and `is_filler=True` since a `FILLER` has no meaningful name
    to extract.
    """
    region_text = _working_storage_region(source_text)

    groups: list[list[tuple[str, str, str]]] = []  # each: list of (level, name_token, raw_text)
    current_group: list[tuple[str, str, str]] | None = None

    for _, raw_text in _iter_field_sentences(region_text):
        level_match = _LEVEL_LINE_RE.match(raw_text)
        if level_match is None:
            # Not a level-numbered field line (e.g. leftover COPY statement text) -- skip.
            continue
        level, name_token = level_match.group(1), level_match.group(2)

        if level in ("01", "1"):
            current_group = []
            groups.append(current_group)
        elif current_group is None:
            # A field appeared before any 01-level header (shouldn't happen in well-formed
            # source); start an implicit group rather than dropping the field.
            current_group = []
            groups.append(current_group)

        current_group.append((level, name_token, raw_text))

    fields: list[FieldDeclaration] = []
    for group in groups:
        group_raw_texts = [raw for _, _, raw in group]
        for index, (level, name_token, raw_text) in enumerate(group):
            sibling_text = "\n".join(
                raw for i, raw in enumerate(group_raw_texts) if i != index
            )
            # A bare group header (e.g. "01  ACCOUNT-RECORD.") has no clause after the name, so
            # the name token is immediately followed by the statement-terminating period -- strip
            # it so the name doesn't come out as "ACCOUNT-RECORD.".
            clean_name = name_token.removesuffix(".")
            is_filler = clean_name.upper() == "FILLER"
            fields.append(
                FieldDeclaration(
                    level=level,
                    name=None if is_filler else clean_name.upper(),
                    raw_text=raw_text,
                    sibling_text=sibling_text,
                    is_filler=is_filler,
                )
            )
    return fields


def _procedure_division_body(source_text: str) -> str:
    header_match = _PROCEDURE_DIVISION_RE.search(source_text)
    if header_match is None:
        return ""
    return source_text[header_match.end() :]


def extract_paragraphs(source_text: str) -> list[Paragraph]:
    """Parse a `PROCEDURE DIVISION` into ordered `(name, body)` paragraph entries.

    A paragraph header is a line consisting of a single COBOL name token followed by a period,
    indented into Area A (columns 8-11) -- the fixed-format convention that distinguishes a
    paragraph name from a statement (which is indented into Area B, column 12+). Statements
    between one header and the next (or end of text) become that paragraph's body, in source
    order, which is what lets a caller narrate paragraph flow instead of guessing it from
    unstructured text.
    """
    body_text = _procedure_division_body(source_text)
    lines = body_text.splitlines()

    headers: list[tuple[int, str]] = []  # (line index into `lines`, paragraph name)
    for index, line in enumerate(lines):
        if _is_comment_line(line):
            continue
        match = _PARAGRAPH_HEADER_RE.match(line)
        if match is None:
            continue
        name = match.group(2)
        if name.upper() in _RESERVED_PARAGRAPH_LOOKALIKES:
            continue
        headers.append((index, name))

    paragraphs: list[Paragraph] = []
    for position, (line_index, name) in enumerate(headers):
        body_start = line_index + 1
        body_end = headers[position + 1][0] if position + 1 < len(headers) else len(lines)
        body = "\n".join(lines[body_start:body_end]).strip("\n")
        paragraphs.append(Paragraph(name=name, body=body))
    return paragraphs
