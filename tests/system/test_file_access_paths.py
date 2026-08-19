"""`design.json` carries how each program reaches its data (G31 stage 2, ADR-0030).

**What this closes.** ADR-0030 established that a reader cannot be rendered from the design as it
stood: a composite declares which entities it carries and nothing about which is a stream, which are
keyed lookups, or what the keys are. Stage 1 parsed those facts; this puts them in the contract, and
gives them a consumer in the same change.

**The consumer matters more than the type.** This repo has produced a computed fact that never
reached its target four times (G21, G24, G28, G26) -- and once shipped a helper that was written,
tested and called by nothing. So `unobtainable_inputs` reads these paths and turns them into a gate
item, and the tests below assert it fires on a design that cannot be built and stays silent on the
one that can.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    SCHEMA_VERSION,
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    FileAccessPath,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
    entity_name_from_record,
    unobtainable_inputs,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from tests.system.test_account_break_posting import POSTING
from tests.system.test_account_break_posting import STEP as POSTING_STEP
from tests.system.test_interest_equivalence import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"


def _entry(program: str) -> ProgramDesignEntry:
    extraction = extract_spec(FIXTURE_ROOT, program, narrate=lambda m, s, u: "narration")
    return ProgramDesignEntry(
        program_name=program,
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )


@pytest.fixture(scope="module")
def cbact04c() -> ProgramDesignEntry:
    return _entry("CBACT04C")


@pytest.fixture(scope="module")
def paths(cbact04c) -> list[FileAccessPath]:
    return build_file_access_paths(FIXTURE_ROOT, [cbact04c])


@pytest.fixture(scope="module")
def design(cbact04c, paths) -> UnifiedDesign:
    entities = build_domain_entities(FIXTURE_ROOT, [cbact04c])
    job = BatchJobDesign(
        job_name="interestJob",
        program_name="CBACT04C",
        description="Monthly interest calculation.",
        domain_entities=[entity.name for entity in entities],
        steps=[STEP, COMPLETE_STEP, POSTING_STEP],
    )
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        batch_jobs=[job],
        rest_endpoints=[],
        file_access_paths=paths,
    )


# --- the facts, from the real program ---------------------------------------------------------------


def test_every_entity_the_interest_step_needs_has_a_declared_access_path(paths):
    """The four components of `TranCatBalWithRate`, each with a way to be read.

    This is the list a rendered reader is built from, and until now none of it existed anywhere in
    `design.json`.
    """
    by_entity = {path.entity_name: path for path in paths if path.entity_name}
    assert {"TranCatBal", "DisGroup", "Account", "CardXref"} <= set(by_entity)

    assert not by_entity["TranCatBal"].is_keyed_lookup, "the driving stream is not a lookup"
    for entity in ("DisGroup", "Account", "CardXref"):
        assert by_entity[entity].is_keyed_lookup
        assert by_entity[entity].effective_key, f"{entity} is a lookup with no key to look up by"


def test_the_xref_path_carries_the_key_the_program_reads_by_not_the_one_it_declares():
    """Finding F2, now a parsed fact rather than something the hand-written wiring knew.

    `CBACT04C` declares `XREF-FILE` on the card number and reads it by the account id -- the
    alternate. `effective_key` is what a renderer must use; `declared_record_key` is kept beside it
    so the disagreement stays visible rather than being flattened into one field.
    """
    xref = next(p for p in build_file_access_paths(FIXTURE_ROOT, [_entry("CBACT04C")])
                if p.select_name == "XREF-FILE")
    assert xref.effective_key == "FD-XREF-ACCT-ID"
    assert xref.declared_record_key == "FD-XREF-CARD-NUM"
    assert xref.alternate_record_keys == ["FD-XREF-ACCT-ID"]


def test_the_same_file_gets_a_different_path_in_a_different_program():
    """Why this is per program. `CBTRN02C` reads `XREF` by its record key; `CBACT04C` does not.

    Recorded on `DomainEntity` instead, one of these two would have to be wrong.
    """
    both = build_file_access_paths(FIXTURE_ROOT, [_entry("CBACT04C"), _entry("CBTRN02C")])
    xrefs = {p.program_name: p for p in both if p.select_name == "XREF-FILE"}
    assert xrefs["CBACT04C"].effective_key == "FD-XREF-ACCT-ID"
    assert xrefs["CBTRN02C"].effective_key == "FD-XREF-CARD-NUM"


def test_a_file_that_is_written_but_never_read_is_kept_with_no_entity(paths):
    """`TRANSACT-FILE` is `CBACT04C`'s output. Dropping it would hide that the program writes at all.

    It carries no `entity_name` because nothing `READ ... INTO` binds it to a record -- which is
    also the honest limit of this stage: the writer side needs `WRITE ... FROM`, which is not parsed
    yet, so an output file is currently visible as a declaration without a record.
    """
    transact = next(path for path in paths if path.select_name == "TRANSACT-FILE")
    assert transact.entity_name == ""
    assert transact.read_line is None
    assert transact.select_line > 0


def test_each_path_carries_the_source_lines_it_came_from(paths):
    """Provenance, per `CLAUDE.md`: the `SELECT` line, and the `READ` line where one exists."""
    source = (FIXTURE_ROOT / "app" / "cbl" / "CBACT04C.cbl").read_text(encoding="latin-1")
    lines = source.splitlines()
    for path in paths:
        assert "SELECT" in lines[path.select_line - 1].upper()
        if path.read_line is not None:
            assert "READ" in lines[path.read_line - 1].upper()


def test_the_entity_name_transform_has_one_implementation():
    """`build_domain_entities` and the access paths must agree on what an entity is called.

    Two spellings of this rule is how a path ends up naming an entity that does not exist -- which
    compiles, renders, and finds nothing.
    """
    assert entity_name_from_record("TRAN-CAT-BAL-RECORD") == "TranCatBal"
    assert entity_name_from_record("ACCOUNT-RECORD") == "Account"
    entities = {e.name for e in build_domain_entities(FIXTURE_ROOT, [_entry("CBACT04C")])}
    named = {p.entity_name for p in build_file_access_paths(FIXTURE_ROOT, [_entry("CBACT04C")])}
    assert (named - {""}) <= entities, "an access path names an entity the design does not have"


def test_the_schema_version_records_the_addition():
    assert SCHEMA_VERSION == "3.2.0"


# --- the consumer, which is the point ---------------------------------------------------------------


def test_the_real_design_reports_nothing_unobtainable(design):
    """No false positives on the design the round trip actually runs.

    Worth asserting first: a check that fires on a working design is worse than no check, because
    it trains a reviewer to skip the category.
    """
    job = design.batch_jobs[0]
    for step in job.steps:
        assert unobtainable_inputs(job, step, design) == []


def test_an_entity_no_step_produces_and_no_file_yields_is_reported(design):
    """The discrimination. A composite widened to something this program never reads must fail.

    `Customer` comes from `CVCUS01Y` -- a real entity of this corpus, read by `CBCUS01C` and not by
    `CBACT04C`. Widening the interest step's input to include it is exactly the mistake the check
    exists to catch: it resolves as a type, it is populatable in principle, and nothing in this
    program can obtain it.
    """
    widened = CompositeType(
        name="TranCatBalWithRate",
        components=[
            *COMPOSITE.components,
            CompositeComponent(field_name="customer", entity_name="Customer"),
        ],
    )
    broken = design.model_copy(
        update={"composite_types": [widened, OUTPUT_COMPOSITE, POSTING]}
    )
    job = broken.batch_jobs[0]
    assert unobtainable_inputs(job, job.steps[0], broken) == ["Customer"]


def test_an_entity_an_earlier_step_produces_is_not_reported(design):
    """The other half, without which the check is noise.

    `completeTransaction` consumes a `Tran`, which `CBACT04C` never reads from a file --
    `computeInterest` makes it. Flagging that would fire on every chained design in the repo.
    """
    job = design.batch_jobs[0]
    complete = next(s for s in job.steps if s.step_name == "completeTransaction")
    assert "Tran" in {c.entity_name for c in OUTPUT_COMPOSITE.components}
    assert unobtainable_inputs(job, complete, design) == []


def test_step_order_decides_it(design):
    """Producing a value *later* does not make it available now, and the check has to know that.

    Reversing the chain makes `completeTransaction` run before the step that produces its input, so
    `Tran` becomes unobtainable -- the same design, failing only because of order.
    """
    job = design.batch_jobs[0]
    reversed_job = job.model_copy(update={"steps": list(reversed(job.steps))})
    complete = next(s for s in reversed_job.steps if s.step_name == "completeTransaction")
    assert unobtainable_inputs(reversed_job, complete, design) == ["Tran"]


def test_a_step_whose_input_is_a_plain_entity_is_handled(design):
    """Not every input is a composite; a bare `DomainEntity` name must resolve the same way."""
    job = design.batch_jobs[0]
    plain = BatchStepDesign(
        step_name="readAccounts",
        source_paragraphs=["1100-GET-ACCT-DATA"],
        role="processor",
        description="Reads accounts.",
        input_type="Account",
        output_type="Account",
        guard_condition=None,
    )
    assert unobtainable_inputs(job.model_copy(update={"steps": [plain]}), plain, design) == []


def test_a_design_carrying_no_access_paths_reports_nothing_rather_than_everything(design):
    """The silence that has to be deliberate, because the field is optional.

    A design from before schema 3.2.0 -- or one a test assembles by hand -- carries no access paths,
    and reading that as "this program reads nothing" would flag every entity of every step. A check
    is least trustworthy exactly where it has least information, so it says nothing there.
    """
    job = design.batch_jobs[0]
    blind = design.model_copy(update={"file_access_paths": []})
    for step in job.steps:
        assert unobtainable_inputs(job, step, blind) == []

    # And it is not silent in general -- the same design with its paths still fires on a widened
    # composite, so this is a scoped exemption rather than a check that never speaks.
    widened = CompositeType(
        name="TranCatBalWithRate",
        components=[
            *COMPOSITE.components,
            CompositeComponent(field_name="customer", entity_name="Customer"),
        ],
    )
    loud = design.model_copy(update={"composite_types": [widened, OUTPUT_COMPOSITE, POSTING]})
    assert unobtainable_inputs(job, job.steps[0], loud) == ["Customer"]
