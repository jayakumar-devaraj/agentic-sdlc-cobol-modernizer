"""Does the renderer generalise, or was it built to the shape of one program?

`rendering/` was written while `CBACT04C` was the only program looking at it (G31, PRs #63-#70).
Every fact it consumes -- `FILE-CONTROL`, record layouts, join predicates, write modes -- was added
because that program needed it, so "the renderer works" and "the renderer works on `CBACT04C`" have
been the same sentence. `CBTRN02C` is the second program with real business logic (G17), it reaches
six files where `CBACT04C` reaches four, and it is the only Track C program that writes a file two
different ways. This module renders its reader and its writers and records what happens -- the
answer, measured rather than argued, being **four of five render and the fifth refuses by name**.

**One of the four rendered wrongly, and that was the finding.** `TCATBAL-FILE` is written by `WRITE`
at line 510 and `REWRITE` at line 528: the program creates a balance row when the lookup finds none
and updates it when it does. `extract_write_bindings` finds both -- deliberately, with
`test_file_control.test_a_file_written_both_ways_keeps_both_bindings` saying why -- and
`build_file_access_paths` then kept `first_write` only, so `design.json` carried the `WRITE` and
lost the `REWRITE`. The rendered writer appended.

**What that cost, in the oracle's own numbers**: 50 balance rows are loaded, `CBTRN02C` creates 44
more, and the file it leaves has 94 (`tools/cobol-oracle/run-oracle.sh` asserts exactly this). An
appending writer over the same input leaves **144** -- the original 50, plus 94 written on top --
and *every one of the 144 records is individually correct*. No field comparison sees it. Only the
row count does, which is the failure mode `java_writer`'s own module docstring exists to prevent,
arriving through the contract rather than through the renderer.

**Fixed by `write_mode` (schema 3.7.0), derived from every binding rather than the first**:
`append`, `replace`, or `upsert`. The mode is a fact about the program, so summarising it from one
of its two statements was the defect -- the same shape as G21, G24 and G28, where a fact the
parser already held was dropped one step before its consumer. `CBACT04C` could never have shown it:
each of its files is written exactly one way.

**The fifth is refused rather than rendered**, and stays refused by decision -- see the reject test
below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_job import render_job_configuration
from cobol_modernizer.rendering.java_reader import render_item_reader
from cobol_modernizer.rendering.java_writer import (
    UnrenderableWriterError,
    render_item_writer,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBTRN02C"

#: The driving file: `DALYTRAN` is read sequentially and every other file is reached from it.
DRIVING_ENTITY = "Dalytran"


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    """`CBTRN02C`'s entities and access paths, built the way `design` builds them.

    No steps and no composites: what is under test is whether the *file* facts a renderer needs
    survive the trip into the contract for a program nobody rendered before.
    """
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


def _step(step_name: str, output_type: str) -> BatchStepDesign:
    """A step declaring one output, which is all either renderer reads off it here."""
    return BatchStepDesign(
        step_name=step_name,
        source_paragraphs=["1000-DALYTRAN-GET-NEXT"],
        role="processor",
        description=f"writes {output_type}",
        input_type=DRIVING_ENTITY,
        output_type=output_type,
        guard_condition=None,
    )


#: Where a rendered working set lands. Passed everywhere a sequential step is rendered, because a
#: reader or writer referring to that class unqualified would compile only by accident of packaging.
WORKING_SET_PACKAGE = "com.modernized.batch.state"


def _render_writer(design: UnifiedDesign, step_name: str, output_type: str) -> str:
    return render_item_writer(
        _step(step_name, output_type),
        design,
        PROGRAM,
        package="com.modernized.batch.writer",
        domain_package="com.modernized.domain",
        working_set_package=WORKING_SET_PACKAGE,
    )


def _path(design: UnifiedDesign, select_name: str):
    return next(p for p in design.file_access_paths if p.select_name == select_name)


def test_the_reader_renders_for_a_program_it_was_not_built_against(design):
    """`DALYTRAN` is a plain sequential read, and the reader renderer handles it unchanged.

    Worth asserting rather than assuming: `CBACT04C`'s driving file is `TCATBAL`, an indexed file
    read sequentially, so this is the first time the renderer has been given an `ORGANIZATION
    SEQUENTIAL` stream as the thing a job iterates.
    """
    source = render_item_reader(
        _step("postTransaction", "Tran"),
        design,
        PROGRAM,
        package="com.modernized.batch.reader",
        domain_package="com.modernized.domain",
    )
    assert "class PostTransactionItemReader" in source
    assert "DALYTRAN-FILE" in source, "the reader should cite the file it was rendered from"


def test_the_transaction_writer_appends_because_the_file_is_opened_output(design):
    """`TRANSACT-FILE` is `OPEN OUTPUT` and written once, at line 564. Appending is correct here."""
    source = _render_writer(design, "postTransaction", "Tran")
    assert "StandardOpenOption.APPEND" in source
    assert "WRITE: each record is appended" in source


def test_the_account_writer_replaces_by_key_because_the_account_file_is_rewritten(design):
    """`ACCOUNT-FILE` is `REWRITE`n at line 554, and the renderer picks the update form.

    The same shape `CBACT04C` exercises, asserted again here because it is reached through a
    *different* key path -- `FD-ACCT-ID` filled from `XREF-ACCT-ID`, a lookup this program does and
    that one does not.
    """
    source = _render_writer(design, "updateAccount", "Account")
    assert "REWRITE of a record that is not in " in source
    assert "StandardOpenOption.APPEND" not in source


def test_the_balance_writer_creates_or_updates_because_the_program_does_both(design):
    """`TCATBAL-FILE` is `WRITE`n at 510 and `REWRITE`n at 528, so its mode is `upsert`.

    **This is the case that found the defect.** The design used to keep whichever binding appeared
    first, which said `append` -- and over the oracle's 50 loaded rows an appending writer leaves
    144 where `CBTRN02C` leaves 94, every record individually correct and only the count wrong.
    """
    path = _path(design, "TCATBAL-FILE")
    assert path.written_entity_name == "TranCatBal"
    assert path.write_mode == "upsert"
    assert path.write_lines == [510, 528], "both statements, in source order"

    source = _render_writer(design, "updateCategoryBalance", "TranCatBal")
    assert "StandardOpenOption.APPEND" not in source
    assert "records.put(key, record)" in source, "replaced when present, added when not"
    assert "REWRITE of a record that is not in " not in source, (
        "the absent-key guard belongs to `replace` alone -- rendered here it would abend on the "
        "first of the 44 rows this program creates"
    )
    assert "at lines 510 and 528" in source, (
        "provenance cites both statements; citing the create alone is what made a create-or-update "
        "look like an append"
    )


def test_the_reject_writer_refuses_by_name_rather_than_inventing_a_type(design):
    """`DALYREJS` is written from `REJECT-RECORD`, which is `WORKING-STORAGE`, not a copybook.

    ADR-0010 promotes copybook-sourced fields only, so no `Reject` entity exists and none should be
    invented here -- 43 of the corpus's 300 daily transactions are rejects, so this is a real output
    of the program and not an edge case. The refusal is the designed behaviour and it names the
    missing thing, which is what makes it actionable; it is the same boundary the master plan's open
    issue 11 records for `FD` layouts generally.
    """
    with pytest.raises(UnrenderableWriterError) as raised:
        _render_writer(design, "rejectTransaction", "Reject")
    assert "Reject" in str(raised.value)

    assert not [e for e in design.domain_entities if e.name == "Reject"]
    assert _path(design, "DALYREJS-FILE").written_entity_name == "Reject", (
        "the file access path names the entity the program writes even though the design has no "
        "such type -- which is precisely what makes the refusal possible"
    )


# --- the composite writer a sequential step needs (ADR-0041) --------------------------------------

POSTING_RESULT = CompositeType(
    name="PostingResult",
    components=[
        CompositeComponent(field_name="tran", entity_name="Tran"),
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
    ],
)


def _sequential_design(design: UnifiedDesign) -> UnifiedDesign:
    return design.model_copy(update={"composite_types": [POSTING_RESULT]})


def _sequential_step() -> BatchStepDesign:
    return _step("postTransaction", "PostingResult").model_copy(
        update={"reads_own_writes": True}
    )


def test_a_composite_output_sends_each_component_where_its_own_file_says(design):
    """One item, three records, three destinations -- and none of them chosen by this renderer.

    `CBTRN02C` posts a transaction, a balance and an account from one daily record. Splitting that
    across three steps would re-decide acceptance three times against three different states, so the
    step produces all three and this writer routes them by the access path each entity already has.
    """
    source = render_item_writer(
        _sequential_step(),
        _sequential_design(design),
        PROGRAM,
        package="com.modernized.batch.writer",
        domain_package="com.modernized.domain",
        working_set_package=WORKING_SET_PACKAGE,
    )

    # Appended, because TRANSACT-FILE is OPEN OUTPUT and written once.
    assert "tranBatch.append(" in source
    assert "StandardOpenOption.APPEND" in source
    # Into the shared store, because these two are read back by the step's own decision.
    assert "state.putAccount(" in source
    assert "state.putTranCatBal(" in source
    # Each component's fields are reached through the component, not off the item.
    assert "item.tran().tranAmt()" in source
    assert "item.account().acctCurrCycCredit()" in source


def test_the_composite_writer_cites_where_every_component_goes(design):
    """A reviewer reading the class has to be able to see all three destinations and their modes."""
    source = render_item_writer(
        _sequential_step(),
        _sequential_design(design),
        PROGRAM,
        package="com.modernized.batch.writer",
        domain_package="com.modernized.domain",
        working_set_package=WORKING_SET_PACKAGE,
    )
    assert "<li>Tran -> TRANSACT-FILE -- append, line 564</li>" in source
    assert "<li>Account -> ACCOUNT-FILE -- replace, line 554</li>" in source
    assert "<li>TranCatBal -> TCATBAL-FILE -- upsert, lines 510 and 528</li>" in source


def test_a_composite_output_is_still_refused_for_an_ordinary_step(design):
    """The refusal that was there before this feature stands, and that is the discrimination case.

    Without `reads_own_writes` nothing says these records belong together or that anything holds
    the ones being replaced -- so an ordinary step outputting a composite is exactly as unrenderable
    as it was, and this asserts the new branch did not quietly relax it.
    """
    with pytest.raises(UnrenderableWriterError, match="nothing says which file each part"):
        render_item_writer(
            _step("postTransaction", "PostingResult"),
            _sequential_design(design),
            PROGRAM,
            package="com.modernized.batch.writer",
            domain_package="com.modernized.domain",
            working_set_package=WORKING_SET_PACKAGE,
        )


def test_a_component_written_by_key_with_no_store_holding_it_is_refused(design):
    """A `replace` component outside the working set has nothing to replace *in*.

    Appending it instead would leave the original rows in place and add new ones -- ADR-0037's
    defect, reintroduced through the composite path. Refused rather than degraded.
    """
    detached = _sequential_design(design).model_copy(
        update={
            "file_access_paths": [
                path.model_copy(update={"is_keyed_lookup": False})
                if path.select_name == "ACCOUNT-FILE"
                else path
                for path in design.file_access_paths
            ]
        }
    )
    with pytest.raises(UnrenderableWriterError, match="nothing holding the records it would"):
        render_item_writer(
            _sequential_step(),
            detached,
            PROGRAM,
            package="com.modernized.batch.writer",
            domain_package="com.modernized.domain",
            working_set_package=WORKING_SET_PACKAGE,
        )


POSTING_INPUT = CompositeType(
    name="PostingInput",
    components=[
        CompositeComponent(field_name="dalytran", entity_name="Dalytran"),
        CompositeComponent(field_name="xref", entity_name="CardXref"),
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
    ],
)


def _sequential_reader(design: UnifiedDesign) -> str:
    step = _sequential_step().model_copy(update={"input_type": "PostingInput"})
    return render_item_reader(
        step,
        design.model_copy(update={"composite_types": [POSTING_INPUT, POSTING_RESULT]}),
        PROGRAM,
        package="com.modernized.batch.reader",
        domain_package="com.modernized.domain",
        working_set_package=WORKING_SET_PACKAGE,
    )


def test_a_sequential_reader_takes_its_updated_lookups_from_the_shared_store(design):
    """**The point of the whole exercise.** `ACCOUNT` and `TCATBAL` come from the working set.

    A private map loaded in this reader's constructor would answer from the file as the job found
    it and never see a single write, which is the 287-record failure ADR-0039 measured. The store
    is asked by method rather than by reaching into a map inside it, so exactly one place knows how
    its records are keyed.
    """
    source = _sequential_reader(design)
    assert "state.account(" in source
    assert "state.tranCatBal(" in source
    assert "accountRecords" not in source
    assert "trancatbalRecords" not in source


def test_the_lookups_it_does_not_write_are_still_its_own(design):
    """`XREF` is read and never written, so nothing is shared and nothing changes for it.

    Without this the assertion above would pass for a reader that had moved *every* lookup into the
    store, which would be a different design and a worse one -- the store exists for records the
    step mutates, not as a general cache.
    """
    source = _sequential_reader(design)
    assert "cardxrefRecords.get(" in source
    assert "Path xreffile" in source


def test_the_shared_lookups_are_not_constructor_paths_any_more(design):
    """The reader cannot be handed the account file: it must not have a second copy of it."""
    source = _sequential_reader(design)
    constructor = next(line for line in source.splitlines() if "public PostTransactionItemReader" in line)
    assert "PostTransactionWorkingSet state" in constructor
    assert "acctfile" not in constructor
    assert "tcatbalf" not in constructor


def test_an_ordinary_step_reader_is_unchanged_by_any_of_this(design):
    """`CBACT04C` is the regression risk, and this is the shape of the assertion that guards it.

    A step that does not declare `reads_own_writes` gets no working set, keeps every lookup in its
    own map, and takes a `Path` per file -- exactly as it did before the store existed.
    """
    source = render_item_reader(
        _step("postTransaction", "Tran").model_copy(update={"input_type": "PostingInput"}),
        design.model_copy(update={"composite_types": [POSTING_INPUT]}),
        PROGRAM,
        package="com.modernized.batch.reader",
        domain_package="com.modernized.domain",
    )
    assert "WorkingSet" not in source
    assert "accountRecords.get(" in source
    # `_camel` rather than the working set's `_member`, which is how this reader has always
    # named its own maps. Asserted in its existing spelling rather than tidied: renaming a
    # rendered field to match a newer module would change every generated reader for nothing.
    assert "trancatbalRecords.get(" in source


# --- the job bean for a sequential step (ADR-0041) ------------------------------------------------


def _sequential_job(design: UnifiedDesign) -> str:
    step = _sequential_step().model_copy(update={"input_type": "PostingInput"})
    full = design.model_copy(update={"composite_types": [POSTING_INPUT, POSTING_RESULT]})
    job = BatchJobDesign(
        job_name="postingJob",
        program_name=PROGRAM,
        domain_entities=[entity.name for entity in full.domain_entities],
        steps=[step],
    )
    return render_job_configuration(
        job,
        full,
        PROGRAM,
        package="com.modernized.batch.job",
        domain_package="com.modernized.domain",
        processor_package="com.modernized.batch.processor",
        reader_package="com.modernized.batch.reader",
        working_set_package=WORKING_SET_PACKAGE,
    )


def test_a_sequential_step_is_chunked_at_one_because_that_is_correctness(design):
    """Every other step chunks at `CHUNK_SIZE`, which its own comment calls a performance decision.

    Here the writer puts into the working set and the reader takes its lookups from it, so any size
    above 1 would let one chunk decide several items against the state as it stood before any of
    them -- ADR-0039's failure, arriving through the transaction boundary. Rendered as a literal
    beside a named constant deliberately: two different kinds of number should not look alike.
    """
    source = _sequential_job(design)
    # `>chunk(1)`, not `.chunk(1)`: the call is preceded by the step's generic types.
    assert ">chunk(1)" in source
    assert "chunk(CHUNK_SIZE)" not in source


def test_the_step_flushes_the_working_set_when_it_ends(design):
    """Nothing reaches disk for those two files until this runs, so its absence loses the run."""
    source = _sequential_job(design)
    assert "state.flush();" in source
    assert "public ExitStatus afterStep(StepExecution stepExecution)" in source
    # Read out of spring-batch-core-6.0.4.jar rather than recalled -- PR #32's trap was a pre-6
    # package that compiles in every example on the internet and not here.
    assert "import org.springframework.batch.core.listener.StepExecutionListener;" in source
    assert "import org.springframework.batch.core.step.StepExecution;" in source
    assert "import org.springframework.batch.core.ExitStatus;" in source


def test_the_working_set_is_referred_to_by_its_full_package(design):
    """The store lives in its own package, and three rendered classes point at it from theirs."""
    source = _sequential_job(design)
    assert f"{WORKING_SET_PACKAGE}.PostTransactionWorkingSet state" in source


def test_a_job_with_no_sequential_step_gains_none_of_this(design):
    """`CBACT04C`'s configuration must be untouched -- no listener, no literal chunk, no imports."""
    step = _step("postTransaction", "Tran").model_copy(update={"input_type": "PostingInput"})
    full = design.model_copy(update={"composite_types": [POSTING_INPUT]})
    job = BatchJobDesign(
        job_name="postingJob",
        program_name=PROGRAM,
        domain_entities=[entity.name for entity in full.domain_entities],
        steps=[step],
    )
    source = render_job_configuration(
        job,
        full,
        PROGRAM,
        package="com.modernized.batch.job",
        domain_package="com.modernized.domain",
        processor_package="com.modernized.batch.processor",
        reader_package="com.modernized.batch.reader",
    )
    assert ">chunk(CHUNK_SIZE)" in source
    assert "StepExecutionListener" not in source
    assert "WorkingSet" not in source


