"""`solution_architect` -- the third `design`-phase node: one unified Java target design.

`spec_extractor` and `spec_critic` each work one program at a time. This node is the first to look
across all four Track C programs together, producing `design.json`'s `unified_design`
(`core/contracts.UnifiedDesign`, ADR-0010) -- the domain model and Spring Batch/REST design
`card-service` (ADR-0009) will eventually be generated from.

Three decisions worth stating explicitly, all from ADR-0010's full reasoning:

1. **Domain entities are merged by copybook name only, mechanically -- never by structural
   resemblance.** `build_domain_entities` groups every program's
   `spec_extractor.group_field_mappings_by_source` output by copybook name: the same name across
   two programs is proof they share the exact same physical record (byte-for-byte, since it's
   literally the same source file). Two different copybook names always stay two different
   entities, even when structurally similar (`CVTRA06Y`'s `DALYTRAN-RECORD` and `CVTRA05Y`'s
   `TRAN-RECORD` are both 350-byte transaction records, but recognizing they're related is a
   judgment call for the LLM-authored design, not something this function may assume). A copybook
   that contributes zero successfully-mapped fields (`CODATECN` -- entirely `REDEFINES` groups)
   produces no entity at all; there's nothing to represent.
2. **Entity/field names are a direct, mechanical transform of the COBOL name, not a business
   rename.** `Account` from `ACCOUNT-RECORD`, `acctCurrBal` from `ACCT-CURR-BAL` -- deliberately
   not "nicer" names like `TransactionCategoryBalance`, since choosing those is exactly the kind
   of semantic judgment reserved for the model, not this deterministic function.
3. **Batch job and REST endpoint design is 100% LLM-authored**, informed by (never re-deriving)
   the deterministic entity data -- the same split `spec_extractor`/`spec_critic` already
   established. Each program's `spec_markdown` is wrapped as untrusted data again in the prompt
   (`core.guardrails.wrap_untrusted_cobol`) even though it's this repo's own prior LLM output --
   `spec_extractor`'s own guardrail is defense in depth, not a guarantee, so a narration that got
   an injection past it must still never reach this node as instructions.

This node's structured output runs through `core.structured_output.parse_with_repair` -- plan step
35's shared loop, which ADR-0010 deferred precisely so it would not be built in miniature a third
time (ADR-0054). Note what it does *not* soften: every validation below still refuses a design
referencing a program, entity, step role or REST method this node did not offer. The loop buys one
more attempt at a parseable answer; it never widens what counts as a valid one. Like `spec_extractor`/
`spec_critic`, the live architect model call is injected (`architect`) so this module's tests
exercise every deterministic step -- domain-entity merging, prompt construction, response
validation -- without a live Anthropic API credential this development environment does not have.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    ComputedComponent,
    ComputedValue,
    ControlBreakDesign,
    DomainEntity,
    DomainField,
    FileAccessPath,
    LookupKeyPart,
    ProgramDesignEntry,
    RestEndpointDesign,
    UnifiedDesign,
    WriteMode,
)
from cobol_modernizer.core.guardrails import wrap_untrusted_cobol
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.model_routing import RoutingDecision, resolve_routing
from cobol_modernizer.core.structured_output import parse_with_repair, strip_code_fence
from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.parsing.computed_fields import computed_fields, referencing_paragraphs
from cobol_modernizer.parsing.control_break import (
    ControlBreak,
    extract_control_breaks,
    landing_field,
)
from cobol_modernizer.parsing.field_references import referenced_fields
from cobol_modernizer.parsing.file_control import (
    RecordBinding,
    WriteBinding,
    extract_file_declarations,
    extract_record_bindings,
    extract_write_bindings,
)
from cobol_modernizer.parsing.key_assignments import (
    KeyAssignment,
    extract_key_assignments,
    key_components,
)
from cobol_modernizer.parsing.record_layout import compute_record_layouts
from cobol_modernizer.prompts_registry_client.loader import read_prompt
from cobol_modernizer.tools.pic_mapper import PicMapping
from cobol_modernizer.tools.tenant_repo import resolve_program

logger = logging.getLogger(__name__)

_NODE_NAME = "solution_architect"

#: v1_2_0 gives the architect the program's computed values and states the rule that a step must be
#: able to return what it computes (ADR-0062). v1_1_0 offered no way to say "a `RatedCategoryBalance`
#: plus the interest computed from it", so the architect designed the only expressible thing and the
#: generated processor discarded its own result. Same two-halves shape as v1_1_0's own fix below:
#: `_refuse_undeliverable_computed_values` enforces it, and enforcing a rule the prompt never stated
#: would punish a model for following the contract it was given.
#:
#: v1_1_0 states what `step_name` has to look like (gap G22). v1_0_0 asked for a `step_name` and
#: never said its shape, while three renderers derive a class name from it - so a model emitting a
#: COBOL-style `1300-COMPUTE-INTEREST` was following the prompt it was given, and failing at
#: `generate` time an approval later. Enforcing the rule without stating it would have been the
#: worse half of the fix on its own.
PROMPT_VERSION = "v1_2_0"

#: Rank for picking the highest tier across a run's programs. `ComplexityTier` is a `str` Enum, so
#: it sorts alphabetically by default -- which would put "complex" below "moderate" and silently
#: choose the wrong end. Explicit ordering rather than relying on the enum's incidental sort.
_TIER_ORDER = {
    ComplexityTier.SIMPLE: 0,
    ComplexityTier.MODERATE: 1,
    ComplexityTier.COMPLEX: 2,
}

_RECORD_NAME_RE = re.compile(r"^\s*01\s+([A-Za-z0-9\-]+)\.", re.MULTILINE)
_RECORD_SUFFIX = "-RECORD"

_VALID_STEP_ROLES = frozenset({"reader", "processor", "writer", "tasklet"})
_VALID_REST_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})


class SolutionArchitectParseError(Exception):
    """The architect model's response could not be parsed into a valid `UnifiedDesign`.

    Raised for malformed JSON, a missing `batch_jobs`/`rest_endpoints` entry for a real program,
    or any reference to a domain entity/program that doesn't exist in what this node actually
    provided as Known Facts. Raised only once `parse_with_repair` has spent its repair attempt
    (ADR-0054), so reaching a caller means the model was asked twice.
    """


def _to_pascal_case(cobol_name: str) -> str:
    return "".join(word.capitalize() for word in cobol_name.split("-") if word)


def _to_camel_case(cobol_name: str) -> str:
    words = [word for word in cobol_name.split("-") if word]
    if not words:
        return cobol_name
    return words[0].lower() + "".join(word.capitalize() for word in words[1:])


def entity_name_from_record(record_name: str) -> str:
    """`TRAN-CAT-BAL-RECORD` -> `TranCatBal`. The one implementation of that transform.

    Extracted so `_derive_entity_name` and `build_file_access_paths` cannot disagree about what an
    entity is called: two spellings of the same rule is how an access path ends up pointing at an
    entity name that does not exist, which compiles and finds nothing.
    """
    if record_name.upper().endswith(_RECORD_SUFFIX):
        record_name = record_name[: -len(_RECORD_SUFFIX)]
    return _to_pascal_case(record_name)


def _key_position(layouts, key_field: str) -> tuple[int | None, int | None]:
    """Where `key_field` sits in the record that declares it.

    Found by searching the program's own `FD` records rather than by trusting that `FD-ACCT-ID` and
    `ACCT-ID` are the same field because their names rhyme. Ambiguity is refused the same way an
    absence is: a key field declared in two records gives a renderer two different offsets and no
    way to choose.
    """
    found = [
        field
        for layout in layouts
        for field in layout.fields
        if field.field_name == key_field
    ]
    if len(found) != 1:
        return None, None
    return found[0].byte_offset, found[0].byte_width


def _key_parts_for(
    read_key_components: list[str],
    assignments: list[KeyAssignment],
    layouts,
) -> list[LookupKeyPart]:
    """The assignments that fill this lookup's key, in key order, fallbacks marked.

    `read_key_components` is the key the program actually reads by, already resolved through any
    group. `XREF-FILE` declares two keys and `CBACT04C` reads on the alternate; emitting parts for
    both would leave a renderer choosing, and the read already chose.

    A key field assigned more than once is a retry path: `FD-DIS-ACCT-GROUP-ID` is filled from the
    account's group and then, on file status 23, from `'DEFAULT'`. Later assignments are marked
    rather than dropped -- dropping them deletes the fallback, and leaving them unmarked would make
    it look like the first attempt.
    """
    parts: list[LookupKeyPart] = []
    for field in read_key_components:
        offset, width = _key_position(layouts, field)
        for index, assignment in enumerate(a for a in assignments if a.key_field == field):
            parts.append(
                LookupKeyPart(
                    key_field=field,
                    source_field=assignment.source_field,
                    literal=assignment.literal,
                    is_fallback=index > 0,
                    key_offset=offset,
                    key_width=width,
                    source_line=assignment.source_line,
                )
            )
    return parts


def attach_control_breaks(
    worktree_root: Path, jobs: list[BatchJobDesign], programs: list[ProgramDesignEntry]
) -> list[BatchJobDesign]:
    """Give each step the control break whose paragraph it declares, if there is one.

    **Attached rather than declared**, for the reason ADR-0030 gave about joins: a control break's
    key and accumulator are facts the COBOL states, and a model asked for them would produce
    plausible totals against the wrong groups. `parsing/control_break.py` recognises the idiom, and
    this puts the result where a renderer can find it -- on the step that declares the paragraph the
    break performs.

    A break whose paragraph no step declares is dropped rather than attached to a guess. That is
    visible as a step with no `control_break` rather than as an error, because a program may
    legitimately do work at a group boundary that this design never split into a step.
    """
    by_program: dict[str, list[tuple[ControlBreak, str | None]]] = {}
    for entry in programs:
        source_text = resolve_program(worktree_root, entry.program_name).source_text
        by_program[entry.program_name] = [
            (found, landing_field(source_text, found.accumulated_from_field))
            for found in extract_control_breaks(source_text)
        ]

    updated: list[BatchJobDesign] = []
    for job in jobs:
        found = by_program.get(job.program_name, [])
        steps = []
        for step in job.steps:
            paragraphs = {name.upper() for name in step.source_paragraphs}
            match = next(
                (
                    (control, landing)
                    for control, landing in found
                    if control.performed_paragraph in paragraphs
                ),
                None,
            )
            if match is None:
                steps.append(step)
                continue
            control, landing = match
            steps.append(
                step.model_copy(
                    update={
                        "control_break": ControlBreakDesign(
                            break_key_field=control.break_key_field,
                            accumulator_field=control.accumulator_field,
                            accumulated_from_field=control.accumulated_from_field,
                            landing_field=landing,
                            performed_paragraph=control.performed_paragraph,
                            test_line=control.test_line,
                            add_line=control.add_line,
                        )
                    }
                )
            )
        updated.append(job.model_copy(update={"steps": steps}))
    return updated


def _write_mode(bindings: list[WriteBinding]) -> WriteMode | None:
    """`append`, `replace`, or `upsert` -- read off every binding for one file, never the first.

    The three are distinguishable only in aggregate: one `WRITE` appends, one `REWRITE` replaces,
    and **both in the same program is neither**. `CBTRN02C` reads a `TCATBAL` row by key, `REWRITE`s
    it when it exists and `WRITE`s it when it does not, which is a create-or-update over one file
    and cannot be represented by picking one of its two statements.

    Returns `None` for a file the program never writes, so "not written" stays distinct from
    "written by appending" -- a default of `append` would make every read-only lookup file look like
    an output.
    """
    if not bindings:
        return None
    modes = {binding.is_update for binding in bindings}
    if modes == {False, True}:
        return "upsert"
    return "replace" if modes == {True} else "append"


def build_file_access_paths(
    worktree_root: Path, programs: list[ProgramDesignEntry]
) -> list[FileAccessPath]:
    """Every program's declared access paths, joined to the records its `READ`s bind them to (G31).

    **Deterministic and model-free**, for the reason ADR-0030 refused the alternative: a model asked
    to describe how four entities join produces plausible rows and a silently wrong comparison, and
    this is the same class of fact `pic_mapper` is not allowed to guess.

    Files the program declares but never `READ ... INTO` are **kept, not dropped** -- `CBACT04C`'s
    `TRANSACT-FILE` is written rather than read, and a reader renderer that only saw read files
    would have no idea the program has an output at all. They carry no `entity_name`, which is what
    says so.

    A file read more than once contributes one path: `CBACT04C` reads `DISCGRP-FILE` twice, on the
    account's own group and again on `'DEFAULT'`, and both reads position on the same key. The
    fallback is a business rule inside the step, not a second access path.
    """
    paths: list[FileAccessPath] = []
    for entry in programs:
        source_text = resolve_program(worktree_root, entry.program_name).source_text
        bindings = extract_record_bindings(source_text)
        writes = extract_write_bindings(source_text)
        # **Every binding per file, not the first.** A file can be written two ways in one program
        # -- `CBTRN02C` creates a `TCATBAL` row when its lookup finds none and updates it when it
        # does -- and reducing that to whichever statement appears first drops the other mode
        # silently. `extract_write_bindings` was built to keep both; this is where they arrive.
        writes_by_file: dict[str, list[WriteBinding]] = {}
        for write in writes:
            writes_by_file.setdefault(write.file_name, []).append(write)
        first_write: dict[str, WriteBinding] = {
            name: group[0] for name, group in writes_by_file.items()
        }
        declarations = extract_file_declarations(source_text)

        # Every field any lookup key is made of, resolved through group keys, so the MOVEs that
        # fill them can be found. Collected across the whole program in one pass: a key field is
        # only interesting because something assigns to it.
        key_fields_by_file: dict[str, list[str]] = {}
        for declaration in declarations:
            if not declaration.is_keyed_lookup:
                continue
            names = list(declaration.alternate_record_keys)
            if declaration.record_key:
                names.append(declaration.record_key)
            components = [
                component
                for name in names
                for component in key_components(source_text, name)
            ]
            key_fields_by_file[declaration.select_name] = components
        # The program's own records, `FD` ones included. Unsizable records are skipped rather than
        # fatal: `CBACT04C`'s WORKING-STORAGE holds a `REDEFINES` group the construct matrix routes
        # to a human, and it has nothing to do with where a key field sits.
        fd_layouts = compute_record_layouts(source_text, skip_unsizable=True)
        assignments = extract_key_assignments(
            source_text, {name for names in key_fields_by_file.values() for name in names}
        )
        first_binding: dict[str, RecordBinding] = {}
        for binding in bindings:
            first_binding.setdefault(binding.file_name, binding)

        for declaration in declarations:
            # Not `binding`: that name is already bound by the loop above as a non-optional
            # `RecordBinding`, and reusing it here made a genuinely-optional lookup read as one.
            matched = first_binding.get(declaration.select_name.upper())
            paths.append(
                FileAccessPath(
                    program_name=entry.program_name,
                    entity_name=(
                        entity_name_from_record(matched.record_name) if matched else ""
                    ),
                    record_name=matched.record_name if matched else "",
                    select_name=declaration.select_name,
                    assign_to=declaration.assign_to,
                    organization=declaration.organization,
                    access_mode=declaration.access_mode,
                    effective_key=(
                        (matched.read_key if matched and matched.read_key else None)
                        or (declaration.record_key if declaration.is_keyed_lookup else None)
                    ),
                    declared_record_key=declaration.record_key,
                    alternate_record_keys=list(declaration.alternate_record_keys),
                    written_entity_name=(
                        entity_name_from_record(
                            first_write[declaration.select_name].record_name
                        )
                        if declaration.select_name in first_write
                        else ""
                    ),
                    write_mode=_write_mode(writes_by_file.get(declaration.select_name, [])),
                    write_lines=[
                        write.source_line
                        for write in writes_by_file.get(declaration.select_name, [])
                    ],
                    key_parts=_key_parts_for(
                        key_components(
                            source_text,
                            (matched.read_key if matched and matched.read_key else None)
                            or declaration.record_key
                            or "",
                        )
                        if declaration.is_keyed_lookup
                        else [],
                        assignments,
                        fd_layouts,
                    ),
                    is_keyed_lookup=declaration.is_keyed_lookup,
                    select_line=declaration.source_line,
                    read_line=matched.source_line if matched else None,
                )
            )
    return paths

def _derive_entity_name(copybook_name: str, copybook_source: str) -> str:
    """The copybook's own `01`-level record name, `-RECORD` stripped, mechanically PascalCased.

    Falls back to the copybook's own name if no `01`-level record is found (shouldn't happen for
    a copybook that contributed at least one mapped field) -- never guesses a business name.
    """
    match = _RECORD_NAME_RE.search(copybook_source)
    if match is None:
        return _to_pascal_case(copybook_name)
    return entity_name_from_record(match.group(1))


def _domain_field(mapping: PicMapping, *, byte_offset: int | None = None) -> DomainField:
    return DomainField(
        java_field_name=_to_camel_case(mapping.field_name),
        cobol_field_name=mapping.field_name,
        java_type=mapping.java_type,
        precision=mapping.precision,
        scale=mapping.scale,
        signed=mapping.signed,
        length=mapping.string_length,
        byte_offset=byte_offset,
    )


def build_domain_entities(
    worktree_root: Path, programs: list[ProgramDesignEntry]
) -> list[DomainEntity]:
    """Merge every program's copybook-sourced fields into one `DomainEntity` per real copybook.

    Re-resolves each program from `worktree_root` rather than trusting only the `ProgramDesignEntry`
    objects passed in -- consistent with `nodes.spec_critic.build_critique_prompt`'s own
    re-resolution, and necessary here regardless: `SpecExtractionResult.field_mappings` is a flat,
    unattributed list with no copybook labels to group by, so the per-copybook grouping this
    function needs has to come from a fresh `group_field_mappings_by_source` call, not the already-
    flattened result `spec_extractor` returned.

    See the module docstring for the merge-by-copybook-name-only and mechanical-naming decisions.
    `FILLER` fields (see `nodes.spec_critic.check_field_reference_fidelity`'s own note on why:
    `pic_mapper` always names them literally `"FILLER"`) are excluded -- padding, not a real
    business field.
    """
    entities: dict[str, DomainEntity] = {}

    for entry in programs:
        resolved = resolve_program(worktree_root, entry.program_name)
        grouped = group_field_mappings_by_source(resolved)

        for source_label, (mappings, _unsupported) in grouped.items():
            if source_label == entry.program_name:
                continue  # only copybook-sourced fields become domain entities
            if not mappings:
                continue  # e.g. CODATECN: zero successfully-mapped fields, not a domain entity

            if source_label not in entities:
                # The record's byte layout, computed from the same copybook the fields came from
                # (G31 finding F1). A copybook holds one `01` record, so the first layout is the
                # one these fields belong to; a source that yields none leaves offsets unset rather
                # than guessed.
                layouts = compute_record_layouts(resolved.copybook_sources[source_label])
                layout = layouts[0] if layouts else None
                entities[source_label] = DomainEntity(
                    name=_derive_entity_name(source_label, resolved.copybook_sources[source_label]),
                    source_copybook=source_label,
                    record_length=layout.record_length if layout else None,
                    used_by_programs=[],
                    fields=[
                        _domain_field(
                            mapping,
                            byte_offset=(
                                layout.offset_of(mapping.field_name) if layout else None
                            ),
                        )
                        for mapping in mappings
                        if mapping.field_name not in (None, "FILLER")
                    ],
                )

            entity = entities[source_label]
            if entry.program_name not in entity.used_by_programs:
                entity.used_by_programs.append(entry.program_name)

    return list(entities.values())



def build_computed_values(
    worktree_root: Path, programs: list[ProgramDesignEntry]
) -> list[ComputedValue]:
    """Every working-storage value each program computes, with `pic_mapper`'s facts attached.

    **The other half of `build_domain_entities`, and the reason this function exists.** That one
    iterates the same groups and skips the program's own, because a domain entity is copybook-
    sourced by definition (ADR-0010). Skipping was right; *discarding* was not. `WS-MONTHLY-INT`'s
    precision and scale were computed here and thrown away one line later, and a model downstream
    inferred them off the `PIC` clause instead.

    Narrowed to values the program's arithmetic actually writes, rather than to every working-storage
    field. Against this corpus that is 3 fields of `CBACT04C`'s 52 and 1 of `CBTRN02C`'s 52 -- the
    business quantities, with none of the `FD-*` file aliases, `IO-STATUS` codes or `APPL-RESULT`
    plumbing that a type-based or scale-based filter would have had to guess about. The narrowing is
    syntactic, so it makes no judgment about which values matter.
    """
    values: list[ComputedValue] = []

    for entry in programs:
        resolved = resolve_program(worktree_root, entry.program_name)
        grouped = group_field_mappings_by_source(resolved)
        own_mappings, _unsupported = grouped.get(entry.program_name, ([], []))
        by_name = {mapping.field_name.upper(): mapping for mapping in own_mappings}

        found = computed_fields(resolved.source_text, set(by_name))
        for cobol_field_name, paragraphs in sorted(found.items()):
            mapping = by_name[cobol_field_name]
            readers = referencing_paragraphs(resolved.source_text, cobol_field_name)
            values.append(
                ComputedValue(
                    program_name=entry.program_name,
                    cobol_field_name=mapping.field_name,
                    java_type=mapping.java_type,
                    precision=mapping.precision,
                    scale=mapping.scale,
                    signed=mapping.signed,
                    computed_in_paragraphs=sorted(paragraphs),
                    escapes_to=sorted(readers - paragraphs),
                    lands_in_field=landing_field(resolved.source_text, cobol_field_name),
                )
            )

    return values


def _entities_of(type_name: str, design: UnifiedDesign) -> list[str]:
    """The domain entities a declared type carries -- itself, or a composite's components."""
    composite = next((c for c in design.composite_types if c.name == type_name), None)
    if composite is not None:
        return [component.entity_name for component in composite.components]
    return [type_name] if any(e.name == type_name for e in design.domain_entities) else []


