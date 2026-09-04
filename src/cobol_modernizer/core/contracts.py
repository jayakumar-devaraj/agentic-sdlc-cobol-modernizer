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

from pydantic import BaseModel, Field, field_validator

from cobol_modernizer.core.java_lexicon import JAVA_IDENTIFIER, why_java_rejects
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

    #: **Must be a legal Java identifier, and it is checked here** (gap G22). Three renderers derive
    #: a class name from it, and the simplest of them just capitalises the first character
    #: (`postAccountInterest` -> `PostAccountInterestItemReader`), so anything Java would reject as
    #: an identifier is rejected as a step name too. A COBOL-style `1300-COMPUTE-INTEREST` yields a
    #: class starting with a digit.
    #:
    #: **Checked on the contract rather than in the renderer, because of *when* the two fail.** The
    #: renderer's own guard fires at `generate` time - *after* a human approved the design at the
    #: gate - so it spends a review and then throws the result away. This is the same principle
    #: ADR-0020 decision 5 already applies to `input_type`/`output_type`: a design that cannot be
    #: generated from should fail before a human approves it, not three layers down afterwards.
    #: `step_name` was simply missed when that rule was written.
    #: `json_schema_extra` rather than `Field(pattern=...)`: a pattern would be enforced by pydantic
    #: *before* the validator below and would report itself as "String should match pattern
    #: '^[A-Za-z_$]...'", which reaches a model as repair instructions and tells it nothing about
    #: what to write. This documents the rule for anyone reading the published schema while leaving
    #: the message that has to be actionable where it can say something useful. The schema cannot
    #: express the reserved-word half at all, so `description` states it in words.
    step_name: str = Field(
        json_schema_extra={
            "pattern": JAVA_IDENTIFIER.pattern,
            "description": (
                "A camelCase Java identifier, e.g. 'computeMonthlyInterest'. A class name is "
                "derived from this directly, so it must also not be a Java reserved word."
            ),
        }
    )
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
    #: decision for item *n* reads what items *1..n-1* wrote. Judged independently, **25 of that
    #: program's 38 rejections disappear**: a stateless implementation writes 287 records where
    #: the program writes 262, every one of them individually correct, so only a count sees it.
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


    @field_validator("step_name")
    @classmethod
    def _step_name_must_be_renderable(cls, value: str) -> str:
        """Reject a name Java cannot take, where the name is produced.

        Reached through `parse_with_repair` on the architect's response, so a model that emits a
        COBOL-style name gets one repair attempt with this message rather than a run that dies in
        `generate` an approval later.
        """
        reason = why_java_rejects(value)
        if reason is not None:
            raise ValueError(
                f"step_name {value!r} {reason}, and a class name is derived from it directly. Use "
                f"a camelCase name such as 'computeMonthlyInterest' - the COBOL paragraph it comes "
                f"from belongs in source_paragraphs, not here."
            )
        return value

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


