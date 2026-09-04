"""The hand-computed expected table for `CBACT04C`'s interest, and what guards it.

`tests/fixtures/golden/CBACT04C/interest-oracle.json` is step 45's oracle. Its expected values are
literals derived by hand from the COBOL; nothing here generates them, and nothing here should ever
be changed to.

**On the double-entry check below.** `test_every_expected_value_survives_an_independent_recompute`
recomputes each row with exact rationals and compares. That is deliberate, and it is not the thing
PR #35 refused. What was refused there was a Python re-implementation of `CBTRN02C`'s *posting
logic* -- validation, rejection, two file lookups -- used to manufacture expected values nobody had
derived independently. Here the semantics are a single documented arithmetic rule, the literals were
derived first and by hand, and the recompute exists to catch a transcription slip in a JSON file.
It cannot validate the COBOL reading itself: both sides encode the same one, which is stated in
`interest-oracle.md` rather than hidden. Two agreeing derivations behind a literal is stronger
evidence than one, and the literal remains the artifact step 45 consumes.
"""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from cobol_modernizer.core.package_data import ORACLE_ROOT
from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.tools.data_loader import derive_layout, parse_record, read_records
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
DATA = FIXTURE_ROOT / "app" / "data" / "ASCII"
ORACLE = ORACLE_ROOT / "CBACT04C" / "interest-oracle.json"

#: The divisor is a literal in the COBOL, not a derived constant. Spelled out here so a reader can
#: see that this module hardcodes the same 1200 the program does rather than computing a rate.
DIVISOR = Decimal(1200)


@pytest.fixture(scope="module")
def oracle():
    return json.loads(ORACLE.read_text())


@pytest.fixture(scope="module")
def rows(oracle):
    return oracle["rows"]


def _exact_quotient(balance: str, rate: str) -> Fraction:
    """The mathematically exact value of `(balance * rate) / 1200`, with no rounding anywhere."""
    return Fraction(Decimal(balance)) * Fraction(Decimal(rate)) / Fraction(DIVISOR)


def _quantize(quotient: Fraction, rounding: str) -> Decimal:
    return (Decimal(quotient.numerator) / Decimal(quotient.denominator)).quantize(
        Decimal("0.01"), rounding=rounding
    )


# --- The literals themselves ----------------------------------------------------------------------


def test_the_headline_rows_are_the_values_that_were_derived_by_hand(rows):
    """A transcription guard on the values most likely to be quietly 'fixed' to a rounded one.

    Spelled out here as well as in the fixture so that editing the JSON alone cannot change what
    step 45 asserts without a test failing and a reviewer seeing both sides of the change.
    """
    by_id = {row["id"]: row["expected"] for row in rows}
    assert by_id["R1"] == "2.42"  # not 2.43 -- the exact tie truncates
    assert by_id["R2"] == "-2.42"  # not -2.43 -- toward zero, not toward negative infinity
    assert by_id["R5"] == "0.00"  # not 0.01 -- a sub-cent result keeps nothing
    assert by_id["R8"] == "-12.47"  # not -12.48


def test_every_row_carries_its_own_derivation_and_reason(rows):
    # A row without a written derivation is a number nobody can check, which is the failure mode
    # this whole fixture exists to avoid.
    assert len(rows) == 9
    for row in rows:
        assert row["derivation"].strip(), row["id"]
        assert row["why"].strip(), row["id"]
        # Every expected value is stored at the receiving field's scale, exactly two places. A bare
        # `0` or `2.4` would still parse and would quietly assert something weaker than intended.
        assert -Decimal(row["expected"]).as_tuple().exponent == 2, row["id"]


def test_every_expected_value_survives_an_independent_recompute(rows):
    """Double-entry, not an oracle -- see this module's docstring before reading anything more into it."""
    for row in rows:
        recomputed = _quantize(_exact_quotient(row["balance"], row["rate"]), ROUND_DOWN)
        assert recomputed == Decimal(row["expected"]), row["id"]


# --- That each row can actually fail --------------------------------------------------------------


def test_each_rejected_value_is_what_that_wrong_mode_really_produces(rows):
    """The teeth. A row whose `rejects` entry is wrong is a row that discriminates nothing.

    This computes the *wrong* answers on purpose: if `HALF_UP` on R1 did not really give 2.43, the
    claim that R1 catches a rounding implementation would be decoration.
    """
    modes = {"HALF_UP": ROUND_HALF_UP, "FLOOR": ROUND_FLOOR}
    checked = 0
    for row in rows:
        quotient = _exact_quotient(row["balance"], row["rate"])
        for mode, claimed in row["rejects"].items():
            if mode not in modes:
                continue  # `unscaled_divide` names an exception, covered separately below
            assert _quantize(quotient, modes[mode]) == Decimal(claimed), f"{row['id']}/{mode}"
            assert Decimal(claimed) != Decimal(row["expected"]), f"{row['id']}/{mode} has no teeth"
            checked += 1
    assert checked >= 8, "the table has lost its discriminating rows"


