"""Render a step's `ItemWriter` from `design.json` -- the other half of G31's file access.

**What this adds.** The reader renderer answered *where does the data come from*; this answers *where
does it go*. Both come from the same place: `FILE-CONTROL` declares the file, `WRITE ... FROM` says
which record is written to it, and the record layouts say how the bytes are arranged.

**`WRITE` and `REWRITE` are not the same statement and are not rendered alike.** `CBACT04C` appends
interest transactions and *rewrites* the account master in place. A writer that appended in both
cases would turn an update of fifty accounts into fifty new records -- and no comparison of the
record *contents* would notice, because every record would be individually correct. Only the file's
length would say. So an update writer loads the file it is updating, replaces records by key, and
writes the result back.

**On rendering a fixed-width serialiser at all.** ADR-0029 declined to build one, on the grounds
that a serialiser whose only consumer is the assertion about it is a check written to match whatever
it needs to match. That reasoning does not apply here and the difference is worth stating: this
writer is *the program's output*, not the test's. A batch program that cannot write its file is not
finished, and the differential still compares field values rather than bytes -- so a compiler that
represents a positive sign differently is a finding rather than a failure.
"""

from __future__ import annotations

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    CompositeType,
    DomainEntity,
    FileAccessPath,
    UnifiedDesign,
)
from cobol_modernizer.rendering.java_names import require_java_identifier
from cobol_modernizer.rendering.java_reader import (
    UnrenderableReaderError,
    _camel,
    _entity,
    _field_width,
)
from cobol_modernizer.rendering.java_working_set import (
    read_modify_written,
    working_set_class_name,
)

_INDENT = " " * 4


class UnrenderableWriterError(Exception):
    """A writer cannot be rendered from this design without inventing something.

    Raised when the step's output is not a single entity, when no declared file is written from
    that entity, when the record layout is incomplete, and when a `REWRITE` target has no key
    position -- an update cannot replace a record it cannot find.
    """


def _lines(write_lines: list[int]) -> str:
    """`line 510` / `lines 510 and 528` -- provenance for however many statements wrote this file.

    An `upsert` is two statements, and citing only the first would attribute a create-or-update to
    the create, which is the misreading that produced this mode in the first place.
    """
    if len(write_lines) == 1:
        return f"line {write_lines[0]}"
    listed = ", ".join(str(line) for line in write_lines[:-1])
    return f"lines {listed} and {write_lines[-1]}"


def writer_class_name(step: BatchStepDesign) -> str:
    """`completeTransaction` -> `CompleteTransactionItemWriter`."""
    base = step.step_name[:1].upper() + step.step_name[1:]
    return f"{base}ItemWriter"


def _serialiser(entity: DomainEntity) -> str:
    """The expression building one record's bytes, field by field, in record order.

    Concatenation in declaration order is only correct because the layout is a partition -- every
    byte of the record belongs to exactly one field or to `FILLER`. `FILLER` is emitted as spaces,
    which is what an uninitialised COBOL record area holds after `INITIALIZE`.
    """
    pieces: list[str] = []
    position = 0
    for field in sorted(entity.fields, key=lambda f: f.byte_offset or 0):
        offset = field.byte_offset or 0
        if offset > position:
            pieces.append(f'CobolText.spaces({offset - position})')
        width = _field_width(field)
        accessor = f"item.{field.java_field_name}()"
        if field.java_type == "String":
            pieces.append(f"CobolText.pad({accessor}, {width})")
        else:
            pieces.append(f"CobolRecord.zoned({accessor}, {width}, {field.scale or 0})")
        position = offset + width

    trailing = (entity.record_length or position) - position
    if trailing > 0:
        pieces.append(f"CobolText.spaces({trailing})")
    return ("\n" + _INDENT * 4 + "+ ").join(pieces)


def writer_path_parameters(
    step: BatchStepDesign, design: UnifiedDesign, program_name: str
) -> list[str]:
    """The `ASSIGN TO` names this step's writer takes as `Path` arguments, in constructor order.

    The writer's half of `reader_path_parameters`, and there for the same reason (ADR-0067): the
    bean that fills a constructor and the constructor itself must derive their order from one place.

    A composite writer's components come in the composite's own order, and a component held in the
    working set contributes **no** path -- it is flushed through the shared state rather than
    written here, so a bean supplying one would be handing over an argument that does not exist.
    """
    composite = next((c for c in design.composite_types if c.name == step.output_type), None)
    if composite is None:
        entity = _entity(design, step.output_type)
        return [_written_path(design, program_name, entity.name, step).assign_to]

    shared = {
        path.written_entity_name
        for path in read_modify_written(design, program_name)
        if path.written_entity_name
    }
    return [
        _written_path(design, program_name, component.entity_name, step).assign_to
        for component in composite.components
        if component.entity_name not in shared
    ]


