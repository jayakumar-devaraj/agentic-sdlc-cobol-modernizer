"""Tests for spec_extractor against the other three real Track C programs.

`test_spec_extractor.py` already verifies `CBACT04C` exhaustively. This file's job is different:
prove `spec_extractor`'s deterministic pipeline (field mapping, paragraph extraction, guardrail
wrapping) generalizes to real, structurally different programs it has never been run against
before -- not repeat `CBACT04C`'s own depth of coverage per program. Each program below exercises
something `CBACT04C` didn't:

- `CBCUS01C` -- the smallest Track C program (5 paragraphs, 1 copybook), a minimal-complexity
  sanity check.
- `CBACT01C` -- `COPY`s `CODATECN`, a copybook with **four** real `REDEFINES` groups (date-format
  conversion aliases) plus a fifth, standalone elementary `REDEFINES`
  (`WS-REISSUE-DATE REDEFINES WS-ACCT-REISSUE-DATE`) that hits `pic_mapper`'s check via its own
  declaration line rather than a sibling's -- neither construct shape appears in `CBACT04C`.
- `CBTRN02C` -- the largest of the four Track C programs (26 paragraphs, 5 copybooks), a real
  daily-transaction-posting program structurally similar to `CBACT04C` but exercising a different
  real copybook (`CVTRA06Y`) and a longer real paragraph flow.

All real numbers below were computed by running this module against the real fixture files
(fetched from `carddemo-tenant-service` and byte-verified -- see `test_tenant_repo.py`'s module
docstring), not hand-derived and hoped correct.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_extractor import build_prompt, extract_field_mappings
from cobol_modernizer.parsing.cobol_parser import extract_paragraphs
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"


# --- CBCUS01C: the smallest Track C program ----------------------------------------------------


def test_cbcus01c_field_mapping_and_paragraph_flow():
    resolved = resolve_program(FIXTURE_ROOT, "CBCUS01C")
    paragraphs = extract_paragraphs(resolved.source_text)
    mappings, unsupported = extract_field_mappings(resolved)

    assert [p.name for p in paragraphs] == [
        "1000-CUSTFILE-GET-NEXT",
        "0000-CUSTFILE-OPEN",
        "9000-CUSTFILE-CLOSE",
        "Z-ABEND-PROGRAM",
        "Z-DISPLAY-IO-STATUS",
    ]

    by_name = {m.field_name: m for m in mappings}
    # CVCUS01Y.cpy's real numeric fields.
    assert (by_name["CUST-ID"].precision, by_name["CUST-ID"].scale) == (9, 0)
    assert (by_name["CUST-SSN"].precision, by_name["CUST-SSN"].scale) == (9, 0)
    assert (by_name["CUST-FICO-CREDIT-SCORE"].precision, by_name["CUST-FICO-CREDIT-SCORE"].scale) == (3, 0)
    assert by_name["CUST-FIRST-NAME"].java_type == "String"

    # Same TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY boilerplate CBACT04C has, reused verbatim
    # across CardDemo's batch programs -- both isolated fields, not silently mapped.
    unsupported_names = {u.field_name for u in unsupported}
    assert unsupported_names == {"TWO-BYTES-LEFT", "TWO-BYTES-RIGHT"}
    for field in unsupported:
        assert "REDEFINES" in field.reason


# --- CBACT01C: CODATECN's four REDEFINES groups + a standalone elementary REDEFINES -----------


def test_cbact01c_field_mapping_and_paragraph_count():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT01C")
    paragraphs = extract_paragraphs(resolved.source_text)
    mappings, _ = extract_field_mappings(resolved)

    assert len(paragraphs) == 16
    assert paragraphs[0].name == "1000-ACCTFILE-GET-NEXT"
    assert paragraphs[-1].name == "9910-DISPLAY-IO-STATUS"

    by_name = {m.field_name: m for m in mappings}
    # CVACT01Y.cpy's fields, same real values test_spec_extractor.py already verifies for CBACT04C
    # -- confirms the same copybook maps identically when COPYd by a different program.
    assert (by_name["ACCT-CURR-BAL"].precision, by_name["ACCT-CURR-BAL"].scale) == (12, 2)
    assert (by_name["ACCT-CREDIT-LIMIT"].precision, by_name["ACCT-CREDIT-LIMIT"].scale) == (12, 2)


def test_cbact01c_isolates_codatecns_four_redefines_groups():
    # CODATECN.cpy has four real REDEFINES groups (CODATECN-1INP/2INP/1OUT/2OUT, each redefining
    # a shared date-string field to reinterpret it under a different format) -- a construct shape
    # CBACT04C's own fixtures never exercise (its REDEFINES groups are simpler: one binary/alpha
    # alias, one timestamp-format alias). Every field inside any of the four groups must be
    # isolated, none silently mapped.
    resolved = resolve_program(FIXTURE_ROOT, "CBACT01C")
    mappings, unsupported = extract_field_mappings(resolved)

    unsupported_names = {u.field_name for u in unsupported}
    for name in [
        "CODATECN-1YYYY",
        "CODATECN-1O-YYYY",
        "CODATECN-2YY",
        "CODATECN-1O-MM",
        "CODATECN-2O-YYYY",
    ]:
        assert name in unsupported_names, f"{name} should be isolated (inside a REDEFINES group)"

    mapped_names = {m.field_name for m in mappings}
    assert unsupported_names.isdisjoint(mapped_names)

    # Real count, computed by running this module against the real fixture. 28 -> 32 under
    # ADR-0011: the four extra are CBACT01C's ARR-ARRAY-REC group (ARR-ACCT-ID,
    # ARR-ACCT-CURR-BAL, ARR-ACCT-CURR-CYC-DEBIT, ARR-FILLER), isolated by the fixed
    # "OCCURS 5 TIMES" rather than by REDEFINES -- they live in the FILE SECTION, which this
    # module could not see before. The 28 CODATECN REDEFINES fields are unchanged.
    assert len(unsupported) == 32
    assert len([u for u in unsupported if "REDEFINES" in u.reason]) == 28


def test_cbact01c_isolates_a_standalone_elementary_redefines():
    # "01 WS-REISSUE-DATE REDEFINES WS-ACCT-REISSUE-DATE PIC X(10)." -- REDEFINES and PIC on the
    # very same declaration line, unlike CBACT04C's REDEFINES headers (which have no PIC of their
    # own and are skipped as structural, with only their *children* isolated). This is the other
    # real shape pic_mapper._check_unsupported_constructs must catch: a field flagged by its own
    # declaration text, not a sibling's.
    resolved = resolve_program(FIXTURE_ROOT, "CBACT01C")
    _, unsupported = extract_field_mappings(resolved)
    matches = [u for u in unsupported if u.field_name == "WS-REISSUE-DATE"]
    assert len(matches) == 1
    assert "REDEFINES" in matches[0].reason


# --- CBTRN02C: the largest Track C program, a new real copybook (CVTRA06Y) --------------------


def test_cbtrn02c_field_mapping_and_paragraph_flow():
    resolved = resolve_program(FIXTURE_ROOT, "CBTRN02C")
    paragraphs = extract_paragraphs(resolved.source_text)
    mappings, unsupported = extract_field_mappings(resolved)

    assert len(paragraphs) == 26
    names = [p.name for p in paragraphs]
    assert names[:6] == [
        "0000-DALYTRAN-OPEN",
        "0100-TRANFILE-OPEN",
        "0200-XREFFILE-OPEN",
        "0300-DALYREJS-OPEN",
        "0400-ACCTFILE-OPEN",
        "0500-TCATBALF-OPEN",
    ]
    # Real transaction-posting flow: validate against xref/account, then post and update balance.
    assert names.index("1500-VALIDATE-TRAN") < names.index("2000-POST-TRANSACTION")
    assert names.index("2000-POST-TRANSACTION") < names.index("2700-UPDATE-TCATBAL")
    assert names.index("2700-UPDATE-TCATBAL") < names.index("2800-UPDATE-ACCOUNT-REC")

    by_name = {m.field_name: m for m in mappings}
    # CVTRA06Y.cpy's DALYTRAN-AMT -- the one field genuinely new to this fixture set (not COPYd
    # by CBACT04C or the other two programs).
    assert (by_name["DALYTRAN-AMT"].precision, by_name["DALYTRAN-AMT"].scale) == (11, 2)
    assert by_name["DALYTRAN-AMT"].java_type == "BigDecimal"
    # CVTRA05Y.cpy's TRAN-AMT and CVTRA01Y.cpy's TRAN-CAT-BAL, shared with CBACT04C.
    assert (by_name["TRAN-AMT"].precision, by_name["TRAN-AMT"].scale) == (11, 2)
    assert (by_name["TRAN-CAT-BAL"].precision, by_name["TRAN-CAT-BAL"].scale) == (11, 2)

    # Real count, computed by running this module against the real fixture. 88 -> 102 under
    # ADR-0011 (CBTRN02C's five FD record layouts); it has no LINKAGE SECTION and no fixed
    # OCCURS, so the unsupported count is unchanged.
    assert len(mappings) == 102
    assert len(unsupported) == 9


def test_cbtrn02c_build_prompt_wraps_every_real_source_unit_with_no_injection_flags():
    resolved = resolve_program(FIXTURE_ROOT, "CBTRN02C")
    paragraphs = extract_paragraphs(resolved.source_text)
    mappings, unsupported = extract_field_mappings(resolved)
    user_content, injection_flags = build_prompt(resolved, paragraphs, mappings, unsupported)

    assert '<untrusted-cobol-source label="CBTRN02C">' in user_content
    for copybook_name in ["CVTRA06Y", "CVTRA05Y", "CVACT03Y", "CVACT01Y", "CVTRA01Y"]:
        assert f'<untrusted-cobol-source label="{copybook_name}">' in user_content
    assert injection_flags == []


# --- Cross-program sanity: every real Track C program parses without raising ------------------


@pytest.mark.parametrize("program_name", ["CBCUS01C", "CBACT01C", "CBTRN02C", "CBACT04C"])
def test_every_track_c_program_resolves_and_maps_without_raising(program_name):
    resolved = resolve_program(FIXTURE_ROOT, program_name)
    extract_paragraphs(resolved.source_text)
    mappings, unsupported = extract_field_mappings(resolved)
    assert mappings  # every real Track C program has at least one mappable field
    assert isinstance(unsupported, list)  # may be empty in principle; never raises to get here
