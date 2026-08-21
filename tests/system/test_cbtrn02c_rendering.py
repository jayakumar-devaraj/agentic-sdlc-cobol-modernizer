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


def _render_writer(design: UnifiedDesign, step_name: str, output_type: str) -> str:
    return render_item_writer(
        _step(step_name, output_type),
        design,
        PROGRAM,
        package="com.modernized.batch.writer",
        domain_package="com.modernized.domain",
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
