"""Render a step's `ItemReader` from `design.json` -- the half of G31 nothing produced.

**What this closes.** `generate` rendered processors and no reader, so a generated project compiled
and could not run (G31). ADR-0030 took a hand-written reader as a deliberate stopgap and required it
to record every fact the design lacked; those became findings F1-F6, and the three PRs before this
one put each of them in the contract:

| fact | where it comes from |
|---|---|
| which file yields which entity, stream or lookup | `FileAccessPath` (`FILE-CONTROL` + `READ ... INTO`) |
| the key a lookup is read by | `effective_key`, which is the read's key rather than the declared one |
| what fills that key | `LookupKeyPart` (`MOVE ... TO` the key field) |
| where every field sits in its record | `DomainField.byte_offset`, `DomainEntity.record_length` |

So this renderer invents nothing. Every offset, key and join it emits is read from the design, which
read it from the COBOL -- which is ADR-0030's whole argument for parsing over asking a model: a
wrong join produces plausible rows and a silently wrong comparison.

**Everything it cannot derive, it refuses.** `UnrenderableReaderError` joins the
`UnsupportedPicConstructError` family. A reader that guessed a key, an offset or a lookup order
would compile, run, and produce records that differ from COBOL's in ways only a differential could
catch -- and the differential is exactly what this exists to be measured by.

**Files are read as fixed-length records**, because that is what a COBOL `WRITE` produces. The
shipped corpus stores some of them as text with line terminators; converting those is the caller's
job, exactly as the oracle pipeline's own `LOADIDX` and `DALYCONV` steps do it. A renderer that
guessed the framing per file would be encoding a property of one distribution.
"""

from __future__ import annotations

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    DomainEntity,
    DomainField,
    FileAccessPath,
    LookupKeyPart,
    UnifiedDesign,
)
from cobol_modernizer.rendering.java_names import require_java_identifier

_INDENT = " " * 4


class UnrenderableReaderError(Exception):
    """A reader cannot be rendered from this design without inventing something.

    Raised for a missing access path, a lookup whose key nothing fills, a key whose byte position is
    unknown, a source field that belongs to no resolved entity, a width mismatch between a key and
    what fills it, and a lookup order that does not resolve. Each is a fact the COBOL either states
    or does not; where it does not, the honest output is a refusal naming what was missing.
    """


def _field_width(field: DomainField) -> int:
    """A field's byte width: its declared length, or its digit count for a numeric."""
    if field.length is not None:
        return field.length
    if field.precision is not None:
        return field.precision
    raise UnrenderableReaderError(
        f"field {field.cobol_field_name!r} has neither a length nor a precision, so its width in "
        "the record is unknown"
    )


def _camel(name: str) -> str:
    parts = [part for part in name.replace("_", "-").split("-") if part]
    return parts[0].lower() + "".join(part.capitalize() for part in parts[1:])


def _component_entities(step: BatchStepDesign, design: UnifiedDesign) -> list[tuple[str, str]]:
    """`(component field name, entity name)` for the step's input, composite or single entity."""
    composite = next((c for c in design.composite_types if c.name == step.input_type), None)
    if composite is not None:
        return [(c.field_name, c.entity_name) for c in composite.components]
    if any(entity.name == step.input_type for entity in design.domain_entities):
        return [(_camel(step.input_type), step.input_type)]
    raise UnrenderableReaderError(
        f"step {step.step_name!r} declares input {step.input_type!r}, which is neither a domain "
        "entity nor a declared composite"
    )


def _entity(design: UnifiedDesign, name: str) -> DomainEntity:
    entity = next((e for e in design.domain_entities if e.name == name), None)
    if entity is None:
        raise UnrenderableReaderError(f"the design has no domain entity {name!r}")
    if entity.record_length is None:
        raise UnrenderableReaderError(
            f"entity {name!r} has no record length, so nothing says where one record ends"
        )
    for field in entity.fields:
        if field.byte_offset is None:
            raise UnrenderableReaderError(
                f"entity {name!r} field {field.cobol_field_name!r} has no byte offset"
            )
    return entity


def _paths_for(design: UnifiedDesign, program_name: str, entity_name: str) -> FileAccessPath:
    matches = [
        path
        for path in design.file_access_paths
        if path.program_name == program_name and path.entity_name == entity_name
    ]
    if len(matches) != 1:
        raise UnrenderableReaderError(
            f"{program_name} has {len(matches)} access paths yielding {entity_name!r}; a reader "
            "needs exactly one, and this is the fact G31 exists for"
        )
    return matches[0]