def unobtainable_inputs(
    job: BatchJobDesign, step: BatchStepDesign, design: UnifiedDesign
) -> list[str]:
    """Entities this step consumes that nothing can supply (G31's check).

    **The third link in a chain this repo has now built one piece at a time.** ADR-0020 checked that
    a step's declared types *resolve*; PR #42 checked that the data its COBOL reads is *reachable*
    from them (G26); this checks that the entities it consumes can actually be **obtained** -- read
    from a file the program declares, or produced by a step that runs before it.

    Both halves are needed or the check is noise. A `Tran` reaching `completeTransaction` is never
    read from a file; `computeInterest` makes it, and flagging it would train a reviewer to ignore
    this. Conversely an entity that no earlier step produces and that appears in no `FILE-CONTROL`
    declaration is a design a renderer cannot build a reader for -- which is precisely the shape
    that left `generate` producing projects that compile and cannot run.

    Deliberately **inputs only**. What a step *writes* is bound by `WRITE ... FROM`, which nothing
    parses yet, so a check over outputs would report every writer as unobtainable -- a false alarm
    that would make this useless. Recorded as a limit rather than approximated.
    """
    declared = [path for path in design.file_access_paths if path.program_name == job.program_name]
    if not declared:
        # **No information is not the same as no access.** `file_access_paths` defaults to empty so
        # a design written before schema 3.2.0 still validates, and a design assembled by hand in a
        # test may not carry it at all. Treating that silence as "this program reads nothing" would
        # report every entity of every step -- a check that fires hardest exactly where it knows
        # least, which is how a category of finding gets ignored.
        return []

    available = {path.entity_name for path in declared if path.entity_name}
    for earlier in job.steps:
        if earlier.step_name == step.step_name:
            break
        available.update(_entities_of(earlier.output_type, design))

    return sorted(
        {entity for entity in _entities_of(step.input_type, design) if entity not in available}
    )

