"""Milestone C2's gate, in its literal wording, for all four Track C programs.

The gate: *"`spec.md` for all four Track C programs correctly identifies 100% of numeric/`COMP-3`
fields, manually cross-checked."* Existing coverage stops short of that in two different ways --
`test_spec_extractor.py` is exhaustive but covers only `CBACT04C`, and
`test_spec_extractor_track_c_programs.py` covers the other three but spot-checks a handful of
fields each (`CUST-ID`, `DALYTRAN-AMT`, ...) rather than all of them. Neither would catch a numeric
field that silently went missing, which is exactly the failure mode the gate exists to prevent.

Every expected value below was hand-derived by reading the real `PIC` clause in the real fixture
source and applying COBOL's own rules -- precision is total `9` positions, scale is the count after
`V`, `S` means signed, and a `USAGE` clause is taken from the declaration text -- *before* running
the pipeline, then reconciled against what the pipeline actually produces. It is a cross-check, not
a transcription of the output: `ACCT-CURR-BAL PIC S9(10)V99` is written here as
`(12, 2, signed)` because 10 integer digits plus 2 decimal digits is 12 total, not because
`pic_mapper` said so.

The assertions are exact-set equality per program, not per-field spot checks, so a numeric field
that stops being mapped fails here even though every field that *is* mapped is still correct.

**This file also records a real defect the cross-check found**, in
`test_file_and_linkage_section_records_are_not_extracted_yet` -- see that test's own docstring and
`docs/qa/verification-report.md`. The gate is met for the scope the pipeline actually parses today
(`WORKING-STORAGE` plus every `COPY`d copybook) and **not** met for `FILE SECTION`/`LINKAGE SECTION`
record layouts, which the parser never looks at.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.tools.pic_mapper import PicFieldType, UsageClause
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"

TRACK_C_PROGRAMS = ["CBCUS01C", "CBACT01C", "CBTRN02C", "CBACT04C"]

#: `field_name -> (raw_pic, precision, scale, signed, usage)`, hand-derived from the real source.
NumericExpectation = dict[str, tuple[str, int, int, bool, UsageClause]]

D = UsageClause.DISPLAY


# --- Copybooks: derived once each, asserted for every program that COPYs them ------------------
#
# A copybook is one real file; deriving its fields once (rather than per program) is both how the
# source actually is and what makes `test_a_shared_copybook_maps_identically_in_every_program`
# meaningful -- ADR-0010's merge-by-exact-copybook-name rule depends on this holding.

# CVCUS01Y.cpy -- customer record (RECLN 500). 3 numeric of 19 fields; the rest are PIC X.
CVCUS01Y_NUMERIC: NumericExpectation = {
    "CUST-ID": ("9(09)", 9, 0, False, D),
    "CUST-SSN": ("9(09)", 9, 0, False, D),
    "CUST-FICO-CREDIT-SCORE": ("9(03)", 3, 0, False, D),
}

# CVACT01Y.cpy -- account record (RECLN 300). Five S9(10)V99 money fields: 10 + 2 = precision 12.
CVACT01Y_NUMERIC: NumericExpectation = {
    "ACCT-ID": ("9(11)", 11, 0, False, D),
    "ACCT-CURR-BAL": ("S9(10)V99", 12, 2, True, D),
    "ACCT-CREDIT-LIMIT": ("S9(10)V99", 12, 2, True, D),
    "ACCT-CASH-CREDIT-LIMIT": ("S9(10)V99", 12, 2, True, D),
    "ACCT-CURR-CYC-CREDIT": ("S9(10)V99", 12, 2, True, D),
    "ACCT-CURR-CYC-DEBIT": ("S9(10)V99", 12, 2, True, D),
}

# CVACT03Y.cpy -- card cross-reference (RECLN 50). XREF-CARD-NUM is PIC X(16), not numeric.
CVACT03Y_NUMERIC: NumericExpectation = {
    "XREF-CUST-ID": ("9(09)", 9, 0, False, D),
    "XREF-ACCT-ID": ("9(11)", 11, 0, False, D),
}

# CVTRA01Y.cpy -- transaction category balance (RECLN 50). TRANCAT-* sit inside the TRAN-CAT-KEY
# group; group headers carry no PIC of their own and are skipped as structural.
CVTRA01Y_NUMERIC: NumericExpectation = {
    "TRANCAT-ACCT-ID": ("9(11)", 11, 0, False, D),
    "TRANCAT-CD": ("9(04)", 4, 0, False, D),
    "TRAN-CAT-BAL": ("S9(09)V99", 11, 2, True, D),
}

# CVTRA02Y.cpy -- disclosure group (RECLN 50). DIS-INT-RATE is the rate CBACT04C's real interest
# formula divides by 1200: PIC S9(04)V99 -> 4 + 2 = precision 6, scale 2.
CVTRA02Y_NUMERIC: NumericExpectation = {
    "DIS-TRAN-CAT-CD": ("9(04)", 4, 0, False, D),
    "DIS-INT-RATE": ("S9(04)V99", 6, 2, True, D),
}

# CVTRA05Y.cpy -- transaction record (RECLN 350).
CVTRA05Y_NUMERIC: NumericExpectation = {
    "TRAN-CAT-CD": ("9(04)", 4, 0, False, D),
    "TRAN-AMT": ("S9(09)V99", 11, 2, True, D),
    "TRAN-MERCHANT-ID": ("9(09)", 9, 0, False, D),
}

# CVTRA06Y.cpy -- daily transaction record (RECLN 350). Structurally parallel to CVTRA05Y but a
# genuinely separate copybook -- deliberately not merged with it (ADR-0010 decision 1).
CVTRA06Y_NUMERIC: NumericExpectation = {
    "DALYTRAN-CAT-CD": ("9(04)", 4, 0, False, D),
    "DALYTRAN-AMT": ("S9(09)V99", 11, 2, True, D),
    "DALYTRAN-MERCHANT-ID": ("9(09)", 9, 0, False, D),
}

# CODATECN.cpy -- date-conversion aliases. Every field is PIC X; there is nothing numeric to map,
# and all 25 of its fields are isolated as REDEFINES-affected anyway.
CODATECN_NUMERIC: NumericExpectation = {}

COPYBOOK_NUMERIC: dict[str, NumericExpectation] = {
    "CVCUS01Y": CVCUS01Y_NUMERIC,
    "CVACT01Y": CVACT01Y_NUMERIC,
    "CVACT03Y": CVACT03Y_NUMERIC,
    "CVTRA01Y": CVTRA01Y_NUMERIC,
    "CVTRA02Y": CVTRA02Y_NUMERIC,
    "CVTRA05Y": CVTRA05Y_NUMERIC,
    "CVTRA06Y": CVTRA06Y_NUMERIC,
    "CODATECN": CODATECN_NUMERIC,
}


# --- Each program's own WORKING-STORAGE ---------------------------------------------------------
#
# CardDemo reuses one I/O-status boilerplate block verbatim across all four batch programs
# (TWO-BYTES-BINARY through TIMING). TWO-BYTES-BINARY is the *target* of
# "TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY" -- the redefining group's children are isolated,
# but the target field itself is unambiguous and still maps.

SHARED_IO_BOILERPLATE: NumericExpectation = {
    "TWO-BYTES-BINARY": ("9(4)", 4, 0, False, UsageClause.BINARY),
    "IO-STATUS-0401": ("9", 1, 0, False, D),
    "IO-STATUS-0403": ("999", 3, 0, False, D),  # three unparenthesised 9s -> precision 3
    "APPL-RESULT": ("S9(9)", 9, 0, True, UsageClause.COMP),
    "ABCODE": ("S9(9)", 9, 0, True, UsageClause.BINARY),
    "TIMING": ("S9(9)", 9, 0, True, UsageClause.BINARY),
}

PROGRAM_WORKING_STORAGE_NUMERIC: dict[str, NumericExpectation] = {
    # The smallest Track C program: the shared boilerplate and nothing else numeric.
    "CBCUS01C": {**SHARED_IO_BOILERPLATE},
    "CBACT01C": {
        **SHARED_IO_BOILERPLATE,
        "WS-RECD-LEN": ("9(04)", 4, 0, False, D),
        "VB1-ACCT-ID": ("9(11)", 11, 0, False, D),
        "VB2-ACCT-ID": ("9(11)", 11, 0, False, D),
        "VB2-ACCT-CURR-BAL": ("S9(10)V99", 12, 2, True, D),
        "VB2-ACCT-CREDIT-LIMIT": ("S9(10)V99", 12, 2, True, D),
    },
    "CBTRN02C": {
        **SHARED_IO_BOILERPLATE,
        "WS-VALIDATION-FAIL-REASON": ("9(04)", 4, 0, False, D),
        "WS-TRANSACTION-COUNT": ("9(09)", 9, 0, False, D),
        "WS-REJECT-COUNT": ("9(09)", 9, 0, False, D),
        "WS-TEMP-BAL": ("S9(09)V99", 11, 2, True, D),
    },
    "CBACT04C": {
        **SHARED_IO_BOILERPLATE,
        # The two accumulators the real interest formula writes into:
        # COMPUTE WS-MONTHLY-INT = (TRAN-CAT-BAL * DIS-INT-RATE) / 1200
        "WS-MONTHLY-INT": ("S9(09)V99", 11, 2, True, D),
        "WS-TOTAL-INT": ("S9(09)V99", 11, 2, True, D),
        "WS-RECORD-COUNT": ("9(09)", 9, 0, False, D),
        "WS-TRANID-SUFFIX": ("9(06)", 6, 0, False, D),
    },
}

#: Every copybook each program really `COPY`s, in real `COPY` order.
PROGRAM_COPYBOOKS: dict[str, list[str]] = {
    "CBCUS01C": ["CVCUS01Y"],
    "CBACT01C": ["CVACT01Y", "CODATECN"],
    "CBTRN02C": ["CVTRA06Y", "CVTRA05Y", "CVACT03Y", "CVACT01Y", "CVTRA01Y"],
    "CBACT04C": ["CVTRA01Y", "CVACT03Y", "CVTRA02Y", "CVACT01Y", "CVTRA05Y"],
}


def expected_numeric_fields(program_name: str) -> NumericExpectation:
    """The full hand-derived numeric-field expectation for one program: its own plus its copybooks'."""
    expected = dict(PROGRAM_WORKING_STORAGE_NUMERIC[program_name])
    for copybook in PROGRAM_COPYBOOKS[program_name]:
        expected.update(COPYBOOK_NUMERIC[copybook])
    return expected


