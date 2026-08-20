"""`FILE-CONTROL`: which files a program reads, how, and by what key.

**Why this exists** (gap G31, ADR-0030). `generate` renders processors and no reader, so a
generated project compiles and cannot run. Rendering a reader needs one fact nothing in the design
carries: *where the data comes from*. `CompositeType` declares that `TranCatBalWithRate` is composed
of four entities; it does not say which of them is a stream and which are lookups, or what the keys
are.

**The COBOL does say.** `CBACT04C` reads `TCATBAL-FILE` with `ACCESS MODE IS SEQUENTIAL` -- one
driving stream -- and the other three with `ACCESS MODE IS RANDOM` and a declared `RECORD KEY`.
That is deterministic, parseable fact of exactly the same grade as a `PIC` clause, which is why
ADR-0030 chose it over asking a model for a join: a wrong join produces plausible rows and a
silently wrong comparison, and this repo has spent a lot of effort keeping models out of that class
of decision (`pic_mapper`'s rule).

**A separate module from `cobol_parser`, deliberately.** That one reads the `DATA` and `PROCEDURE`
divisions; this reads `ENVIRONMENT`. They share the fixed-format comment rule and nothing else, and
the shared part is imported rather than copied so column 7 cannot be right in one place and wrong in
the other.

**Unknown clauses raise.** `UnsupportedFileControlError` joins the `UnsupportedPicConstructError`
family for the reason ADR-0011 gives: a clause this parser skips is a fact that reaches nothing
downstream and is invisible in the output, which is the worst of the available failure modes. A
clause it does not understand is unambiguous evidence that it does not understand it.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# The same fixed-format comment rule `cobol_parser` applies. Imported rather than reimplemented:
# two copies of "column 7 means comment" is two places for it to drift.
from cobol_modernizer.parsing.cobol_parser import _iter_code_lines

_FILE_CONTROL_RE = re.compile(r"^\s*FILE-CONTROL\s*\.", re.IGNORECASE)
_DATA_DIVISION_RE = re.compile(r"^\s*DATA\s+DIVISION\s*\.", re.IGNORECASE)
_IO_SECTION_RE = re.compile(r"^\s*I-O-CONTROL\s*\.", re.IGNORECASE)

#: Words COBOL allows purely for readability. Dropped before clause matching so `ACCESS MODE IS
#: RANDOM` and `ACCESS RANDOM` -- both legal -- parse identically.
_NOISE_WORDS = frozenset({"IS", "ARE", "MODE", "TO"})

#: What this module understands. Anything else in a `SELECT` raises rather than being ignored.
_ORGANIZATIONS = frozenset({"SEQUENTIAL", "INDEXED", "RELATIVE", "LINE SEQUENTIAL"})
_ACCESS_MODES = frozenset({"SEQUENTIAL", "RANDOM", "DYNAMIC"})

#: COBOL's own defaults when a `SELECT` omits the clause, not this module's guesses.
DEFAULT_ORGANIZATION = "SEQUENTIAL"
DEFAULT_ACCESS_MODE = "SEQUENTIAL"


class UnsupportedFileControlError(Exception):
    """A `SELECT` uses a construct this parser does not model.

    Joins `UnsupportedPicConstructError` and `UnsupportedCopyConstructError`: fail loudly on an
    unambiguous case rather than guess. The cases below are refused on purpose rather than for lack
    of effort, because each changes what a rendered reader would have to do:

    - `SELECT OPTIONAL` -- a missing file is not an error, so the reader has an empty-stream case
      the COBOL handles and a naive translation would not.
    - `WITH DUPLICATES` on an alternate key -- the key selects *many* records, so a lookup becomes a
      join, and rendering it as a single-row fetch would silently drop rows.
    - `RELATIVE KEY`, `PASSWORD`, `LOCK MODE` and anything else unrecognised -- unmodelled, and a
      clause skipped in silence is a fact that reaches nothing downstream.
    """


class FileDeclaration(BaseModel):
    """One `SELECT`, as declared.

    Every field is read from the source; nothing here is inferred except the two documented COBOL
    defaults. `source_line` is the provenance `CLAUDE.md` requires of every derived artifact -- the
    exact line this came from, so a rendered reader can be traced back to the clause that produced
    it.
    """

    select_name: str
    assign_to: str
    organization: str
    access_mode: str
    record_key: str | None = None
    alternate_record_keys: list[str] = []
    file_status: str | None = None
    source_line: int

    @property
    def is_keyed_lookup(self) -> bool:
        """True when the program reads this file *by key* rather than as a stream.

        This is `ACCESS MODE`'s own meaning, not an inference about intent: `RANDOM` and `DYNAMIC`
        both position by key, `SEQUENTIAL` does not. It is the distinction a rendered reader turns
        into "one driving query plus N keyed lookups", and the reason ADR-0030 preferred parsing
        this over asking a model to describe the join.
        """
        return self.access_mode in {"RANDOM", "DYNAMIC"}


def _file_control_region(source_text: str) -> list[tuple[int, str]]:
    """The code lines of the `FILE-CONTROL` paragraph, with their 1-indexed source line numbers.

    Ends at `DATA DIVISION` or `I-O-CONTROL`, whichever comes first. A program with no
    `FILE-CONTROL` -- every copybook, and any program that opens no files -- yields nothing, which
    is a fact rather than an error.
    """
    lines = _iter_code_lines(source_text)
    start = None
    for index, (_line_no, text) in enumerate(lines):
        if _FILE_CONTROL_RE.match(text):
            start = index + 1
            break
    if start is None:
        return []

    region = []
    for line_no, text in lines[start:]:
        if _DATA_DIVISION_RE.match(text) or _IO_SECTION_RE.match(text):
            break
        region.append((line_no, text))
    return region


def _select_sentences(region: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Group the region into `(line of the SELECT, whole sentence)` pairs.

    A sentence runs to the terminating period, so a `SELECT` spanning six physical lines -- which
    every one in this corpus does -- is one unit. Text before the first `SELECT` is ignored rather
    than refused: it is the paragraph header's own remainder, never a clause.
    """
    sentences: list[tuple[int, str]] = []
    current: list[str] = []
    current_line = 0
    for line_no, text in region:
        stripped = text.strip()
        if not current and not stripped.upper().startswith("SELECT"):
            continue
        if not current:
            current_line = line_no
        current.append(stripped)
        if stripped.endswith("."):
            sentences.append((current_line, " ".join(current).rstrip(".")))
            current = []
    if current:
        # An unterminated SELECT is malformed COBOL, and guessing where it ended would invent a
        # declaration. Reported by the caller, which has the file name for the message.
        sentences.append((current_line, " ".join(current)))
    return sentences


