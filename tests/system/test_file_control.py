"""`FILE-CONTROL` parsing: the fact a rendered reader needs and the design has never carried (G31).

**Tested against the real programs, not synthetic ones.** `tests/fixtures/tenant_repo_sample/` is
byte-verified against the tenant repository's own blobs, so every assertion here is about COBOL
somebody actually wrote. The synthetic cases below exist only for the refusals, which the corpus
does not contain -- and their absence from the corpus is exactly why they need a test.

**The claim ADR-0030 made about `CBACT04C` is checked rather than repeated.** It argued that the
program's access paths are *"one driving stream, three keyed lookups"* and that this is
deterministic, parseable fact of the same grade as a `PIC` clause. That sentence is now a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.parsing.file_control import (
    DEFAULT_ACCESS_MODE,
    DEFAULT_ORGANIZATION,
    FileDeclaration,
    UnsupportedFileControlError,
    extract_file_declarations,
    extract_record_bindings,
    extract_write_bindings,
    fd_record_areas,
    parse_select,
)

CBL = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample" / "app" / "cbl"
CPY = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample" / "app" / "cpy"

#: Every Track C program. Read as latin-1 because the corpus is fixed-width bytes, not UTF-8.
PROGRAMS = ("CBCUS01C", "CBACT01C", "CBTRN02C", "CBACT04C")


def source(name: str) -> str:
    return (CBL / f"{name}.cbl").read_text(encoding="latin-1")


def declarations(name: str) -> list[FileDeclaration]:
    return extract_file_declarations(source(name))


# --- the real corpus -------------------------------------------------------------------------------


@pytest.mark.parametrize("program", PROGRAMS)
def test_every_program_in_the_corpus_parses_completely(program):
    """No `SELECT` in Track C uses a construct this parser skips.

    The value of this test is what it does when it fails: `UnsupportedFileControlError` names the
    line and the clause, so a program added to the corpus with an unmodelled access path is a red
    build rather than a reader quietly rendered against a file nobody read the declaration of.
    """
    found = declarations(program)
    assert found, f"{program} declares no files, which no Track C program does"
    for declaration in found:
        assert declaration.assign_to
        assert declaration.source_line > 0


def test_cbact04c_is_one_driving_stream_and_three_keyed_lookups():
    """ADR-0030's central claim, as an assertion instead of a sentence.

    `TCATBAL-FILE` is `ACCESS MODE IS SEQUENTIAL` -- the stream the program walks -- and `XREF`,
    `ACCOUNT` and `DISCGRP` are `RANDOM` with declared keys. That is the whole shape a rendered
    reader has to produce, and it is read off the source rather than inferred from the composite's
    component list, which cannot express it.
    """
    by_name = {d.select_name: d for d in declarations("CBACT04C")}

    driving = [d for d in by_name.values() if not d.is_keyed_lookup and d.organization == "INDEXED"]
    assert [d.select_name for d in driving] == ["TCATBAL-FILE"]

    lookups = {d.select_name for d in by_name.values() if d.is_keyed_lookup}
    assert lookups == {"XREF-FILE", "ACCOUNT-FILE", "DISCGRP-FILE"}

    assert by_name["ACCOUNT-FILE"].record_key == "FD-ACCT-ID"
    assert by_name["DISCGRP-FILE"].record_key == "FD-DISCGRP-KEY"
    # The output file: sequential, no key, and not a lookup. A reader rendered from lookups alone
    # would still need to know this one is written rather than read.
    assert by_name["TRANSACT-FILE"].organization == "SEQUENTIAL"
    assert by_name["TRANSACT-FILE"].record_key is None


def test_the_xref_lookup_is_by_its_alternate_key_not_its_record_key():
    """The fact the hand-written wiring needed and the design could not supply (finding F2).

    `XREF-FILE`'s `RECORD KEY` is the card number, but `1110-GET-XREF-DATA` reads it
    `KEY IS FD-XREF-ACCT-ID` -- the *alternate*. A renderer that keyed this lookup on the record key
    would look correct, compile, and find nothing: the account id is what `CBACT04C` has in hand.
    Dropping alternate keys would therefore lose the only usable access path for this file.
    """
    xref = {d.select_name: d for d in declarations("CBACT04C")}["XREF-FILE"]
    assert xref.record_key == "FD-XREF-CARD-NUM"
    assert xref.alternate_record_keys == ["FD-XREF-ACCT-ID"]
    assert "FD-XREF-ACCT-ID" in source("CBACT04C"), "the alternate key is read in the procedure too"


def test_the_same_file_can_be_a_stream_in_one_program_and_a_lookup_in_another():
    """`TCATBAL` is `SEQUENTIAL` in `CBACT04C` and `RANDOM` in `CBTRN02C`, and both are true.

    Worth pinning because it is the reason this belongs per-program rather than per-entity: an
    access path is a property of how *this* program reads the file, so a design that recorded it on
    the domain entity would have to pick one and be wrong for the other.
    """
    cbact04c = {d.select_name: d for d in declarations("CBACT04C")}["TCATBAL-FILE"]
    cbtrn02c = {d.select_name: d for d in declarations("CBTRN02C")}["TCATBAL-FILE"]

    assert cbact04c.access_mode == "SEQUENTIAL" and not cbact04c.is_keyed_lookup
    assert cbtrn02c.access_mode == "RANDOM" and cbtrn02c.is_keyed_lookup
    assert cbact04c.record_key == cbtrn02c.record_key == "FD-TRAN-CAT-KEY"


def test_each_declaration_carries_the_line_it_came_from():
    """Provenance, which `CLAUDE.md` requires of every derived artifact.

    Checked against the file rather than against a remembered number: the line this reports must
    actually be the `SELECT`.
    """
    lines = source("CBACT04C").splitlines()
    for declaration in declarations("CBACT04C"):
        text = lines[declaration.source_line - 1]
        assert "SELECT" in text.upper()
        assert declaration.select_name in text.upper()


def test_a_copybook_declares_no_files_and_that_is_not_an_error():
    """`CVACT01Y` has no `FILE-CONTROL`. Absence of a paragraph is a fact, not a failure."""
    assert extract_file_declarations((CPY / "CVACT01Y.cpy").read_text(encoding="latin-1")) == []


# --- the refusals ----------------------------------------------------------------------------------


def test_an_unrecognised_clause_is_refused_rather_than_skipped():
    """ADR-0011's argument, applied here: a skipped clause is invisible downstream.

    `PASSWORD` is a real IBM clause this parser does not model. Skipping it would produce a
    declaration that looks complete and is missing a fact nobody can see is missing.
    """
    with pytest.raises(UnsupportedFileControlError, match="unrecognised clause"):
        parse_select("SELECT ACCT-FILE ASSIGN TO ACCTFILE PASSWORD IS WS-PASS", 12)


def test_select_optional_is_refused_because_a_missing_file_is_not_an_error():
    with pytest.raises(UnsupportedFileControlError, match="OPTIONAL"):
        parse_select("SELECT OPTIONAL ACCT-FILE ASSIGN TO ACCTFILE", 3)


def test_an_alternate_key_with_duplicates_is_refused_because_a_lookup_becomes_a_join():
    """The subtlest of the refusals, and the reason it is not tolerated.

    `WITH DUPLICATES` means the key selects many records. Rendered as a single-row fetch it returns
    one of them, silently, and the output differs from COBOL's by rows nobody asked about.
    """
    with pytest.raises(UnsupportedFileControlError, match="DUPLICATES"):
        parse_select(
            "SELECT XREF-FILE ASSIGN TO XREFFILE ORGANIZATION IS INDEXED "
            "RECORD KEY IS FD-XREF-CARD-NUM "
            "ALTERNATE RECORD KEY IS FD-XREF-ACCT-ID WITH DUPLICATES",
            9,
        )


def test_an_indexed_file_without_a_record_key_is_refused():
    """Indexed with no key cannot be read by key, so a lookup rendered from it would have none."""
    with pytest.raises(UnsupportedFileControlError, match="no RECORD KEY"):
        parse_select("SELECT ACCT-FILE ASSIGN TO ACCTFILE ORGANIZATION IS INDEXED", 5)


def test_a_select_without_assign_is_refused():
    with pytest.raises(UnsupportedFileControlError, match="no ASSIGN"):
        parse_select("SELECT ACCT-FILE ORGANIZATION IS SEQUENTIAL", 5)


@pytest.mark.parametrize(
    "clause, expected",
    [
        ("ORGANIZATION IS TAPE", "ORGANIZATION"),
        ("ACCESS MODE IS SORTED", "ACCESS MODE"),
    ],
)
def test_an_unknown_organization_or_access_mode_is_refused(clause, expected):
    with pytest.raises(UnsupportedFileControlError, match=expected):
        parse_select(f"SELECT F ASSIGN TO DD {clause}", 7)


# --- the defaults ----------------------------------------------------------------------------------


def test_omitted_clauses_take_cobols_defaults_rather_than_none():
    """A `SELECT` with neither clause is a sequential file read sequentially -- COBOL's rule.

    Defaulted rather than left `None` because a renderer would then have to re-derive the same rule
    from an absence, and two places deciding what a missing clause means is one too many.
    """
    declaration = parse_select("SELECT REPORT-FILE ASSIGN TO REPTOUT", 21)
    assert declaration.organization == DEFAULT_ORGANIZATION
    assert declaration.access_mode == DEFAULT_ACCESS_MODE
    assert not declaration.is_keyed_lookup
    assert declaration.record_key is None
    assert declaration.file_status is None


def test_noise_words_and_line_breaks_do_not_change_the_parse():
    """`ACCESS MODE IS RANDOM` and `ACCESS RANDOM` are the same declaration in COBOL.

    The corpus writes these across six physical lines with padded column alignment, so the parser
    has to be indifferent to both -- and `LINE SEQUENTIAL` has to survive as one organization rather
    than becoming an unknown `LINE` clause.
    """
    terse = parse_select("SELECT F ASSIGN DD ORGANIZATION INDEXED ACCESS RANDOM RECORD KEY K", 1)
    verbose = parse_select(
        "SELECT F ASSIGN TO DD ORGANIZATION IS INDEXED ACCESS MODE IS RANDOM RECORD KEY IS K", 1
    )
    assert terse == verbose
    assert parse_select("SELECT F ASSIGN TO DD ORGANIZATION IS LINE SEQUENTIAL", 1).organization == (
        "LINE SEQUENTIAL"
    )


# --- the refusal paths coverage would otherwise leave untested --------------------------------------
#
# Every one of these is a branch that only runs on malformed input, which is precisely the shape of
# code this repo has repeatedly found untested (G21 and its four recurrences). A refusal nobody has
# exercised is a refusal nobody knows fires.


def test_text_between_the_paragraph_header_and_the_first_select_is_ignored():
    """A `FILE-CONTROL` paragraph is allowed to contain more than `SELECT`s.

    Ignored rather than refused because the alternative is refusing a program for a line that
    declares no file -- the refusals in this module are for clauses *inside* a `SELECT`, where
    skipping loses a fact.
    """
    program = """       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SPECIAL-NAMES-ISH LINE THAT DECLARES NO FILE.
           SELECT ACCT-FILE ASSIGN TO ACCTFILE
                  ORGANIZATION IS SEQUENTIAL.
       DATA DIVISION.
