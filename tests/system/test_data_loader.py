"""`tools/data_loader.py` against CardDemo's **real, byte-verified** data files.

The fixture under `tests/fixtures/tenant_repo_sample/app/data/ASCII/` was fetched from
`carddemo-tenant-service` and each file's git blob SHA checked against the remote before it was
committed, the same way PR #10 verified the copybook fixtures. That matters more here than usual:
two of the three defects this module exists for are *properties of the bytes* — a record width that
contradicts its copybook, and line endings that are not uniform within one file — so a fixture that
git had silently normalised would make every test below vacuous.

`.gitattributes` already covers this path with `-text`, which is what keeps the mixed CR/LF intact.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.tools.data_loader import (
    DataFormatError,
    FixedWidthField,
    decode_zoned_decimal,
    derive_layout,
    field_byte_width,
    measure_record_length,
    parse_record,
    read_records,
)
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
DATA = FIXTURE_ROOT / "app" / "data" / "ASCII"


@pytest.fixture(scope="module")
def groups():
    return group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))


# --- The sign overpunch, which is where money is lost ---------------------------------------------


def test_the_overpunch_carries_a_digit_as_well_as_a_sign():
    # The plan's own example. Stripping the `{` gives 19.40 -- a factor of ten low, silently, and in
    # the direction that makes a balance look smaller.
    assert decode_zoned_decimal("00000001940{", scale=2, signed=True) == Decimal("194.00")


def test_the_negative_overpunch_is_the_same_digit_with_the_opposite_sign():
    assert decode_zoned_decimal("00000001940}", scale=2, signed=True) == Decimal("-194.00")


@pytest.mark.parametrize(
    ("final", "expected"),
    [("{", "0"), ("A", "1"), ("E", "5"), ("I", "9")],
)
def test_every_positive_overpunch_letter_decodes_to_its_digit(final, expected):
    assert decode_zoned_decimal(f"12{final}", scale=0, signed=True) == Decimal(f"12{expected}")


@pytest.mark.parametrize(
    ("final", "expected"),
    [("}", "0"), ("J", "1"), ("N", "5"), ("R", "9")],
)
def test_every_negative_overpunch_letter_decodes_to_its_digit(final, expected):
    assert decode_zoned_decimal(f"12{final}", scale=0, signed=True) == Decimal(f"-12{expected}")


def test_an_unsigned_field_is_read_as_written():
    assert decode_zoned_decimal("000123", scale=2, signed=False) == Decimal("1.23")


def test_an_unrecognised_overpunch_raises_rather_than_being_guessed_at():
    # A byte that is neither a digit nor an overpunch means the layout is wrong, and reading past it
    # would produce a plausible number from the wrong columns.
    with pytest.raises(DataFormatError, match="unrecognised sign overpunch"):
        decode_zoned_decimal("0000019Z", scale=2, signed=True)


def test_a_non_numeric_field_raises():
    with pytest.raises(DataFormatError, match="non-numeric"):
        decode_zoned_decimal("00XX19", scale=2, signed=False)


# --- Record width: measured, not read off the copybook --------------------------------------------


def test_cardxrefs_real_width_contradicts_its_copybook(groups):
    """The finding, detected mechanically instead of asserted in prose.

    `CVACT03Y` documents `RECLN 50`; the file is 36 bytes per record, because the trailing
    `FILLER X(14)` is absent. A reader that trusted the copybook would slice every field of every
    record past its end.
    """
    mapped, _ = groups["CVACT03Y"]
    copybook_width = sum(f.length for f in derive_layout(mapped))
    file_width = measure_record_length((DATA / "cardxref.txt").read_bytes())

    assert copybook_width == 50
    assert file_width == 36
    assert copybook_width != file_width


@pytest.mark.parametrize(("copybook", "datafile"), [("CVTRA01Y", "tcatbal"), ("CVTRA02Y", "discgrp")])
def test_the_other_two_copybooks_do_agree_with_their_files(groups, copybook, datafile):
    # Stated so the test above reads as a real discrepancy rather than as this module being unable
    # to derive a width at all.
    mapped, _ = groups[copybook]
    assert sum(f.length for f in derive_layout(mapped)) == measure_record_length(
        (DATA / f"{datafile}.txt").read_bytes()
    )


def test_a_ragged_file_raises_instead_of_picking_a_width():
    with pytest.raises(DataFormatError, match="not a uniform width"):
        measure_record_length(b"12345\n123\n")


def test_an_empty_file_raises():
    with pytest.raises(DataFormatError, match="no records"):
        measure_record_length(b"\n\n")


# --- Line endings that are not uniform within one file --------------------------------------------


def test_tcatbal_really_does_mix_its_line_endings():
    # The property the reader is built around, asserted on the bytes so the fixture cannot be
    # silently normalised without this failing.
    raw = (DATA / "tcatbal.txt").read_bytes()
    assert raw.count(b"\r") == 49
    assert raw.count(b"\n") == 50


def test_every_record_is_read_despite_the_mixed_endings():
    # Splitting on CRLF would merge the one LF-only record into its neighbour and yield 49.
    assert len(read_records(DATA / "tcatbal.txt")) == 50


def test_no_record_carries_a_stray_carriage_return():
    # Not stripping \r corrupts the final field of the 49 records that have one.
    for record in read_records(DATA / "tcatbal.txt"):
        assert "\r" not in record


def test_a_record_of_the_wrong_width_is_refused(tmp_path):
    path = tmp_path / "ragged.txt"
    path.write_bytes(b"1234567890\n1234567890\n12345\n")
    with pytest.raises(DataFormatError):
        read_records(path)


# --- Layout derivation ------------------------------------------------------------------------------


def test_offsets_are_contiguous_and_start_at_zero(groups):
    mapped, _ = groups["CVTRA01Y"]
    layout = derive_layout(mapped)
    assert layout[0].start == 0
    for previous, current in itertools.pairwise(layout):
        assert current.start == previous.start + previous.length


def test_a_signed_numeric_field_keeps_its_computed_scale(groups):
    mapped, _ = groups["CVTRA01Y"]
    balance = next(f for f in derive_layout(mapped) if f.name == "TRAN-CAT-BAL")
    assert balance.scale == 2
    assert balance.signed is True


def test_a_field_with_no_derivable_width_raises():
    class _Unsized:
        field_name = "MYSTERY"
        raw_pic = "PIC ????"
        string_length = None
        precision = None

    with pytest.raises(DataFormatError, match="unknown"):
        field_byte_width(_Unsized())


# --- A real record, end to end ----------------------------------------------------------------------


def test_a_real_tcatbal_record_parses_through_its_derived_layout(groups):
    mapped, _ = groups["CVTRA01Y"]
    record = read_records(DATA / "tcatbal.txt")[0]
    values = parse_record(record, derive_layout(mapped))

    assert values["TRANCAT-ACCT-ID"] == Decimal(1)
    assert values["TRANCAT-TYPE-CD"] == "01"
    # The overpunched `{` in this record is a positive zero -- the decoder's job, at real offsets.
    assert values["TRAN-CAT-BAL"] == Decimal("0.00")


def test_every_tcatbal_record_parses(groups):
    mapped, _ = groups["CVTRA01Y"]
    layout = derive_layout(mapped)
    for record in read_records(DATA / "tcatbal.txt"):
        parse_record(record, layout)


def test_a_field_running_past_the_record_end_raises():
    with pytest.raises(DataFormatError, match="of a 5-byte record"):
        parse_record("12345", [FixedWidthField(name="X", start=0, length=10)])


def test_an_empty_numeric_field_raises_rather_than_reading_as_zero():
    # A blank where a number should be means the layout is wrong or the record is truncated.
    # Reading it as zero would put a plausible balance into an equivalence test.
    with pytest.raises(DataFormatError, match="empty numeric field"):
        decode_zoned_decimal("      ", scale=2, signed=True)


def test_a_short_record_is_named_in_the_error(tmp_path):
    # The message has to say which record, or a 50-record file gives a reviewer nothing to look at.
    path = tmp_path / "short.txt"
    path.write_bytes(b"1234567890\n12345\n")
    with pytest.raises(DataFormatError, match="not a uniform width"):
        read_records(path)
