"""An accumulator belongs to its group, not to the row that feeds it (ADR-0063).

The first live run of ADR-0062 produced a design whose `computeMonthlyInterest` returned an item
carrying `totalInterest`, and `generate` filled it the only way a stateless processor can::

    BigDecimal totalInterest = monthlyInterest;

`WS-TOTAL-INT` is a per-account running total -- `MOVE 0` at the account break, `ADD` once per
category, `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` once per account -- so an account with four category
balances would be posted the last category's interest instead of the sum.

**ADR-0062 did not merely miss this; it required it.** The first test below is the one that matters:
the correct design was *refused* before this change, so no correct design was reachable.

Every fixture here uses the real deterministic facts for `CBACT04C` and the real
`ControlBreakDesign` that `attach_control_breaks` produces for it, because the distinction being
tested is between two grains of the same real program.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    ComputedComponent,
    ControlBreakDesign,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_computed_values,
    build_domain_entities,
    misplaced_accumulators,
    undeliverable_computed_values,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBACT04C"

#: Exactly what `parsing/control_break.py` finds in the real `CBACT04C`.
BREAK = ControlBreakDesign(
    break_key_field="TRANCAT-ACCT-ID",
    accumulator_field="WS-TOTAL-INT",
    accumulated_from_field="WS-MONTHLY-INT",
    landing_field="TRAN-AMT",
    performed_paragraph="1050-UPDATE-ACCOUNT",
    test_line=194,
    add_line=467,
)


@pytest.fixture(scope="module")
def facts():
    extraction = extract_spec(
        FIXTURE_ROOT,
        PROGRAM,
        narrate=lambda m, s, u: u.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0],
    )
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    entries = [
        ProgramDesignEntry(program_name=PROGRAM, spec_extraction=extraction, critique=critique)
    ]
    return (
        build_domain_entities(FIXTURE_ROOT, entries),
        build_computed_values(FIXTURE_ROOT, entries),
    )


def _design(
    facts, *, row_carries_total: bool, posting_carries_total: bool = True, attach_break: bool = True
):
    """The step-51 job, with the one field under test toggled on the row-grain item."""
    entities, computed = facts

    row_fields = [
        ComputedComponent(field_name="monthlyInterest", cobol_field_name="WS-MONTHLY-INT")
    ]
    if row_carries_total:
        row_fields.append(
            ComputedComponent(field_name="totalInterest", cobol_field_name="WS-TOTAL-INT")
        )

    accrued = CompositeType(
        name="AccruedCategoryInterest",
        components=[CompositeComponent(field_name="categoryBalance", entity_name="TranCatBal")],
        computed_fields=row_fields,
    )
    posting = CompositeType(
        name="AccountInterestPosting",
        components=[CompositeComponent(field_name="account", entity_name="Account")],
        computed_fields=(
            [ComputedComponent(field_name="totalInterest", cobol_field_name="WS-TOTAL-INT")]
            if posting_carries_total
            else []
        ),
    )
    processor = BatchStepDesign(
        step_name="computeMonthlyInterest",
        source_paragraphs=["1300-COMPUTE-INTEREST"],
        role="processor",
        description="d",
        input_type="RatedCategoryBalance",
        output_type="AccruedCategoryInterest",
        guard_condition=None,
    )
    # A writer, whose ADR-0027 already-summed item is what it READS.
    posting_step = BatchStepDesign(
        step_name="postAccountInterest",
        source_paragraphs=["1050-UPDATE-ACCOUNT"],
        role="writer",
        description="d",
        input_type="AccountInterestPosting",
        output_type="Account",
        guard_condition=None,
        control_break=BREAK if attach_break else None,
    )
    job = BatchJobDesign(
        job_name="interestJob",
        program_name=PROGRAM,
        description="d",
        domain_entities=[],
        steps=[processor, posting_step],
    )
    design = UnifiedDesign(
        domain_entities=entities,
        batch_jobs=[job],
        rest_endpoints=[],
        composite_types=[accrued, posting],
        computed_values=computed,
    )
    return job, processor, design


def test_the_correct_design_is_accepted_and_was_previously_refused(facts) -> None:
    """The test this ADR exists for. Before ADR-0063 this returned ``['WS-TOTAL-INT']``.

    `WS-TOTAL-INT` is computed in a paragraph `computeMonthlyInterest` owns, escapes to
    `1050-UPDATE-ACCOUNT`, and is `MOVE`d into no record -- so every clause of ADR-0062's rule
    demanded the producing step carry it. At row grain the only way to satisfy that is to fabricate
    a total, which is exactly what the live run generated. A check that refuses the correct design
    is worse than no check: it trains a producer to satisfy it incorrectly.
    """
    job, processor, design = _design(facts, row_carries_total=False)

    assert undeliverable_computed_values(job, processor, design) == []
    assert misplaced_accumulators(job, design) == []


def test_the_accumulator_on_a_row_grain_item_is_refused(facts) -> None:
    """The step-51 defect. Excusing the producing step alone would only *permit* this."""
    job, _processor, design = _design(facts, row_carries_total=True)

    assert misplaced_accumulators(job, design) == [
        ("AccruedCategoryInterest", "WS-TOTAL-INT", "postAccountInterest")
    ]


def test_the_posting_item_may_carry_it_because_the_writer_reads_it(facts) -> None:
    """**The case a first version of this check got wrong**, and the reason for the fixture's shape.

    ADR-0027's already-summed `(account, totalInterest)` item is what `postAccountInterest`
    *consumes*: as a writer it has `input_type = AccountInterestPosting` and `output_type = Account`.
    Checking only the owning step's `output_type` reported the correct carrier as misplaced -- the
    exact inversion of the defect. Both types the step operates on are entitled.
    """
    job, _processor, design = _design(facts, row_carries_total=False)
    posting = next(s for s in job.steps if s.step_name == "postAccountInterest")

    assert posting.input_type == "AccountInterestPosting"
    assert posting.output_type == "Account"
    assert misplaced_accumulators(job, design) == []


def test_the_row_grain_value_is_untouched(facts) -> None:
    """`WS-MONTHLY-INT` is a genuine per-row value and ADR-0062 handles it correctly.

    This is the test that separates a fix from a retreat: the narrowing must not switch off the
    rule it narrows.
    """
    job, processor, design = _design(facts, row_carries_total=False)
    accrued = next(c for c in design.composite_types if c.name == "AccruedCategoryInterest")

    assert [c.cobol_field_name for c in accrued.computed_fields] == ["WS-MONTHLY-INT"]
    assert misplaced_accumulators(job, design) == []
    # And removing it is still refused, so ADR-0062's rule is intact for row-grain values.
    stripped = accrued.model_copy(update={"computed_fields": []})
    weakened = design.model_copy(update={"composite_types": [stripped, design.composite_types[1]]})
    assert undeliverable_computed_values(job, processor, weakened) == ["WS-MONTHLY-INT"]


def test_the_owner_is_derived_from_the_attached_control_break(facts) -> None:
    """No new declared field: the fact was already on the step and simply never consulted."""
    _job, _processor, design = _design(facts, row_carries_total=False)

    assert design.accumulator_owners(PROGRAM) == {"WS-TOTAL-INT": "postAccountInterest"}
    assert design.accumulator_owners("CBTRN02C") == {}


def test_a_job_with_no_control_break_is_unaffected(facts) -> None:
    """A program whose idiom `parsing/control_break.py` does not recognise gets no break at all,
    and this check must then report nothing rather than guessing which value is an accumulator."""
    job, _processor, design = _design(facts, row_carries_total=True)
    without = job.steps[1].model_copy(update={"control_break": None})
    stripped_job = job.model_copy(update={"steps": [job.steps[0], without]})
    stripped = design.model_copy(update={"batch_jobs": [stripped_job]})

    assert stripped.accumulator_owners(PROGRAM) == {}
    assert misplaced_accumulators(stripped_job, stripped) == []


# --- the state the checks actually run in -------------------------------------------------------

#: What `build_accumulator_paragraphs` reads straight from `CBACT04C`.
FROM_COBOL = {"WS-TOTAL-INT": "1050-UPDATE-ACCOUNT"}


def test_the_owner_resolves_before_the_control_break_is_attached(facts) -> None:
    """**The test that would have caught the defect a live run found.**

    `attach_control_breaks` runs *after* `parse_with_repair`, so when the design is validated no
    step carries a `ControlBreakDesign` yet. Resolving the owner from `step.control_break` therefore
    returned nothing for every step, the excusal never applied, and run
    `step52-cbact04c-20260904-144214` was refused for `WS-TOTAL-INT` -- the exact value ADR-0063
    exists to excuse -- and stopped at `safe_stop` having produced no design.

    Every other test in this module built the design with the break already attached, which is the
    *post*-attachment state and not the one the check runs in. They all passed. The suite was
    measuring a state the production path never reaches.
    """
    job, processor, design = _design(facts, row_carries_total=False, attach_break=False)

    assert all(step.control_break is None for step in job.steps), "fixture must be pre-attachment"
    assert design.accumulator_owners(PROGRAM, FROM_COBOL) == {"WS-TOTAL-INT": "postAccountInterest"}
    assert undeliverable_computed_values(job, processor, design, FROM_COBOL) == []


def test_pre_attachment_still_refuses_the_row_grain_accumulator(facts) -> None:
    """The refusing half must work in the same state, or it only fires after the gate."""
    job, _processor, design = _design(facts, row_carries_total=True, attach_break=False)

    assert misplaced_accumulators(job, design, FROM_COBOL) == [
        ("AccruedCategoryInterest", "WS-TOTAL-INT", "postAccountInterest")
    ]


def test_without_the_cobol_map_pre_attachment_nothing_is_excused(facts) -> None:
    """The regression itself, pinned: no map and no attached break means no owner is known.

    Kept as a test rather than a comment because it is the precise shape of the defect -- the
    check silently degrades to ADR-0062's behaviour and refuses a correct design.
    """
    job, processor, design = _design(facts, row_carries_total=False, attach_break=False)

    assert design.accumulator_owners(PROGRAM) == {}
    assert undeliverable_computed_values(job, processor, design) == ["WS-TOTAL-INT"]