def _owning_entity(design: UnifiedDesign, resolved: list[str], cobol_field: str) -> str | None:
    """Which already-resolved entity carries `cobol_field`, if any."""
    for name in resolved:
        entity = next(e for e in design.domain_entities if e.name == name)
        if any(field.cobol_field_name == cobol_field for field in entity.fields):
            return name
    return None


def _lookup(entity_name: str, key: str, shared: set[str]) -> str:
    """Where this reader gets a lookup record from: its own map, or the step's working set.

    The shared form is a method call rather than a map access on purpose -- the working set
    owns how its records are keyed, and a reader reaching into a map inside it would be a
    second place that has to agree about the key position.
    """
    if entity_name in shared:
        member = entity_name[:1].lower() + entity_name[1:]
        return f"state.{member}({key})"
    return f"{_camel(entity_name)}Records.get({key})"


def _order_lookups(
    design: UnifiedDesign, driving: str, lookups: dict[str, FileAccessPath]
) -> list[str]:
    """Lookups in an order where every key source is already available.

    `DISCGRP`'s key is filled from `ACCT-GROUP-ID`, a field of the *account* record, so the account
    lookup has to run first. Nothing in the design states that ordering; it falls out of which
    entity owns each key source, which is why this is derived rather than declared.

    A lookup whose sources are never satisfied is refused rather than emitted last and hoped for.
    """
    ordered: list[str] = []
    remaining = dict(lookups)
    while remaining:
        progressed = False
        for name, path in list(remaining.items()):
            sources = [
                part.source_field
                for part in path.key_parts
                if part.source_field and not part.is_fallback
            ]
            available = [driving, *ordered]
            if all(_owning_entity(design, available, source) for source in sources):
                ordered.append(name)
                del remaining[name]
                progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(remaining))
            raise UnrenderableReaderError(
                f"lookups {unresolved} cannot be ordered: each needs a key value no earlier read "
                "provides. Either a key source belongs to a record this step does not read, or two "
                "lookups depend on each other"
            )
    return ordered


def _key_expression(
    design: UnifiedDesign,
    part: LookupKeyPart,
    available: list[str],
    variables: dict[str, str],
) -> str:
    """The Java expression producing one key component's value, as stored bytes."""
    if part.key_offset is None or part.key_width is None:
        raise UnrenderableReaderError(
            f"key field {part.key_field!r} has no byte position in its own record, so a lookup "
            "against it cannot be rendered"
        )
    if part.literal is not None:
        return f'CobolText.pad("{part.literal}", {part.key_width})'

    owner = _owning_entity(design, available, part.source_field or "")
    if owner is None:
        raise UnrenderableReaderError(
            f"key field {part.key_field!r} is filled from {part.source_field!r}, which belongs to "
            "no record this reader has read by that point"
        )
    entity = _entity(design, owner)
    field = next(f for f in entity.fields if f.cobol_field_name == part.source_field)
    width = _field_width(field)
    if width != part.key_width:
        raise UnrenderableReaderError(
            f"{part.source_field!r} is {width} bytes and key field {part.key_field!r} is "
            f"{part.key_width}; a MOVE between them pads or truncates, and rendering it as a "
            "straight copy would look right and match nothing"
        )
    return f"CobolRecord.text({variables[owner]}, {field.byte_offset}, {width})"


def _record_parser(entity: DomainEntity, domain_package: str) -> str:
    """A private method turning one record's bytes into the entity, field by field from the design."""
    arguments = []
    for field in entity.fields:
        width = _field_width(field)
        if field.java_type == "String":
            arguments.append(
                f"{_INDENT * 4}CobolRecord.text(record, {field.byte_offset}, {width})"
            )
        else:
            arguments.append(
                f"{_INDENT * 4}CobolRecord.number(record, {field.byte_offset}, {width}, "
                f"{field.scale or 0})"
            )
    joined = ",\n".join(arguments)
    return (
        f"{_INDENT}private static {domain_package}.{entity.name} to{entity.name}(String record) {{\n"
        f"{_INDENT * 2}return new {domain_package}.{entity.name}(\n{joined});\n"
        f"{_INDENT}}}"
    )


def reader_class_name(step: BatchStepDesign) -> str:
    """`computeInterest` -> `ComputeInterestItemReader`. Mechanical, like the processor's."""
    base = step.step_name[:1].upper() + step.step_name[1:]
    return f"{base}ItemReader"


