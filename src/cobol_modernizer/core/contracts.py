"""`design.json`'s schema and the `design`/`generate` CLI I/O contracts (ADR-0008, ADR-0010).

Per ADR-0001, this repo has no human-in-the-loop gate of its own -- control-plane's existing,
durable gate reviews `design.json` between the `design` and `generate` invocations (ADR-0003).
This module defines the *shape* of what that review actually sees. Two things it deliberately does
NOT do, both named explicitly in ADR-0008 so a future change doesn't quietly cross either line:

1. It never decides what happens with a `GateItem` -- no approve/reject, no blocking. Consistent
   with `core/guardrails.InjectionFlag`'s own posture ("flagged for a human or downstream gate to
   weigh, never used to block processing outright"), `build_gate_items` only surfaces facts.
   Deciding whether `gate_item_count > 0` should pause anything is control-plane's gate policy,
   not this module's.
2. `DesignDocument.unified_design`'s models (`UnifiedDesign` and friends) are pure data shapes,
   defined here per ADR-0010 rather than in `nodes/solution_architect.py` itself, so that module
   can depend on `core/contracts.py` the same direction every other node already depends on
   `core/` -- not the other way around. The actual behavior that produces a `UnifiedDesign`
   (deterministic domain-entity merging, the LLM-authored batch/REST design call) lives in
   `nodes/solution_architect.py`; this module only says what the result looks like.

`LOW_CONFIDENCE_THRESHOLD` (0.7) is a tentative default, not a benchmarked number, in the same
spirit as ADR-0004's tentative model tiers -- no real critique run against a live model has
happened yet to calibrate against (this dev environment has no Anthropic API credential; see
`nodes/spec_critic.py`'s own module docstring). Revisit once one has.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from cobol_modernizer.nodes.spec_critic import SpecCritiqueResult
from cobol_modernizer.nodes.spec_extractor import SpecExtractionResult

BatchStepRole = Literal["reader", "processor", "writer", "tasklet"]
RestMethod = Literal["GET", "POST", "PUT", "DELETE"]
#: How a program writes one file. `append` is `WRITE` alone (an `OPEN OUTPUT` sequential file),
#: `replace` is `REWRITE` alone (a record found by key is replaced in place), and `upsert` is both
#: in the same program -- COBOL's read-by-key, `REWRITE` if found, `WRITE` if not.
WriteMode = Literal["append", "replace", "upsert"]


class DomainField(BaseModel):
    """One field of a `DomainEntity`, reusing `pic_mapper`'s deterministic output verbatim.

    `java_field_name` and the entity's own `name` are direct, mechanical transforms of the COBOL
    name (kebab-case to camelCase/PascalCase, `-RECORD` suffix stripped from the entity name) --
    not a business-semantic rename. See ADR-0010 decision 2 for why: a human-readable name is a
    judgment call for the LLM-authored parts of `UnifiedDesign` to make, not this deterministic
    field.
    """

    java_field_name: str
    cobol_field_name: str
    java_type: str
    precision: int | None
    scale: int | None
    #: The declared width of a `PIC X(n)` field, and `None` for a numeric one (gap G28).
    #:
    #: `pic_mapper` has always computed this and `_to_domain_field` dropped it one line before it
    #: would have reached the design -- so a `PIC X(50)` became a bare `String` and the width
    #: existed nowhere the generator could see. A model asked to translate `MOVE SPACES` therefore
    #: wrote `""`, and flagged that an empty string and fifty spaces are not the same record on
    #: disk. Same defect class as G21 and G24: a computed fact that never reached the target.
    #:
    #: Optional with a default, unlike `BatchStepDesign.guard_condition`, and the asymmetry is
    #: deliberate: a guard is LLM judgment, where a missing key and a considered `null` must look
    #: different. This is deterministic -- the producer always fills it -- so there is no silence
    #: to distinguish, and a required key would break every existing design for no signal.
    length: int | None = None
    signed: bool
    #: Where this field starts in its record, in bytes (G31 finding F1).
    #:
    #: `DomainField` carried a width and no position, so a reader built from the design had to
    #: assume fields are contiguous from byte zero -- true of these four copybooks only because
    #: every `FILLER` in them is trailing, and false the moment one sits in the middle. Computed
    #: over *every* declaration including `FILLER`, which is what makes an interior one a non-event
    #: rather than a silent mis-slice of every field after it.
    #:
    #: `None` only where no layout was computed at all: `parsing/record_layout.py` refuses a record
    #: it cannot size rather than returning a partial one, because a missing width is a wrong offset
    #: for every field that follows.
    byte_offset: int | None = None


class DomainEntity(BaseModel):
    """One unified domain entity -- every program that `COPY`s the same copybook shares one.

    `source_copybook` is the exact copybook name that produced this entity; two different
    copybook names are never merged into one entity, even when structurally similar (ADR-0010
    decision 1) -- `source_copybook` is what makes that traceable and checkable.
    """

    name: str
    source_copybook: str
    #: The record's total length in bytes, `FILLER` included. `CVTRA01Y`'s comment says
    #: `RECLN = 50`; nothing in the contract said so until now, and a fixed-width reader needs it
    #: to know where one record ends and the next begins.
    record_length: int | None = None
    used_by_programs: list[str]
    fields: list[DomainField]


class BatchStepDesign(BaseModel):
    """One step of a `BatchJobDesign`, LLM-authored (ADR-0010 decision 3).

    `source_paragraphs` names the real COBOL paragraph(s) (from `SpecExtractionResult.paragraph_names`)
    this step's logic comes from -- provenance one hop further than `spec_extractor`'s own
    source-label tracing (ADR-0006), now into the batch design itself.
    """

    step_name: str
    source_paragraphs: list[str]
    role: BatchStepRole
    description: str
    #: The `ItemProcessor<I, O>` type arguments, by name (ADR-0020). Each resolves against a
    #: `DomainEntity` or a `CompositeType` declared on the same `UnifiedDesign`. Required, and
    #: required to resolve: a processor is parameterised by two types, and generating one from a
    #: design that names neither means guessing them -- which produces Java that compiles and is
    #: wrong, the one failure mode this repo's whole deterministic core exists to prevent.
    input_type: str
    output_type: str
    #: The condition under which this step produces output at all, verbatim from the COBOL
    #: (ADR-0022). `null` states that the step is unconditional -- and it is a **required key** with
    #: a nullable value for the same reason `modernization_engineer`'s `notes` is mandatory: a
    #: design with nothing to declare must say so, so that silence is a statement rather than an
    #: omission. Defaulting it would make "this step is unconditional" and "nobody considered it"
    #: identical, which is precisely how the defect this field exists for got through.
    #:
    #: Found by step 45's first real run (audit G25). `CBACT04C` guards its interest calculation
    #: with `IF DIS-INT-RATE NOT = 0` -- and that guard is not in `1300-COMPUTE-INTEREST`, it is in
    #: the **unnamed main body** that performs it. So it could not be added to `source_paragraphs`:
    #: there is no paragraph to name, and naming `PROCEDURE DIVISION` would scope the step to the
    #: file opens and the account update too. A model given the paragraph translated it faithfully
    #: and still produced a processor that emits a transaction record COBOL never writes.
    guard_condition: str | None
    #: The control break this step runs at, when it runs at one (G31, ADR-0032). Deterministic --
    #: `parsing/control_break.py` recognises the idiom or does not -- so it defaults to `None`
    #: rather than being a required-but-nullable key like `guard_condition`, which is LLM judgment
    #: and where silence and "no break" have to look different.
    control_break: ControlBreakDesign | None = None
    #: Names of the `BatchJobDesign.job_parameters` this step consumes (ADR-0026). Declared per step
    #: rather than injected wholesale so a constructor carries what that step needs and no more --
    #: ADR-0020's posture that a step declares what it needs, applied to invocation facts instead of
    #: types. Optional with an empty default; see `BatchJobDesign.job_parameters` for why an empty
    #: list is a statement here where a `null` guard would not have been.
    job_parameters: list[str] = []
    #: True when this step's own logic reads state that this step writes, so items cannot be
    #: processed independently of one another (ADR-0039, ADR-0040).
    #:
    #: **Measured on `CBTRN02C`, which is the reason this exists.** Its `1500-B-LOOKUP-ACCT`
    #: decides whether a transaction is accepted by comparing the credit limit against cycle
    #: fields that `2800-UPDATE-ACCOUNT-REC` rewrites for every accepted transaction, so the
    #: decision for item *n* reads what items *1..n-1* wrote. Judged independently, **30 of that
    #: program's 43 rejections disappear**: a stateless implementation writes 287 records where
    #: the program writes 257, every one of them individually correct, so only a count sees it.
    #:
    #: **Declared, not derived, and the distinction is the whole point.** The derivable
    #: condition -- a file that is both a keyed lookup and written back -- is *also* true of
    #: `CBACT04C`'s account writer, which is correct as it stands because its input is aggregated
    #: to one item per account. A derivation right about one program and wrong about the other
    #: would be worse than a declaration (ADR-0039). Defaults to `False`, which is the safe
    #: direction: a step that does not say this renders exactly as it did before the field
    #: existed.
    reads_own_writes: bool = False
    #: Entity names whose keyed lookup this step tolerates finding nothing (ADR-0042). The rendered
    #: reader hands the processor `null` for these instead of refusing the record.
    #:
    #: **A miss is not always an error, and `CBTRN02C` is where that stops being theoretical.** Its
    #: `2700-UPDATE-TCATBAL` reads a balance row and, `INVALID KEY`, *creates* one -- 44 times on
    #: this corpus, which is exactly how 50 balance rows become the 94 the oracle asserts. A reader
    #: that refused the record would abend on the first transaction posting to a category the
    #: account has no balance for.
    #:
    #: **Declared rather than derived, and the derivation was tried first.** Both Track C programs
    #: write `INVALID KEY` clauses that only `DISPLAY` and continue, so the clause does not separate
    #: "handled" from "unhandled" -- `CBACT04C`'s reader refuses a miss and is right only because
    #: none happens on its corpus. Deriving optionality from the clause would change a reader
    #: measured green at 500 of 500 whose bodies do not null-check, so the fact is stated per step
    #: instead. Empty by default, which keeps every existing step refusing exactly as it does now.
    optional_lookups: list[str] = []


class ControlBreakDesign(BaseModel):
    """What a step groups by and what it accumulates -- the fact a control break hides in an idiom.

    **Why it is on the step rather than in the job.** A control break performs one paragraph at the
    group boundary, and that paragraph is what a step declares in `source_paragraphs`. Attaching it
    anywhere else would leave a renderer matching paragraph names to find it.

    **`accumulated_from_field` is what to sum, and `landing_field` is where a generated type can
    find it.** `WS-TOTAL-INT` accumulates `WS-MONTHLY-INT`, which is also moved to `TRAN-AMT`. The
    accumulator is a program variable no generated record has; the landing field is a column an
    aggregation can actually sum, which is what makes ADR-0027's "already-summed item" renderable
    rather than a note.

    Deterministic throughout: every field is read from the COBOL by
    `parsing/control_break.py`, and a program whose idiom does not match completely gets no
    `ControlBreakDesign` at all rather than a partial one.
    """

    break_key_field: str
    accumulator_field: str
    accumulated_from_field: str
    #: The record field `accumulated_from_field` is also moved into, when there is one. `None` when
    #: the value never reaches a record, which makes the group total unreachable from what a step
    #: sees -- a finding rather than a default.
    landing_field: str | None = None
    performed_paragraph: str
    test_line: int
    add_line: int


class CompositeComponent(BaseModel):
    """One component of a `CompositeType`: a field name, and the entity it holds."""

    field_name: str
    entity_name: str


class CompositeType(BaseModel):
    """A record composed of existing domain entities, for a value no single copybook describes.

    Introduced by ADR-0020 because a real `solution_architect` run chains processor steps, and the
    values flowing between them are not entities: "a `TranCatBal` with its `Account` and `CardXref`
    resolved" is a type the target genuinely needs and no copybook declares.

    **Every component references an entity that already exists.** A composite never introduces a
    field a copybook did not produce, so `pic_mapper`'s computed precision and scale still reach the
    generated code unchanged -- the composite only says which records travel together.

    Rendered deterministically rather than generated (ADR-0010's line, unmoved): the *shape* is a
    mechanical transform of this declaration, and only the *name* is judgment.
    """

    name: str
    components: list[CompositeComponent]


class JobParameter(BaseModel):
    """One property of an invocation rather than of an item (ADR-0026, gap G29).

    **Why this is a declared fact and not something a body reads.** A stateless `ItemProcessor` has
    no access to when it ran or what it was invoked with, and a real model given no alternative
    reached for `LocalDateTime.now()` -- which compiles, reads correctly, and makes the same input
    produce a different record on every run. `NonDeterministicBodyError` refuses that; this is what
    it refuses *in favour of*.

    `source_cobol` names where the value comes from in the program, so a reviewer can check the
    declaration against the source rather than trust it. `CBACT04C`'s `PARM-DATE` is the
    unambiguous case: it is in the `LINKAGE SECTION` and arrives via
    `PROCEDURE DIVISION USING EXTERNAL-PARMS`, so it is a job parameter in the COBOL too.
    """

    name: str
    java_type: str
    description: str
    #: The COBOL field or construct this stands in for, for provenance. `null` where there is no
    #: single one -- the run timestamp stands in for `FUNCTION CURRENT-DATE`, which ADR-0026 records
    #: as a deliberate divergence rather than an equivalence.
    source_cobol: str | None = None


class BatchJobDesign(BaseModel):
    """One program's Spring Batch job design -- one per Track C program, LLM-authored."""

    program_name: str
    job_name: str
    domain_entities: list[str]
    steps: list[BatchStepDesign]
    #: Invocation-level facts this job's steps may consume (ADR-0026). **Optional with an empty
    #: default, deliberately unlike ADR-0022's required `guard_condition`**: a guard needed `null`
    #: to be distinguishable from "nobody checked", whereas `[]` already says *this job takes none*
    #: and has no silent state to be confused with. Additive, so schema 3.0.0 -> 3.1.0 rather than
    #: a breaking bump.
    job_parameters: list[JobParameter] = []


class RestEndpointDesign(BaseModel):
    """One thin-REST-layer endpoint, LLM-authored -- control/observability or a point query.

    Per ADR-0009, this layer triggers/monitors Spring Batch jobs and serves genuine point queries
    against `domain_entities` -- never the bulk batch logic itself.
    """

    method: RestMethod
    path: str
    domain_entity: str
    description: str


class LookupKeyPart(BaseModel):
    """One component of a keyed lookup's key, and where its value comes from.

    **The join predicate, read from the COBOL rather than declared by anyone.** `FILE-CONTROL` says
    `ACCOUNT-FILE` is read by `FD-ACCT-ID`; `MOVE TRANCAT-ACCT-ID TO FD-ACCT-ID` says what goes in
    it. ADR-0030 refused an LLM-declared join because a wrong one produces plausible rows and a
    silently wrong comparison -- and it turns out nobody needs to declare it, because the program
    already does.

    A key can have several parts: `DISCGRP-FILE`'s is a group of three, filled by three separate
    `MOVE`s.

    `literal` is set instead of `source_field` when the program moves a constant. That is not an
    edge case here -- it is how `1200-GET-INTEREST-RATE` retries under `'DEFAULT'`, which is finding
    F4's "business logic living in wiring" arriving as a fact a renderer can carry.
    """

    key_field: str
    source_field: str | None = None
    literal: str | None = None
    #: True for an assignment to a key field that was already assigned earlier in the program -- the
    #: retry path rather than the first attempt. Derived from source order, which is the only thing
    #: that distinguishes them.
    is_fallback: bool = False
    #: Where this key field sits in the looked-up file's own record, from the program's `FD`
    #: declaration. Carried rather than left to be matched by name: `FD-ACCT-ID` and the entity's
    #: `ACCT-ID` line up because `READ ... INTO` copies the same bytes, and inferring that from the
    #: `FD-` prefix would be a naming convention standing in for a fact. `None` when the `FD` record
    #: could not be sized, which makes the lookup unrenderable rather than approximate.
    key_offset: int | None = None
    key_width: int | None = None
    source_line: int


class FileAccessPath(BaseModel):
    """How one program reaches one domain entity's data -- the fact G31 found missing.

    **Why this exists.** ADR-0030 established that a reader cannot be rendered from the design as it
    stood: `CompositeType` declares that `TranCatBalWithRate` is composed of four entities, and says
    nothing about which of them is a stream, which are keyed lookups, or what the keys are. The
    COBOL says all of it -- `FILE-CONTROL` for the declaration, `READ ... INTO` for which record a
    file yields -- and `parsing/file_control.py` reads both. This is where that lands so a renderer
    can use it.

    **Per program, not per entity, and that is measured rather than stylistic.** `TCATBAL` is
    `ACCESS MODE IS SEQUENTIAL` in `CBACT04C` and `RANDOM` in `CBTRN02C`; both are true, and an
    access path recorded on `DomainEntity` would have to pick one and be wrong for the other.

    **`effective_key` is the one derived field, and it is derived because the two sources disagree
    on purpose.** `CBACT04C` declares `XREF-FILE` with `RECORD KEY IS FD-XREF-CARD-NUM` and reads it
    `KEY IS FD-XREF-ACCT-ID` -- the alternate. The declaration says which keys the file supports; the
    read says which one this program positions on. A renderer that took the declared key would
    compile and find nothing, so the field that answers *"what do I look this up by"* is the read's
    key when it names one and the declared record key otherwise.
    """

    program_name: str
    #: The domain entity this file yields, derived from the `READ ... INTO` record name by the same
    #: mechanical transform `build_domain_entities` applies -- never a business rename (ADR-0010).
    entity_name: str
    record_name: str
    select_name: str
    #: The external name in `ASSIGN TO`, which is the DD/environment name rather than a path.
    assign_to: str
    organization: str
    access_mode: str
    #: What to look this entity up by. `None` for a stream the program walks in order.
    effective_key: str | None = None
    #: What the file *declares*, kept beside `effective_key` so a disagreement between them stays
    #: visible instead of being flattened away.
    declared_record_key: str | None = None
    alternate_record_keys: list[str] = []
    #: True when the program positions by key rather than walking the file -- `ACCESS MODE`'s own
    #: meaning, and the "one driving stream, N keyed lookups" split a reader is rendered from.
    is_keyed_lookup: bool = False
    #: The entity this file is *written* from, when the program writes it (`WRITE ... FROM`).
    #: Separate from `entity_name`, which is the read side: `CBACT04C` reads `ACCOUNT-FILE` and
    #: rewrites it, and a single field would have to lose one of those. Empty when the program never
    #: writes this file.
    written_entity_name: str = ""
    #: How the program writes this file, derived from **every** `WRITE`/`REWRITE ... FROM` that
    #: names it rather than from the first one found. `None` when the program never writes it.
    #:
    #: A renderer that treated `WRITE` and `REWRITE` alike would turn an update of fifty accounts
    #: into fifty new ones, which no comparison of the *records* would catch, only one of the file's
    #: length. **`upsert` exists because a file can be written both ways and one program does it**:
    #: `CBTRN02C` `WRITE`s a `TCATBAL` row when its lookup finds none and `REWRITE`s it when it does
    #: (lines 510 and 528), creating 44 rows on top of the 50 it loads. Reducing that to the first
    #: binding said `append`, which over the same input leaves 144 rows where the program leaves 94
    #: -- every record individually correct, and only the count wrong.
    write_mode: WriteMode | None = None
    #: Provenance for the write side: every `WRITE`/`REWRITE` line that names this file, in source
    #: order. A list rather than one line because `upsert` is two statements, and citing only the
    #: first would attribute a create-or-update to the create.
    write_lines: list[int] = []
    #: What this lookup is looked up *by*, in key order. Empty for a driving stream, and empty for
    #: a keyed file whose key nothing fills -- which is a finding rather than a default, since a
    #: lookup with no source cannot be rendered.
    key_parts: list[LookupKeyPart] = []
    #: Provenance, as `CLAUDE.md` requires: the `SELECT` line, and the `READ` line when one bound
    #: this file to a record.
    select_line: int
    read_line: int | None = None


class UnifiedDesign(BaseModel):
    """`design.json`'s `unified_design` -- ADR-0010's real shape for what ADR-0008 left untyped.

    `domain_entities` is deterministic (see `nodes/solution_architect.build_domain_entities`);
    `batch_jobs` and `rest_endpoints` are LLM-authored, informed by but never re-deriving the
    entity data.
    """

    domain_entities: list[DomainEntity]
    #: How each program reaches its data (G31, ADR-0030). Deterministic, parsed from
    #: `FILE-CONTROL` and `READ ... INTO` -- never model-authored, for the reason ADR-0030
    #: refused an LLM-declared join: a wrong join produces plausible rows and a silently wrong
    #: comparison. Defaults to empty so a design produced before schema 3.2.0 still validates;
    #: the producer always fills it, so there is no silence to distinguish here (unlike
    #: ADR-0022's `guard_condition`, which is LLM judgment and required-but-nullable).
    file_access_paths: list[FileAccessPath] = []
    batch_jobs: list[BatchJobDesign]
    rest_endpoints: list[RestEndpointDesign]
    #: Target-side types composed of domain entities (ADR-0020). Defaults to empty because a design
    #: whose steps all operate on plain entities needs none.
    composite_types: list[CompositeType] = Field(default_factory=list)

    def resolve_type(self, name: str) -> DomainEntity | CompositeType | None:
        """The entity or composite `name` refers to, or `None` when it refers to neither.

        `None` is what `generate` turns into a blocked step. A type name that resolves to nothing is
        a design that cannot be generated from, and saying so is more useful than rendering Java
        against a class that will not exist.
        """
        for entity in self.domain_entities:
            if entity.name == name:
                return entity
        for composite in self.composite_types:
            if composite.name == name:
                return composite
        return None

    def unresolvable_type_names(self) -> list[str]:
        """Every type name in this design that resolves to nothing, in declaration order.

        Checked where the design is *produced* as well as where it is consumed (ADR-0020 decision 5):
        a design that cannot be generated from should fail before a human approves it at the gate,
        not after.
        """
        missing: list[str] = []
        for composite in self.composite_types:
            for component in composite.components:
                if self.resolve_type(component.entity_name) is None:
                    missing.append(component.entity_name)
        for job in self.batch_jobs:
            for step in job.steps:
                for name in (step.input_type, step.output_type):
                    if self.resolve_type(name) is None:
                        missing.append(name)
        return missing

#: design.json's own envelope version -- bump this on any breaking change to DesignDocument's
#: shape, e.g. once solution_architect gives `unified_design` a real type.
SCHEMA_VERSION = "3.9.0"  # 3.9.0: BatchStepDesign.optional_lookups (ADR-0042)
#: 3.8.0 added BatchStepDesign.reads_own_writes (ADR-0040).
#: 3.7.0 added FileAccessPath.write_mode/write_lines -- the upsert mode (ADR-0037).
#: 3.6.0 added BatchStepDesign.control_break (G31, ADR-0032).
#: 3.5.0 added the FileAccessPath write side -- WRITE ... FROM (G31).
#: 3.4.0 added FileAccessPath.key_parts, the join predicate (G31).
#: 3.3.0 added DomainField.byte_offset and DomainEntity.record_length (G31 finding F1).
#: 3.2.0 added UnifiedDesign.file_access_paths (G31, ADR-0030).

#: A rule_confidence entry scoring below this becomes a `low_confidence_rule` GateItem. See the
#: module docstring -- a tentative default, not a benchmarked number.
LOW_CONFIDENCE_THRESHOLD = 0.7

GateItemCategory = Literal[
    "unsupported_construct", "fidelity_issue", "low_confidence_rule", "injection_flag"
]


class GateItem(BaseModel):
    """One fact a human (via control-plane's gate) should look at before approving `design.json`.

    `summary` is a short, skimmable one-liner; `detail` carries the full context (the original
    exception message, fidelity-check finding, rationale, or matched text) -- a reviewer scanning
    `gate_items` reads summaries first, then opens `detail` on whichever ones warrant it.
    """

    category: GateItemCategory
    program_name: str
    summary: str
    detail: str


class ProgramDesignEntry(BaseModel):
    """One program's full `spec_extractor` + `spec_critic` output, as it goes into `design.json`."""

    program_name: str
    spec_extraction: SpecExtractionResult
    critique: SpecCritiqueResult


class RunCost(BaseModel):
    """What one `design` invocation actually consumed, for the reviewing gate to see (ADR-0018).

    Actuals, summed from real `ModelCallResult`s -- deliberately not
    `RoutingDecision.estimated_cost_usd`, which is what *selection predicted* from a measured
    token profile before any call ran. Both are worth having and the gap between them is worth
    surfacing, but conflating them in one field would make the number unfalsifiable. Estimated
    cost is not carried here yet; that is a follow-up, named rather than half-built.

    `notional_cost_usd` is `None` when no backend reported a cost, and is a **partial** sum when
    `calls_without_reported_cost > 0` -- read the two together or not at all. The SDK backend
    reports none by design (`core/model_client.py` keeps no rate card, so it cannot go stale), so
    on that backend the token counts are the real signal and the dollar figure is absent rather
    than wrong. On a subscription the CLI's own `total_cost_usd` is notional in the first place:
    it is what the call *would* cost at API rates, not what anyone was billed.
    """

    model_calls: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    notional_cost_usd: float | None = None
    calls_without_reported_cost: int = 0


class DesignDocument(BaseModel):
    """The `design.json` contract: everything control-plane's gate needs to review one `design`
    invocation across every program it covered.

    Build this via `build_design_document`, not by hand, so `gate_items` can never go stale
    relative to `programs`. `unified_design` is `None` until `nodes/solution_architect.py` has
    actually run -- it's a real, typed `UnifiedDesign` (ADR-0010), not a placeholder dict.

    `cost` is `None` only when the document was built outside a `model_client.collect_usage`
    scope -- i.e. by a test constructing one directly. A real `run_design` always populates it.
    """

    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    programs: list[ProgramDesignEntry]
    gate_items: list[GateItem]
    unified_design: UnifiedDesign | None = None
    cost: RunCost | None = None


class DesignCliResult(BaseModel):
    """The `cobol-modernizer design --json` stdout contract -- a summary, not the full `design.json`.

    Per ADR-0008 decision 3: `status` reports only whether this invocation itself succeeded: it is
    deliberately not `"gate_required"` or similar -- whether `gate_item_count > 0` should pause
    anything is control-plane's gate policy to decide, not a judgment this repo's CLI bakes in.

    `run_id` echoes the correlation id this invocation logged under (ADR-0012). Control-plane may
    supply its own via `--run-id`, in which case this is that value verbatim and its audit-log
    entry and this CLI's stderr lines share one identifier; otherwise the CLI generates one and
    this field is how the caller learns it. Echoing it back matters most in the `status="error"`
    case, which is exactly when someone needs to find the right stderr lines.
    """

    status: Literal["ok", "error"]
    phase: Literal["design"] = "design"
    run_id: str
    programs: list[str]
    output_path: str
    gate_item_count: int
    detail: str


class GenerateCliResult(BaseModel):
    """The `cobol-modernizer generate --json` stdout contract.

    Deliberately minimal -- matches the current `cli.py` stub's shape. Self-healing-loop-specific
    fields (attempt count, compile diagnosis) are Milestone C4 work, once `build_validator` exists
    to define them; inventing them now would be the same speculative-design mistake
    `DesignDocument.unified_design` avoids.

    `run_id` carries the same meaning as `DesignCliResult.run_id` and was added for the same
    reason, late: until this field existed, `--run-id` was accepted by `design` and **not** by
    `generate`, so the half of the pipeline that runs the compile loop and the second model stage
    could not be correlated with control-plane's audit chain at all. That silently violated the
    specialist contract's rule that a specialist accepts `--run-id` to join the chain -- on the
    noisier half. Both phases now echo it.
    """

    status: Literal["ok", "error"]
    phase: Literal["generate"] = "generate"
    run_id: str
    output_path: str
    detail: str
    #: Processor steps this run attempted. Zero means the design yielded nothing generable, which
    #: is reported as an error rather than a vacuous success -- see `GenerateOutcome.succeeded`.
    steps_total: int = 0
    #: Steps whose file compiled as part of the target project.
    steps_compiled: int = 0
    #: Steps stopped without spending the attempt budget: a design defect, an error in rendered
    #: scaffolding, or a build failure with no located diagnostic. **Not** the same as exhausted:
    #: these were never worth retrying, and conflating them would hide the difference between "the
    #: model could not fix it" and "no model could".
    steps_blocked: int = 0
    #: Steps that spent every heal attempt and still did not compile.
    steps_exhausted: int = 0
    #: Steps present in the design that this pipeline does not render at all -- readers, writers
    #: and tasklets (G27). **Not a failure, and not nothing.** A writer is usually Spring Batch
    #: wiring; `CBACT04C`'s `1050-UPDATE-ACCOUNT` is a control-break balance update that cannot be
    #: an ItemProcessor. Both arrive here as `role != "processor"`, so the count is surfaced and a
    #: human at the gate decides which this was. Until this field existed the step reached no
    #: outcome and no count, and a job of one processor plus one writer reported a clean success.
    steps_not_generated: int = 0


def build_gate_items(programs: list[ProgramDesignEntry]) -> list[GateItem]:
    """Deterministically consolidate every gate-worthy fact across `programs` into one list.

    Four sources, per program, in this order: `unsupported_fields` (every `REDEFINES`/unresolvable
    `PIC` clause), `critique.fidelity_issues` (every mechanically-proven narration defect --
    surfaced individually even though ADR-0007 already forces `overall_confidence` to `0.0` for
    these, since a reviewer needs to know *what* is wrong, not just that something is),
    `critique.rule_confidence` entries scoring below `LOW_CONFIDENCE_THRESHOLD`, and
    `spec_extraction.injection_flags`. Never raises, never filters based on severity -- every fact
    is surfaced; weighing them is the reviewer's job, not this function's (see module docstring).
    """
    items: list[GateItem] = []

    for entry in programs:
        program_name = entry.program_name

        for field in entry.spec_extraction.unsupported_fields:
            items.append(
                GateItem(
                    category="unsupported_construct",
                    program_name=program_name,
                    summary=f"Unsupported construct on field {field.field_name or '(unnamed)'!r}",
                    detail=field.reason,
                )
            )

        for issue in entry.critique.fidelity_issues:
            items.append(
                GateItem(
                    category="fidelity_issue",
                    program_name=program_name,
                    summary="Narration fidelity issue",
                    detail=issue,
                )
            )

        for rule in entry.critique.rule_confidence:
            if rule.confidence < LOW_CONFIDENCE_THRESHOLD:
                items.append(
                    GateItem(
                        category="low_confidence_rule",
                        program_name=program_name,
                        summary=f"Low-confidence rule ({rule.confidence:.2f}): {rule.rule}",
                        detail=rule.rationale,
                    )
                )

        for flag in entry.spec_extraction.injection_flags:
            items.append(
                GateItem(
                    category="injection_flag",
                    program_name=program_name,
                    summary=f"Injection-phrase heuristic matched: {flag.pattern}",
                    detail=f"line {flag.line_number}: {flag.matched_text!r}",
                )
            )

    return items


def build_design_document(
    programs: list[ProgramDesignEntry],
    unified_design: UnifiedDesign | None = None,
    cost: RunCost | None = None,
    design_gate_items: Sequence[GateItem] = (),
) -> DesignDocument:
    """Build a `DesignDocument`, deriving `gate_items` from `programs` so the two can't drift apart.

    `design_gate_items` carries facts that are properties of the *design* rather than of any
    program's extraction -- today, output types whose data the step cannot reach (G26). They are
    passed in rather than derived here because deriving them needs the tenant's COBOL source, which
    this module deliberately does not read.
    """
    return DesignDocument(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        programs=programs,
        gate_items=[*build_gate_items(programs), *design_gate_items],
        unified_design=unified_design,
        cost=cost,
    )