def undeliverable_computed_values(
    job: BatchJobDesign, step: BatchStepDesign, design: UnifiedDesign
) -> list[str]:
    """Values this step computes that its output type has nowhere to put (ADR-0062).

    **The check the step-49 defect needed and nobody had.** `computeMonthlyInterest` was designed
    `in = out = RatedCategoryBalance`; the renderer emitted a structurally correct
    `ItemProcessor<RatedCategoryBalance, RatedCategoryBalance>`; and the model computed the monthly
    interest, wrote a javadoc saying it "accumulates it into the account's running month total", and
    returned `item`. Discarding a value is legal Java, every component behaved correctly, and the
    only way to notice was to read the generated body.

    Three narrowings, and each of them turns a false alarm into silence rather than the reverse:

    1. **Processors only.** A reader's and a writer's outputs are bound by `READ ... INTO` and
       `WRITE ... FROM`, and a tasklet has no item at all -- which is why `unobtainable_inputs`
       states the same limit for outputs. Without this, `CBACT01C`'s and `CBCUS01C`'s open/close
       tasklets would be reported for computing `APPL-RESULT`, a status code that is control flow.
    2. **Only values that escape the paragraph computing them.** `CBTRN02C`'s `WS-TEMP-BAL` is
       computed and then compared against a credit limit in the same paragraph, and `CBACT04C`'s
       `WS-TRANID-SUFFIX` is built and consumed inside `1300-B-WRITE-TX`. Neither has anywhere to
       go, neither needs one, and a rule without this would refuse both correct designs. It is also
       what makes this different from refusing `input_type == output_type`, which is mechanical,
       cheap and wrong: a processor returning `null` to filter is a legitimate X -> X step, and this
       fires on none of them.
    3. **A value that lands in a record the output already carries is delivered.**
       `WS-MONTHLY-INT` reaches `TRAN-AMT`, so a design whose output composite carries `Tran` needs
       no computed field -- the value rides the record. Refusing that would force a redundant
       declaration and make the honest design the one that fails.

    Against this corpus the rule fires on exactly `WS-MONTHLY-INT` and `WS-TOTAL-INT` in
    `computeMonthlyInterest` -- the two halves of the real defect, one of them the accumulation the
    generated javadoc claimed and did not perform -- and on nothing else in four programs.

    Returns sorted COBOL field names, so a refusal message is stable across runs.
    """
    if step.role != "processor":
        return []

    owned = {name.upper() for name in step.source_paragraphs}
    carried = set(_entities_of(step.output_type, design))

    output = next((c for c in design.composite_types if c.name == step.output_type), None)
    declared = (
        {computed.cobol_field_name.upper() for computed in output.computed_fields}
        if output is not None
        else set()
    )

    owners: dict[str, set[str]] = {}
    for entity in design.domain_entities:
        for field in entity.fields:
            owners.setdefault(field.cobol_field_name.upper(), set()).add(entity.name)

    undelivered: list[str] = []
    for value in design.computed_values:
        if value.program_name != job.program_name:
            continue
        if not {name.upper() for name in value.computed_in_paragraphs} & owned:
            continue
        if not value.escapes_to:
            continue
        if value.cobol_field_name.upper() in declared:
            continue
        landing = (value.lands_in_field or "").upper()
        if landing and owners.get(landing, set()) & carried:
            continue
        undelivered.append(value.cobol_field_name)

    return sorted(undelivered)


