"""Render the shared state a sequential step reads and writes (ADR-0040, ADR-0041).

**What this is for.** An ordinary rendered step keeps its lookups inside its reader and its output
inside its writer, and the two never meet -- which is correct as long as what a step decides for one
item does not depend on what it decided for the ones before. `CBTRN02C` breaks that: its acceptance
test compares a credit limit against cycle fields its own posting rewrites, so item *n* must see
items *1..n-1*'s writes or 30 of its 43 rejections disappear (ADR-0039).

**One store, two consumers.** The working set holds each read-modify-written entity's records in a
map keyed by the record key, seeded from the input file. The reader resolves its lookups from it
instead of from a private copy, the writer updates it instead of holding its own, and it writes
every map back to its own output file at the end of the step. That is COBOL's `OPEN I-O` in the
shape Spring Batch can hold.

**It renders no business logic.** Nothing here adds, validates or decides; it moves records in and
out of a map by key. What the value *becomes* is the processor's, which is the line ADR-0019 draws
and this does not cross.

**Insertion order is preserved, and rows created during the run land at the end.** A record whose
key the file did not have is added rather than refused, because `upsert` files exist (ADR-0037) --
`CBTRN02C` creates 44 of its 94 balance rows. Whether that ordering matches an indexed unload's key
order is ADR-0037's stated open question and is a property of the comparison, not of this store.
"""

from __future__ import annotations

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    DomainEntity,
    FileAccessPath,
    UnifiedDesign,
)
from cobol_modernizer.rendering.java_names import require_java_identifier
from cobol_modernizer.rendering.java_reader import (
    UnrenderableReaderError,
    _camel,
    _entity,
)

_INDENT = " " * 4


class UnrenderableWorkingSetError(Exception):
    """A working set cannot be rendered from this design without inventing something.

    Raised when the step declares `reads_own_writes` and nothing in the design says which entities
    it read-modify-writes, and when such an entity's key position is unknown -- a store that cannot
    key a record cannot answer the lookup the step's decision depends on.
    """


def working_set_class_name(step: BatchStepDesign) -> str:
    """`postTransaction` -> `PostTransactionWorkingSet`."""
    base = step.step_name[:1].upper() + step.step_name[1:]
    return f"{base}WorkingSet"


def read_modify_written(design: UnifiedDesign, program_name: str) -> list[FileAccessPath]:
    """Every file this program reads by key **and** writes back, in declared order.

    This is the derivable half of ADR-0040 and it is used here rather than as a detector: once a
    step has *declared* that it reads its own writes, which files that concerns is a fact about the
    program, not a judgement. The declaration says *that* it happens; this says *where*.
    """
    return [
        path
        for path in design.file_access_paths
        if path.program_name == program_name
        and path.is_keyed_lookup
        and path.write_mode in ("replace", "upsert")
    ]


def _member(entity_name: str) -> str:
    """`TranCatBal` -> `tranCatBal`. The entity name is already Pascal case; only the first letter
    moves.

    Not `java_reader._camel`, which splits on hyphens and lowercases each part -- right for a COBOL
    `ASSIGN TO` name and wrong here, where it would render `trancatbal` beside a `putTranCatBal`
    that came from the same entity.
    """
    return entity_name[:1].lower() + entity_name[1:]


def _key_position(path: FileAccessPath) -> tuple[int, int]:
    parts = [part for part in path.key_parts if part.key_offset is not None]
    if not parts:
        raise UnrenderableWorkingSetError(
            f"{path.select_name} is read by {path.effective_key!r} and nothing says where that key "
            "sits in the record, so its rows cannot be held by key"
        )
    return parts[0].key_offset or 0, sum(part.key_width or 0 for part in parts)