class ComputedValue(BaseModel):
    """A working-storage value the program computes, with `pic_mapper`'s facts about it.

    **The sixth sighting of the class `CLAUDE.md` names** (G21, G24, G26, G28, G30): a fact the
    deterministic layer already held, dropped one step before its consumer. `pic_mapper` computes
    `WS-MONTHLY-INT` at precision 11, scale 2; `group_field_mappings_by_source` groups it under the
    program; and `build_domain_entities` discards that whole group, because a domain entity is
    copybook-sourced by definition. The number then reached the generated code anyway -- as
    `requireFits(..., 11, 2)` -- because a model read it off the `PIC` clause in narration and said
    plainly that it was inferring. **A `COMPUTE`'s target scale is exactly the kind of number that
    must be computed and handed over rather than narrated**, for the same reason `pic_mapper` may
    not call a model: a wrong scale on a currency field looks exactly like a right one.

    Deterministic throughout, and carrying no Java name on purpose. What this value should be
    *called* in a generated record is judgment, and belongs to the `ComputedComponent` that claims
    it; what it *is* -- type, precision, scale, sign, and which paragraphs produce it -- is read
    from the COBOL and is not the model's to choose.

    `program_name` is required because these are program-scoped, unlike a `DomainEntity`: two
    programs may both declare `WS-TEMP-BAL` with different pictures, and merging them by name would
    be the mistake ADR-0010 decision 1 refuses for copybooks.
    """

    program_name: str
    cobol_field_name: str
    java_type: str
    precision: int | None = None
    scale: int | None = None
    signed: bool = False
    #: The paragraph(s) whose arithmetic writes this field. This is what lets a caller charge the
    #: value to the step that declares that paragraph in `source_paragraphs`, rather than to the
    #: job as a whole -- `WS-MONTHLY-INT` is computed in `1300-COMPUTE-INTEREST` and
    #: `WS-TRANID-SUFFIX` in `1300-B-WRITE-TX`, and those are two different steps.
    computed_in_paragraphs: list[str]
    #: Paragraphs that read this value without computing it. **Empty means the value is a local
    #: intermediate and needs to survive nothing**, which is the distinction that makes a refusal
    #: safe: `CBTRN02C`'s `WS-TEMP-BAL` is computed and then compared against a credit limit in the
    #: same paragraph, and `CBACT04C`'s `WS-TRANID-SUFFIX` is built and consumed inside
    #: `1300-B-WRITE-TX`. Neither has anywhere to go and neither needs one. `WS-MONTHLY-INT`
    #: escapes to `1300-B-WRITE-TX` and `WS-TOTAL-INT` to `1050-UPDATE-ACCOUNT` -- both owned by
    #: other steps, which is exactly why the value has to cross a step boundary to get there.
    escapes_to: list[str] = []
    #: The record field this value is `MOVE`d into, if any -- `WS-MONTHLY-INT` reaches `TRAN-AMT`.
    #: A second, legitimate way for a value to be delivered: if the step's output type already
    #: carries the entity owning this field, the value lands without needing a computed field.
    lands_in_field: str | None = None


class CompositeComponent(BaseModel):
    """One component of a `CompositeType`: a field name, and the entity it holds."""

    field_name: str
    entity_name: str


class ComputedComponent(BaseModel):
    """One computed field of a `CompositeType`: a field name, and the COBOL value it carries.

    The exact shape of `CompositeComponent`, one level down: that names an entity, this names a
    value in `UnifiedDesign.computed_values`. Both leave the *naming* to the model and take
    everything else from the deterministic layer -- `field_name` is judgment (`monthlyInterest`),
    and `cobol_field_name` is a reference that must resolve (`WS-MONTHLY-INT`), which is what
    carries `pic_mapper`'s type, precision and scale into the generated record unchanged.

    Resolved in the context of the job whose step declares the composite, because
    `UnifiedDesign.computed_values` is program-scoped and a composite is not.
    """

    field_name: str
    cobol_field_name: str