def unreachable_entities(
    step: BatchStepDesign,
    *,
    source_text: str,
    entities: list[DomainEntity],
    composites: list[CompositeType],
    owned_elsewhere: frozenset[str] = frozenset(),
) -> list[str]:
    """Entities the step's COBOL reads that its declared types cannot reach (gap G26).

    `owned_elsewhere` names paragraphs that other steps of the same job own. A `PERFORM` into one
    of those is a call into another step's work, so it is a boundary rather than part of this
    step's reach -- without it, splitting a paragraph chain into steps would leave every caller
    charged with its callee's data and report a correct design as broken.

    ADR-0020 decision 5 checks a step's `input_type`/`output_type` **resolve** -- that each names a
    declared entity or composite. This is the other half: whether the data those paragraphs
    actually touch is *in* the types the step was handed. Resolution passing while this fails is
    exactly the state that produced G26 -- every type name real, the design ungeneratable, and the
    only signal a model refusing to invent the values it could not reach.

    Deterministic and deliberately shallow: declared field names matched against the paragraph
    text, following `PERFORM`. It does not decide whether a reference matters. A name that appears
    is reported and a human weighs it, which is the specialist contract's rule that this repo emits
    facts for a gate and never decisions.

    Returns sorted names, so a gate item is stable across runs.
    """
    by_name = {entity.name: entity for entity in entities}
    composites_by_name = {composite.name: composite for composite in composites}

    def entities_of(type_name: str) -> set[str]:
        if type_name in composites_by_name:
            return {c.entity_name for c in composites_by_name[type_name].components}
        return {type_name} if type_name in by_name else set()

    reachable = entities_of(step.input_type) | entities_of(step.output_type)

    # Every declared field name, mapped back to the entities that own it. A name shared by two
    # entities counts as reachable if *either* is: the COBOL's own ambiguity should not become a
    # false alarm, because a gate nobody trusts is worse than one that occasionally under-reports.
    owners: dict[str, set[str]] = {}
    for entity in entities:
        for field in entity.fields:
            owners.setdefault(field.cobol_field_name.upper(), set()).add(entity.name)

    referenced = referenced_fields(
        source_text,
        list(step.source_paragraphs),
        set(owners),
        stop_at=owned_elsewhere - set(step.source_paragraphs),
    )

    unreachable: set[str] = set()
    for field_name in referenced:
        candidates = owners[field_name]
        if not (candidates & reachable):
            unreachable |= candidates
    return sorted(unreachable)