def test_the_negative_tie_separates_all_three_rounding_modes(rows):
    # R2 is the row that earns its place: one input, three different answers.
    r2 = next(row for row in rows if row["id"] == "R2")
    quotient = _exact_quotient(r2["balance"], r2["rate"])
    assert _quantize(quotient, ROUND_DOWN) == Decimal("-2.42")
    assert _quantize(quotient, ROUND_HALF_UP) == Decimal("-2.43")
    assert _quantize(quotient, ROUND_FLOOR) == Decimal("-2.43")


def test_the_non_terminating_row_really_is_non_terminating(rows):
    # R3's whole job is to be a quotient `BigDecimal.divide` cannot represent without a scale, so
    # that a generated division omitting one throws instead of quietly working.
    r3 = next(row for row in rows if row["id"] == "R3")
    quotient = _exact_quotient(r3["balance"], r3["rate"])
    assert quotient == Fraction(25, 12)

    # A reduced fraction terminates in decimal iff its denominator is 2^a * 5^b. Stating it this
    # way is exact and needs no runtime: anything left over means no finite decimal represents this
    # quotient, which is precisely when `BigDecimal.divide` without a scale throws.
    remainder = quotient.denominator
    for factor in (2, 5):
        while remainder % factor == 0:
            remainder //= factor
    assert remainder > 1, "R3 must be a quotient no finite decimal can represent"


# --- The zero-rate path, which is not an arithmetic row -------------------------------------------


def test_the_zero_rate_case_carries_no_expected_value(oracle):
    """`IF DIS-INT-RATE NOT = 0` is a control-flow fact, and writing it as `0.00` would lose it.

    An implementation that returns 0.00 for a zero rate agrees numerically with every arithmetic
    check and still emits a transaction record COBOL never writes. Keeping this row out of `rows`
    is what stops it being consumed as an expected value by a harness that only reads that list.
    """
    (r10,) = oracle["not_computed"]
    assert r10["id"] == "R10"
    assert r10["expected"] is None
    assert r10["rate"] == "0.00"
    assert "no transaction" in r10["derivation"].lower()
    assert all(row["id"] != "R10" for row in oracle["rows"])


def test_a_zero_rate_is_a_real_case_and_not_a_hypothetical():
    # `discgrp.txt` really contains a 0.00 rate, so the guard is reachable on real data.
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))
    layout = derive_layout(groups["CVTRA02Y"][0])
    rates = {
        parse_record(record, layout)["DIS-INT-RATE"] for record in read_records(DATA / "discgrp.txt")
    }
    assert Decimal("0.00") in rates


# --- The provenance claims, checked against the real files -----------------------------------------


def test_every_rate_in_the_table_is_a_rate_that_really_occurs(rows, oracle):
    # A table built on rates CardDemo does not use would be arithmetic practice, not an oracle for
    # this program.
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))
    layout = derive_layout(groups["CVTRA02Y"][0])
    real = {
        parse_record(record, layout)["DIS-INT-RATE"] for record in read_records(DATA / "discgrp.txt")
    }
    used = {Decimal(row["rate"]) for row in rows}
    used.add(Decimal(oracle["not_computed"][0]["rate"]))
    assert used <= real, f"rates not present in discgrp.txt: {sorted(used - real)}"


def test_the_extreme_rows_are_dailytrans_real_extremes(rows):
    """R7 and R8 claim to use `dailytran.txt`'s largest and smallest amounts. Checked, not asserted."""
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBTRN02C"))
    layout = derive_layout(groups["CVTRA06Y"][0])
    amounts = [
        parse_record(record, layout)["DALYTRAN-AMT"]
        for record in read_records(DATA / "dailytran.txt")
    ]
    by_id = {row["id"]: Decimal(row["balance"]) for row in rows}
    assert by_id["R7"] == max(amounts)
    assert by_id["R8"] == min(amounts)


def test_r1s_balance_is_a_real_account_balance():
    """R1 claims `194.00` is a real `ACCT-CURR-BAL`. `acctdata.txt` is in the fixture so this is checkable."""
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))
    layout = derive_layout(groups["CVACT01Y"][0])
    balances = {
        parse_record(record, layout)["ACCT-CURR-BAL"]
        for record in read_records(DATA / "acctdata.txt")
    }
    assert Decimal("194.00") in balances


def test_the_pics_the_table_states_are_the_pics_the_copybooks_declare(oracle):
    """The scale is the whole answer, so it is read from `pic_mapper` rather than trusted from prose."""
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))
    declared = oracle["source"]["fields"]

    for copybook, field_name in (("CVTRA01Y", "TRAN-CAT-BAL"), ("CVTRA02Y", "DIS-INT-RATE")):
        field = next(f for f in groups[copybook][0] if f.field_name == field_name)
        assert field.precision == declared[field_name]["precision"], field_name
        assert field.scale == declared[field_name]["scale"], field_name
