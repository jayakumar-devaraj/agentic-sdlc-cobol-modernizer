"""The rendered `ItemWriter` (G31): the record it builds, the mode it writes in, and its refusals.

**The end-to-end proof is the round trip**, which now runs on rendered writers: the candidate files
it compares are COBOL's own record format, produced by this renderer and parsed with the same layout
the oracle is read with. 500 of 500 transaction fields and 598 of 600 account fields.

**`WRITE` and `REWRITE` are the distinction this module exists to pin.** `CBACT04C` appends interest
transactions and *rewrites* the account master in place. A writer that appended in both cases would
turn an update of fifty accounts into fifty new records, and every record would still be individually
correct -- only the file's length would say otherwise, which is exactly the kind of defect a
field-level differential cannot see.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_writer import (
    UnrenderableWriterError,
    render_item_writer,
    writer_class_name,
)
from tests.system.test_account_break_posting import POSTING
from tests.system.test_account_break_posting import STEP as POSTING_STEP
from tests.system.test_interest_equivalence import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PACKAGE = "com.modernized.batch.writer"
DOMAIN = "com.modernized.batch.domain"


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name="CBACT04C",
                description="Monthly interest calculation.",
                domain_entities=[entity.name for entity in entities],
                steps=[STEP, COMPLETE_STEP, POSTING_STEP],
            )
        ],
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


def render(step, design: UnifiedDesign) -> str:
    return render_item_writer(
        step, design, "CBACT04C", package=PACKAGE, domain_package=DOMAIN
    )


# --- the record it builds -----------------------------------------------------------------------------


def test_the_class_name_is_mechanical():
    assert writer_class_name(COMPLETE_STEP) == "CompleteTransactionItemWriter"


def test_the_transaction_record_is_built_field_by_field_in_record_order(design):
    """Every field at its declared width, from the design's layout -- not from a format string."""
    rendered = render(COMPLETE_STEP, design)
    assert "CobolText.pad(item.tranId(), 16)" in rendered
    assert "CobolRecord.zoned(item.tranAmt(), 11, 2)" in rendered
    assert "CobolText.pad(item.tranDesc(), 100)" in rendered
    # Field order is record order, so the amount comes after the description.
    assert rendered.index("item.tranDesc()") < rendered.index("item.tranAmt()")


def test_the_records_trailing_filler_is_written_as_spaces(design):
    """`CVTRA05Y` declares 350 bytes and its fields account for 330.

    Without the `FILLER`, every record would be twenty bytes short and the file would not be a whole
    number of records -- which the reader's own length check would then reject, one layer too late
    to say why.
    """
    assert "CobolText.spaces(20)" in render(COMPLETE_STEP, design)
    # The account record's fields stop at 122 of 300.
    assert "CobolText.spaces(178)" in render(POSTING_STEP, design)


def test_an_appending_writer_truncates_its_file_first(design):
    """`OPEN OUTPUT` starts an empty file; appending to a stale one would double it on a re-run."""
    rendered = render(COMPLETE_STEP, design)
    assert "Files.deleteIfExists(transact)" in rendered
    assert "StandardOpenOption.APPEND" in rendered


def test_a_rewriting_writer_replaces_by_key_and_keeps_the_file(design):
    """`REWRITE` updates in place: same records, same order, same count.

    Loaded into a `LinkedHashMap` keyed on the record key's own bytes, so an account the job never
    posts survives untouched -- which is what makes this an update rather than a new file that
    happens to have the same rows.
    """
    rendered = render(POSTING_STEP, design)
    assert "LinkedHashMap" in rendered
    assert "CobolRecord.text(existing, 0, 11)" in rendered
    assert "StandardOpenOption.APPEND" not in rendered


def test_rewriting_a_record_that_is_not_there_throws(design):
    """COBOL's `REWRITE` fails on a record it cannot find; silently adding one would grow the file."""
    assert "REWRITE of a record that is not in" in render(POSTING_STEP, design)


def test_the_provenance_names_both_declarations(design):
    """`CLAUDE.md`'s rule: the `SELECT` line and the `WRITE` line that produced this writer."""
    rendered = render(COMPLETE_STEP, design)
    assert "TRANSACT-FILE at line 53" in rendered
    assert "line 500" in rendered


# --- the refusals --------------------------------------------------------------------------------------


def test_a_composite_output_is_refused(design):
    """A composite spans several records and nothing says which file each part belongs to."""
    with pytest.raises(UnrenderableWriterError, match="composite"):
        render(STEP, design)


def test_an_entity_no_declared_file_is_written_from_is_refused(design):
    """A `WRITE` this parse could not attribute to a file leaves the entity with nowhere to go."""
    unwritten = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(update={"written_entity_name": ""})
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableWriterError, match="writes 'Tran' to 0 declared files"):
        render(COMPLETE_STEP, unwritten)


def test_a_rewrite_with_no_key_position_is_refused(design):
    """Without the key's position, a record to replace cannot be found.

    Appending instead would leave the original rows in place and add fifty more -- correct records
    in a wrong file, which the field comparison would not catch.
    """
    keyless = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(update={"key_parts": []})
                if path.written_entity_name == "Account"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableWriterError, match=r"written by key \(replace\)"):
        render(POSTING_STEP, keyless)


def test_an_entity_without_a_record_length_is_refused(design):
    """Nothing would say how long the record it writes should be."""
    unsized = design.model_copy(
        update={
            "domain_entities": [
                entity.model_copy(update={"record_length": None})
                if entity.name == "Tran"
                else entity
                for entity in design.domain_entities
            ]
        }
    )
    with pytest.raises(UnrenderableWriterError, match="no record length"):
        render(COMPLETE_STEP, unsized)


def test_an_output_type_that_is_not_an_entity_is_refused(design):
    unknown = COMPLETE_STEP.model_copy(update={"output_type": "NotAType"})
    with pytest.raises(UnrenderableWriterError, match="no domain entity"):
        render(unknown, design)


def test_a_gap_between_fields_is_written_as_spaces(design):
    """An interior `FILLER` occupies bytes the entity has no field for, and they still have to be
    written.

    None of this corpus's four copybooks has one -- the same luck finding F1 recorded for the reader
    -- so this is synthetic. Without it, every field after the gap would land at the wrong offset in
    the output file while each individual value stayed correct.
    """
    gapped = design.model_copy(
        update={
            "domain_entities": [
                entity.model_copy(
                    update={
                        "fields": [
                            field.model_copy(update={"byte_offset": (field.byte_offset or 0) + 8})
                            if field.cobol_field_name != "TRAN-ID"
                            else field
                            for field in entity.fields
                        ],
                        "record_length": (entity.record_length or 0) + 8,
                    }
                )
                if entity.name == "Tran"
                else entity
                for entity in design.domain_entities
            ]
        }
    )
    rendered = render(COMPLETE_STEP, gapped)
    assert "CobolText.spaces(8)" in rendered
    # And the trailing filler is unchanged, so the gap was added rather than moved.
    assert "CobolText.spaces(20)" in rendered