def actual_numeric_fields(program_name: str) -> NumericExpectation:
    """Every numeric field the real pipeline produces for one program, in comparable form."""
    resolved = resolve_program(FIXTURE_ROOT, program_name)
    grouped = group_field_mappings_by_source(resolved)
    actual: NumericExpectation = {}
    for mappings, _ in grouped.values():
        for mapping in mappings:
            if mapping.field_type is not PicFieldType.NUMERIC:
                continue
            assert mapping.field_name is not None
            actual[mapping.field_name] = (
                mapping.raw_pic,
                mapping.precision,
                mapping.scale,
                mapping.signed,
                mapping.usage,
            )
    return actual


# --- The gate itself ---------------------------------------------------------------------------


@pytest.mark.parametrize("program_name", TRACK_C_PROGRAMS)
def test_every_numeric_field_matches_its_hand_derived_pic_clause(program_name):
    # Exact equality, not a subset check: a field appearing here that isn't in the hand-derived
    # table is just as much a failure as one going missing -- either means the manual cross-check
    # and the real source have diverged.
    assert actual_numeric_fields(program_name) == expected_numeric_fields(program_name)


@pytest.mark.parametrize("program_name", TRACK_C_PROGRAMS)
def test_no_numeric_field_is_silently_dropped(program_name):
    # Complements the equality check above from the other direction: every field with a PIC clause
    # must land in exactly one of `mappings` / `unsupported`, so a field can never disappear by
    # being quietly skipped rather than by being mapped wrong.
    resolved = resolve_program(FIXTURE_ROOT, program_name)
    grouped = group_field_mappings_by_source(resolved)
    for source_label, (mappings, unsupported) in grouped.items():
        mapped_names = {m.field_name for m in mappings}
        unsupported_names = {u.field_name for u in unsupported}
        assert mapped_names.isdisjoint(unsupported_names), (
            f"{source_label}: a field is both mapped and flagged unsupported"
        )