def _tokens(sentence: str) -> list[str]:
    """Uppercased words with COBOL's noise words removed, so optional syntax parses uniformly.

    `LINE SEQUENTIAL` is joined first: it is one organization written as two words, and dropping it
    to two tokens would leave `LINE` looking like an unknown clause.
    """
    upper = re.sub(r"\s+", " ", sentence.strip()).upper()
    upper = upper.replace("LINE SEQUENTIAL", "LINE_SEQUENTIAL")
    return [token for token in upper.split(" ") if token and token not in _NOISE_WORDS]


def parse_select(sentence: str, source_line: int) -> FileDeclaration:
    """One `SELECT` sentence into a `FileDeclaration`, refusing anything unmodelled.

    Raises:
        UnsupportedFileControlError: an unknown clause, an unknown organization or access mode, a
            `SELECT OPTIONAL`, or an alternate key `WITH DUPLICATES`.
    """
    tokens = _tokens(sentence)
    if not tokens or tokens[0] != "SELECT":
        raise UnsupportedFileControlError(
            f"line {source_line}: not a SELECT statement: {sentence[:80]!r}"
        )
    if len(tokens) > 1 and tokens[1] == "OPTIONAL":
        raise UnsupportedFileControlError(
            f"line {source_line}: SELECT OPTIONAL is not modelled -- a missing file is not an "
            "error for this program, and a reader rendered without that case would fail where the "
            "COBOL continues"
        )
    if len(tokens) < 2:
        raise UnsupportedFileControlError(f"line {source_line}: SELECT names no file")

    select_name = tokens[1]
    assign_to: str | None = None
    organization = DEFAULT_ORGANIZATION
    access_mode = DEFAULT_ACCESS_MODE
    record_key: str | None = None
    alternate_keys: list[str] = []
    file_status: str | None = None

    index = 2
    while index < len(tokens):
        token = tokens[index]
        if token == "ASSIGN":
            assign_to, index = _value_after(tokens, index, source_line, "ASSIGN")
        elif token == "ORGANIZATION":
            value, index = _value_after(tokens, index, source_line, "ORGANIZATION")
            organization = value.replace("_", " ")
            if organization not in _ORGANIZATIONS:
                raise UnsupportedFileControlError(
                    f"line {source_line}: ORGANIZATION {organization!r} is not modelled; known: "
                    f"{sorted(_ORGANIZATIONS)}"
                )
        elif token == "ACCESS":
            access_mode, index = _value_after(tokens, index, source_line, "ACCESS")
            if access_mode not in _ACCESS_MODES:
                raise UnsupportedFileControlError(
                    f"line {source_line}: ACCESS MODE {access_mode!r} is not modelled; known: "
                    f"{sorted(_ACCESS_MODES)}"
                )
        elif token == "RECORD" and index + 1 < len(tokens) and tokens[index + 1] == "KEY":
            record_key, index = _value_after(tokens, index + 1, source_line, "RECORD KEY")
        elif token == "ALTERNATE":
            if tokens[index : index + 3] != ["ALTERNATE", "RECORD", "KEY"]:
                raise UnsupportedFileControlError(
                    f"line {source_line}: expected ALTERNATE RECORD KEY, got "
                    f"{' '.join(tokens[index : index + 3])!r}"
                )
            key, index = _value_after(tokens, index + 2, source_line, "ALTERNATE RECORD KEY")
            alternate_keys.append(key)
            if index < len(tokens) and tokens[index] == "WITH":
                raise UnsupportedFileControlError(
                    f"line {source_line}: alternate key {key!r} declares WITH DUPLICATES, which "
                    "makes the key select many records -- a rendered lookup would silently return "
                    "one of them"
                )
        elif token == "FILE" and index + 1 < len(tokens) and tokens[index + 1] == "STATUS":
            file_status, index = _value_after(tokens, index + 1, source_line, "FILE STATUS")
        else:
            raise UnsupportedFileControlError(
                f"line {source_line}: unrecognised clause {token!r} in SELECT {select_name}; the "
                "remaining text was "
                f"{' '.join(tokens[index:])[:80]!r}. Refused rather than skipped: a clause dropped "
                "here is a fact that reaches nothing downstream"
            )

    if assign_to is None:
        raise UnsupportedFileControlError(
            f"line {source_line}: SELECT {select_name} has no ASSIGN clause, so nothing says which "
            "external file it is"
        )
    if organization == "INDEXED" and record_key is None:
        raise UnsupportedFileControlError(
            f"line {source_line}: SELECT {select_name} is INDEXED with no RECORD KEY, which cannot "
            "be read by key or rendered as a lookup"
        )
    return FileDeclaration(
        select_name=select_name,
        assign_to=assign_to,
        organization=organization,
        access_mode=access_mode,
        record_key=record_key,
        alternate_record_keys=alternate_keys,
        file_status=file_status,
        source_line=source_line,
    )


