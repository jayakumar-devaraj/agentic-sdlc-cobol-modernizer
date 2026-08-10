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

from cobol_modernizer.core.contracts import DomainEntity, DomainField
from cobol_modernizer.rendering.java_names import (
    UnrenderableJavaNameError,
    require_java_identifier,
)

logger = logging.getLogger(__name__)

__all__ = ["UnrenderableJavaNameError", "render_record"]


def _component_doc(field: DomainField) -> str:
    """One `@param` line: the COBOL origin, plus the computed numeric shape when there is one."""
    detail = f"from COBOL {field.cobol_field_name}"
    if field.precision is not None:
        signedness = "signed" if field.signed else "unsigned"
        detail += f"; PIC precision {field.precision}, scale {field.scale}, {signedness}"
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