def _render_known_facts(
    domain_entities: list[DomainEntity],
    programs: list[ProgramDesignEntry],
    computed_values: list[ComputedValue] | None = None,
) -> str:
    """Render the deterministic facts block the prompt's system instructions call "Known Facts"."""
    lines = ["# Known Facts for solution_architect", ""]

    lines.append("## Domain entities (deterministic -- reproduce exactly, never invent/rename/merge)")
    for entity in domain_entities:
        lines.append(
            f"### {entity.name} (from {entity.source_copybook}, "
            f"used by {', '.join(entity.used_by_programs)})"
        )
        lines += ["| Field | Java type | Precision | Scale | Signed |", "|---|---|---|---|---|"]
        for field in entity.fields:
            precision = field.precision if field.precision is not None else "-"
            scale = field.scale if field.scale is not None else "-"
            lines.append(
                f"| {field.java_field_name} ({field.cobol_field_name}) | {field.java_type} "
                f"| {precision} | {scale} | {field.signed} |"
            )
        lines.append("")

    if computed_values:
        lines.append("## Computed values per program (deterministic -- never re-read from a PIC clause)")
        lines += [
            "| Program | COBOL field | Java type | Precision | Scale | Computed in | Also read by |",
            "|---|---|---|---|---|---|---|",
        ]
        for value in computed_values:
            precision = value.precision if value.precision is not None else "-"
            scale = value.scale if value.scale is not None else "-"
            # "Also read by" is the column that decides whether a value has to cross a step
            # boundary. An empty one is a local intermediate and needs no home.
            escapes = ", ".join(value.escapes_to) or "(nothing -- local to its paragraph)"
            lines.append(
                f"| {value.program_name} | {value.cobol_field_name} | {value.java_type} "
                f"| {precision} | {scale} | {', '.join(value.computed_in_paragraphs)} "
                f"| {escapes} |"
            )
        lines.append("")

    lines.append("## Paragraph flow per program (source order)")
    for entry in programs:
        lines.append(f"### {entry.program_name}")
        for name in entry.spec_extraction.paragraph_names:
            lines.append(f"- {name}")
        lines.append("")

    return "\n".join(lines)


