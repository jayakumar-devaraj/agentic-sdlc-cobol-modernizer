"""Render the reader that turns a stream into one item per control-break group.

**This is ADR-0027's "already-summed item", generated.** That ADR moved the accumulation out of the
processor -- a stateless `ItemProcessor` cannot hold a running total, and Spring Batch's chunk
boundaries do not align with COBOL's account breaks -- and into the reader, where the item arriving
is one group with its total already computed. Until the control break was parsed, nothing said what
to group by or what to sum, so that reader was hand-written.

**Everything here comes from the parsed break** (`ControlBreakDesign`): the key to group on, the
field the accumulated value lands in, and therefore what to add up. The only construction decision
is what to do with the *other* fields of the summed record, and it is made by copying rather than
inventing -- see `_carrier`.

**The equality this rests on is COBOL's own.** `WS-TOTAL-INT` accumulates `WS-MONTHLY-INT`, and every
`WS-MONTHLY-INT` is moved to `TRAN-AMT` under the same guard, so the sum of a group's `TRAN-AMT` *is*
`WS-TOTAL-INT` at the break. ADR-0027 argued that from the source; this renders it.
"""

from __future__ import annotations

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    CompositeType,
    DomainEntity,
    UnifiedDesign,
)
from cobol_modernizer.rendering.java_job import staging_class_name
from cobol_modernizer.rendering.java_names import require_java_identifier
from cobol_modernizer.rendering.java_reader import (
    UnrenderableReaderError,
    _entity,
    _field_width,
    locate_item_field,
)

_INDENT = " " * 4


class UnrenderableAggregationError(Exception):
    """A control-break aggregation cannot be rendered without inventing something.

    Raised when the step carries no control break, when the source type cannot reach the break key
    or the accumulated field, and when the output type needs a component the source stream does not
    carry. Each is a fact about the design rather than about the COBOL, and each has a shape a human
    can act on -- widen a composite, or point the step at a different stream.
    """


def aggregating_reader_class_name(step: BatchStepDesign) -> str:
    """`postAccountInterest` -> `PostAccountInterestItemReader`, the same shape as any reader's."""
    base = step.step_name[:1].upper() + step.step_name[1:]
    return f"{base}ItemReader"


def _composite(design: UnifiedDesign, name: str) -> CompositeType | None:
    return next((c for c in design.composite_types if c.name == name), None)


def _carrier(
    entity: DomainEntity, landing_java_field: str, domain_package: str, holder: str | None
) -> str:
    """The summed record: the group's first one, with the accumulated column replaced by the total.

    **Copied rather than fabricated.** The hand-written version filled every other field with
    PIC-width spaces and zeros, which is defensible when the consuming body reads only the total --
    and it is an invention either way. Taking the group's first record carries values that exist, so
    a body that reads more than the total sees real data rather than padding, and nothing here has
    to decide what a neutral value looks like.
    """
    # `first` is the source *item*, so its fields are reached through the component that holds this
    # entity -- `first.tran().tranId()`, not `first.tranId()`. Getting that wrong is a compile error
    # rather than a silent one, which is the only reason it was cheap to find.
    source = f"first.{holder}()" if holder else "first"
    arguments = []
    for field in entity.fields:
        _field_width(field)  # refuses a field whose width is unknown, before it reaches Java
        if field.java_field_name == landing_java_field:
            arguments.append(f"{_INDENT * 4}total")
        else:
            arguments.append(f"{_INDENT * 4}{source}.{field.java_field_name}()")
    joined = ",\n".join(arguments)
    return f"new {domain_package}.{entity.name}(\n{joined})"