def _value_after(
    tokens: list[str], index: int, source_line: int, clause: str
) -> tuple[str, int]:
    """The single word following a clause keyword, and the index past it."""
    if index + 1 >= len(tokens):
        raise UnsupportedFileControlError(
            f"line {source_line}: {clause} names no value"
        )
    return tokens[index + 1], index + 2


def extract_file_declarations(source_text: str) -> list[FileDeclaration]:
    """Every `SELECT` in a program's `FILE-CONTROL`, in source order.

    Returns an empty list for a program or copybook with no `FILE-CONTROL` -- which is a fact about
    the source, not a failure.

    Raises:
        UnsupportedFileControlError: any `SELECT` this parser does not fully model.
    """
    return [
        parse_select(sentence, line_no)
        for line_no, sentence in _select_sentences(_file_control_region(source_text))
    ]

#: `READ <file> INTO <record>`. The `INTO` phrase is what links a file declaration to a copybook
#: record -- and therefore to a domain entity -- and nothing else in the source states that link:
#: `FILE-CONTROL` names the file, the `FD` names a record area, and only `READ ... INTO` says which
#: `01`-level record the program actually works with.
_READ_INTO_RE = re.compile(r"\bREAD\s+([A-Z0-9-]+)\s+INTO\s+([A-Z0-9-]+)", re.IGNORECASE)

