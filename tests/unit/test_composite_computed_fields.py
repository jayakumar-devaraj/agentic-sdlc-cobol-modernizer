"""A composite can carry a value no record holds, and the renderer emits it.

ADR-0020 built `CompositeType` on an invariant: every component references an entity that already
exists, so `pic_mapper`'s precision and scale reach the generated code unchanged. That is the right
invariant and ADR-0062 keeps it. What it widened is *what already exists*: `computed_values` are
produced by the deterministic layer too, so a composite carrying one still invents nothing.

The narrow reading is what produced the defect these tests exist for. `computeMonthlyInterest` was
designed `in = out = RatedCategoryBalance` because that was the only expressible answer, and the
generated processor computed the interest and returned the item unchanged.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    ComputedComponent,
    ComputedValue,
    UnifiedDesign,
)
from cobol_modernizer.rendering.java_records import (
    UnresolvedComputedValueError,
    render_composite,
)

PACKAGE = "com.modernized.batch.domain"

MONTHLY_INTEREST = ComputedValue(
    program_name="CBACT04C",
    cobol_field_name="WS-MONTHLY-INT",
    java_type="BigDecimal",
    precision=11,
    scale=2,
    signed=True,
    computed_in_paragraphs=["1300-COMPUTE-INTEREST"],
)

ACCRUED = CompositeType(
    name="AccruedCategoryInterest",
    components=[CompositeComponent(field_name="categoryBalance", entity_name="TranCatBal")],
    computed_fields=[
        ComputedComponent(field_name="monthlyInterest", cobol_field_name="WS-MONTHLY-INT")
    ],
)


def test_the_computed_field_becomes_a_record_component_with_pic_mappers_type() -> None:
    """`BigDecimal monthlyInterest` -- and the type is `pic_mapper`'s, not the renderer's."""
    source = render_composite(ACCRUED, package=PACKAGE, computed_values=[MONTHLY_INTEREST])

    assert "public record AccruedCategoryInterest(" in source
    assert "        TranCatBal categoryBalance," in source
    assert "        BigDecimal monthlyInterest" in source


def test_the_javadoc_traces_the_computed_field_to_its_cobol() -> None:
    """A composite claims no copybook, but a computed field still names where it came from.

    `CLAUDE.md` requires every generated artifact to trace to the COBOL it derives from. A computed
    field's trace is the working-storage declaration and the paragraph whose arithmetic writes it,
    which is exactly what a reviewer needs to check `precision 11, scale 2` against the source
    instead of trusting it.
    """
    source = render_composite(ACCRUED, package=PACKAGE, computed_values=[MONTHLY_INTEREST])

    assert (
        " * @param monthlyInterest WS-MONTHLY-INT (BigDecimal, precision 11, scale 2), "
        "computed by 1300-COMPUTE-INTEREST" in source
    )


def test_an_unresolved_computed_field_is_refused_rather_than_typed_by_guess() -> None:
    """The renderer has no way to know a currency field's type, and does not pretend to.

    This is the backstop, not the gate: `unresolvable_computed_field_names` refuses the same design
    where it is produced. Reaching here means a design was assembled by hand or loaded from an
    older document, and inventing `BigDecimal` would be a guess that compiles.
    """
    with pytest.raises(UnresolvedComputedValueError, match="WS-MONTHLY-INT"):
        render_composite(ACCRUED, package=PACKAGE, computed_values=[])


def test_the_design_refuses_a_computed_field_that_resolves_to_nothing() -> None:
    """Refused where produced, in the job's program scope."""
    design = UnifiedDesign(
        domain_entities=[],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name="CBACT04C",
                description="d",
                domain_entities=[],
                steps=[
                    BatchStepDesign(
                        step_name="computeMonthlyInterest",
                        source_paragraphs=["1300-COMPUTE-INTEREST"],
                        role="processor",
                        description="d",
                        input_type="TranCatBal",
                        output_type="AccruedCategoryInterest",
                        guard_condition=None,
                    )
                ],
            )
        ],
        rest_endpoints=[],
        composite_types=[ACCRUED],
        computed_values=[],
    )

    assert design.unresolvable_computed_field_names() == ["WS-MONTHLY-INT"]
    assert "WS-MONTHLY-INT" in design.unresolvable_type_names()


def test_resolution_respects_the_program_the_job_names() -> None:
    """The same COBOL name in another program does not satisfy this composite.

    `WS-TEMP-BAL` exists in `CBTRN02C` and nothing stops a second program declaring
    `WS-MONTHLY-INT` with a different picture. Resolving across programs would hand this composite
    the wrong precision for a currency field, silently, and in the direction that still compiles.
    """
    other_program = MONTHLY_INTEREST.model_copy(update={"program_name": "CBTRN02C"})
    design = UnifiedDesign(
        domain_entities=[],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name="CBACT04C",
                description="d",
                domain_entities=[],
                steps=[
                    BatchStepDesign(
                        step_name="computeMonthlyInterest",
                        source_paragraphs=["1300-COMPUTE-INTEREST"],
                        role="processor",
                        description="d",
                        input_type="TranCatBal",
                        output_type="AccruedCategoryInterest",
                        guard_condition=None,
                    )
                ],
            )
        ],
        rest_endpoints=[],
        composite_types=[ACCRUED],
        computed_values=[other_program],
    )

    assert design.unresolvable_computed_field_names() == ["WS-MONTHLY-INT"]


def test_a_composite_with_no_computed_fields_is_unchanged() -> None:
    """Additive: every composite written before ADR-0062 renders exactly as it did."""
    plain = CompositeType(
        name="RatedCategoryBalance",
        components=[
            CompositeComponent(field_name="categoryBalance", entity_name="TranCatBal"),
            CompositeComponent(field_name="disclosureGroup", entity_name="DisGroup"),
        ],
    )

    assert plain.computed_fields == []
    assert render_composite(plain, package=PACKAGE) == render_composite(
        plain, package=PACKAGE, computed_values=[MONTHLY_INTEREST]
    )