def render_aggregating_reader(
    step: BatchStepDesign,
    source_step: BatchStepDesign,
    design: UnifiedDesign,
    *,
    package: str,
    domain_package: str,
    staging_package: str,
) -> str:
    """Render the group-by-and-sum reader for `step`, over `source_step`'s staged output.

    Raises:
        UnrenderableAggregationError: any fact the design does not carry.
    """
    control = step.control_break
    if control is None:
        raise UnrenderableAggregationError(
            f"step {step.step_name!r} has no control break, so there is nothing to group by"
        )
    class_name = aggregating_reader_class_name(step)
    require_java_identifier(class_name, source_name=step.step_name, kind="Reader class name")

    source_type = source_step.output_type
    key = locate_item_field(design, source_type, control.break_key_field)
    if key is None:
        raise UnrenderableAggregationError(
            f"{source_type!r} cannot reach {control.break_key_field!r}, so a reader over it cannot "
            "group. Widen that type, or point this step at a stream that carries it"
        )

    # **What to sum, in the same two forms and the same order as `aggregation_blockers`.** The value
    # itself where the stream carries it as a computed field (ADR-0062), else the record column it
    # is moved into. Choosing differently here than the planner did would render a reader for a step
    # nothing planned, which is the class of defect this pair exists to keep closed.
    summed_field = control.accumulated_from_field
    summed = locate_item_field(design, source_type, summed_field)
    if summed is None and control.landing_field:
        summed_field = control.landing_field
        summed = locate_item_field(design, source_type, summed_field)
    if summed is None:
        if control.landing_field is None:
            raise UnrenderableAggregationError(
                f"step {step.step_name!r} accumulates {control.accumulated_from_field!r}, which "
                f"{source_type!r} does not carry and which is never moved into a record field -- so "
                "the total exists only in a program variable and no stream carries it"
            )
        raise UnrenderableAggregationError(
            f"{source_type!r} cannot reach {control.landing_field!r}, so a reader over it cannot "
            "sum. Widen that type, or point this step at a stream that carries it"
        )
    total_accessor = summed.accessor
    landing_entity = summed.entity
    landing_component = summed.component
    key_accessor = key.accessor

    output = _composite(design, step.input_type)
    if output is None:
        raise UnrenderableAggregationError(
            f"step {step.step_name!r} consumes {step.input_type!r}, which is not a declared "
            "composite; an aggregation produces one item per group and needs to know its shape"
        )

    arguments: list[str] = []
    for component in output.components:
        try:
            _entity(design, component.entity_name)
        except UnrenderableReaderError as exc:
            raise UnrenderableAggregationError(str(exc)) from exc

        if landing_entity is not None and component.entity_name == landing_entity.name:
            # The column the total replaces. Resolved here rather than above because it exists only
            # where the summed value travels inside a record: a stream carrying the value itself has
            # no column to replace, and `landing_entity` is `None` in exactly that case.
            landing_field_java = next(
                f.java_field_name
                for f in landing_entity.fields
                if f.cobol_field_name == summed_field
            )
            arguments.append(
                f"{_INDENT * 3}"
                f"{_carrier(landing_entity, landing_field_java, domain_package, landing_component)}"
            )
            continue
        source_composite = _composite(design, source_type)
        holder = next(
            (
                candidate.field_name
                for candidate in (source_composite.components if source_composite else [])
                if candidate.entity_name == component.entity_name
            ),
            None,
        )
        if holder is None:
            raise UnrenderableAggregationError(
                f"the group item needs a {component.entity_name!r} and {source_type!r} carries "
                "none, so there is nowhere to take one from"
            )
        arguments.append(f"{_INDENT * 3}first.{holder}()")

    for computed in output.computed_fields:
        # **Where the total goes when the group item declares it** (ADR-0063). An accumulator is a
        # property of the group, so the item that may carry it is exactly this one -- and it is
        # filled by summing rather than by copying, which is the whole difference between this and
        # `_carrier` above. Any *other* computed field here would be a row-grain value on a
        # group-grain item, which is the defect ADR-0063 was written about; refused rather than
        # taken from the group's first record, where it would look right and be one row's number.
        if computed.cobol_field_name.upper() != control.accumulator_field.upper():
            raise UnrenderableAggregationError(
                f"the group item {step.input_type!r} declares computed field "
                f"{computed.field_name!r} carrying {computed.cobol_field_name!r}, which is not this "
                f"break's accumulator ({control.accumulator_field!r}). An aggregation produces one "
                "item per group and has no row-grain value to put there (ADR-0063)"
            )
        arguments.append(f"{_INDENT * 3}total")

    # The javadoc states the equality this reader rests on, and there are two of them. Stating the
    # `MOVE` one over a stream that carries the value directly would be a claim about the COBOL that
    # this render did not use -- the same class of wrong sentence as the accumulation javadoc
    # ADR-0063 was written about.
    equality = (
        f"moves every one of them into {control.landing_field}, so the sum of a group's "
        f"{control.landing_field}"
        if summed_field != control.accumulated_from_field
        else f"the stream carries each one, so the sum of a group's {summed_field}"
    )

    constructed = ",\n".join(arguments)
    staging = staging_class_name(source_type)
    qualified_source = f"{domain_package}.{source_type}"
    qualified_output = f"{domain_package}.{step.input_type}"

    return f"""package {package};

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import org.springframework.batch.infrastructure.item.ItemReader;

/**
 * {class_name} -- one item per control-break group, for step "{step.step_name}".
 *
 * <p>Rendered from the control break {control.performed_paragraph} runs at
 * ({control.break_key_field}, line {control.test_line}). The COBOL accumulates
 * {control.accumulated_from_field} into {control.accumulator_field} and
 * {equality} is that accumulator at the break -- which is what makes this a
 * re-ordering of the original rather than a re-implementation (ADR-0027).
 *
 * <p>Groups arrive in key order, matching a program that reads its driving file by key.
 */
public class {class_name} implements ItemReader<{qualified_output}> {{

{_INDENT}private final {staging_package}.{staging} source;
{_INDENT}private List<{qualified_output}> groups;
{_INDENT}private int next;

{_INDENT}public {class_name}({staging_package}.{staging} source) {{
{_INDENT * 2}this.source = source;
{_INDENT}}}

{_INDENT}@Override
{_INDENT}public {qualified_output} read() {{
{_INDENT * 2}if (groups == null) {{
{_INDENT * 3}groups = aggregate();
{_INDENT * 2}}}
{_INDENT * 2}return next < groups.size() ? groups.get(next++) : null;
{_INDENT}}}

{_INDENT}private List<{qualified_output}> aggregate() {{
{_INDENT * 2}Map<BigDecimal, BigDecimal> totals = new TreeMap<>();
{_INDENT * 2}Map<BigDecimal, {qualified_source}> firsts = new LinkedHashMap<>();
{_INDENT * 2}for ({qualified_source} item : source.items()) {{
{_INDENT * 3}BigDecimal key = {key_accessor};
{_INDENT * 3}totals.merge(key, {total_accessor}, BigDecimal::add);
{_INDENT * 3}firsts.putIfAbsent(key, item);
{_INDENT * 2}}}

{_INDENT * 2}List<{qualified_output}> aggregated = new ArrayList<>(totals.size());
{_INDENT * 2}for (Map.Entry<BigDecimal, BigDecimal> entry : totals.entrySet()) {{
{_INDENT * 3}{qualified_source} first = firsts.get(entry.getKey());
{_INDENT * 3}BigDecimal total = entry.getValue();
{_INDENT * 3}aggregated.add(
{_INDENT * 4}new {qualified_output}(
{constructed}));
{_INDENT * 2}}}
{_INDENT * 2}return aggregated;
{_INDENT}}}
}}
"""
