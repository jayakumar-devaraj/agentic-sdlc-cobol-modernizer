"""The rendered `ItemReader` (G31): what it emits, and everything it refuses to guess.

**The end-to-end proof is elsewhere.** `test_hand_written_round_trip` builds this reader into a real
Maven project, runs it over the oracle's inputs, and compares what comes out against what the
unmodified `CBACT04C` wrote -- 500 of 500 transaction fields and 598 of 600 account fields, the same
numbers the hand-written reader produced before it was deleted. That is the measurement; this module
is about the shape of the output and, mostly, about the refusals.

**Why the refusals get more tests than the output.** A reader that guesses a key, an offset or a
lookup order compiles, runs, and produces records that differ from COBOL's in ways only a
differential catches. Every fact this renderer needs is one the COBOL states, so a missing one means
something was not parsed -- and the honest response is a refusal that names it, not a default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    CompositeComponent,
    CompositeType,
    LookupKeyPart,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_reader import (
    UnrenderableReaderError,
    reader_class_name,
    render_item_reader,
)
from tests.support.interest_design import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PACKAGE = "com.modernized.batch.reader"
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
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name="CBACT04C",
                description="Monthly interest calculation.",
                domain_entities=[entity.name for entity in entities],
                steps=[STEP, COMPLETE_STEP],
            )
        ],
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


def render(design: UnifiedDesign) -> str:
    return render_item_reader(
        STEP, design, "CBACT04C", package=PACKAGE, domain_package=DOMAIN
    )


# --- what it emits -----------------------------------------------------------------------------------


def test_the_class_name_is_mechanical(design):
    assert reader_class_name(STEP) == "ComputeInterestItemReader"
    assert "public class ComputeInterestItemReader implements ItemReader<" in render(design)


def test_the_driving_file_is_read_at_its_own_record_length(design):
    """`TCATBAL` is walked in order, 50 bytes at a time -- the length from `DomainEntity`."""
    assert "CobolRecord.fixedRecords(tcatbalf, 50)" in render(design)


def test_each_lookup_is_indexed_by_the_key_bytes_the_program_reads_by(design):
    """`XREF` is keyed at offset 25 -- the *alternate* key, not the declared record key at 0.

    This is finding F2 arriving in generated code: a reader built on the declared key would index on
    the card number, compile, and find nothing, because the account id is what `CBACT04C` has.
    """
    rendered = render(design)
    assert "cardxrefRecords.put(CobolRecord.text(row, 25, 11), row)" in rendered
    assert "accountRecords.put(CobolRecord.text(row, 0, 11), row)" in rendered
    # The composite key is one slice spanning its three components: 10 + 2 + 4.
    assert "disgroupRecords.put(CobolRecord.text(row, 0, 16), row)" in rendered


def test_the_default_fallback_is_rendered_as_a_second_attempt(design):
    """Finding F4 -- the `'DEFAULT'` retry -- as generated code rather than a note in a README.

    The COBOL re-reads under `'DEFAULT'` on file status 23, which is a null lookup here. The literal
    is padded to the key's declared width, because a ten-byte key field holds `DEFAULT` plus three
    spaces and a seven-character probe matches nothing.
    """
    rendered = render(design)
    assert "if (disgroupRecord == null)" in rendered
    assert 'CobolText.pad("DEFAULT", 10)' in rendered


def test_the_account_lookup_is_rendered_before_the_group_that_depends_on_it(design):
    """Ordering, derived from which record each key source belongs to.

    `DISCGRP`'s key is filled from `ACCT-GROUP-ID`, a field of the account record, so the account
    read has to come first. Nothing declares that; it falls out of the sources.
    """
    rendered = render(design)
    assert rendered.index("String accountRecord =") < rendered.index("String disgroupRecord =")
    assert "CobolRecord.text(accountRecord, 112, 10)" in rendered


def test_a_missing_lookup_row_throws_rather_than_substituting(design):
    """The COBOL abends on a failed keyed read; a default would post interest against nothing."""
    rendered = render(design)
    assert "require(accountRecord," in rendered
    assert "throw new IllegalStateException(message)" in rendered


def test_the_provenance_names_the_declarations_it_came_from(design):
    """`CLAUDE.md`'s rule: a generated artifact traces to the source lines it was derived from."""
    rendered = render(design)
    assert "TCATBAL-FILE (line 28)" in rendered
    assert "CBACT04C" in rendered


# --- the refusals ------------------------------------------------------------------------------------


def test_a_component_with_no_access_path_is_refused(design):
    """An entity the program declares no way to read cannot be read, and saying so is the answer."""
    stripped = design.model_copy(
        update={
            "file_access_paths": [
                path for path in design.file_access_paths if path.entity_name != "Account"
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="0 access paths yielding 'Account'"):
        render(stripped)


def test_a_lookup_whose_key_nothing_fills_is_refused(design):
    """A key with no source is a join this renderer cannot write, and guessing one is the whole risk.

    ADR-0030 refused an LLM-declared join for exactly this: a wrong one produces plausible rows.
    """
    blinded = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(update={"key_parts": []})
                if path.entity_name == "Account"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="nothing in the program fills that key"):
        render(blinded)


def test_a_key_whose_byte_position_is_unknown_is_refused(design):
    """Without the key's position in its own record, the map would be indexed on the wrong bytes."""
    lost = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(
                    update={
                        "key_parts": [
                            part.model_copy(update={"key_offset": None, "key_width": None})
                            for part in path.key_parts
                        ]
                    }
                )
                if path.entity_name == "Account"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="no byte position"):
        render(lost)


