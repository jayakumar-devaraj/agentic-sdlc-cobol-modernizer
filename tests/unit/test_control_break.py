"""The control break: recognising the idiom, carrying it in the contract, and what it unblocks.

**Why this is the last fact G31 needed.** Everything else a rendered job needs is *declared* -- a
`PIC` clause, a `SELECT`, a `READ ... INTO`, a `WRITE ... FROM`. A control break is an idiom: four
statements spread across a loop, saying what a program groups by and what it accumulates. Nothing
declares it, which is why `postAccountInterest` could not be rendered and why ADR-0027's
"already-summed item" was a note rather than something a renderer could produce.

**Recognition is a conjunction, and that is the safety.** An inequality test is not a control break
-- `CBACT04C` has `IF DIS-INT-RATE NOT = 0` twelve lines after the real one. What makes a break is
that the field being tested is then *moved into the field it was tested against*, while an
accumulator is zeroed beside it and added to elsewhere. A wrong grouping key produces plausible
totals against the wrong accounts, which is `pic_mapper`'s objection in a new place.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    SCHEMA_VERSION,
    BatchJobDesign,
    CompositeComponent,
    CompositeType,
    ComputedComponent,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    attach_control_breaks,
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.parsing.control_break import extract_control_breaks, landing_field
from cobol_modernizer.rendering.java_job import aggregation_blockers, plan_steps
from tests.support.interest_design import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)
from tests.support.posting_design import POSTING
from tests.support.posting_design import STEP as POSTING_STEP

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
CBL = FIXTURE_ROOT / "app" / "cbl"


def source(program: str) -> str:
    return (CBL / f"{program}.cbl").read_text(encoding="latin-1")


# --- recognising the idiom -------------------------------------------------------------------------


def test_the_break_in_cbact04c_is_recognised_completely():
    """Every field, from the real program: the key, the accumulator, and what feeds it."""
    breaks = extract_control_breaks(source("CBACT04C"))
    assert len(breaks) == 1

    found = breaks[0]
    assert found.break_key_field == "TRANCAT-ACCT-ID"
    assert found.saved_key_field == "WS-LAST-ACCT-NUM"
    assert found.accumulator_field == "WS-TOTAL-INT"
    assert found.accumulated_from_field == "WS-MONTHLY-INT"
    assert found.performed_paragraph == "1050-UPDATE-ACCOUNT"


def test_an_inequality_test_that_is_not_a_break_is_not_reported():
    """`IF DIS-INT-RATE NOT = 0` sits twelve lines from the real break in the same program.

    What separates them is that a break *advances* its saved key -- `MOVE TRANCAT-ACCT-ID TO
    WS-LAST-ACCT-NUM`. Without that requirement this test would report two breaks for `CBACT04C`,
    one of them on a rate.
    """
    assert "IF DIS-INT-RATE NOT = 0" in source("CBACT04C")
    assert {b.break_key_field for b in extract_control_breaks(source("CBACT04C"))} == {
        "TRANCAT-ACCT-ID"
    }


@pytest.mark.parametrize("program", ["CBTRN02C", "CBACT01C", "CBCUS01C"])
def test_a_program_with_no_control_break_reports_none(program):
    """Three of the four Track C programs group nothing, and that is a fact rather than a failure."""
    assert extract_control_breaks(source(program)) == []


def test_the_block_is_read_to_its_matching_end_if():
    """The break block contains an `IF/ELSE/END-IF` of its own.

    Stopping at the first `END-IF` would cut the block before `MOVE 0 TO WS-TOTAL-INT` and leave a
    real control break unrecognised -- which is how a correct parser silently reports nothing.
    """
    text = source("CBACT04C")
    inner = text.index("IF WS-FIRST-TIME NOT = 'Y'")
    reset = text.index("MOVE 0 TO WS-TOTAL-INT")
    assert inner < reset, "the nested IF really does precede the reset"
    assert extract_control_breaks(text)[0].accumulator_field == "WS-TOTAL-INT"


def test_the_accumulated_value_is_traced_to_the_record_field_it_lands_in():
    """`WS-TOTAL-INT` is a program variable no generated record has; `TRAN-AMT` is a column.

    Without this hop an aggregation would have a field name it could not find anywhere in the types
    it is given.
    """
    assert landing_field(source("CBACT04C"), "WS-MONTHLY-INT") == "TRAN-AMT"
    assert landing_field(source("CBACT04C"), "WS-NEVER-MOVED") is None


def test_a_period_terminated_break_is_still_recognised():
    """`END-IF` is optional in COBOL: a sentence can close with a period instead.

    `CBACT04C` uses the explicit form, so without this the scope-terminator-free variant -- which is
    the older and more common style in real estates -- would silently report no break.
    """
    program = """       PROCEDURE DIVISION.
           IF KEY-FIELD NOT = SAVED-KEY
              PERFORM 9000-POST-TOTAL
              MOVE 0 TO WS-TOTAL
              MOVE KEY-FIELD TO SAVED-KEY.
           ADD WS-ITEM TO WS-TOTAL.