def _written_path(
    design: UnifiedDesign, program_name: str, entity_name: str, step: BatchStepDesign
) -> FileAccessPath:
    """The one declared file `program_name` writes `entity_name` to."""
    written = [
        path
        for path in design.file_access_paths
        if path.program_name == program_name and path.written_entity_name == entity_name
    ]
    if len(written) != 1:
        raise UnrenderableWriterError(
            f"{program_name} writes {entity_name!r} to {len(written)} declared files, and step "
            f"{step.step_name!r} outputs it; a writer needs exactly one file to write to"
        )
    return written[0]


def render_item_writer(
    step: BatchStepDesign,
    design: UnifiedDesign,
    program_name: str,
    *,
    package: str,
    domain_package: str,
    working_set_package: str | None = None,
) -> str:
    """Render the `ItemWriter` for `step`'s output, from the file the program writes it to.

    Raises:
        UnrenderableWriterError: any fact the design does not carry.
    """
    class_name = writer_class_name(step)
    require_java_identifier(class_name, source_name=step.step_name, kind="Writer class name")

    composite = next((c for c in design.composite_types if c.name == step.output_type), None)
    if composite is not None:
        if not step.reads_own_writes:
            raise UnrenderableWriterError(
                f"step {step.step_name!r} outputs the composite {step.output_type!r}; a composite "
                "spans several records and nothing says which file each part belongs to"
            )
        # **What changed, and why only here.** The refusal above stands for an ordinary step: a
        # composite output is several records and the design does not say what to do with them.
        # A step that declares `reads_own_writes` is the case where it does say -- every component
        # is an entity with its own access path, so which file each part belongs to is read rather
        # than guessed, and a step that updates three files in one pass has no other honest shape
        # (ADR-0041). `CBTRN02C` posts a transaction, a balance and an account from one daily
        # record, and splitting that across three steps would re-decide acceptance three times
        # against three different states.
        if working_set_package is None:
            raise UnrenderableWriterError(
                f"step {step.step_name!r} reads state it writes, so its updates go to a working "
                "set -- and nothing said which package that class is in"
            )
        return _render_composite_writer(
            step,
            design,
            program_name,
            composite,
            package=package,
            domain_package=domain_package,
            working_set_package=working_set_package,
        )
    try:
        entity = _entity(design, step.output_type)
    except UnrenderableReaderError as exc:
        raise UnrenderableWriterError(str(exc)) from exc

    written = [
        path
        for path in design.file_access_paths
        if path.program_name == program_name and path.written_entity_name == entity.name
    ]
    if len(written) != 1:
        raise UnrenderableWriterError(
            f"{program_name} writes {entity.name!r} to {len(written)} declared files; a writer "
            "needs exactly one, and a WRITE this parse could not attribute to a file leaves none"
        )
    path = written[0]

    by_key = path.write_mode in ("replace", "upsert")

    key_offset = key_width = None
    if by_key:
        keys = [part for part in path.key_parts if part.key_offset is not None]
        if not keys:
            raise UnrenderableWriterError(
                f"{path.select_name} is written by key ({path.write_mode}) and nothing says where "
                "its record key sits, so a record to replace cannot be found. Appending instead "
                "would leave the original rows in place and add new ones"
            )
        key_offset, key_width = keys[0].key_offset, keys[0].key_width

    serialiser = _serialiser(entity)
    parameter = _camel(path.assign_to)
    qualified = f"{domain_package}.{entity.name}"

    if by_key:
        # The `replace` guard is the only difference between the two keyed modes, and it is
        # load-bearing in both directions: a `REWRITE`-only file must never gain a record, and an
        # `upsert` file must be allowed to -- `CBTRN02C` creates 44 of its 94 balance rows. Rendering
        # the guard for `upsert` would abend on the first created row; dropping it for `replace`
        # would silently append the fifty accounts it was written to prevent.
        absent = (
            f"{_INDENT * 3}if (!records.containsKey(key)) {{\n"
            f"{_INDENT * 4}throw new IllegalStateException(\n"
            f'{_INDENT * 5}"REWRITE of a record that is not in " + output + ": key " + key);\n'
            f"{_INDENT * 3}}}\n"
            if path.write_mode == "replace"
            else ""
        )
        state = (
            f"{_INDENT}private final Path output;\n"
            f"{_INDENT}private final Map<String, String> records = new LinkedHashMap<>();"
        )
        constructor_body = (
            f"{_INDENT * 2}this.output = {parameter};\n"
            f"{_INDENT * 2}for (String existing : CobolRecord.fixedRecords({parameter}, "
            f"{entity.record_length})) {{\n"
            f"{_INDENT * 3}records.put(CobolRecord.text(existing, {key_offset}, {key_width}), "
            "existing);\n"
            f"{_INDENT * 2}}}"
        )
        write_body = (
            f"{_INDENT * 2}for ({qualified} item : chunk.getItems()) {{\n"
            f"{_INDENT * 3}String record =\n{_INDENT * 4}{serialiser};\n"
            f"{_INDENT * 3}String key = CobolRecord.text(record, {key_offset}, {key_width});\n"
            f"{absent}"
            f"{_INDENT * 3}records.put(key, record);\n"
            f"{_INDENT * 2}}}\n"
            f"{_INDENT * 2}Files.writeString(\n"
            f"{_INDENT * 3}output, String.join(\"\", records.values()), "
            "StandardCharsets.ISO_8859_1);"
        )
        mode = (
            (
                "REWRITE: records are replaced by key and the file keeps its original order and "
                "membership. Appending would leave the originals in place"
            )
            if path.write_mode == "replace"
            else (
                "WRITE and REWRITE both: a record replaces the one with its key when the file has "
                "one and is added when it does not, which is COBOL's read-by-key create-or-update. "
                "Rendering this as an append would leave the replaced originals in place"
            )
        )
    else:
        state = f"{_INDENT}private final Path output;"
        constructor_body = (
            f"{_INDENT * 2}this.output = {parameter};\n"
            f"{_INDENT * 2}Files.createDirectories({parameter}.toAbsolutePath().getParent());\n"
            f"{_INDENT * 2}Files.deleteIfExists({parameter});"
        )
        write_body = (
            f"{_INDENT * 2}StringBuilder batch = new StringBuilder();\n"
            f"{_INDENT * 2}for ({qualified} item : chunk.getItems()) {{\n"
            f"{_INDENT * 3}batch.append(\n{_INDENT * 4}{serialiser});\n"
            f"{_INDENT * 2}}}\n"
            f"{_INDENT * 2}Files.writeString(\n"
            f"{_INDENT * 3}output, batch.toString(), StandardCharsets.ISO_8859_1,\n"
            f"{_INDENT * 3}StandardOpenOption.CREATE, StandardOpenOption.APPEND);"
        )
        mode = "WRITE: each record is appended, as an OPEN OUTPUT sequential file"

    return f"""package {package};

import com.modernized.batch.cobol.CobolRecord;
import com.modernized.batch.cobol.CobolText;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemWriter;

/**
 * {class_name} -- writes {entity.name} to {path.select_name} for step "{step.step_name}".
 *
 * <p>Rendered from design.json. The record layout, the file and the write mode all come from
 * {program_name}'s own declarations: {path.select_name} at line {path.select_line}, written from
 * {entity.name} at {_lines(path.write_lines)}.
 *
 * <p>{mode}.
 */
public class {class_name} implements ItemWriter<{qualified}> {{

{state}

{_INDENT}public {class_name}(Path {parameter}) throws IOException {{
{constructor_body}
{_INDENT}}}

{_INDENT}@Override
{_INDENT}public void write(Chunk<? extends {qualified}> chunk) throws IOException {{
{write_body}
{_INDENT}}}
}}
"""


