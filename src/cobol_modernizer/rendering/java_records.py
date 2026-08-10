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
import re

from cobol_modernizer.core.contracts import DomainEntity, DomainField

logger = logging.getLogger(__name__)

#: Every Java reserved word, plus the three literals that are not technically keywords but are
#: equally illegal as identifiers. `pic_mapper`/`solution_architect` transform a COBOL name
#: mechanically, and nothing in that transform knows what Java forbids -- a COBOL field named
#: `CLASS` or `NEW-COUNT`'s sibling `NEW` becomes `class`/`new` and would not compile. Rendering
#: it anyway would produce a file that fails at `javac` with an error pointing at generated code
#: rather than at the real cause, so this is checked here and raised with the COBOL name attached.
_JAVA_RESERVED = frozenset(
    (
        "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
        "const", "continue", "default", "do", "double", "else", "enum", "extends", "final",
        "finally", "float", "for", "goto", "if", "implements", "import", "instanceof", "int",
        "interface", "long", "native", "new", "package", "private", "protected", "public",
        "return", "short", "static", "strictfp", "super", "switch", "synchronized", "this",
        "throw", "throws", "transient", "try", "void", "volatile", "while",
        # `true`/`false`/`null` are literals rather than keywords in the JLS, but are equally
        # illegal as identifiers, so they belong in the same check.
        "true", "false", "null",
    )
)

#: A legal Java identifier, restricted to the ASCII subset a mechanical COBOL-name transform can
#: actually produce. Deliberately narrower than the JLS (which allows most Unicode letters): a name
#: outside this set did not come from the transform this renderer is fed by, and silently accepting
#: it would mean rendering something no one checked.
_JAVA_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


class UnrenderableJavaNameError(Exception):
    """A `DomainEntity`/`DomainField` name cannot be rendered as a legal Java identifier.

    Joins the `UnsupportedPicConstructError`/`UnsupportedCopyConstructError` family: an unambiguous
    case that must fail loudly rather than be guessed at or silently mangled. Renaming the field to
    dodge the collision (`class` -> `class_`) is exactly the kind of quiet fix that makes generated
    code stop matching the COBOL it claims to implement, so the caller is told instead.
    """


def _require_java_identifier(name: str, *, cobol_name: str, kind: str) -> str:
    """Return `name` unchanged, or raise if Java could not accept it as an identifier."""
    if not _JAVA_IDENTIFIER.match(name):
        raise UnrenderableJavaNameError(
            f"{kind} {name!r} (from COBOL {cobol_name!r}) is not a legal Java identifier"
        )
    if name in _JAVA_RESERVED:
        raise UnrenderableJavaNameError(
            f"{kind} {name!r} (from COBOL {cobol_name!r}) is a Java reserved word"
        )
    return name


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
    class_name = _require_java_identifier(
        entity.name, cobol_name=entity.source_copybook, kind="Entity name"
    )
    for field in entity.fields:
        _require_java_identifier(
            field.java_field_name, cobol_name=field.cobol_field_name, kind="Field name"
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