"""
    assert [d.select_name for d in extract_file_declarations(program)] == ["ACCT-FILE"]


def test_an_unterminated_select_is_still_reported_rather_than_dropped():
    """A `SELECT` with no closing period is malformed COBOL, and silence would lose the file.

    It reaches `parse_select` and fails there on whatever it actually contains -- the point being
    that the sentence is not discarded merely for lacking its period.
    """
    program = """       FILE-CONTROL.
           SELECT ACCT-FILE ASSIGN TO ACCTFILE
                  ORGANIZATION IS TAPE
"""
    with pytest.raises(UnsupportedFileControlError, match="ORGANIZATION"):
        extract_file_declarations(program)


def test_a_sentence_that_is_not_a_select_is_refused_by_the_parser_itself():
    with pytest.raises(UnsupportedFileControlError, match="not a SELECT"):
        parse_select("ASSIGN TO ACCTFILE", 4)


def test_a_select_that_names_no_file_is_refused():
    with pytest.raises(UnsupportedFileControlError, match="names no file"):
        parse_select("SELECT", 4)


def test_alternate_not_followed_by_record_key_is_refused():
    """`ALTERNATE AREA` is not `ALTERNATE RECORD KEY`, and guessing which was meant invents a key."""
    with pytest.raises(UnsupportedFileControlError, match="expected ALTERNATE RECORD KEY"):
        parse_select(
            "SELECT F ASSIGN TO DD ORGANIZATION IS INDEXED RECORD KEY IS K ALTERNATE AREA IS X", 6
        )


def test_a_clause_with_no_value_is_refused():
    """`FILE STATUS` with nothing after it names no variable; taking the next clause would be worse."""
    with pytest.raises(UnsupportedFileControlError, match="names no value"):
        parse_select("SELECT F ASSIGN TO DD FILE STATUS", 8)


# --- record bindings: which record a file yields, and by which key -----------------------------------


def bindings(name: str):
    return extract_record_bindings(source(name))


def test_the_read_statements_bind_each_file_to_the_record_it_yields():
    """`FILE-CONTROL` names files and copybooks name records; only `READ ... INTO` joins the two.

    Without this link an access path can say a file is read by a key and still not say *what* it
    produces, which is the difference between a reader a renderer can build and a fact nobody can
    use.
    """
    bound = {b.file_name: b.record_name for b in bindings("CBACT04C")}
    assert bound == {
        "TCATBAL-FILE": "TRAN-CAT-BAL-RECORD",
        "ACCOUNT-FILE": "ACCOUNT-RECORD",
        "XREF-FILE": "CARD-XREF-RECORD",
        "DISCGRP-FILE": "DIS-GROUP-RECORD",
    }


def test_the_read_key_is_found_on_a_continuation_line():
    """The reason these are parsed as statements rather than lines.

    `READ XREF-FILE INTO CARD-XREF-RECORD` and its `KEY IS FD-XREF-ACCT-ID` are on *different*
    physical lines. A line-scoped match finds the file and the record and silently misses the key --
    which is the single fact that distinguishes the alternate key from the declared one.
    """
    xref = next(b for b in bindings("CBACT04C") if b.file_name == "XREF-FILE")
    assert xref.read_key == "FD-XREF-ACCT-ID"


def test_invalid_key_is_not_mistaken_for_the_read_key():
    """`INVALID KEY DISPLAY ...` contains the word KEY, and would otherwise yield a key of DISPLAY.

    A plausible-looking wrong answer, which is the failure mode this repo refuses everywhere else --
    `ACCOUNT-FILE`'s read has an `INVALID KEY` clause and no key phrase of its own.
    """
    account = next(b for b in bindings("CBACT04C") if b.file_name == "ACCOUNT-FILE")
    assert account.read_key is None
    assert "INVALID KEY" in source("CBACT04C").upper()


def test_repeated_reads_of_one_file_are_kept():
    """`CBACT04C` reads `DISCGRP-FILE` twice: the account's group, then `'DEFAULT'`.

    Collapsing them would hide the fallback -- a business rule (finding F4) -- at the exact layer
    that exists to recover it.
    """
    discgrp = [b for b in bindings("CBACT04C") if b.file_name == "DISCGRP-FILE"]
    assert len(discgrp) == 2
    assert discgrp[0].source_line != discgrp[1].source_line


def test_a_read_without_into_binds_no_record():
    """`READ f` reads into the FD's own record area and names no copybook record.

    Skipped rather than guessed: picking a layout for it would invent the one fact this parse
    exists to establish.
    """
    program = """       PROCEDURE DIVISION.
           READ ACCT-FILE
               AT END MOVE 'Y' TO EOF
           END-READ.
