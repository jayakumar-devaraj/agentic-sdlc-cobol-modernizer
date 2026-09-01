"""Byte offsets and record lengths (G31 finding F1), checked against three independent sources.

**The layouts here are not asserted against numbers this module invented.** Three things already
know where these fields sit, and they were derived separately:

1. `test_cobol_oracle_comparison.TRAN_LAYOUT` and `ACCOUNT_LAYOUT` -- hand-written offsets, and
   **verified against COBOL's own output**: the differential built on them matches `transact.dat`
   500 of 500 fields and `acctdata-posted.dat` 598 of 600. Offsets that were wrong would have made
   that impossible.
2. The copybooks' own `RECLN` comments, written by whoever wrote the record.
3. The hand-written wiring's Java constants, which the round trip runs through real Maven.

Agreement between a computation and one hand-derivation is weak evidence; agreement with a
hand-derivation *that a differential against real COBOL output already validated* is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import ProgramDesignEntry
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.parsing.record_layout import (
    UnsupportedRecordLayoutError,
    compute_record_layouts,
    display_width,
)
from cobol_modernizer.tools.pic_mapper import map_pic_clause
from tests.unit.test_cobol_oracle_comparison import ACCOUNT_LAYOUT, TRAN_LAYOUT

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
CPY = FIXTURE_ROOT / "app" / "cpy"


def layout_of(copybook: str):
    layouts = compute_record_layouts((CPY / f"{copybook}.cpy").read_text(encoding="latin-1"))
    assert len(layouts) == 1, f"{copybook} should hold exactly one 01-level record"
    return layouts[0]


# --- against the differential's own layouts ----------------------------------------------------------


@pytest.mark.parametrize(
    "copybook, reference",
    [("CVTRA05Y", TRAN_LAYOUT), ("CVACT01Y", ACCOUNT_LAYOUT)],
)
def test_computed_offsets_match_the_layouts_the_differential_validated(copybook, reference):
    """The check that makes this trustworthy rather than merely self-consistent.

    Every field in the reference layout must sit at the computed offset and be the computed width.
    Those references are what `compare()` reads `transact.dat` and `acctdata-posted.dat` with, and
    they agree with COBOL's own output on 1,098 fields -- so a disagreement here is this module
    being wrong, not the reference.
    """
    computed = {field.field_name: field for field in layout_of(copybook).fields}
    for name, offset, width, _scale in reference:
        assert name in computed, f"{name} is in the differential's layout and not in the computed one"
        assert computed[name].byte_offset == offset, name
        assert computed[name].byte_width == width, name


def test_the_record_lengths_match_what_the_copybooks_themselves_declare():
    """`RECLN` in each copybook's comment header -- a third source, written by hand years ago.

    Read out of the comment rather than hardcoded here, so this compares two derivations instead of
    restating one.
    """
    for copybook in ("CVTRA01Y", "CVTRA02Y", "CVACT01Y", "CVACT03Y", "CVTRA05Y"):
        text = (CPY / f"{copybook}.cpy").read_text(encoding="latin-1")
        declared = re.search(r"RECLN\s*=?\s*(\d+)", text, re.IGNORECASE)
        assert declared is not None, f"{copybook} states no RECLN"
        assert layout_of(copybook).record_length == int(declared.group(1)), copybook


def test_the_fields_tile_the_record_with_no_gaps_and_no_overlap():
    """Offsets are only meaningful if they are a partition: contiguous, ordered, summing to length.

    A layout that skipped a `FILLER` would still produce plausible-looking offsets for the fields
    before it -- and this is what catches that, since the total would then fall short of `RECLN`.
    """
    for copybook in ("CVTRA01Y", "CVTRA02Y", "CVACT01Y", "CVACT03Y", "CVTRA05Y"):
        layout = layout_of(copybook)
        position = 0
        for field in layout.fields:
            assert field.byte_offset == position, f"{copybook}: gap or overlap at {field}"
            position += field.byte_width
        assert position == layout.record_length


def test_filler_occupies_bytes_and_is_not_a_field():
    """`FILLER` is padding, so it shifts what follows and appears in no entity.

    Both halves matter: counted in the layout, absent from `DomainEntity.fields`.
    """
    layout = layout_of("CVTRA01Y")
    fillers = [field for field in layout.fields if field.is_filler]
    assert [f.byte_offset for f in fillers] == [28]
    assert [f.byte_width for f in fillers] == [22]
    assert all(field.field_name is None for field in fillers)


def test_an_interior_filler_shifts_every_field_after_it():
    """The case the hand-written reader would have got wrong, and the reason F1 was a finding.

    None of the four copybooks used has an interior `FILLER`, so the contiguous-from-zero assumption
    held by luck. This is what that luck running out looks like.
    """
    record = """\
       01  PADDED-RECORD.
           05  FIRST-FIELD      PIC X(04).
           05  FILLER           PIC X(06).
           05  SECOND-FIELD     PIC 9(03).