def render_working_set(
    step: BatchStepDesign,
    design: UnifiedDesign,
    program_name: str,
    *,
    package: str,
) -> str:
    """Render the store this step's reader and writer share.

    Raises:
        UnrenderableWorkingSetError: any fact the design does not carry.
    """
    class_name = working_set_class_name(step)
    require_java_identifier(class_name, source_name=step.step_name, kind="Working set class name")

    paths = read_modify_written(design, program_name)
    if not paths:
        raise UnrenderableWorkingSetError(
            f"step {step.step_name!r} declares reads_own_writes and {program_name} has no file it "
            "both reads by key and writes back, so there is no state for the step to share. Either "
            "the declaration is wrong or the access paths are"
        )

    entities: dict[str, DomainEntity] = {}
    for path in paths:
        try:
            entities[path.select_name] = _entity(design, path.written_entity_name)
        except UnrenderableReaderError as exc:
            raise UnrenderableWorkingSetError(str(exc)) from exc

    fields: list[str] = []
    parameters: list[str] = []
    loads: list[str] = []
    accessors: list[str] = []
    flushes: list[str] = []

    for path in paths:
        entity = entities[path.select_name]
        offset, width = _key_position(path)
        name = _member(entity.name)
        parameter = _camel(path.assign_to)

        fields.append(f"{_INDENT}private final Path {name}File;")
        fields.append(
            f"{_INDENT}private final Map<String, String> {name}Records = new LinkedHashMap<>();"
        )
        parameters.append(f"Path {parameter}")
        loads.append(
            f"{_INDENT * 2}this.{name}File = {parameter};\n"
            f"{_INDENT * 2}for (String existing : CobolRecord.fixedRecords({parameter}, "
            f"{entity.record_length})) {{\n"
            f"{_INDENT * 3}{name}Records.put("
            f"CobolRecord.text(existing, {offset}, {width}), existing);\n"
            f"{_INDENT * 2}}}"
        )
        accessors.append(
            f"{_INDENT}/** {entity.name} as this step has it now, or null when the file has no "
            f"such key ({path.select_name}, line {path.select_line}). */\n"
            f"{_INDENT}public String {name}(String key) {{\n"
            f"{_INDENT * 2}return {name}Records.get(key);\n"
            f"{_INDENT}}}\n\n"
            f"{_INDENT}/** Replace {entity.name} at this key, or add it when the file had none "
            f"-- {path.write_mode} ({_written_at(path)}). */\n"
            f"{_INDENT}public void put{entity.name}(String record) {{\n"
            f"{_INDENT * 2}{name}Records.put(CobolRecord.text(record, {offset}, {width}), record);\n"
            f"{_INDENT}}}"
        )
        flushes.append(
            f"{_INDENT * 2}Files.writeString(\n"
            f"{_INDENT * 3}{name}File, String.join(\"\", {name}Records.values()), "
            "StandardCharsets.ISO_8859_1);"
        )

    sources = ", ".join(f"{path.select_name} (line {path.select_line})" for path in paths)

    return f"""package {package};

import com.modernized.batch.cobol.CobolRecord;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * {class_name} -- the state batch step "{step.step_name}" both reads and writes.
 *
 * <p>Rendered from design.json because the step declares {{@code reads_own_writes}}: what it
 * decides for one item depends on what it wrote for the ones before, so its lookups and its output
 * have to be the same records. Seeded from {sources}.
 *
 * <p>This class holds no business logic. It moves records in and out of a map by key; what a record
 * becomes is the processor's, which is the line ADR-0019 draws.
 *
 * <p>Not restartable, and deliberately so: the state lives here for the length of the step, and a
 * job that fails half way through has written nothing. See ADR-0041 for why that trade is accepted
 * rather than hidden.
 */
public class {class_name} {{

{chr(10).join(fields)}

{_INDENT}public {class_name}({", ".join(parameters)}) throws IOException {{
{chr(10).join(loads)}
{_INDENT}}}

{(chr(10) * 2).join(accessors)}

{_INDENT}/** Write every map back to its own file, once, at the end of the step. */
{_INDENT}public void flush() throws IOException {{
{chr(10).join(flushes)}
{_INDENT}}}
}}
"""


def _written_at(path: FileAccessPath) -> str:
    if len(path.write_lines) == 1:
        return f"line {path.write_lines[0]}"
    listed = ", ".join(str(line) for line in path.write_lines[:-1])
    return f"lines {listed} and {path.write_lines[-1]}"
