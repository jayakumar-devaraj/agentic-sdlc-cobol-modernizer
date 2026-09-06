"""The rendered control-break aggregation (ADR-0032's amendment) -- ADR-0027's item, generated.

**What this completes.** ADR-0027 moved the accumulation out of the processor and into the reader,
so the item arriving is one account with its interest already summed. Until the control break was
parsed, nothing said what to group by or what to sum, and that reader stayed hand-written. It is
rendered now, and the round trip runs on it: 500 of 500 transaction fields and 598 of 600 account
fields, unchanged.

**The equality it rests on is COBOL's own.** `WS-TOTAL-INT` accumulates `WS-MONTHLY-INT`, and every
`WS-MONTHLY-INT` is moved into `TRAN-AMT` under the same guard -- so the sum of a group's `TRAN-AMT`
*is* that accumulator at the break. That is what makes this a re-ordering of the original rather
than a re-implementation of it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
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
from cobol_modernizer.rendering.java_aggregation import (
    UnrenderableAggregationError,
    aggregating_reader_class_name,
    render_aggregating_reader,
)
from cobol_modernizer.rendering.java_job import aggregating_reader_class_name as job_side_name
from cobol_modernizer.rendering.java_job import aggregation_source
from tests.support.interest_design import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)
from tests.support.posting_design import POSTING
from tests.support.posting_design import STEP as POSTING_STEP

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
READERS = "com.modernized.batch.reader"
DOMAIN = "com.modernized.batch.domain"
JOBS = "com.modernized.batch.job"


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


def render(design: UnifiedDesign, step=None) -> str:
    job = design.batch_jobs[0]
    step = step or next(s for s in job.steps if s.step_name == "postAccountInterest")
    source = aggregation_source(job, step, design)
    return render_aggregating_reader(
        step,
        source,
        design,
        package=READERS,
        domain_package=DOMAIN,
        staging_package=JOBS,
    )


# --- what it emits ---------------------------------------------------------------------------------


def test_it_groups_on_the_parsed_break_key(design):
    """`TRANCAT-ACCT-ID`, reached through the composite component that carries it.

    The accessor is derived: the break gives a COBOL field name, and the path to it is whichever
    component of the source type owns that field.
    """
    assert "BigDecimal key = item.balance().trancatAcctId();" in render(design)


def test_it_sums_the_field_the_accumulated_value_lands_in(design):
    """`WS-TOTAL-INT` is a program variable no record has; `TRAN-AMT` is the column to add up."""
    assert "totals.merge(key, item.tran().tranAmt(), BigDecimal::add);" in render(design)


def test_the_summed_record_copies_the_group_rather_than_inventing_padding(design):
    """The other fields come from the group's first record, through the component that holds it.

    The hand-written version filled them with PIC-width spaces and zeros. Both are choices; copying
    carries values that exist, so a body reading more than the total sees real data. Reaching them
    as `first.tranId()` instead of `first.tran().tranId()` was a real bug here, caught by javac.
    """
    rendered = render(design)
    assert "first.tran().tranId()" in rendered
    assert "\n                total," in rendered or "total," in rendered
    assert "CobolText.spaces" not in rendered


def test_the_group_carries_the_other_components_from_the_same_item(design):
    """`AccountInterestPosting` needs an `Account`, and the source stream has one per record."""
    assert "first.account()" in render(design)


def test_groups_arrive_in_key_order(design):
    """A `TreeMap` on the break key, matching a program that reads its driving file by key.

    Order is load-bearing here: the account file is compared record for record against what COBOL
    wrote, and COBOL wrote it in key order.
    """
    assert "new TreeMap<>()" in render(design)


def test_the_class_name_matches_what_the_job_renderer_expects(design):
    """Two modules name this class, and they must agree.

    `java_job` cannot import `java_aggregation` -- that would be circular -- so it carries its own
    copy of the transform. This is the test that keeps the duplicate honest.
    """
    step = next(s for s in design.batch_jobs[0].steps if s.step_name == "postAccountInterest")
    assert aggregating_reader_class_name(step) == "PostAccountInterestItemReader"
    assert job_side_name(step) == aggregating_reader_class_name(step)


def test_the_provenance_names_the_break_it_came_from(design):
    """`CLAUDE.md`'s rule: the generated file traces back to the line it was derived from."""
    rendered = render(design)
    assert "1050-UPDATE-ACCOUNT" in rendered
    assert "TRANCAT-ACCT-ID, line 194" in rendered


# --- ADR-0063's shape: the value on the stream, the total on the group -----------------------------


