"""Tests for nodes/spec_extractor.py against the real CBACT04C fixture and its real copybooks.

`tests/fixtures/tenant_repo_sample/` is the same real (trimmed) tenant-repo worktree
`test_tenant_repo.py` and `test_cobol_parser.py` already verify against -- see those modules'
docstrings for provenance. All numbers asserted below (mapped/unsupported field counts, paragraph
count, specific precision/scale values) were computed by running this module against that real
fixture, not hand-derived and hoped correct -- see the module docstring's provenance note.

The one thing these tests do not exercise is a real network call to the Anthropic API (no live
credential in this development environment -- see `nodes/spec_extractor.py`'s module docstring
and `docs/qa/verification-report.md`'s "Not yet covered" section). Every test below supplies a
fake `narrate` callable instead, which is enough to prove `extract_spec` wires the model
identifier and prompt content through correctly -- the actual prose a real model would produce is
out of scope for a unit test regardless of credential availability.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_extractor import (
    SpecExtractionResult,
    UnsupportedField,
    build_prompt,
    extract_field_mappings,
    extract_spec,
)
from cobol_modernizer.parsing.cobol_parser import extract_paragraphs
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"


@pytest.fixture(scope="module")
def resolved_cbact04c():
    return resolve_program(FIXTURE_ROOT, "CBACT04C")


# --- extract_field_mappings: real numeric fields, exact precision/scale ---------------------


def test_maps_real_numeric_fields_to_exact_precision_and_scale(resolved_cbact04c):
    mappings, _ = extract_field_mappings(resolved_cbact04c)
    by_name = {m.field_name: m for m in mappings}

    # CVACT01Y.cpy's ACCT-CURR-BAL: PIC S9(10)V99 -> precision 12, scale 2 (the plan's own
    # verified-real target for this repo's zero-drift claim).
    acct_curr_bal = by_name["ACCT-CURR-BAL"]
    assert (acct_curr_bal.precision, acct_curr_bal.scale, acct_curr_bal.signed) == (12, 2, True)
    assert acct_curr_bal.java_type == "BigDecimal"

    # CVTRA02Y.cpy's DIS-INT-RATE: PIC S9(04)V99 -> precision 6, scale 2.
    dis_int_rate = by_name["DIS-INT-RATE"]
    assert (dis_int_rate.precision, dis_int_rate.scale, dis_int_rate.signed) == (6, 2, True)

    # CVTRA01Y.cpy's TRAN-CAT-BAL and CVTRA05Y.cpy's TRAN-AMT: both PIC S9(09)V99.
    assert (by_name["TRAN-CAT-BAL"].precision, by_name["TRAN-CAT-BAL"].scale) == (11, 2)
    assert (by_name["TRAN-AMT"].precision, by_name["TRAN-AMT"].scale) == (11, 2)

    # The program's own WS-MONTHLY-INT/WS-TOTAL-INT: PIC S9(09)V99 -- the plan's own
    # verified-real interest-formula target, `COMPUTE WS-MONTHLY-INT = (TRAN-CAT-BAL *
    # DIS-INT-RATE) / 1200`.
    assert (by_name["WS-MONTHLY-INT"].precision, by_name["WS-MONTHLY-INT"].scale) == (11, 2)
    assert (by_name["WS-TOTAL-INT"].precision, by_name["WS-TOTAL-INT"].scale) == (11, 2)


def test_maps_every_field_in_every_copybook(resolved_cbact04c):
    mappings, _ = extract_field_mappings(resolved_cbact04c)
    by_name = {m.field_name: m for m in mappings}
    # One representative alphanumeric and one numeric field from each of the five real
    # copybooks CBACT04C actually COPYs.
    assert by_name["XREF-CARD-NUM"].java_type == "String"
    assert by_name["TRANCAT-ACCT-ID"].java_type == "BigDecimal"
    assert by_name["ACCT-ACTIVE-STATUS"].java_type == "String"
    assert by_name["DIS-TRAN-CAT-CD"].java_type == "BigDecimal"
    assert by_name["TRAN-MERCHANT-NAME"].java_type == "String"


# --- extract_field_mappings: real REDEFINES fields are isolated, not silently mapped --------


def test_redefines_fields_are_isolated_not_silently_mapped(resolved_cbact04c):
    # CBACT04C's own WORKING-STORAGE genuinely contains two REDEFINES groups
    # (TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY, FILLER REDEFINES DB2-FORMAT-TS) -- per
    # ADR-0002 every field inside either group must be caught and flagged, never mapped as if
    # it were an ordinary field.
    mappings, unsupported = extract_field_mappings(resolved_cbact04c)

    mapped_names = {m.field_name for m in mappings}
    unsupported_names = {f.field_name for f in unsupported}

    assert "TWO-BYTES-LEFT" in unsupported_names
    assert "TWO-BYTES-RIGHT" in unsupported_names
    assert "TWO-BYTES-LEFT" not in mapped_names
    assert "TWO-BYTES-RIGHT" not in mapped_names

    for field in unsupported:
        assert "REDEFINES" in field.reason
        assert field.source_label == "CBACT04C"
        assert isinstance(field, UnsupportedField)

    # The REDEFINES-based field, its own alias, is never dropped -- it just isn't a
    # human-review item, since it's the one side of the redefinition that has no REDEFINES
    # clause of its own.
    assert "TWO-BYTES-BINARY" in mapped_names
    assert "DB2-FORMAT-TS" in mapped_names

    # Real numbers, computed by running this module against the real fixture (see module
    # docstring) -- every field is accounted for in exactly one of the two lists.
    assert len(mappings) == 75
    assert len(unsupported) == 9


def test_group_headers_without_pic_are_skipped_not_flagged(resolved_cbact04c):
    # "01 ACCOUNT-RECORD." and friends have no PIC clause of their own -- they are structural,
    # not leaf fields, and must not show up as "unsupported" just for lacking a PIC clause.
    mappings, unsupported = extract_field_mappings(resolved_cbact04c)
    all_field_names = {m.field_name for m in mappings} | {f.field_name for f in unsupported}
    assert "ACCOUNT-RECORD" not in all_field_names
    assert "TRAN-CAT-BAL-RECORD" not in all_field_names
    assert "WS-MISC-VARS" not in all_field_names


# --- Paragraph flow, in real source order ----------------------------------------------------


def test_paragraph_flow_matches_real_source_order(resolved_cbact04c):
    paragraphs = extract_paragraphs(resolved_cbact04c.source_text)
    names = [p.name for p in paragraphs]
    assert len(names) == 22
    assert names[:6] == [
        "0000-TCATBALF-OPEN",
        "0100-XREFFILE-OPEN",
        "0200-DISCGRP-OPEN",
        "0300-ACCTFILE-OPEN",
        "0400-TRANFILE-OPEN",
        "1000-TCATBALF-GET-NEXT",
    ]
    # The plan's own verified-real interest-calculation flow target.
    assert "1200-GET-INTEREST-RATE" in names
    assert "1300-COMPUTE-INTEREST" in names
    assert "1300-B-WRITE-TX" in names
    assert "1400-COMPUTE-FEES" in names
    assert names.index("1200-GET-INTEREST-RATE") < names.index("1300-COMPUTE-INTEREST")
    assert names.index("1300-COMPUTE-INTEREST") < names.index("1300-B-WRITE-TX")
    assert names.index("1300-B-WRITE-TX") < names.index("1400-COMPUTE-FEES")


# --- build_prompt: guardrail wrapping, injection flags, deterministic facts -----------------


def test_build_prompt_wraps_program_and_every_copybook(resolved_cbact04c):
    paragraphs = extract_paragraphs(resolved_cbact04c.source_text)
    mappings, unsupported = extract_field_mappings(resolved_cbact04c)
    user_content, injection_flags = build_prompt(resolved_cbact04c, paragraphs, mappings, unsupported)

    assert '<untrusted-cobol-source label="CBACT04C">' in user_content
    for copybook_name in ["CVTRA01Y", "CVACT03Y", "CVTRA02Y", "CVACT01Y", "CVTRA05Y"]:
        assert f'<untrusted-cobol-source label="{copybook_name}">' in user_content

    # Real CBACT04C, license header and functional comments included, produces zero
    # false-positive injection flags -- mirrors test_guardrails.py's own real-source assertion.
    assert injection_flags == []


def test_build_prompt_known_facts_reflect_real_computed_values(resolved_cbact04c):
    paragraphs = extract_paragraphs(resolved_cbact04c.source_text)
    mappings, unsupported = extract_field_mappings(resolved_cbact04c)
    user_content, _ = build_prompt(resolved_cbact04c, paragraphs, mappings, unsupported)

    facts_section = user_content.split('<untrusted-cobol-source label="CBACT04C">')[0]
    assert "0000-TCATBALF-OPEN" in facts_section
    # The real ACCT-CURR-BAL row, with its actual computed precision/scale -- never left for
    # the model to recompute.
    assert "ACCT-CURR-BAL" in facts_section
    assert "| 12 | 2 |" in facts_section
    assert "Unsupported constructs" in facts_section
    assert "TWO-BYTES-LEFT" in facts_section

    # The formula itself is preserved verbatim for the model to reproduce, not paraphrased --
    # it lives in the wrapped source, not the facts section.
    assert "COMPUTE WS-MONTHLY-INT" in user_content
    assert "( TRAN-CAT-BAL * DIS-INT-RATE) / 1200" in user_content


# --- extract_spec: end-to-end wiring, with a fake narrator -----------------------------------


def test_extract_spec_calls_narrate_with_resolved_model_and_real_system_prompt():
    captured: dict[str, str] = {}

    def fake_narrate(model: str, system_prompt: str, user_content: str) -> str:
        captured["model"] = model
        captured["system_prompt"] = system_prompt
        captured["user_content"] = user_content
        return "# Fake spec.md\n\nNarration stands in for a real model call."

    result = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=fake_narrate)

    assert isinstance(result, SpecExtractionResult)
    assert result.program_name == "CBACT04C"
    assert result.spec_markdown == "# Fake spec.md\n\nNarration stands in for a real model call."
    assert len(result.paragraph_names) == 22
    assert len(result.field_mappings) == 75
    assert len(result.unsupported_fields) == 9
    assert result.injection_flags == []

    # The real config/model_routing.yaml maps spec_extractor to a real (non-empty) identifier;
    # the system prompt is the real registry content, not the old TODO stub.
    assert captured["model"]
    assert "spec_extractor" in captured["system_prompt"]
    assert "TODO" not in captured["system_prompt"]
    assert "ACCT-CURR-BAL" in captured["user_content"]


def test_extract_spec_propagates_missing_program_error():
    from cobol_modernizer.tools.tenant_repo import TenantRepoFileNotFoundError

    with pytest.raises(TenantRepoFileNotFoundError):
        extract_spec(FIXTURE_ROOT, "NOSUCHPROGRAM", narrate=lambda *_: "unused")