def render_item_reader(
    step: BatchStepDesign,
    design: UnifiedDesign,
    program_name: str,
    *,
    package: str,
    domain_package: str,
    working_set_package: str | None = None,
) -> str:
    """Render the `ItemReader` that feeds `step`, from the design's access paths and layouts.

    Raises:
        UnrenderableReaderError: any fact the design does not carry. Never guessed -- see the
            module docstring.
    """
    class_name = reader_class_name(step)
    require_java_identifier(class_name, source_name=step.step_name, kind="Reader class name")

    components = _component_entities(step, design)
    paths = {entity: _paths_for(design, program_name, entity) for _field, entity in components}

    streams = [name for name, path in paths.items() if not path.is_keyed_lookup]
    if len(streams) != 1:
        raise UnrenderableReaderError(
            f"step {step.step_name!r} needs exactly one driving stream and its input resolves to "
            f"{len(streams)} ({', '.join(sorted(streams)) or 'none'}). A reader with no stream has "
            "nothing to iterate; one with two has no defined order"
        )
    driving = streams[0]
    lookups = {name: path for name, path in paths.items() if path.is_keyed_lookup}
    for name, path in lookups.items():
        if not path.key_parts:
            raise UnrenderableReaderError(
                f"lookup {name!r} ({path.select_name}) is read by {path.effective_key!r} and "
                "nothing in the program fills that key, so there is no join to render"
            )
    order = _order_lookups(design, driving, lookups)

    entities = {name: _entity(design, name) for name in paths}
    variables = {driving: "record"}
    for name in order:
        variables[name] = f"{_camel(name)}Record"

    # **Which lookups this reader must not own a copy of.** A step that declares `reads_own_writes`
    # decides an item's outcome from records it is also updating, so its lookups have to be the
    # ones the writer has been changing -- a private map loaded in this constructor would answer
    # from the file as the job found it and never see a single write (ADR-0041). For every other
    # step this set is empty and the reader is rendered exactly as it was.
    #
    # Imported here rather than at module scope: `java_working_set` imports this module for
    # `_entity` and `_camel`, so a top-level import would be a cycle. Kept as one definition in
    # one place anyway -- the alternative was to restate what "read-modify-written" means in a
    # second module, which is how two definitions of one fact start disagreeing.
    from cobol_modernizer.rendering.java_working_set import (
        read_modify_written,
        working_set_class_name,
    )

    shared = (
        {path.written_entity_name for path in read_modify_written(design, program_name)}
        if step.reads_own_writes
        else set()
    )
    if shared and working_set_package is None:
        raise UnrenderableReaderError(
            f"step {step.step_name!r} reads state it writes, so its lookups come from a working "
            "set -- and nothing said which package that class is in. A reader referring to it "
            "unqualified would compile only by accident of packaging"
        )
    working_set = (
        f"{working_set_package}.{working_set_class_name(step)}" if shared else ""
    )
    held = [name for name in order if name not in shared]

    parameters = ", ".join(
        ([f"{working_set} state"] if shared else [])
        + [f"Path {_camel(paths[name].assign_to)}" for name in [driving, *held]]
    )
    loads = [
        (
            f"{_INDENT * 2}this.drivingRecords = CobolRecord.fixedRecords("
            f"{_camel(paths[driving].assign_to)}, {entities[driving].record_length});"
        )
    ]
    if shared:
        loads.insert(0, f"{_INDENT * 2}this.state = state;")
    for name in held:
        path = paths[name]
        key_offset = path.key_parts[0].key_offset
        key_width = sum(
            part.key_width or 0 for part in path.key_parts if not part.is_fallback
        )
        loads.append(
            f"{_INDENT * 2}for (String row : CobolRecord.fixedRecords("
            f"{_camel(path.assign_to)}, {entities[name].record_length})) {{\n"
            f"{_INDENT * 3}this.{_camel(name)}Records.put("
            f"CobolRecord.text(row, {key_offset}, {key_width}), row);\n"
            f"{_INDENT * 2}}}"
        )

    body: list[str] = [
        f"{_INDENT * 2}if (next >= drivingRecords.size()) {{",
        f"{_INDENT * 3}return null;",
        f"{_INDENT * 2}}}",
        f"{_INDENT * 2}String record = drivingRecords.get(next++);",
    ]
    for name in order:
        path = paths[name]
        available = [driving, *order[: order.index(name)]]
        primary = [part for part in path.key_parts if not part.is_fallback]
        key = " + ".join(
            _key_expression(design, part, available, variables)
            for part in primary
        )
        variable = variables[name]
        body.append(
            f"{_INDENT * 2}String {variable} = {_lookup(name, key, shared)};"
        )

        fallbacks = [part for part in path.key_parts if part.is_fallback]
        if fallbacks:
            overridden = {part.key_field for part in fallbacks}
            retry_parts = [
                next(f for f in fallbacks if f.key_field == part.key_field)
                if part.key_field in overridden
                else part
                for part in primary
            ]
            retry = " + ".join(
                _key_expression(design, part, available, variables)
                for part in retry_parts
            )
            body += [
                f"{_INDENT * 2}if ({variable} == null) {{",
                # The COBOL retries on file status 23 -- record not found -- which is a null here.
                f"{_INDENT * 3}{variable} = {_lookup(name, retry, shared)};",
                f"{_INDENT * 2}}}",
            ]
        if name in step.optional_lookups:
            # **A miss this program handles, so the reader hands it over rather than refusing.**
            # `CBTRN02C` creates a TCATBAL row when its read finds none -- 44 of the 94 rows the
            # oracle holds -- and a refusal here would abend on the first of them (ADR-0042).
            # The processor sees `null` and the COBOL's own INVALID KEY branch is its to translate.
            body.append(
                f"{_INDENT * 2}// {path.select_name} may have no record for this key, and"
                f" {step.step_name} says so:"
            )
            body.append(f"{_INDENT * 2}// null here is the INVALID KEY branch, not a failure.")
        else:
            body.append(
                f'{_INDENT * 2}require({variable}, "{path.select_name} has no record for the key '
                f'built from {", ".join(p.source_field or repr(p.literal) for p in primary)}");'
            )

    def _argument(entity: str) -> str:
        if entity == driving:
            return f"to{entity}(record)"
        variable = variables[entity]
        if entity in step.optional_lookups:
            # The record parser slices fixed offsets and would throw on the null this lookup is
            # allowed to produce. The component carries the miss instead, which is what lets a
            # body translate the COBOL's own INVALID KEY branch (ADR-0042).
            return f"{variable} == null ? null : to{entity}({variable})"
        return f"to{entity}({variable})"

    constructor_arguments = ", ".join(_argument(entity) for _field, entity in components)

    maps = "\n".join(
        ([f"{_INDENT}private final {working_set} state;"] if shared else [])
        + [
            f"{_INDENT}private final Map<String, String> {_camel(name)}Records = new HashMap<>();"
            for name in held
        ]
    )
    parsers = "\n\n".join(_record_parser(entities[name], domain_package) for name in paths)
    sources = ", ".join(
        f"{path.select_name} (line {path.select_line})" for path in paths.values()
    )

    return f"""package {package};

import com.modernized.batch.cobol.CobolRecord;
import com.modernized.batch.cobol.CobolText;
import java.io.IOException;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.springframework.batch.infrastructure.item.ItemReader;

/**
 * {class_name} -- the reader for batch step "{step.step_name}".
 *
 * <p>Rendered from design.json: one driving stream and {len(order)} keyed lookup(s), with every
 * offset, key and join read from {program_name}'s own declarations ({sources}).
 *
 * <p>Nothing here was inferred. A fact the design did not carry would have been a refusal
 * (UnrenderableReaderError) rather than a guess, because a guessed join produces plausible rows and
 * a silently wrong result.
 */
public class {class_name} implements ItemReader<{domain_package}.{step.input_type}> {{

{_INDENT}private final List<String> drivingRecords;
{maps}
{_INDENT}private int next;

{_INDENT}public {class_name}({parameters}) throws IOException {{
{chr(10).join(loads)}
{_INDENT}}}

{_INDENT}@Override
{_INDENT}public {domain_package}.{step.input_type} read() {{
{chr(10).join(body)}
{_INDENT * 2}return new {domain_package}.{step.input_type}({constructor_arguments});
{_INDENT}}}

{_INDENT}/** The COBOL abends when a keyed read finds nothing; substituting a default would post
{_INDENT}  * an interest figure against data that does not exist. */
{_INDENT}private static <T> T require(T value, String message) {{
{_INDENT * 2}if (value == null) {{
{_INDENT * 3}throw new IllegalStateException(message);
{_INDENT * 2}}}
{_INDENT * 2}return value;
{_INDENT}}}

{parsers}
}}
"""