def build_architect_prompt(
    domain_entities: list[DomainEntity],
    programs: list[ProgramDesignEntry],
    computed_values: list[ComputedValue] | None = None,
) -> str:
    """Build the user-turn prompt content: Known Facts followed by every wrapped, untrusted narration.

    See the module docstring for why each `spec_markdown` is wrapped again here, even though it's
    this repo's own prior LLM output. Delimiter-forgery detection
    (`core.guardrails.DelimiterForgeryError`) is not caught here, same as `spec_extractor`'s and
    `spec_critic`'s own prompt builders -- an unambiguous hard failure that must propagate.
    """
    known_facts = _render_known_facts(domain_entities, programs, computed_values)
    wrapped_sections = [
        wrap_untrusted_cobol(entry.spec_extraction.spec_markdown, source_label=entry.program_name)
        for entry in programs
    ]
    return known_facts + "\n\n" + "\n\n".join(wrapped_sections)


def _require_keys(item: object, keys: set[str], what: str) -> dict:
    if not isinstance(item, dict) or not keys <= set(item):
        raise SolutionArchitectParseError(f"solution_architect {what} missing required fields: {item!r}")
    return item


def _parse_unified_design_response(
    raw_response: str,
    domain_entities: list[DomainEntity],
    programs: list[ProgramDesignEntry],
    computed_values: list[ComputedValue] | None = None,
) -> tuple[list[BatchJobDesign], list[RestEndpointDesign], list[CompositeType]]:
    """Parse and validate the architect model's JSON response against the real Known Facts.

    Raises:
        SolutionArchitectParseError: malformed JSON, a missing top-level key, a missing
            `batch_jobs` entry for a real program, or any reference to a domain entity, program,
            step role, or REST method that isn't one of the real ones this node actually offered.
            Called through `parse_with_repair`, so one such failure buys a second attempt before it
            propagates -- see the module docstring.
    """
    entity_names = {entity.name for entity in domain_entities}
    program_names = {entry.program_name for entry in programs}

    candidate = strip_code_fence(raw_response)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SolutionArchitectParseError(
            f"solution_architect response is not valid JSON: {exc}. Raw response: {raw_response!r}"
        ) from None

    if not isinstance(parsed, dict) or not {"batch_jobs", "rest_endpoints"} <= set(parsed):
        raise SolutionArchitectParseError(
            f"solution_architect response must be a JSON object with batch_jobs and "
            f"rest_endpoints keys: {raw_response!r}"
        )

    # ADR-0020: composites are optional, because a design whose steps all operate on plain entities
    # needs none. Parsed before the jobs so a step's types can resolve against them.
    composite_types: list[CompositeType] = []
    for composite in parsed.get("composite_types", []):
        composite = _require_keys(composite, {"name", "components"}, "composite_types entry")
        components: list[CompositeComponent] = []
        for component in composite["components"]:
            component = _require_keys(
                component, {"field_name", "entity_name"}, "composite component"
            )
            if component["entity_name"] not in entity_names:
                raise SolutionArchitectParseError(
                    f"solution_architect composite {composite['name']!r} references an unknown "
                    f"domain entity: {component['entity_name']!r}"
                )
            components.append(
                CompositeComponent(
                    field_name=component["field_name"], entity_name=component["entity_name"]
                )
            )
        computed_names = {value.cobol_field_name.upper() for value in computed_values or []}
        computed_components: list[ComputedComponent] = []
        for computed in composite.get("computed_fields", []):
            computed = _require_keys(
                computed, {"field_name", "cobol_field_name"}, "composite computed field"
            )
            # Refused here for the same reason an unknown `entity_name` is, one branch above: a
            # composite may only reference what the deterministic layer actually produced. The
            # difference is what a wrong answer costs -- an unknown entity fails at render time,
            # while an unknown computed value would have to be given a Java type and precision by
            # something, and every candidate for "something" is a guess.
            if computed["cobol_field_name"].upper() not in computed_names:
                raise SolutionArchitectParseError(
                    f"solution_architect composite {composite['name']!r} declares computed field "
                    f"{computed['field_name']!r} carrying "
                    f"{computed['cobol_field_name']!r}, which is not one of this run's computed "
                    f"values: {sorted(computed_names)}. A computed field's type, precision and "
                    "scale come from the Known Facts; name one of those values or drop the field."
                )
            computed_components.append(
                ComputedComponent(
                    field_name=computed["field_name"],
                    cobol_field_name=computed["cobol_field_name"],
                )
            )
        composite_types.append(
            CompositeType(
                name=composite["name"],
                components=components,
                computed_fields=computed_components,
            )
        )

    known_type_names = entity_names | {composite.name for composite in composite_types}

    batch_jobs: list[BatchJobDesign] = []
    for job in parsed["batch_jobs"]:
        job = _require_keys(job, {"program_name", "job_name", "domain_entities", "steps"}, "batch_jobs entry")
        if job["program_name"] not in program_names:
            raise SolutionArchitectParseError(
                f"solution_architect batch_jobs entry names an unknown program: {job['program_name']!r}"
            )
        for entity_name in job["domain_entities"]:
            if entity_name not in entity_names:
                raise SolutionArchitectParseError(
                    f"solution_architect batch_jobs entry references an unknown domain entity: "
                    f"{entity_name!r}"
                )

        steps: list[BatchStepDesign] = []
        for step in job["steps"]:
            step = _require_keys(
                step,
                {
                    "step_name", "source_paragraphs", "role", "description",
                    "input_type", "output_type", "guard_condition",
                },
                "batch step",
            )
            if step["role"] not in _VALID_STEP_ROLES:
                raise SolutionArchitectParseError(
                    f"solution_architect batch step has an unknown role: {step['role']!r}"
                )
            # ADR-0020 decision 5: resolution is checked where the design is *produced*. A design
            # that cannot be generated from should fail before a human approves it at the gate,
            # not three layers down in `generate` afterwards.
            for field_name in ("input_type", "output_type"):
                if step[field_name] not in known_type_names:
                    raise SolutionArchitectParseError(
                        f"solution_architect batch step {step['step_name']!r} has "
                        f"{field_name}={step[field_name]!r}, which is neither a domain entity nor "
                        f"a declared composite type"
                    )
            steps.append(
                BatchStepDesign(
                    step_name=step["step_name"],
                    source_paragraphs=step["source_paragraphs"],
                    role=step["role"],
                    description=step["description"],
                    input_type=step["input_type"],
                    output_type=step["output_type"],
                    guard_condition=step["guard_condition"],
                )
            )

        batch_jobs.append(
            BatchJobDesign(
                program_name=job["program_name"],
                job_name=job["job_name"],
                domain_entities=job["domain_entities"],
                steps=steps,
            )
        )

    covered_programs = {job.program_name for job in batch_jobs}
    missing_programs = program_names - covered_programs
    if missing_programs:
        raise SolutionArchitectParseError(
            f"solution_architect response is missing batch_jobs for: {sorted(missing_programs)}"
        )

    rest_endpoints: list[RestEndpointDesign] = []
    for endpoint in parsed["rest_endpoints"]:
        endpoint = _require_keys(
            endpoint, {"method", "path", "domain_entity", "description"}, "rest_endpoints entry"
        )
        if endpoint["method"] not in _VALID_REST_METHODS:
            raise SolutionArchitectParseError(
                f"solution_architect rest_endpoints entry has an unknown method: {endpoint['method']!r}"
            )
        if endpoint["domain_entity"] not in entity_names:
            raise SolutionArchitectParseError(
                f"solution_architect rest_endpoints entry references an unknown domain entity: "
                f"{endpoint['domain_entity']!r}"
            )
        rest_endpoints.append(
            RestEndpointDesign(
                method=endpoint["method"],
                path=endpoint["path"],
                domain_entity=endpoint["domain_entity"],
                description=endpoint["description"],
            )
        )

    _refuse_undeliverable_computed_values(
        batch_jobs, composite_types, domain_entities, computed_values or []
    )

    return batch_jobs, rest_endpoints, composite_types