#: The key a *read* positions on, which is not always the key the file declares.
_READ_KEY_RE = re.compile(r"\bKEY\s+(?:IS\s+)?([A-Z0-9-]+)", re.IGNORECASE)

#: Everything after one of these is exception handling, not the read's own phrases. Cut there
#: before looking for `KEY`, because `INVALID KEY DISPLAY '...'` contains the word KEY and would
#: otherwise yield a "key" of `DISPLAY` -- a plausible-looking wrong answer, which is the failure
#: mode this repo refuses everywhere else.
_EXCEPTION_PHRASES = ("INVALID KEY", "NOT INVALID KEY", "AT END", "NOT AT END")


class RecordBinding(BaseModel):
    """One `READ ... INTO ...`: which file was read, into which record, positioned by which key.

    **`read_key` is the finding that makes this worth parsing rather than assuming.** `CBACT04C`
    declares `XREF-FILE` with `RECORD KEY IS FD-XREF-CARD-NUM` and then reads it
    `KEY IS FD-XREF-ACCT-ID` -- the *alternate*. A reader rendered against the declared record key
    would compile and find nothing, because the account id is what the program has in hand. The
    declaration says which keys the file supports; this says which one the program uses.

    `read_key` is `None` for a sequential read, where position comes from file order.
    """

    file_name: str
    record_name: str
    read_key: str | None = None
    source_line: int


def _read_statements(source_text: str) -> list[tuple[int, str]]:
    """`(line of the READ, statement text)` for each `READ`, joined across continuation lines.

    A `READ` runs to its terminating period or `END-READ`, and in this corpus the `KEY IS` phrase is
    always on the line *after* the `READ` -- so a line-scoped match finds the file and the record and
    silently misses the key, which is the one fact this parse exists for.
    """
    statements: list[tuple[int, str]] = []
    current: list[str] = []
    current_line = 0
    for line_no, text in _iter_code_lines(source_text):
        stripped = text.strip()
        if not current:
            if not re.match(r"^READ\b", stripped, re.IGNORECASE):
                continue
            current_line = line_no
        current.append(stripped)
        joined = " ".join(current)
        if stripped.endswith(".") or "END-READ" in stripped.upper():
            statements.append((current_line, joined))
            current = []
    if current:
        statements.append((current_line, " ".join(current)))
    return statements