def _adr63(design: UnifiedDesign, *, group_also_carries_the_row_value: bool = False) -> UnifiedDesign:
    """The design as ADR-0063 requires it, rather than as ADR-0027 could express it.

    Two changes, and they are the two that ADR-0062's `computed_fields` made possible. The source
    stream returns `WS-MONTHLY-INT` itself instead of carrying it inside a `Tran`, and the group
    item declares `WS-TOTAL-INT` instead of holding a `Tran` whose amount column gets overwritten.
    `TranWithContext` keeps its `balance` component, because the break key is still a record field.
    """
    source = OUTPUT_COMPOSITE.model_copy(
        update={
            "components": [c for c in OUTPUT_COMPOSITE.components if c.entity_name != "Tran"],
            "computed_fields": [
                ComputedComponent(field_name="monthlyInterest", cobol_field_name="WS-MONTHLY-INT")
            ],
        }
    )
    group = POSTING.model_copy(
        update={
            "components": [c for c in POSTING.components if c.entity_name != "Tran"],
            "computed_fields": [
                ComputedComponent(field_name="totalInterest", cobol_field_name="WS-TOTAL-INT"),
                *(
                    [ComputedComponent(field_name="monthlyInterest", cobol_field_name="WS-MONTHLY-INT")]
                    if group_also_carries_the_row_value
                    else []
                ),
            ],
        }
    )
    return design.model_copy(update={"composite_types": [COMPOSITE, source, group]})


def test_it_sums_the_value_the_stream_carries_when_no_column_holds_it(design):
    """`item.monthlyInterest()`, not `item.tran().tranAmt()`.

    This is the whole defect. `aggregation_source` walks back to the nearest stream carrying what it
    groups by and what it sums, and it asked only for the landing column -- so on a design obeying
    ADR-0063 it found nothing, returned `None`, and the step fell through to a file reader that
    correctly refused an in-memory aggregate.
    """
    rendered = render(_adr63(design))
    assert "totals.merge(key, item.monthlyInterest(), BigDecimal::add);" in rendered
    assert "tranAmt" not in rendered


def test_the_total_lands_in_the_group_items_accumulator_and_nothing_is_copied_into_it(design):
    """`new AccountInterestPosting(first.account(), total)` -- ADR-0027's item, named by ADR-0063.

    The old shape had to overwrite one column of a copied `Tran`, because a composite could carry
    nothing but records. With the accumulator declared on the group item there is no record to copy
    and no column to overwrite, so `_carrier` does not run at all.
    """
    rendered = render(_adr63(design))
    assert "first.account()" in rendered
    assert "total));" in rendered
    # `_carrier` copies every field of the landing entity; if it had run, these would be here.
    assert "first.tran()" not in rendered


def test_the_javadoc_does_not_claim_a_move_this_render_did_not_use(design):
    """A sentence about the COBOL has to be true of the reader underneath it.

    The old javadoc always said the value is moved into `TRAN-AMT` and that the sum of a group's
    `TRAN-AMT` is the accumulator. That is still true of the program and no longer describes what
    this reader adds up -- which is the same class of wrong sentence as the accumulation javadoc
    ADR-0063 was written about.
    """
    rendered = render(_adr63(design))
    assert "the stream carries each one, so the sum of a group's WS-MONTHLY-INT" in rendered
    assert "moves every one of them into TRAN-AMT" not in rendered


def test_a_row_grain_computed_field_on_the_group_item_is_refused(design):
    """Step 51's defect, arriving from the other direction.

    ADR-0063 refuses `WS-TOTAL-INT` on the row item at design time. This is the mirror: a
    *row-grain* value declared on the group item, where the only honest source would be one
    record of the group -- a number that looks right and is one row's.
    """
    with pytest.raises(UnrenderableAggregationError, match="no row-grain value to put there"):
        render(_adr63(design, group_also_carries_the_row_value=True))


# --- the refusals ----------------------------------------------------------------------------------


def test_a_step_with_no_control_break_is_refused(design):
    """Without a break there is nothing to group by, and grouping by anything else is a guess."""
    with pytest.raises(UnrenderableAggregationError, match="no control break"):
        render_aggregating_reader(
            COMPLETE_STEP,
            STEP,
            design,
            package=READERS,
            domain_package=DOMAIN,
            staging_package=JOBS,
        )