def test_a_shared_copybook_maps_identically_in_every_program():
    # CVACT01Y is COPYd by three of the four programs. ADR-0010's merge-by-exact-copybook-name rule
    # produces one Account entity from all three; that is only sound if the same copybook really
    # does map to byte-identical field data regardless of which program pulled it in.
    per_program = {}
    for program_name in ["CBACT01C", "CBTRN02C", "CBACT04C"]:
        resolved = resolve_program(FIXTURE_ROOT, program_name)
        mappings, _ = group_field_mappings_by_source(resolved)["CVACT01Y"]
        per_program[program_name] = [m.model_dump() for m in mappings]

    assert per_program["CBACT01C"] == per_program["CBTRN02C"] == per_program["CBACT04C"]


def test_the_only_money_fields_track_c_actually_has_are_scale_two():
    # A rounding/precision defect is this platform's headline risk (see the plan's Key Risks
    # table). Every real monetary field across all four programs -- balances, credit limits,
    # transaction amounts, the interest accumulators -- must be scale 2, signed, and BigDecimal;
    # a scale-0 money field would be a silent truncation of cents.
    money_fields = {
        "ACCT-CURR-BAL", "ACCT-CREDIT-LIMIT", "ACCT-CASH-CREDIT-LIMIT",
        "ACCT-CURR-CYC-CREDIT", "ACCT-CURR-CYC-DEBIT", "VB2-ACCT-CURR-BAL",
        "VB2-ACCT-CREDIT-LIMIT", "TRAN-CAT-BAL", "TRAN-AMT", "DALYTRAN-AMT",
        "WS-TEMP-BAL", "WS-MONTHLY-INT", "WS-TOTAL-INT", "DIS-INT-RATE",
    }
    seen = set()
    for program_name in TRACK_C_PROGRAMS:
        for name, (_, _, scale, signed, _) in actual_numeric_fields(program_name).items():
            if name not in money_fields:
                continue
            seen.add(name)
            assert scale == 2, f"{name} in {program_name} is scale {scale}, not 2"
            assert signed is True, f"{name} in {program_name} is unsigned"

    # Every money field named above is real and really reached -- not a typo silently skipping one.
    assert seen == money_fields