def extract_record_bindings(source_text: str) -> list[RecordBinding]:
    """Every `READ ... INTO ...` in the source, in source order.

    Duplicates are kept rather than collapsed: `CBACT04C` reads `DISCGRP-FILE` twice -- once on the
    account's own group and once on `'DEFAULT'` -- and the second read is the fallback a rendered
    reader has to reproduce. Collapsing them would hide a business rule (finding F4) at the exact
    layer that exists to recover it.
    """
    bindings: list[RecordBinding] = []
    for line_no, statement in _read_statements(source_text):
        match = _READ_INTO_RE.search(statement)
        if match is None:
            # `READ <file>` with no `INTO` reads into the FD's own record area. It binds no
            # copybook record, so there is nothing here to link -- and inventing one would be a
            # guess about which layout the program meant.
            continue
        head = statement.upper()
        for phrase in _EXCEPTION_PHRASES:
            index = head.find(phrase)
            if index != -1:
                head = head[:index]
        key_match = _READ_KEY_RE.search(head[match.end() :])
        bindings.append(
            RecordBinding(
                file_name=match.group(1).upper(),
                record_name=match.group(2).upper(),
                read_key=key_match.group(1).upper() if key_match else None,
                source_line=line_no,
            )
        )
    return bindings


#: `FD <file>.` and the `01` record area that follows it. A write names the *record area*, not the
#: file, so without this a `WRITE FD-TRANFILE-REC FROM TRAN-RECORD` cannot be attributed to
#: `TRANSACT-FILE` at all.
_FD_RE = re.compile(r"^\s*FD\s+([A-Z0-9-]+)", re.IGNORECASE)
_LEVEL_01_RE = re.compile(r"^\s*01\s+([A-Z0-9-]+)", re.IGNORECASE)

#: `WRITE <record area> FROM <record>` and its update sibling. `REWRITE` is included because it is
#: how COBOL updates a keyed file in place -- `CBACT04C` posts interest to the account master that
#: way -- and a writer renderer that only knew `WRITE` would silently produce no output for it.
_WRITE_FROM_RE = re.compile(
    r"\b(RE)?WRITE\s+([A-Z0-9-]+)\s+FROM\s+([A-Z0-9-]+)", re.IGNORECASE
)


class WriteBinding(BaseModel):
    """One `WRITE`/`REWRITE ... FROM`: which file is written, from which record, and how.

    `is_update` distinguishes `REWRITE` from `WRITE`: one replaces a record found by key, the other
    appends. A renderer that treated them alike would turn an update of fifty accounts into fifty
    new ones -- which is not a defect any comparison of the *records* would catch, only a comparison
    of the file's length.
    """

    file_name: str
    record_name: str
    is_update: bool = False
    source_line: int


def fd_record_areas(source_text: str) -> dict[str, str]:
    """`{record area name: file name}` for every `FD` in the `FILE SECTION`.

    The association is positional in COBOL -- the `01` immediately after an `FD` is that file's
    record -- so it is read that way rather than by matching names, which only rhyme by convention.
    """
    areas: dict[str, str] = {}
    pending: str | None = None
    for _line_no, text in _iter_code_lines(source_text):
        fd = _FD_RE.match(text)
        if fd:
            pending = fd.group(1).upper()
            continue
        record = _LEVEL_01_RE.match(text)
        if record and pending:
            areas[record.group(1).upper()] = pending
            pending = None
    return areas


def extract_write_bindings(source_text: str) -> list[WriteBinding]:
    """Every `WRITE`/`REWRITE ... FROM` in the source, attributed to the file it writes.

    A write whose record area belongs to no `FD` is skipped: it names something this parse cannot
    attribute to a file, and inventing an attribution would put records in the wrong output.
    """
    areas = fd_record_areas(source_text)
    bindings: list[WriteBinding] = []
    for line_no, text in _iter_code_lines(source_text):
        match = _WRITE_FROM_RE.search(text)
        if match is None:
            continue
        area = match.group(2).upper()
        if area not in areas:
            continue
        bindings.append(
            WriteBinding(
                file_name=areas[area],
                record_name=match.group(3).upper(),
                is_update=bool(match.group(1)),
                source_line=line_no,
            )
        )
    return bindings