def test_a_width_mismatch_between_a_key_and_its_source_is_refused(design):
    """A `MOVE` between fields of different widths pads or truncates, and this renders a copy.

    Rendering it anyway would produce a lookup that looks right and matches nothing -- the exact
    failure the differential exists to catch, arriving from a fact that was available all along.
    """
    mismatched = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(
                    update={
                        "key_parts": [
                            part.model_copy(update={"key_width": 7}) for part in path.key_parts
                        ]
                    }
                )
                if path.entity_name == "Account"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="pads or truncates"):
        render(mismatched)


def test_a_key_source_from_a_record_this_step_never_reads_is_refused(design):
    """A join onto a record the reader does not have is unorderable, and it says so rather than
    emitting the lookup last and hoping."""
    dangling = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(
                    update={
                        "key_parts": [
                            LookupKeyPart(
                                key_field="FD-ACCT-ID",
                                source_field="CUST-FIRST-NAME",
                                key_offset=0,
                                key_width=11,
                                source_line=1,
                            )
                        ]
                    }
                )
                if path.entity_name == "Account"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="cannot be ordered"):
        render(dangling)


def test_an_input_with_no_driving_stream_is_refused(design):
    """A reader with nothing to iterate has no records; one with two streams has no defined order."""
    lookups_only = design.model_copy(
        update={
            "composite_types": [
                CompositeType(
                    name="TranCatBalWithRate",
                    components=[
                        CompositeComponent(field_name="account", entity_name="Account"),
                        CompositeComponent(field_name="cardXref", entity_name="CardXref"),
                    ],
                ),
                OUTPUT_COMPOSITE,
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="exactly one driving stream"):
        render(lookups_only)


def test_an_entity_without_a_record_length_is_refused(design):
    """Nothing would say where one record ends and the next begins."""
    unsized = design.model_copy(
        update={
            "domain_entities": [
                entity.model_copy(update={"record_length": None})
                if entity.name == "TranCatBal"
                else entity
                for entity in design.domain_entities
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="no record length"):
        render(unsized)


def test_an_input_type_that_is_neither_entity_nor_composite_is_refused(design):
    unknown = STEP.model_copy(update={"input_type": "NotAType"})
    with pytest.raises(UnrenderableReaderError, match="neither a domain entity"):
        render_item_reader(unknown, design, "CBACT04C", package=PACKAGE, domain_package=DOMAIN)


def test_a_step_whose_input_is_a_plain_entity_renders_a_stream_reader(design):
    """Not every input is a composite. A bare entity is a reader over one file and no lookups."""
    plain = STEP.model_copy(update={"input_type": "TranCatBal", "step_name": "readBalances"})
    rendered = render_item_reader(
        plain, design, "CBACT04C", package=PACKAGE, domain_package=DOMAIN
    )
    assert "CobolRecord.fixedRecords(tcatbalf, 50)" in rendered
    # No lookup maps: the assertion is about the *fields*, since `drivingRecords.get(next++)` is
    # the stream's own cursor and matching on `Records.get(` would have caught that instead.
    assert "private final Map<String, String>" not in rendered
    assert "drivingRecords.get(next++)" in rendered


def test_a_composite_naming_an_entity_the_design_does_not_have_is_refused(design):
    """`resolve_type` would catch this earlier; the renderer refuses rather than assuming it did."""
    ghost = design.model_copy(
        update={
            "composite_types": [
                CompositeType(
                    name="TranCatBalWithRate",
                    components=[
                        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
                        CompositeComponent(field_name="ghost", entity_name="NoSuchEntity"),
                    ],
                ),
                OUTPUT_COMPOSITE,
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="access paths yielding 'NoSuchEntity'"):
        render(ghost)


def test_a_field_with_neither_length_nor_precision_is_refused(design):
    """Its width in the record is unknown, so every field after it would be sliced at the wrong
    offset."""
    widthless = design.model_copy(
        update={
            "domain_entities": [
                entity.model_copy(
                    update={
                        "fields": [
                            field.model_copy(update={"length": None, "precision": None})
                            if field.cobol_field_name == "TRANCAT-TYPE-CD"
                            else field
                            for field in entity.fields
                        ]
                    }
                )
                if entity.name == "TranCatBal"
                else entity
                for entity in design.domain_entities
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="neither a length nor a precision"):
        render(widthless)


def test_an_entity_field_without_a_byte_offset_is_refused(design):
    """Layouts are all-or-nothing per record; one missing offset makes the record unreadable."""
    offsetless = design.model_copy(
        update={
            "domain_entities": [
                entity.model_copy(
                    update={
                        "fields": [
                            field.model_copy(update={"byte_offset": None})
                            for field in entity.fields
                        ]
                    }
                )
                if entity.name == "TranCatBal"
                else entity
                for entity in design.domain_entities
            ]
        }
    )
    with pytest.raises(UnrenderableReaderError, match="no byte offset"):
        render(offsetless)