"""
    layout = compute_record_layouts(record)[0]
    by_name = {field.field_name: field for field in layout.fields}
    assert by_name["FIRST-FIELD"].byte_offset == 0
    assert by_name["SECOND-FIELD"].byte_offset == 10, "the FILLER before it was not counted"
    assert layout.record_length == 13


def test_a_group_item_contributes_no_bytes_of_its_own():
    """`05 TRAN-CAT-KEY.` owns its children's bytes, not additional ones.

    Counting the group as well would double every byte under it, and the first symptom would be a
    record longer than its own `RECLN`.
    """
    layout = layout_of("CVTRA01Y")
    assert "TRAN-CAT-KEY" not in {field.field_name for field in layout.fields}
    assert layout.fields[0].field_name == "TRANCAT-ACCT-ID"
    assert layout.record_length == 50


# --- the refusals ------------------------------------------------------------------------------------


def test_a_packed_or_binary_field_is_refused_rather_than_sized_as_digits():
    """`COMP-3` holds two digits per byte, so treating it as one would misplace everything after it.

    No record in this corpus uses one, which is exactly why the refusal needs a test: the first
    tenant record that does would otherwise produce a layout that is wrong and looks fine.
    """
    mapping = map_pic_clause("05  WS-PACKED  PIC S9(7) COMP-3.")
    with pytest.raises(UnsupportedRecordLayoutError, match="USAGE"):
        display_width(mapping)


def test_a_record_containing_a_field_pic_mapper_rejects_is_refused_whole():
    """A layout missing one width is not partial -- it is wrong for every field that follows.

    `REDEFINES` is the construct ADR-0002 already routes to a human, and a record carrying one
    cannot be sized at all.
    """
    record = """\
       01  OVERLAID-RECORD.
           05  RAW-DATE         PIC X(10).
           05  DATE-PARTS REDEFINES RAW-DATE.
               10  DATE-YEAR    PIC X(04).
"""
    with pytest.raises(UnsupportedRecordLayoutError, match="every offset after it"):
        compute_record_layouts(record)


def test_a_copybook_with_no_record_yields_no_layout():
    """`CODATECN` maps zero fields and is not an entity; it must not yield a phantom record."""
    assert compute_record_layouts("      * just a comment\n") == []


# --- what reaches the design -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def entities():
    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    return build_domain_entities(FIXTURE_ROOT, [entry])


def test_every_entity_carries_its_record_length_and_every_field_its_offset(entities):
    """The fact reaching the contract, which is the whole point of computing it.

    G21 and its four recurrences were all *"a computed fact this repo holds and never hands over"*,
    so the assertion that matters is not that the layout is right but that it arrives.
    """
    assert entities
    for entity in entities:
        assert entity.record_length and entity.record_length > 0, entity.name
        for field in entity.fields:
            assert field.byte_offset is not None, f"{entity.name}.{field.cobol_field_name}"
        offsets = [field.byte_offset for field in entity.fields]
        assert offsets == sorted(offsets), "fields are out of record order"


def test_the_entity_offsets_agree_with_the_hand_written_readers_constants(entities):
    """The wiring the round trip actually runs uses these numbers, written out by hand.

    `TranCatBalWithRateItemReader` slices `tcatbal-posted.dat` at 0/11/13/17 and `acctdata` at
    0/11/12/24..., and that reader produced a candidate matching COBOL's own output. If the design's
    offsets disagree with it, one of the two is wrong and the round trip is the arbiter.
    """
    by_name = {entity.name: entity for entity in entities}
    tran_cat_bal = {f.cobol_field_name: f.byte_offset for f in by_name["TranCatBal"].fields}
    assert tran_cat_bal == {
        "TRANCAT-ACCT-ID": 0,
        "TRANCAT-TYPE-CD": 11,
        "TRANCAT-CD": 13,
        "TRAN-CAT-BAL": 17,
    }
    assert by_name["TranCatBal"].record_length == 50

    account = {f.cobol_field_name: f.byte_offset for f in by_name["Account"].fields}
    assert account["ACCT-GROUP-ID"] == 112, "the field the rate lookup keys on"
    assert by_name["Account"].record_length == 300


def test_offset_of_returns_none_for_a_field_the_record_does_not_have():
    """A lookup that answered `0` for an unknown field would place it at the start of the record."""
    assert layout_of("CVTRA01Y").offset_of("NO-SUCH-FIELD") is None
    assert layout_of("CVTRA01Y").offset_of("TRAN-CAT-BAL") == 17


def test_an_alphanumeric_with_no_declared_length_is_refused():
    """A `PIC X` this module cannot size is refused rather than assumed to be one byte."""
    mapping = map_pic_clause("05  ODD-FIELD  PIC X(4).").model_copy(
        update={"string_length": None}
    )
    with pytest.raises(UnsupportedRecordLayoutError, match="no declared length"):
        display_width(mapping)


def test_a_numeric_with_no_precision_is_refused():
    mapping = map_pic_clause("05  ODD-NUMBER  PIC 9(4).").model_copy(update={"precision": None})
    with pytest.raises(UnsupportedRecordLayoutError, match="no precision"):
        display_width(mapping)


def test_a_field_declared_before_any_01_level_is_not_placed_in_a_record():
    """A fragment whose fields precede any record header belongs to no record this can describe.

    Skipped rather than invented into one: a record boundary guessed here would put real fields at
    offsets measured from nothing.
    """
    fragment = """           05  ORPHAN-FIELD     PIC X(04).
       01  REAL-RECORD.
           05  FIRST-FIELD      PIC X(02).
"""
    layouts = compute_record_layouts(fragment)
    assert [layout.record_name for layout in layouts] == ["REAL-RECORD"]
    assert [field.field_name for field in layouts[0].fields] == ["FIRST-FIELD"]
