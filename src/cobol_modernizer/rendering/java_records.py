"""Render a `DomainEntity` as a Java record -- deterministically, with no model call.

**Why a record and not a JPA `@Entity`.** A JPA entity requires an identifier, and a COBOL copybook
does not declare one. `CVACT01Y` says what bytes an account record contains; it does not say which
field is the primary key, and inferring one from a name that merely looks key-ish is precisely the
kind of guess this repo fails loudly on rather than makes. Entity identity is decided at step 40a,
against the real data files, where `XREF-FILE`'s keyed lookups and the verified per-file record
lengths are actual evidence. Until then this renders the part that *is* deterministic -- the record
shape, the types, and the provenance -- and leaves the part that is not to a step that will have
grounds to decide it.

That is also why precision and scale appear in Javadoc rather than in a `@Column`: the annotation
would be an assertion about a schema no one has defined yet. `tools/pic_mapper.py` computed those
numbers and step 40a will turn them into `NUMERIC(p,s)`; carrying them here as documented fact
keeps them visible without pretending the mapping exists.

**Provenance is rendered, not optional.** Every record names its source copybook and the programs
that `COPY` it, and every component names the COBOL field it came from. `CLAUDE.md` requires a
generated artifact to trace back to its COBOL source; ADR-0006 scopes that to source-label level
for now, and this is that level, in the generated file itself rather than only in a side-channel.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from cobol_modernizer.core.contracts import (
    CompositeType,
    ComputedValue,
    DomainEntity,
    DomainField,
)
from cobol_modernizer.rendering.java_names import (
    UnrenderableJavaNameError,
    require_java_identifier,
)

logger = logging.getLogger(__name__)


class UnresolvedComputedValueError(Exception):
    """A composite declares a computed field naming a value the design does not carry.

    A backstop rather than the first line of defence: `UnifiedDesign.unresolvable_type_names`
    refuses this where the design is *produced*, before a human approves it (ADR-0020 decision 5,
    ADR-0059's shape). Reaching here means a design was assembled some other way -- by hand, or
    loaded from a `design.json` older than the composite that references it -- and the honest
    response is still to fail loudly, because the alternative is inventing a Java type for a
    currency field.
    """


__all__ = [
    "UnrenderableJavaNameError",
    "UnresolvedComputedValueError",
    "render_composite",
    "render_record",
]


def render_composite(
    composite: CompositeType,
    *,
    package: str,
    computed_values: Sequence[ComputedValue] = (),
) -> str:
    """Render a `CompositeType` as a Java record of other records and computed values (ADR-0020).

    **Its provenance names the entities it composes, never a source copybook.** A composite is a
    target-side invention -- "a `TranCatBal` with its `Account` resolved" corresponds to no copybook
    -- so claiming one would satisfy `CLAUDE.md`'s trace-to-source requirement by pointing at
    something that does not exist. Naming the composed entities keeps the trace real: each of them
    carries its own copybook, one hop away.

    A computed component traces to a real COBOL field too, just not a copybook one, so its `@param`
    names the working-storage field it carries (ADR-0062). Its Java type comes from
    `computed_values` -- `pic_mapper`'s answer -- and never from this function, which is the whole
    point of the field existing: a `COMPUTE`'s target scale must be computed and handed over, not
    read off a `PIC` clause by whatever writes the body.

    Deterministic for the same reason `render_record` is: this is a mechanical transform of a
    declaration, and a model asked to perform it would vary between runs for no gain.
    """
    class_name = require_java_identifier(
        composite.name, source_name="(composite)", kind="Composite name"
    )
    for component in composite.components:
        require_java_identifier(
            component.field_name, source_name=component.entity_name, kind="Component name"
        )

    by_cobol_name = {value.cobol_field_name.upper(): value for value in computed_values}
    resolved: list[tuple[str, ComputedValue]] = []
    for computed in composite.computed_fields:
        require_java_identifier(
            computed.field_name, source_name=computed.cobol_field_name, kind="Computed field name"
        )
        value = by_cobol_name.get(computed.cobol_field_name.upper())
        if value is None:
            raise UnresolvedComputedValueError(
                f"composite {composite.name!r} declares computed field "
                f"{computed.field_name!r} carrying {computed.cobol_field_name!r}, which is not in "
                "the design's computed_values. A computed field's Java type, precision and scale "
                "come from pic_mapper; there is nothing to render it from."
            )
        resolved.append((computed.field_name, value))

    composed = ", ".join(component.entity_name for component in composite.components)
    lines = [f"package {package};", ""]

    # Entity components need no import -- every domain type is rendered into this same package.
    # A computed component is the first thing a composite can hold that is not one of them, and
    # `BigDecimal` is `java.math`. Rendering the field without this produces a record that reads
    # correctly and does not compile, which the first render of `AccruedCategoryInterest` did.
    if any(value.java_type == "BigDecimal" for _name, value in resolved):
        lines += ["import java.math.BigDecimal;", ""]

    lines += [
        "/**",
        f" * {class_name} -- a composite of {composed or '(no records)'}.",
        " *",
        " * <p>Target-side only: this type composes records that travel together through a batch",
        " * step chain, and corresponds to no single COBOL copybook. Each component below carries",
        " * its own copybook provenance; this record does not claim one of its own.",
        " *",
    ]
    lines += [
        f" * @param {component.field_name} the {component.entity_name} record"
        for component in composite.components
    ]
    lines += [
        f" * @param {field_name} {value.cobol_field_name} "
        f"({value.java_type}, precision {value.precision}, scale {value.scale}), computed by "
        f"{', '.join(value.computed_in_paragraphs)}"
        for field_name, value in resolved
    ]
    lines += [" */"]

    parameters = [
        f"        {component.entity_name} {component.field_name}"
        for component in composite.components
    ] + [f"        {value.java_type} {field_name}" for field_name, value in resolved]

    if not parameters:
        lines += [f"public record {class_name}() {{}}", ""]
        return "\n".join(lines)

    lines += [f"public record {class_name}("]
    lines += [",\n".join(parameters)]
    lines += [") {}", ""]

    logger.debug(
        "rendered composite %s from %d entity component(s) and %d computed value(s)",
        class_name,
        len(composite.components),
        len(resolved),
    )
    return "\n".join(lines)


def _component_doc(field: DomainField) -> str:
    """One `@param` line: the COBOL origin, plus the computed numeric shape when there is one."""
    detail = f"from COBOL {field.cobol_field_name}"
    if field.precision is not None:
        signedness = "signed" if field.signed else "unsigned"
        detail += f"; PIC precision {field.precision}, scale {field.scale}, {signedness}"
    elif field.length is not None:
        # The declared width, so a reviewer of generated code can see that a shorter value is not
        # the same record on disk (G28). Numerics carry precision/scale above for the same reason.
        detail += f"; PIC X({field.length}), space-padded to that width"
    return f" * @param {field.java_field_name} {detail}"


def render_record(entity: DomainEntity, *, package: str) -> str:
    """Render `entity` as a Java record source file.

    Pure: the same `DomainEntity` renders byte-identically every time, which is what makes the
    output reviewable once rather than per-run. Raises `UnrenderableJavaNameError` rather than
    emitting a file that will not compile.
    """
    class_name = require_java_identifier(
        entity.name, source_name=entity.source_copybook, kind="Entity name"
    )
    for field in entity.fields:
        require_java_identifier(
            field.java_field_name, source_name=field.cobol_field_name, kind="Field name"
        )

    lines: list[str] = [f"package {package};", ""]

    if any(field.java_type == "BigDecimal" for field in entity.fields):
        lines += ["import java.math.BigDecimal;", ""]

    used_by = ", ".join(entity.used_by_programs)
    preamble = f"""\
/**
 * {class_name} -- generated from copybook {entity.source_copybook}.
 *
 * <p>Used by: {used_by}.
 *
 * <p>Field types, precision and scale are computed from the COBOL PIC clauses by pic_mapper,
 * not inferred by a model. Persistence mapping is deliberately absent: a copybook does not
 * declare a primary key, so entity identity is decided against the real data files rather
 * than guessed from a field name.
 *"""
    lines += preamble.splitlines()
    lines += [_component_doc(field) for field in entity.fields]
    lines += [" */"]

    if not entity.fields:
        # A record with no components is legal Java and is the honest rendering of an entity that
        # contributed no successfully-mapped fields. solution_architect drops those before they
        # reach here (ADR-0010), so this is a defensive shape, not an expected one.
        lines += [f"public record {class_name}() {{}}", ""]
        return "\n".join(lines)

    lines += [f"public record {class_name}("]
    components = [f"        {f.java_type} {f.java_field_name}" for f in entity.fields]
    lines += [",\n".join(components)]
    lines += [") {}", ""]

    logger.debug(
        "rendered record %s from %s (%d field(s))",
        class_name,
        entity.source_copybook,
        len(entity.fields),
    )
    return "\n".join(lines)