def _refuse_undeliverable_computed_values(
    batch_jobs: list[BatchJobDesign],
    composite_types: list[CompositeType],
    domain_entities: list[DomainEntity],
    computed_values: list[ComputedValue],
) -> None:
    """Refuse a design whose processor computes a value it cannot return (ADR-0062).

    **Refused where it is produced, before a human approves it** -- ADR-0020 decision 5's rule and
    ADR-0059's shape. The alternative is what actually happened: the design passed every check,
    a human approved it at the gate, `generate` rendered a structurally correct processor, and the
    defect surfaced only when someone read the generated Java at the release gate.

    Reached through `parse_with_repair`, so a model that produced such a design gets one repair
    attempt carrying this message rather than a run that dies an approval later. The message
    therefore names the fix -- declare a computed field on the output composite -- because it is
    read by a model as instructions, not only by a person as a diagnosis.
    """
    design = UnifiedDesign(
        domain_entities=domain_entities,
        batch_jobs=batch_jobs,
        rest_endpoints=[],
        composite_types=composite_types,
        computed_values=computed_values,
    )

    for job in batch_jobs:
        for step in job.steps:
            undelivered = undeliverable_computed_values(job, step, design)
            if not undelivered:
                continue
            names = ", ".join(undelivered)
            raise SolutionArchitectParseError(
                f"solution_architect step {step.step_name!r} computes {names}, which its output "
                f"type {step.output_type!r} cannot carry. Paragraph(s) "
                f"{', '.join(step.source_paragraphs)} compute those values and other paragraphs "
                f"read them, so they must survive the step -- but {step.output_type!r} has no "
                f"field for them, and a processor that computes a value it cannot return discards "
                f"it silently. Declare an output composite with a computed_fields entry naming "
                f"each of {names} (its Java type, precision and scale come from computed_values, "
                f"so give only field_name and cobol_field_name), or make the output type carry the "
                f"record the value is moved into."
            )