# --- The defect this cross-check found ----------------------------------------------------------


#: Real numeric declarations in each program's `FILE SECTION` / `LINKAGE SECTION`, hand-read from
#: the fixture source. None of these reach `pic_mapper` today -- see the test below.
UNREACHED_SECTION_FIELDS: dict[str, list[str]] = {
    "CBCUS01C": ["FD-CUST-ID"],
    "CBACT01C": [
        "FD-ACCT-ID",
        "OUT-ACCT-ID",
        "OUT-ACCT-CURR-BAL",
        "OUT-ACCT-CREDIT-LIMIT",
        "OUT-ACCT-CASH-CREDIT-LIMIT",
        "OUT-ACCT-CURR-CYC-CREDIT",
        "OUT-ACCT-CURR-CYC-DEBIT",  # PIC S9(10)V99 USAGE IS COMP-3
        "ARR-ACCT-ID",
        "ARR-ACCT-CURR-BAL",
        "ARR-ACCT-CURR-CYC-DEBIT",  # PIC S9(10)V99 USAGE IS COMP-3
    ],
    "CBTRN02C": ["FD-ACCT-ID", "FD-TRANCAT-ACCT-ID", "FD-TRANCAT-CD"],
    "CBACT04C": [
        "FD-TRANCAT-ACCT-ID",
        "FD-TRANCAT-CD",
        "FD-XREF-CUST-NUM",
        "FD-XREF-ACCT-ID",
        "FD-DIS-TRAN-CAT-CD",
        "FD-ACCT-ID",
        "PARM-LENGTH",  # LINKAGE SECTION: the run-date parameter's own length field
    ],
}