# --- a lookup the program tolerates missing (ADR-0042) --------------------------------------------


def _optional_reader(design: UnifiedDesign) -> str:
    step = _sequential_step().model_copy(
        update={"input_type": "PostingInput", "optional_lookups": ["TranCatBal"]}
    )
    return render_item_reader(
        step,
        design.model_copy(update={"composite_types": [POSTING_INPUT, POSTING_RESULT]}),
        PROGRAM,
        package="com.modernized.batch.reader",
        domain_package="com.modernized.domain",
        working_set_package=WORKING_SET_PACKAGE,
    )


def test_a_declared_optional_lookup_is_not_required(design):
    """`2700-UPDATE-TCATBAL` reads a balance row and, INVALID KEY, creates one.

    It does that 44 times on this corpus -- which is exactly how the 50 balance rows the job starts
    from become the 94 the oracle holds. A reader that refused the record would abend on the first
    of them, so the miss is handed to the processor as the COBOL's own INVALID KEY branch.
    """
    source = _optional_reader(design)
    assert "TCATBAL-FILE has no record for the key" not in source
    assert "null here is the INVALID KEY branch, not a failure." in source


def test_the_lookups_that_were_not_declared_optional_are_still_required(design):
    """The discrimination case: one entity was named, and only that one changed.

    Without this the assertion above would pass for a reader that had stopped requiring anything,
    which is the change that turns a wrong join into plausible rows instead of a loud failure.
    """
    source = _optional_reader(design)
    assert 'require(cardxrefRecord, "XREF-FILE has no record for the key' in source
    assert 'require(accountRecord, "ACCOUNT-FILE has no record for the key' in source


def test_an_optional_component_is_not_parsed_when_it_is_absent(design):
    """The record parser slices fixed offsets and would throw on the null it is now allowed to get.

    So the component carries the miss instead. Asserted because the refusal and the parse are two
    separate places, and removing only the first produces a reader that fails one line later with a
    message about offsets rather than about a missing record.
    """
    source = _optional_reader(design)
    assert "trancatbalRecord == null ? null : toTranCatBal(trancatbalRecord)" in source


def test_a_step_declaring_no_optional_lookups_requires_all_of_them(design):
    """`CBACT04C`'s posture, unchanged: a keyed read that finds nothing refuses the record."""
    source = _sequential_reader(design)
    assert 'require(trancatbalRecord, "TCATBAL-FILE has no record for the key' in source