#: `(model, system_prompt, user_content) -> raw response text`. Injected so `design_solution`'s
#: tests exercise every deterministic step above without a live model credential.
ArchitectFn = Callable[[RoutingDecision, str, str], str]


def _default_architect(routing: RoutingDecision, system_prompt: str, user_content: str) -> str:
    """Call a real model through `core/model_client.py` (ADR-0013), which owns backend choice,
    timeout, retry/backoff, and usage capture -- this node does not reimplement any of that."""
    return call_model(
        _NODE_NAME,
        routing.model,
        system_prompt,
        user_content,
        effort=routing.effort,
        max_output_tokens=routing.max_output_tokens,
    ).text


def _load_system_prompt() -> str:
    return read_prompt(_NODE_NAME, PROMPT_VERSION)


def design_solution(
    worktree_root: Path,
    programs: list[ProgramDesignEntry],
    *,
    model_routing_config: Path | None = None,
    architect: ArchitectFn = _default_architect,
) -> UnifiedDesign:
    """Design one unified `UnifiedDesign` across every program in `programs`.

    Args:
        worktree_root: The cloned tenant-repo worktree path -- re-resolves each program's real
            source to build the domain entities and prompt against (see `build_domain_entities`).
        programs: Every program's `spec_extractor` + `spec_critic` output to design against.
            Processed regardless of `critique.overall_confidence` -- weighing a low-confidence
            program is a human/gate decision (`core.contracts.build_gate_items`), not this node's.
        model_routing_config: Overrides `core/model_routing.py`'s default config path -- tests only.
        architect: Overrides the default live Anthropic call -- tests only.

    Raises:
        tenant_repo.TenantRepoFileNotFoundError: a program's source or a copybook is missing.
        parsing.cobol_parser.UnsupportedCopyConstructError: a `COPY ... REPLACING` was found.
        core.guardrails.DelimiterForgeryError: a narration contains this repo's prompt delimiter.
        core.model_routing.ModelRoutingConfigError: the config is missing/malformed/incomplete.
        SolutionArchitectParseError: the architect model's response isn't valid structured JSON
            against the real Known Facts.

    None of these are caught here -- each is unambiguous enough to fail loudly, consistent with
    `nodes.spec_extractor.extract_spec` and `nodes.spec_critic.critique_spec`.
    """
    domain_entities = build_domain_entities(worktree_root, programs)
    computed_values = build_computed_values(worktree_root, programs)
    user_content = build_architect_prompt(domain_entities, programs, computed_values)
    system_prompt = _load_system_prompt()

    # One call reasoning across every program at once (ADR-0010), so it takes the *highest* tier
    # present -- not an average. A run containing one hard program is a hard run: averaging would
    # let three simple programs pull the architect down to a tier the fourth needs.
    tier = max(
        (entry.spec_extraction.complexity.tier for entry in programs),
        key=_TIER_ORDER.__getitem__,
        default=ComplexityTier.COMPLEX,
    )
    routing_kwargs = {} if model_routing_config is None else {"config_path": model_routing_config}
    routing = resolve_routing(_NODE_NAME, tier, **routing_kwargs)
    logger.info(
        "solution_architect routing: programs=%d tier=%s model=%s effort=%s",
        len(programs), routing.tier.value, routing.model, routing.effort,
    )
    raw_response = architect(routing, system_prompt, user_content)
    batch_jobs, rest_endpoints, composite_types = parse_with_repair(
        _NODE_NAME,
        raw_response,
        lambda text: _parse_unified_design_response(
            text, domain_entities, programs, computed_values
        ),
        lambda instruction: architect(routing, system_prompt, f"{user_content}\n\n{instruction}"),
        on=SolutionArchitectParseError,
    )

    return UnifiedDesign(
        domain_entities=domain_entities,
        # Deterministic, and attached here rather than asked of the architect above: a control
        # break's key and accumulator are facts the COBOL states (ADR-0032).
        batch_jobs=attach_control_breaks(worktree_root, batch_jobs, programs),
        rest_endpoints=rest_endpoints,
        composite_types=composite_types,
        # Deterministic, and built here rather than asked of the model above: the architect decides
        # the step chain, the COBOL decides how data is reached (G31, ADR-0030).
        file_access_paths=build_file_access_paths(worktree_root, programs),
        # Deterministic for the same reason, and for one more: a `COMPUTE`'s target precision and
        # scale are numbers a wrong answer to looks exactly like a right one (ADR-0062).
        computed_values=computed_values,
    )