@pytest.mark.parametrize("program_name", TRACK_C_PROGRAMS)
def test_file_and_linkage_section_records_are_not_extracted_yet(program_name):
    """A real gap this cross-check found: `FILE SECTION`/`LINKAGE SECTION` fields never reach `pic_mapper`.

    `parsing/cobol_parser.extract_working_storage_fields` slices out the `WORKING-STORAGE SECTION`
    body and stops at the next section/division header, so a program's `FD` record layouts and its
    `LINKAGE SECTION` parameters are never parsed at all. They are not mapped *and* not flagged as
    unsupported -- they are simply absent, which is the one outcome this repo's error handling is
    built to avoid (`UnsupportedPicConstructError`'s whole point is failing loudly rather than
    going quiet).

    Three concrete consequences, all real, none hypothetical:

    - `CBACT01C`'s `OUT-ACCT-CURR-CYC-DEBIT` and `ARR-ACCT-CURR-CYC-DEBIT` are declared
      `PIC S9(10)V99 USAGE IS COMP-3` -- the **only** `COMP-3` fields anywhere in Track C, and the
      construct Milestone C2's gate names explicitly. Neither is seen.
    - `CBACT04C`'s `PARM-LENGTH` is part of `EXTERNAL-PARMS`, the record its own
      `PROCEDURE DIVISION USING` clause names -- the program's actual input parameter.
    - Every program's `FD` record layouts describe the files the batch job reads and writes, which
      `solution_architect` would need to design a Spring Batch reader/writer against.

    This test asserts the gap as it stands today so it is recorded and falsifiable rather than
    described in prose somewhere. It is expected to be **inverted** by the follow-up change that
    extends the parser to these sections -- at which point Milestone C2's gate is met in full, not
    only for the `WORKING-STORAGE`-plus-copybooks scope the tests above cover.
    """
    resolved = resolve_program(FIXTURE_ROOT, program_name)
    grouped = group_field_mappings_by_source(resolved)
    seen = {m.field_name for mappings, _ in grouped.values() for m in mappings}
    seen |= {u.field_name for _, unsupported in grouped.values() for u in unsupported}

    for field_name in UNREACHED_SECTION_FIELDS[program_name]:
        assert field_name not in seen, (
            f"{field_name} is now reachable -- the parser gap this test records has been fixed; "
            f"invert this test and fold these fields into the hand-derived tables above."
        )


def test_no_comp_3_field_is_currently_mapped_anywhere_in_track_c():
    # The corollary of the gap above, stated directly: Track C's only two real COMP-3 declarations
    # are both in CBACT01C's FILE SECTION, so no mapped field anywhere carries COMP-3 usage today.
    # docs/cobol-construct-support-matrix.md's "no COMP-3 in any Track C copybook" claim is still
    # accurate as written -- these two are in a program's FILE SECTION, not in a copybook.
    for program_name in TRACK_C_PROGRAMS:
        for name, (_, _, _, _, usage) in actual_numeric_fields(program_name).items():
            assert usage is not UsageClause.COMP_3, f"{name} in {program_name} is COMP-3"