"""
    assert extract_record_bindings(program) == []


def test_an_unterminated_read_is_still_returned():
    """A `READ` with no period and no `END-READ` is malformed, and dropping it would lose a binding."""
    program = """       PROCEDURE DIVISION.
           READ ACCT-FILE INTO ACCOUNT-RECORD
"""
    assert [b.record_name for b in extract_record_bindings(program)] == ["ACCOUNT-RECORD"]


# --- the write side ----------------------------------------------------------------------------------


def test_each_fd_record_area_is_attributed_to_its_file():
    """A write names the record *area*, not the file, so without this it cannot be attributed at all.

    Read positionally -- the `01` after an `FD` is that file's record -- rather than by matching
    names, which only rhyme by convention.
    """
    areas = fd_record_areas(source("CBACT04C"))
    assert areas["FD-TRANFILE-REC"] == "TRANSACT-FILE"
    assert areas["FD-ACCTFILE-REC"] == "ACCOUNT-FILE"
    assert len(areas) == 5


def test_the_writes_are_found_and_attributed():
    """`CBACT04C` appends transactions and rewrites accounts, and the two are different statements."""
    writes = {b.file_name: b for b in extract_write_bindings(source("CBACT04C"))}
    assert writes["TRANSACT-FILE"].record_name == "TRAN-RECORD"
    assert not writes["TRANSACT-FILE"].is_update
    assert writes["ACCOUNT-FILE"].record_name == "ACCOUNT-RECORD"
    assert writes["ACCOUNT-FILE"].is_update, "REWRITE is an update, not an append"


def test_a_file_written_both_ways_keeps_both_bindings():
    """`CBTRN02C` both creates and updates `TCATBAL` rows -- the 50 new rows the oracle found.

    Collapsing them would erase the fact that the program can *create* a balance row, which is the
    difference between 50 rows and 100.
    """
    tcatbal = [
        b for b in extract_write_bindings(source("CBTRN02C")) if b.file_name == "TCATBAL-FILE"
    ]
    assert {b.is_update for b in tcatbal} == {False, True}


def test_a_write_to_an_unknown_record_area_is_skipped_rather_than_guessed():
    """A record area belonging to no `FD` cannot be attributed, and inventing one would put records
    in the wrong output file."""
    program = """       PROCEDURE DIVISION.
           WRITE SOME-UNKNOWN-REC FROM WS-RECORD.
"""
    assert extract_write_bindings(program) == []


def test_each_write_binding_carries_its_source_line():
    lines = source("CBACT04C").splitlines()
    for binding in extract_write_bindings(source("CBACT04C")):
        assert "WRITE" in lines[binding.source_line - 1].upper()
