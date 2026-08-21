"""The store a sequential step's reader and writer share (ADR-0040, ADR-0041).

`CBTRN02C` is the program that needs it: its acceptance decision compares a credit limit against
cycle fields its own posting rewrites, so a step processing item *n* must see what items *1..n-1*
wrote (ADR-0039, measured -- 30 of its 43 rejections are ordering, not the transaction).

**What is asserted here is that it invents nothing.** Every offset, width, record length and write
mode below is read out of the design, which read it out of the COBOL. The renderer is given no way
to guess: a key with no byte position is a refusal, not a zero.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_working_set import (
    UnrenderableWorkingSetError,
    read_modify_written,
    render_working_set,
    working_set_class_name,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBTRN02C"
PACKAGE = "com.modernized.batch.state"

STEP = BatchStepDesign(
    step_name="postTransaction",
    source_paragraphs=["1500-B-LOOKUP-ACCT", "2700-UPDATE-TCATBAL", "2800-UPDATE-ACCOUNT-REC"],
    role="processor",
    description="Posts an accepted daily transaction.",
    input_type="Dalytran",
    output_type="Tran",
    guard_condition=None,
    reads_own_writes=True,
)


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name=PROGRAM,
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    return UnifiedDesign(
        domain_entities=build_domain_entities(FIXTURE_ROOT, [entry]),
        composite_types=[],
        batch_jobs=[],
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


@pytest.fixture(scope="module")
def source(design) -> str:
    return render_working_set(STEP, design, PROGRAM, package=PACKAGE)


def test_it_holds_exactly_the_files_the_program_reads_by_key_and_writes_back(design):
    """`ACCOUNT` and `TCATBAL`, and not the other four.

    `DALYTRAN` is the driving stream, `XREF` is read and never written, `TRANSACT` is written and
    never read, and `DALYREJS` is written and out of scope (ADR-0038). Only two files are both.
    """
    assert [path.select_name for path in read_modify_written(design, PROGRAM)] == [
        "ACCOUNT-FILE",
        "TCATBAL-FILE",
    ]


def test_the_class_is_named_for_its_step(design):
    assert working_set_class_name(STEP) == "PostTransactionWorkingSet"
    assert "public class PostTransactionWorkingSet {" in render_working_set(
        STEP, design, PROGRAM, package=PACKAGE
    )


def test_each_entity_is_keyed_where_its_own_record_says(source):
    """Offsets and widths from the design, not from the shape of the name.

    `FD-ACCT-ID` is `PIC 9(11)` at the front of a 300-byte record; `FD-TRAN-CAT-KEY` is three
    components -- 11 + 2 + 4 -- at the front of a 50-byte one. A store that keyed either of them
    wrongly would answer the wrong record to a lookup the acceptance decision depends on.
    """
    assert "CobolRecord.fixedRecords(acctfile, 300)" in source
    assert "accountRecords.put(CobolRecord.text(existing, 0, 11), existing);" in source
    assert "CobolRecord.fixedRecords(tcatbalf, 50)" in source
    assert "tranCatBalRecords.put(CobolRecord.text(existing, 0, 17), existing);" in source


def test_the_accessors_are_named_for_the_entity_rather_than_the_file(source):
    """`tranCatBal`/`putTranCatBal`, not `trancatbal` beside `putTranCatBal`.

    Cosmetic in isolation, and not in aggregate: a body reads these names, and two spellings of one
    entity in one class is the kind of thing a model reasonably gets wrong.
    """
    assert "public String tranCatBal(String key) {" in source
    assert "public void putTranCatBal(String record) {" in source
    assert "public String account(String key) {" in source
    assert "public void putAccount(String record) {" in source
    assert "trancatbal" not in source


def test_the_write_mode_and_both_write_lines_reach_the_generated_provenance(source):
    """An `upsert` cites both statements; a `replace` cites its one (ADR-0037).

    The store treats them alike -- `put` replaces or adds either way -- so the distinction survives
    only in what the class says about itself. A reviewer checking whether creating a row is legal
    here has nothing else to read.
    """
    assert "-- replace (line 554)." in source
    assert "-- upsert (lines 510 and 528)." in source


def test_it_interprets_no_field_and_therefore_holds_no_business_logic(source):
    """Records go in and out as text. Nothing here parses a number or compares one.

    This is the ADR-0019 line stated as an assertion: what a record *becomes* is the processor's
    work. A working set that started computing balances would be business logic nobody reviewed as
    business logic, in a class whose name says it is plumbing.
    """
    assert "BigDecimal" not in source
    assert "CobolRecord.number" not in source
    assert "compareTo" not in source


def test_a_step_whose_program_read_modify_writes_nothing_is_refused(design):
    """The declaration and the access paths have to agree, and the refusal says they do not."""
    appending_only = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(update={"write_mode": "append"})
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableWorkingSetError, match="no file it both reads by key and writes"):
        render_working_set(STEP, appending_only, PROGRAM, package=PACKAGE)


def test_a_key_with_no_byte_position_is_refused_rather_than_defaulted(design):
    """Offset 0 is a plausible guess and would key every record by the wrong bytes.

    Refused for the same reason `java_reader` refuses it: a store that answers the wrong record
    produces a decision that looks considered and is not.
    """
    keyless = design.model_copy(
        update={
            "file_access_paths": [
                path.model_copy(update={"key_parts": []})
                if path.select_name == "ACCOUNT-FILE"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableWorkingSetError, match="nothing says where that key sits"):
        render_working_set(STEP, keyless, PROGRAM, package=PACKAGE)