class CompositeType(BaseModel):
    """A record composed of existing domain entities, for a value no single copybook describes.

    Introduced by ADR-0020 because a real `solution_architect` run chains processor steps, and the
    values flowing between them are not entities: "a `TranCatBal` with its `Account` and `CardXref`
    resolved" is a type the target genuinely needs and no copybook declares.

    **Every component references something that already exists**, and ADR-0062 widened *what* only
    by one step. `components` reference entities, as they always have. `computed_fields` reference
    entries in `UnifiedDesign.computed_values` -- values the program's own arithmetic writes, which
    `pic_mapper` has already typed and given a precision and scale. So the invariant this class was
    built on still holds where it matters: **a composite never invents a field's precision.** It
    only widened from "a copybook produced this" to "the deterministic layer produced this",
    because the narrower reading is what left `computeMonthlyInterest` with `in = out =
    RatedCategoryBalance` and a monthly interest it computed and discarded.

    Rendered deterministically rather than generated (ADR-0010's line, unmoved): the *shape* is a
    mechanical transform of this declaration, and only the *name* is judgment.
    """

    name: str
    components: list[CompositeComponent]
    #: Values the composite carries that no record holds (ADR-0062). Defaults to empty because most
    #: composites are joins of records and carry none.
    computed_fields: list[ComputedComponent] = Field(default_factory=list)


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
    #: Working-storage values each program computes (ADR-0062). Deterministic, parsed from
    #: arithmetic receiving positions -- never model-authored, for the reason `pic_mapper` may not
    #: call a model. Defaults to empty so a design produced before schema 3.10.0 still validates;
    #: the producer always fills it, so there is no silence to distinguish here.
    computed_values: list[ComputedValue] = Field(default_factory=list)

    def resolve_computed_value(
        self, program_name: str, cobol_field_name: str
    ) -> ComputedValue | None:
        """The computed value `cobol_field_name` names within `program_name`, or `None`.

        Scoped by program deliberately: `WS-TEMP-BAL` in one program is not `WS-TEMP-BAL` in
        another, and resolving across programs would hand a composite the wrong precision for a
        currency field -- silently, and in the direction that still compiles.
        """
        for value in self.computed_values:
            if value.program_name == program_name and value.cobol_field_name == cobol_field_name:
                return value
        return None

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
        missing.extend(self.unresolvable_computed_field_names())
        return missing

    def accumulator_owners(
        self, program_name: str, accumulator_paragraphs: dict[str, str] | None = None
    ) -> dict[str, str]:
        """`{COBOL accumulator field: the step that owns its control break}` for one program.

        **Grain, derived rather than declared** (ADR-0063). A control break's `accumulator_field` is
        group-scoped by the definition of a control break: it is zeroed at the group boundary,
        added to once per row, and read once per group. That makes it a property of the *group*,
        not of the row that feeds it -- and a `BigDecimal` field cannot tell those apart, which is
        how ADR-0062 came to require a per-row item to carry one.

        Nothing new is declared. `attach_control_breaks` already put a `ControlBreakDesign` on the
        step declaring the break's `performed_paragraph`, and that step is exactly the one entitled
        to carry the accumulator -- ADR-0027's already-summed `(account, totalInterest)` item. The
        fact was present and correct and simply never consulted.
        """
        owners: dict[str, str] = {}
        for job in self.batch_jobs:
            if job.program_name != program_name:
                continue

            # **Resolved from `source_paragraphs`, not from an attached `ControlBreakDesign`**, and
            # the difference is not cosmetic. `attach_control_breaks` runs *after*
            # `parse_with_repair`, so at the moment the design is validated no step carries a break
            # yet and reading `step.control_break` returns nothing for every step. A first version
            # did read it; its unit tests passed because they built designs with the break already
            # attached -- the post-attachment state -- and the defect surfaced only when a live run
            # was refused for the value the rule is supposed to excuse.
            for field, paragraph in (accumulator_paragraphs or {}).items():
                for step in job.steps:
                    if paragraph.upper() in {name.upper() for name in step.source_paragraphs}:
                        owners[field.upper()] = step.step_name
                        break

            # Post-attachment the break itself is authoritative, and agrees.
            for step in job.steps:
                if step.control_break is not None:
                    owners[step.control_break.accumulator_field.upper()] = step.step_name
        return owners

    def unresolvable_computed_field_names(self) -> list[str]:
        """Every `ComputedComponent` whose `cobol_field_name` resolves to no computed value.

        Resolved in the context of the job that reaches the composite, because
        `computed_values` is program-scoped and a `CompositeType` is not. A composite no step
        names is checked against every program instead -- it is unreachable and about to be
        reported as such, and refusing it here for the wrong reason would say so misleadingly.
        """
        programs_by_composite: dict[str, set[str]] = {}
        for job in self.batch_jobs:
            for step in job.steps:
                for type_name in (step.input_type, step.output_type):
                    programs_by_composite.setdefault(type_name, set()).add(job.program_name)

        every_program = {value.program_name for value in self.computed_values}

        missing: list[str] = []
        for composite in self.composite_types:
            if not composite.computed_fields:
                continue
            programs = programs_by_composite.get(composite.name) or every_program
            for computed in composite.computed_fields:
                if not any(
                    self.resolve_computed_value(program, computed.cobol_field_name) is not None
                    for program in programs
                ):
                    missing.append(computed.cobol_field_name)
        return missing

#: design.json's own envelope version -- bump this on any breaking change to DesignDocument's
#: shape, e.g. once solution_architect gives `unified_design` a real type.
SCHEMA_VERSION = "3.10.0"  # 3.10.0: UnifiedDesign.computed_values (ADR-0062)
#: 3.9.0 added BatchStepDesign.optional_lookups (ADR-0042).
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