"""
    found = extract_control_breaks(program)
    assert len(found) == 1
    assert found[0].break_key_field == "KEY-FIELD"
    assert found[0].accumulated_from_field == "WS-ITEM"
    assert found[0].performed_paragraph == "9000-POST-TOTAL"


def test_a_zeroed_field_that_is_never_added_to_is_not_an_accumulator():
    """A flag reset at a break is not a running total, and calling it one would invent a sum."""
    program = """\
       PROCEDURE DIVISION.
           IF KEY-FIELD NOT = SAVED-KEY
              PERFORM 9000-DO-SOMETHING
              MOVE 0 TO WS-FLAG
              MOVE KEY-FIELD TO SAVED-KEY
           END-IF.
"""
    assert extract_control_breaks(program) == []


def test_a_break_with_no_performed_paragraph_is_not_reported():
    """With nothing performed at the boundary there is no step to attach the break to."""
    program = """\
       PROCEDURE DIVISION.
           IF KEY-FIELD NOT = SAVED-KEY
              MOVE 0 TO WS-TOTAL
              MOVE KEY-FIELD TO SAVED-KEY
           END-IF.
           ADD WS-ITEM TO WS-TOTAL.
"""
    assert extract_control_breaks(program) == []


def test_each_break_carries_the_lines_it_was_read_from():
    """Provenance, per `CLAUDE.md`: the test and the accumulation, checked against the file."""
    lines = source("CBACT04C").splitlines()
    found = extract_control_breaks(source("CBACT04C"))[0]
    assert "NOT=" in lines[found.test_line - 1].replace("NOT =", "NOT=")
    assert "ADD" in lines[found.add_line - 1].upper()


# --- what reaches design.json ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    job = BatchJobDesign(
        job_name="interestJob",
        program_name="CBACT04C",
        domain_entities=[entity.name for entity in entities],
        steps=[STEP, COMPLETE_STEP, POSTING_STEP],
    )
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        batch_jobs=attach_control_breaks(FIXTURE_ROOT, [job], [entry]),
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


def test_the_break_attaches_to_the_step_that_declares_its_paragraph(design):
    """`1050-UPDATE-ACCOUNT` is `postAccountInterest`'s paragraph, and that is the whole matching rule.

    Attached rather than declared: a model asked which step aggregates would be answering a question
    the source already answers.
    """
    by_name = {step.step_name: step for step in design.batch_jobs[0].steps}
    assert by_name["computeInterest"].control_break is None
    assert by_name["completeTransaction"].control_break is None

    attached = by_name["postAccountInterest"].control_break
    assert attached is not None
    assert attached.break_key_field == "TRANCAT-ACCT-ID"
    assert attached.landing_field == "TRAN-AMT"


def test_a_break_whose_paragraph_no_step_declares_is_dropped(design):
    """A program may do work at a boundary this design never split into a step.

    Dropped rather than attached to a guess, and visible as a step without a break rather than as an
    error.
    """
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extract_spec(
            FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration"
        ),
        critique=critique_spec(
            FIXTURE_ROOT,
            extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "n"),
            critique=lambda m, s, u: "[]",
        ),
    )
    job = BatchJobDesign(
        job_name="interestJob",
        program_name="CBACT04C",
        domain_entities=[],
        steps=[STEP, COMPLETE_STEP],
    )
    updated = attach_control_breaks(FIXTURE_ROOT, [job], [entry])[0]
    assert all(step.control_break is None for step in updated.steps)


def test_the_schema_carries_the_control_break():
    schema = (
        Path(__file__).resolve().parents[2] / "schemas" / "design_document.schema.json"
    ).read_text(encoding="utf-8")
    assert "control_break" in schema
    major, minor, _patch = (int(part) for part in SCHEMA_VERSION.split("."))
    assert (major, minor) >= (3, 6)


# --- what it unblocks, and what it does not ----------------------------------------------------------


def test_the_refusal_names_the_missing_field_when_no_stream_carries_it(design):
    """The refusal that asked for the composite to be widened, kept as a test after it was.

    `TranWithContext` carries `TranCatBal` now, so the aggregation renders and nothing is refused --
    which would leave this message untested. Narrowing the composite back reproduces the state the
    design was in, and the point of the message: not *"nothing says how to group"* but *"this exact
    field is not reachable"*, which is a question a human can answer.
    """
    narrowed = design.model_copy(
        update={
            "composite_types": [
                composite.model_copy(update={"components": composite.components[:3]})
                if composite.name == "TranWithContext"
                else composite
                for composite in design.composite_types
            ]
        }
    )
    _renderable, skipped, _staged = plan_steps(narrowed.batch_jobs[0], narrowed, "CBACT04C")
    _step, reason = skipped[0]

    assert "control break on TRANCAT-ACCT-ID" in reason
    assert "summing WS-MONTHLY-INT which lands in TRAN-AMT" in reason
    assert "TRANCAT-ACCT-ID is not" in reason
    assert "widen that type" in reason


def test_the_widened_composite_makes_the_step_renderable(design):
    """The other half: with the break key on the stream, nothing is refused.

    Both directions matter. A test that only ever saw the refusal would keep passing if widening the
    composite had changed nothing.
    """
    renderable, skipped, _staged = plan_steps(design.batch_jobs[0], design, "CBACT04C")
    assert skipped == []
    assert "postAccountInterest" in [step.step_name for step in renderable]


def test_the_blockers_are_exactly_what_the_upstream_type_cannot_reach(design):
    """`Tran` carries the amount and not the account id, so one of the two is missing.

    Worth asserting as a pair: a check reporting *both* would mean the landing-field hop had not
    worked, and one reporting neither would mean the aggregation was renderable and something else
    stopped it.
    """
    job = design.batch_jobs[0]
    posting = next(step for step in job.steps if step.step_name == "postAccountInterest")

    # A `Tran` carries the amount and not the account id -- the account id is only inside its
    # description text, which is not a field anything can group on.
    assert aggregation_blockers(posting, "Tran", design) == ["TRANCAT-ACCT-ID"]
    # `TranWithContext` was widened to carry `TranCatBal`, so it reaches both. That widening is what
    # made the aggregation renderable, and asserting the pair is what shows the difference is real
    # rather than a check that never fires.
    assert aggregation_blockers(posting, "TranWithContext", design) == []


def _accrued(*, carries_the_value: bool) -> CompositeType:
    """ADR-0063's row-grain item, with and without the computed field that makes it summable."""
    return CompositeType(
        name="AccruedCategoryInterest",
        components=[CompositeComponent(field_name="categoryBalance", entity_name="TranCatBal")],
        computed_fields=(
            [ComputedComponent(field_name="monthlyInterest", cobol_field_name="WS-MONTHLY-INT")]
            if carries_the_value
            else []
        ),
    )