def _render_composite_writer(
    step: BatchStepDesign,
    design: UnifiedDesign,
    program_name: str,
    composite: CompositeType,
    *,
    package: str,
    domain_package: str,
    working_set_package: str,
) -> str:
    """One writer for a step that produces several records from one item (ADR-0041).

    **Every mutation the step performs happens here.** The processor returns what each record
    *becomes* and touches nothing; this puts the read-modify-written components into the shared
    working set and appends the rest to their own files. Keeping mutation out of the processor is
    what lets the processor stay a pure function of (item, state as the step found it), which is
    ADR-0019's posture and the reason a body can be reviewed on its own.

    **A rejected item never arrives.** Spring Batch drops an item the processor returns `null` for
    before the writer sees it, so `CBTRN02C`'s rejects are excluded from all three files by the same
    mechanism rather than by three separate tests that could disagree (ADR-0038).
    """
    class_name = writer_class_name(step)
    working_set = f"{working_set_package}.{working_set_class_name(step)}"
    qualified = f"{domain_package}.{composite.name}"

    shared = {
        path.written_entity_name: path
        for path in read_modify_written(design, program_name)
        if path.written_entity_name
    }

    fields: list[str] = [f"{_INDENT}private final {working_set} state;"]
    parameters: list[str] = [f"{working_set} state"]
    assignments: list[str] = [f"{_INDENT * 2}this.state = state;"]
    puts: list[str] = []
    appends: list[str] = []
    flushes: list[str] = []
    cited: list[str] = []

    for component in composite.components:
        try:
            entity = _entity(design, component.entity_name)
        except UnrenderableReaderError as exc:
            raise UnrenderableWriterError(str(exc)) from exc

        record = _serialiser(entity).replace("item.", f"item.{component.field_name}().")
        if entity.name in shared:
            path = shared[entity.name]
            puts.append(
                f"{_INDENT * 3}state.put{entity.name}(\n{_INDENT * 4}{record});"
            )
        else:
            written = [
                p
                for p in design.file_access_paths
                if p.program_name == program_name and p.written_entity_name == entity.name
            ]
            if len(written) != 1:
                raise UnrenderableWriterError(
                    f"{program_name} writes {entity.name!r} to {len(written)} declared files, and "
                    f"{step.step_name!r} outputs it as part of {composite.name!r}; a component "
                    "needs exactly one file to go to"
                )
            path = written[0]
            if path.write_mode != "append":
                raise UnrenderableWriterError(
                    f"{entity.name!r} is written {path.write_mode!r} to {path.select_name} and is "
                    "not in this step's working set, so there is nothing holding the records it "
                    "would replace"
                )
            parameter = _camel(path.assign_to)
            buffer = f"{_camel(entity.name)}Batch"
            fields.append(f"{_INDENT}private final Path {parameter};")
            parameters.append(f"Path {parameter}")
            assignments.append(
                f"{_INDENT * 2}this.{parameter} = {parameter};\n"
                f"{_INDENT * 2}Files.createDirectories({parameter}.toAbsolutePath().getParent());\n"
                f"{_INDENT * 2}Files.deleteIfExists({parameter});"
            )
            appends.append(
                f"{_INDENT * 2}StringBuilder {buffer} = new StringBuilder();"
            )
            puts.append(f"{_INDENT * 3}{buffer}.append(\n{_INDENT * 4}{record});")
            flushes.append(
                f"{_INDENT * 2}Files.writeString(\n"
                f"{_INDENT * 3}{parameter}, {buffer}.toString(), StandardCharsets.ISO_8859_1,\n"
                f"{_INDENT * 3}StandardOpenOption.CREATE, StandardOpenOption.APPEND);"
            )
        cited.append(
            f" *   <li>{entity.name} -> {path.select_name} -- {path.write_mode}, "
            f"{_lines(path.write_lines)}</li>"
        )

    body = "\n".join(
        [
            *appends,
            f"{_INDENT * 2}for ({qualified} item : chunk.getItems()) {{",
            *puts,
            f"{_INDENT * 2}}}",
            *flushes,
        ]
    )

    return f"""package {package};

import com.modernized.batch.cobol.CobolRecord;
import com.modernized.batch.cobol.CobolText;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import org.springframework.batch.infrastructure.item.Chunk;
import org.springframework.batch.infrastructure.item.ItemWriter;

/**
 * {class_name} -- writes every record step "{step.step_name}" produces from one item.
 *
 * <p>Rendered from design.json. Each component of {composite.name} goes where {program_name}'s own
 * declarations send it:
 *
 * <ul>
{chr(10).join(cited)}
 * </ul>
 *
 * <p>Components this step read-modify-writes go into {working_set} rather than to a file, so
 * the next item's read sees them (ADR-0041). Everything else is appended. All mutation is here and none
 * is in the processor, which returns what each record becomes and touches nothing.
 *
 * <p>An item the processor rejected never reaches this class -- Spring Batch drops a null before
 * the writer -- so a rejected transaction is absent from every one of these files by one mechanism.
 */
public class {class_name} implements ItemWriter<{qualified}> {{

{chr(10).join(fields)}

{_INDENT}public {class_name}({", ".join(parameters)}) throws IOException {{
{chr(10).join(assignments)}
{_INDENT}}}

{_INDENT}@Override
{_INDENT}public void write(Chunk<? extends {qualified}> chunk) throws IOException {{
{body}
{_INDENT}}}
}}
"""
