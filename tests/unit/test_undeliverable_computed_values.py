"""The refusal step 49 needed: a processor that computes a value it cannot return.

`ComputeMonthlyInterestProcessor` in `card-service`'s first pushed branch does this::

    BigDecimal monthlyInterest = CobolArithmetic.requireFits(...);   // computed
    return item;                                                     // and discarded

Its javadoc claims it "accumulates it into the account's running month total". Nothing
accumulates. It compiles, because discarding a value is legal Java, and every component that
produced it behaved correctly -- the design typed the step `in = out = RatedCategoryBalance`,
which is a record with no field to carry the result.

These tests run the real deterministic facts (`build_computed_values` over the real `CBACT04C`
and `CBTRN02C`) against real design shapes, because the two mistakes worth catching are both about
which designs must *not* be refused: a filtering X -> X processor, and a value that never leaves
the paragraph that computes it.
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
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_computed_values,
    build_domain_entities,
    undeliverable_computed_values,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
INTEREST_PARAGRAPHS = ["1300-COMPUTE-INTEREST", "1400-COMPUTE-FEES"]

RATED = CompositeType(
    name="RatedCategoryBalance",
    components=[
        CompositeComponent(field_name="categoryBalance", entity_name="TranCatBal"),
        CompositeComponent(field_name="disclosureGroup", entity_name="DisGroup"),
    ],
)


def _entry(program: str) -> ProgramDesignEntry:
    extraction = extract_spec(
        FIXTURE_ROOT,
        program,
        narrate=lambda m, s, u: u.split(f'<untrusted-cobol-source label="{program}">')[0],
    )
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return ProgramDesignEntry(program_name=program, spec_extraction=extraction, critique=critique)


@pytest.fixture(scope="module")
def facts():
    """The real deterministic layer, not a fixture standing in for it."""
    entries = [_entry("CBACT04C"), _entry("CBTRN02C")]
    return (
        build_domain_entities(FIXTURE_ROOT, entries),
        build_computed_values(FIXTURE_ROOT, entries),
    )


def _design(
    facts,
    *,
    output_type: str,
    composites: list[CompositeType],
    paragraphs: list[str] | None = None,
    role: str = "processor",
    program: str = "CBACT04C",
):
    entities, computed = facts
    step = BatchStepDesign(
        step_name="computeMonthlyInterest",
        source_paragraphs=paragraphs or INTEREST_PARAGRAPHS,
        role=role,
        description="Compute the monthly interest for a rated category balance.",
        input_type="RatedCategoryBalance",
        output_type=output_type,
        guard_condition=None,
    )
    job = BatchJobDesign(
        job_name="interestJob",
        program_name=program,
        description="Post monthly interest.",
        domain_entities=[],
        steps=[step],
    )
    design = UnifiedDesign(
        domain_entities=entities,
        batch_jobs=[job],
        rest_endpoints=[],
        composite_types=composites,
        computed_values=computed,
    )
    return job, step, design


def test_the_step_49_design_is_refused(facts) -> None:
    """The exact shape that reached `card-service`: `in = out = RatedCategoryBalance`.

    Both values are reported, not just the headline one. `WS-TOTAL-INT` is the accumulation the
    generated javadoc claimed and did not perform, and a refusal naming only `WS-MONTHLY-INT`
    would invite a design that fixes half of it.
    """
    job, step, design = _design(facts, output_type="RatedCategoryBalance", composites=[RATED])

    assert undeliverable_computed_values(job, step, design) == ["WS-MONTHLY-INT", "WS-TOTAL-INT"]


def test_declaring_the_computed_fields_satisfies_it(facts) -> None:
    """The fix the refusal message names, and the design that should have been produced."""
    accrued = CompositeType(
        name="AccruedCategoryInterest",
        components=list(RATED.components),
        computed_fields=[
            ComputedComponent(field_name="monthlyInterest", cobol_field_name="WS-MONTHLY-INT"),
            ComputedComponent(field_name="accountTotalInterest", cobol_field_name="WS-TOTAL-INT"),
        ],
    )
    job, step, design = _design(
        facts, output_type="AccruedCategoryInterest", composites=[RATED, accrued]
    )

    assert undeliverable_computed_values(job, step, design) == []


def test_a_filtering_x_to_x_processor_is_not_refused(facts) -> None:
    """The trap this check had to avoid, and why it is not a rule about `input_type == output_type`.

    `ComputeMonthlyInterestProcessor` returns `null` when the rate is zero, which is filtering, and
    filtering is a legitimate X -> X processor. Refusing identical types would be mechanical, cheap
    and wrong: it would fire on correct designs. This step declares a paragraph that computes
    nothing, so nothing is reported even though its types are identical.
    """
    job, step, design = _design(
        facts,
        output_type="RatedCategoryBalance",
        composites=[RATED],
        paragraphs=["1100-GET-ACCT-DATA"],
    )

    assert step.input_type == step.output_type
    assert undeliverable_computed_values(job, step, design) == []


def test_a_value_that_never_leaves_its_paragraph_is_not_refused(facts) -> None:
    """`CBTRN02C`'s `WS-TEMP-BAL` is computed and compared against a credit limit in one paragraph.

    A rule requiring every computed value to be carried would refuse this correct design. The
    escape analysis is what separates it from `WS-MONTHLY-INT`, which is read in `1300-B-WRITE-TX`
    -- a paragraph another step owns.
    """
    job, step, design = _design(
        facts,
        output_type="RatedCategoryBalance",
        composites=[RATED],
        paragraphs=["1500-B-LOOKUP-ACCT"],
        program="CBTRN02C",
    )

    assert undeliverable_computed_values(job, step, design) == []


def test_only_processors_answer_for_computed_values(facts) -> None:
    """A writer's output is bound by `WRITE ... FROM`, and a tasklet has no item at all.

    Without this, `CBACT01C`'s and `CBCUS01C`'s open/close tasklets would be reported for computing
    `APPL-RESULT` -- a status code that is control flow, not a business value. It is the same limit
    `unobtainable_inputs` already states for the output side.
    """
    for role in ("reader", "writer", "tasklet"):
        job, step, design = _design(
            facts, output_type="RatedCategoryBalance", composites=[RATED], role=role
        )
        assert undeliverable_computed_values(job, step, design) == [], role


def test_a_value_landing_in_a_record_the_output_carries_is_delivered(facts) -> None:
    """`WS-MONTHLY-INT` is moved into `TRAN-AMT`, so an output carrying `Tran` needs no field.

    Refusing this would force a redundant declaration and make the honest design the failing one.
    `WS-TOTAL-INT` is never moved into any record, so it is still reported -- the correct asymmetry
    rather than an inconsistency.
    """
    with_tran = CompositeType(
        name="RatedBalanceWithTransaction",
        components=[
            *RATED.components,
            CompositeComponent(field_name="transaction", entity_name="Tran"),
        ],
    )
    job, step, design = _design(
        facts, output_type="RatedBalanceWithTransaction", composites=[RATED, with_tran]
    )

    assert undeliverable_computed_values(job, step, design) == ["WS-TOTAL-INT"]