def test_an_accumulator_that_never_reaches_a_record_is_refused(design):
    """A total living only in a program variable is not summable from any stream.

    The parse records that as `landing_field = None`, and this is what happens next: a refusal
    naming the accumulated field rather than a reader that sums something arbitrary.
    """
    job = design.batch_jobs[0]
    step = next(s for s in job.steps if s.step_name == "postAccountInterest")
    stranded = step.model_copy(
        update={"control_break": step.control_break.model_copy(update={"landing_field": None})}
    )
    with pytest.raises(UnrenderableAggregationError, match="never moved into a record field"):
        render_aggregating_reader(
            stranded,
            STEP,
            design,
            package=READERS,
            domain_package=DOMAIN,
            staging_package=JOBS,
        )


def test_a_source_that_cannot_reach_the_break_key_is_refused(design):
    """The state this design was in before the composite was widened.

    `Tran` carries the amount and not the account id, so a reader over it could sum and not group.
    """
    job = design.batch_jobs[0]
    step = next(s for s in job.steps if s.step_name == "postAccountInterest")
    with pytest.raises(UnrenderableAggregationError, match="cannot reach 'TRANCAT-ACCT-ID'"):
        render_aggregating_reader(
            step,
            COMPLETE_STEP,
            design,
            package=READERS,
            domain_package=DOMAIN,
            staging_package=JOBS,
        )


def test_an_output_needing_a_component_the_stream_does_not_carry_is_refused(design):
    """The group item is built from one record of the group, so every component has to be in it."""
    # `DisGroup` is a real entity of this design -- it is a component of the *input* composite --
    # and `TranWithContext` does not carry one. An entity the design does not have at all fails
    # earlier and for a different reason, which would have made this test pass without exercising
    # the case it names.
    widened_output = CompositeType(
        name="AccountInterestPosting",
        components=[
            *POSTING.components,
            CompositeComponent(field_name="rate", entity_name="DisGroup"),
        ],
    )
    broken = design.model_copy(
        update={"composite_types": [COMPOSITE, OUTPUT_COMPOSITE, widened_output]}
    )
    with pytest.raises(UnrenderableAggregationError, match="carries none"):
        render(broken)


def test_an_output_that_is_not_a_composite_is_refused(design):
    """An aggregation produces one item per group and has to know that item's shape."""
    job = design.batch_jobs[0]
    step = next(s for s in job.steps if s.step_name == "postAccountInterest")
    plain = step.model_copy(update={"input_type": "Account"})
    with pytest.raises(UnrenderableAggregationError, match="not a declared composite"):
        render_aggregating_reader(
            plain,
            STEP,
            design,
            package=READERS,
            domain_package=DOMAIN,
            staging_package=JOBS,
        )


def test_an_output_component_naming_an_entity_the_design_lacks_is_refused(design):
    """A different failure from the one above, and worth both tests.

    `DisGroup` exists and is simply not on this stream; `Customer` is not in this design at all. The
    second fails earlier, with a message about the design rather than about the stream -- and if the
    two were not tested separately, the earlier check would silently satisfy the later one's test.
    """
    ghost_output = CompositeType(
        name="AccountInterestPosting",
        components=[
            *POSTING.components,
            CompositeComponent(field_name="customer", entity_name="Customer"),
        ],
    )
    broken = design.model_copy(
        update={"composite_types": [COMPOSITE, OUTPUT_COMPOSITE, ghost_output]}
    )
    with pytest.raises(UnrenderableAggregationError, match="no domain entity 'Customer'"):
        render(broken)


def test_a_source_component_naming_an_entity_the_design_lacks_is_skipped_then_refused(design):
    """The accessor search walks past a component it cannot resolve rather than crashing on it.

    It then refuses for the honest reason -- the break key is unreachable -- instead of reporting a
    missing entity the caller did not ask about.
    """
    ghost_source = CompositeType(
        name="TranWithContext",
        components=[
            CompositeComponent(field_name="ghost", entity_name="NoSuchEntity"),
            CompositeComponent(field_name="tran", entity_name="Tran"),
            CompositeComponent(field_name="account", entity_name="Account"),
        ],
    )
    broken = design.model_copy(
        update={"composite_types": [COMPOSITE, ghost_source, POSTING]}
    )
    job = broken.batch_jobs[0]
    step = next(s for s in job.steps if s.step_name == "postAccountInterest")
    with pytest.raises(UnrenderableAggregationError, match="cannot reach 'TRANCAT-ACCT-ID'"):
        render_aggregating_reader(
            step,
            STEP,
            broken,
            package=READERS,
            domain_package=DOMAIN,
            staging_package=JOBS,
        )
