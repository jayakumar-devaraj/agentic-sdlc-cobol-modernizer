"""Tests for pic_mapper against real CardDemo copybook field declarations.

The field declarations below are transcribed from the actual `carddemo-tenant-service` repo
(verified byte-identical to upstream), fetched directly:

    gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/cpy/CVACT01Y.cpy \\
        --jq '.content' | base64 -d
    gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/cpy/CVTRA05Y.cpy \\
        --jq '.content' | base64 -d
    gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/cpy/CVTRA02Y.cpy \\
        --jq '.content' | base64 -d

confirmed against docs/cobol-construct-support-matrix.md's own verification. `USAGE COMP-3`
handling is exercised with a synthetic declaration -- deliberately, since the construct-support
matrix confirms no Track C copybook declares `COMP-3` or any explicit `USAGE` clause; this
module must still handle it correctly for Track B / any future program.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.tools.pic_mapper import (
    PicFieldType,
    UnsupportedPicConstructError,
    UsageClause,
    map_pic_clause,
)

# --- CVACT01Y.cpy (ACCOUNT-RECORD) -------------------------------------------------------

ACCT_ID = "05  ACCT-ID                           PIC 9(11)."
ACCT_ACTIVE_STATUS = "05  ACCT-ACTIVE-STATUS                PIC X(01)."
ACCT_CURR_BAL = "05  ACCT-CURR-BAL                     PIC S9(10)V99."
ACCT_CREDIT_LIMIT = "05  ACCT-CREDIT-LIMIT                 PIC S9(10)V99."
ACCT_CASH_CREDIT_LIMIT = "05  ACCT-CASH-CREDIT-LIMIT            PIC S9(10)V99."
ACCT_CURR_CYC_CREDIT = "05  ACCT-CURR-CYC-CREDIT              PIC S9(10)V99."
ACCT_CURR_CYC_DEBIT = "05  ACCT-CURR-CYC-DEBIT               PIC S9(10)V99."

# --- CVTRA05Y.cpy (TRAN-RECORD) ----------------------------------------------------------

TRAN_AMT = "05  TRAN-AMT                                PIC S9(09)V99."
TRAN_MERCHANT_ID = "05  TRAN-MERCHANT-ID                        PIC 9(09)."

# --- CVTRA02Y.cpy (DIS-GROUP-RECORD) ------------------------------------------------------

DIS_INT_RATE = "05  DIS-INT-RATE                            PIC S9(04)V99."


def test_acct_id_unsigned_integer():
    result = map_pic_clause(ACCT_ID)
    assert result.field_name == "ACCT-ID"
    assert result.field_type == PicFieldType.NUMERIC
    assert result.precision == 11
    assert result.scale == 0
    assert result.signed is False
    assert result.java_type == "BigDecimal"


def test_acct_active_status_is_string_not_numeric():
    result = map_pic_clause(ACCT_ACTIVE_STATUS)
    assert result.field_name == "ACCT-ACTIVE-STATUS"
    assert result.field_type == PicFieldType.ALPHANUMERIC
    assert result.precision is None
    assert result.scale is None
    assert result.string_length == 1
    assert result.java_type == "String"


@pytest.mark.parametrize(
    "declaration,field_name",
    [
        (ACCT_CURR_BAL, "ACCT-CURR-BAL"),
        (ACCT_CREDIT_LIMIT, "ACCT-CREDIT-LIMIT"),
        (ACCT_CASH_CREDIT_LIMIT, "ACCT-CASH-CREDIT-LIMIT"),
        (ACCT_CURR_CYC_CREDIT, "ACCT-CURR-CYC-CREDIT"),
        (ACCT_CURR_CYC_DEBIT, "ACCT-CURR-CYC-DEBIT"),
    ],
)
def test_acct_signed_decimal_fields(declaration, field_name):
    result = map_pic_clause(declaration)
    assert result.field_name == field_name
    assert result.field_type == PicFieldType.NUMERIC
    assert result.precision == 12
    assert result.scale == 2
    assert result.signed is True
    assert result.java_type == "BigDecimal"


def test_tran_amt_signed_decimal():
    result = map_pic_clause(TRAN_AMT)
    assert result.field_name == "TRAN-AMT"
    assert result.precision == 11
    assert result.scale == 2
    assert result.signed is True


def test_tran_merchant_id_unsigned_integer():
    result = map_pic_clause(TRAN_MERCHANT_ID)
    assert result.field_name == "TRAN-MERCHANT-ID"
    assert result.precision == 9
    assert result.scale == 0
    assert result.signed is False


def test_dis_int_rate_signed_decimal():
    result = map_pic_clause(DIS_INT_RATE)
    assert result.field_name == "DIS-INT-RATE"
    assert result.precision == 6
    assert result.scale == 2
    assert result.signed is True


def test_default_usage_is_display_when_no_usage_clause_present():
    # Verified against docs/cobol-construct-support-matrix.md: none of the four Track C
    # programs' copybooks declare a USAGE clause -- every numeric field is default DISPLAY.
    result = map_pic_clause(ACCT_CURR_BAL)
    assert result.usage == UsageClause.DISPLAY


def test_comp_3_usage_is_detected_and_still_maps_correctly():
    # Synthetic: no Track C fixture exercises COMP-3 (confirmed in the construct-support
    # matrix), but pic_mapper must not silently ignore a USAGE clause just because today's
    # fixtures don't need it -- it must still be identified for Track B / future programs.
    declaration = "05  WS-INTEREST-AMT               PIC S9(7)V99 COMP-3."
    result = map_pic_clause(declaration)
    assert result.usage == UsageClause.COMP_3
    assert result.field_type == PicFieldType.NUMERIC
    assert result.precision == 9
    assert result.scale == 2
    assert result.signed is True


def test_comp_3_usage_written_as_computational_3():
    declaration = "05  WS-INTEREST-AMT               PIC S9(7)V99 COMPUTATIONAL-3."
    result = map_pic_clause(declaration)
    assert result.usage == UsageClause.COMP_3


def test_redefines_is_rejected_not_guessed_at():
    declaration = "05  WS-ALT-VIEW REDEFINES WS-ORIGINAL-FIELD  PIC X(10)."
    with pytest.raises(UnsupportedPicConstructError) as exc_info:
        map_pic_clause(declaration)
    assert exc_info.value.construct == "REDEFINES"


def test_occurs_depending_on_is_rejected_not_guessed_at():
    declaration = "05  WS-TABLE  OCCURS 1 TO 50 TIMES DEPENDING ON WS-COUNT  PIC X(10)."
    with pytest.raises(UnsupportedPicConstructError) as exc_info:
        map_pic_clause(declaration)
    assert exc_info.value.construct == "OCCURS ... DEPENDING ON"


def test_redefines_on_adjacent_sibling_field_is_still_rejected():
    # The ADR-0002 boundary applies to "the same or an adjacent record structure" -- a
    # REDEFINES on a sibling field in the same 01-level group must still block mapping of
    # this field, not just a REDEFINES on the field's own line.
    field_declaration = "05  ACCT-CURR-BAL                     PIC S9(10)V99."
    sibling_with_redefines = "05  ACCT-CURR-BAL-ALT REDEFINES ACCT-CURR-BAL  PIC X(12)."
    with pytest.raises(UnsupportedPicConstructError):
        map_pic_clause(field_declaration, adjacent_text=sibling_with_redefines)


def test_fixed_occurs_is_rejected_because_pic_mapping_cannot_express_cardinality():
    # This REVERSES an earlier decision here (the previous test asserted a fixed OCCURS mapped
    # fine, on the reasoning that it is unambiguous). It is unambiguous -- that was never the
    # problem. The problem is that PicMapping has no cardinality field, so the mapping returned
    # was a correct precision and scale attached to a silently wrong shape: one scalar standing
    # in for a 12-element table. A consumer generating Java from it gets `BigDecimal total`
    # rather than a collection, which compiles and is wrong -- the exact failure mode this
    # module exists to prevent. Rejected until PicMapping can carry cardinality. See ADR-0011.
    declaration = "05  WS-MONTHLY-TOTAL OCCURS 12 TIMES  PIC S9(09)V99."
    with pytest.raises(UnsupportedPicConstructError) as exc_info:
        map_pic_clause(declaration)
    assert exc_info.value.construct == "OCCURS (fixed)"


def test_occurs_depending_on_keeps_its_own_name_rather_than_the_fixed_occurs_one():
    # Specific-before-general ordering: an OCCURS ... DEPENDING ON also matches the plain OCCURS
    # pattern, so without ordering it would be reported under the less informative name.
    declaration = "05  WS-TABLE  OCCURS 1 TO 50 TIMES DEPENDING ON WS-COUNT  PIC X(10)."
    with pytest.raises(UnsupportedPicConstructError) as exc_info:
        map_pic_clause(declaration)
    assert exc_info.value.construct == "OCCURS ... DEPENDING ON"


def test_fixed_occurs_on_the_parent_group_line_still_rejects_its_children():
    # The real shape from CBACT01C's FILE SECTION, verbatim: OCCURS sits on the parent group
    # line and the PIC-carrying children below it have no OCCURS text of their own. Checking
    # only a field's own declaration would map both children as scalars and never notice.
    parent_and_siblings = "\n".join([
        "       01 ARR-ARRAY-REC.",
        "          05  ARR-ACCT-ID                PIC 9(11).",
        "          05  ARR-ACCT-BAL OCCURS 5  TIMES.",
        "            10  ARR-ACCT-CURR-CYC-DEBIT  PIC S9(10)V99",
        "                                         USAGE IS COMP-3.",
    ])
    child = "            10  ARR-ACCT-CURR-BAL        PIC S9(10)V99."
    with pytest.raises(UnsupportedPicConstructError) as exc_info:
        map_pic_clause(child, adjacent_text=parent_and_siblings)
    assert exc_info.value.construct == "OCCURS (fixed)"


def test_no_pic_clause_fails_loudly_rather_than_guessing():
    with pytest.raises(ValueError):
        map_pic_clause("05  WS-SOME-GROUP-LEVEL-FIELD.")


def test_mixed_numeric_and_alphanumeric_tokens_fail_loudly_rather_than_guessing():
    with pytest.raises(ValueError):
        map_pic_clause("05  WS-WEIRD-FIELD  PIC 9(3)X(2).")