class EquivalenceVerdict(BaseModel):
    """What comparing the generated code's output against COBOL's own output found (ADR-0064).

    **`not_run` is a verdict, not an absence, and that is the whole point.** Two live runs shipped
    wrong money past the release gate -- a processor that computed a value and discarded it, and one
    that set a per-account running total to a single row's amount -- and in both cases the gate said
    *"Generated and compiled N processor step(s)."* That sentence is true, contains no claim about
    correctness, and reads as success. A gate that reports quantities trains the approver to
    approve.

    So this field always exists and always says something. A run that could not execute the
    comparison reports `not_run` **with a reason**, which is a materially different thing for a
    human to weigh than a summary that simply omits the subject.

    `mismatches` is bounded by the caller: a differential that diverges early can produce one entry
    per field per record, and a gate item nobody can read is not evidence either. `excluded_fields`
    is carried because what a differential *does not* compare is exactly what a reviewer needs in
    order to price it -- ADR-0029's exclusions are decisions, and hiding them would make this
    number look stronger than it is.
    """

    status: Literal["matched", "mismatched", "not_run"]
    #: Why, in one line. Required for every status, including `matched` -- the qualifiers on what
    #: was compared belong beside the answer, not in a document a reviewer would have to find.
    reason: str
    records_compared: int = 0
    fields_compared: int = 0
    mismatches: list[str] = Field(default_factory=list)
    #: Fields the comparison deliberately skipped, each traceable to the decision that makes it
    #: unproducible (ADR-0026's `TRAN-ID` and timestamps).
    excluded_fields: list[str] = Field(default_factory=list)


#: What a `generate` run reports when nothing ran the differential. Deliberately the default, so a
#: result cannot be silent on the subject: silence is what let two defects reach a human gate.
NOT_RUN = EquivalenceVerdict(
    status="not_run",
    reason=(
        "no equivalence comparison was run: this phase compiled the generated code and did not "
        "execute it against the COBOL oracle"
    ),
)


class EquivalenceTestVerdict(BaseModel):
    """What the *rendered JUnit equivalence test* did, at unit granularity (ADR-0065).

    **Separate from `EquivalenceVerdict`, and the distinction is the point.** That one is the
    record-level differential: a built-and-run job's output files compared against COBOL's. This one
    is a per-row check of one `COMPUTE`, rendered beside the processor and run by Maven. Folding
    them into one field would let a green unit test read as a passing differential, which claims
    orders of magnitude more than it has evidence for -- the exact overclaim ADR-0064 exists to
    remove, one level down.

    `refused` is the status that carries the most information and is easiest to mistake for an
    error. It means the renderer would not write a test against this design because the step
    computing the value has nowhere declared to put it -- **step 49's defect exactly**, caught before
    any Java is written. A gate must render it as a finding, never as a tooling failure.
    """

    status: Literal["passed", "failed", "refused", "not_rendered"]
    #: Why, in one line, for every status including `passed` -- what a green run does and does not
    #: cover belongs beside the answer. ADR-0065's Consequences table is the long form.
    reason: str
    #: The rendered class, empty unless one was written. Names the file a reviewer opens.
    test_class: str = ""


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
    #: What the differential found (ADR-0064). Defaults to `not_run` rather than being optional, so
    #: every result states something about correctness instead of leaving the subject out -- the
    #: omission is what two live defects passed through.
    #: `deep=True` is load-bearing: a shallow copy shares `mismatches` and `excluded_fields`
    #: with the module-level `NOT_RUN`, so one run appending to its own verdict would mutate
    #: every later run's default. Caught by a test rather than by review.
    equivalence: EquivalenceVerdict = Field(
        default_factory=lambda: NOT_RUN.model_copy(deep=True)
    )
    #: What the rendered JUnit equivalence test did (ADR-0065). Defaults to `not_rendered` for the
    #: same reason the field above defaults to `not_run`: a run that checked nothing must say so
    #: rather than leave the subject out. Narrower than `equivalence` and never a substitute for it.
    equivalence_test: EquivalenceTestVerdict = Field(
        default_factory=lambda: EquivalenceTestVerdict(
            status="not_rendered", reason="no equivalence test was rendered for this design"
        )
    )


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