def test_a_stream_carrying_the_value_itself_needs_no_landing_column(design):
    """The live shape, and the one this check refused.

    ADR-0063 requires the row-grain item to carry `WS-MONTHLY-INT` and *not* the account total. A
    design that obeys it returns the value as a `computed_fields` entry (ADR-0062) rather than
    inside a `Tran`, so nothing on that stream is called `TRAN-AMT` -- and a check that asks only
    for the landing column reports a correct design as unrenderable.

    Asserted as a pair, like the `Tran`/`TranWithContext` case above: without the computed field the
    same composite is blocked on exactly `TRAN-AMT`, so this is the difference the computed field
    makes rather than a check that stopped firing.
    """
    job = design.batch_jobs[0]
    posting = next(step for step in job.steps if step.step_name == "postAccountInterest")

    carried = design.model_copy(
        update={"composite_types": [*design.composite_types, _accrued(carries_the_value=True)]}
    )
    assert aggregation_blockers(posting, "AccruedCategoryInterest", carried) == []

    bare = design.model_copy(
        update={"composite_types": [*design.composite_types, _accrued(carries_the_value=False)]}
    )
    assert aggregation_blockers(posting, "AccruedCategoryInterest", bare) == ["TRAN-AMT"]


def test_a_step_with_no_control_break_reports_no_blockers(design):
    """The function has to be silent where there is nothing to aggregate, or every step looks broken."""
    job = design.batch_jobs[0]
    compute = next(step for step in job.steps if step.step_name == "computeInterest")
    assert aggregation_blockers(compute, "TranCatBalWithRate", design) == []


def test_with_no_upstream_type_every_field_is_a_blocker(design):
    """A first step cannot aggregate anything: there is no stream behind it to group."""
    job = design.batch_jobs[0]
    posting = next(step for step in job.steps if step.step_name == "postAccountInterest")
    assert aggregation_blockers(posting, None, design) == ["TRANCAT-ACCT-ID", "TRAN-AMT"]
