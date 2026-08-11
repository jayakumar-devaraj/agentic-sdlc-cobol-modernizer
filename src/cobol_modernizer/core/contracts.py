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


class DomainEntity(BaseModel):
    """One unified domain entity -- every program that `COPY`s the same copybook shares one.

    `source_copybook` is the exact copybook name that produced this entity; two different
    copybook names are never merged into one entity, even when structurally similar (ADR-0010
    decision 1) -- `source_copybook` is what makes that traceable and checkable.
    """

    name: str
    source_copybook: str
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


class BatchJobDesign(BaseModel):
    """One program's Spring Batch job design -- one per Track C program, LLM-authored."""

    program_name: str
    job_name: str
    domain_entities: list[str]
    steps: list[BatchStepDesign]


class RestEndpointDesign(BaseModel):
    """One thin-REST-layer endpoint, LLM-authored -- control/observability or a point query.

    Per ADR-0009, this layer triggers/monitors Spring Batch jobs and serves genuine point queries
    against `domain_entities` -- never the bulk batch logic itself.
    """

    method: RestMethod
    path: str
    domain_entity: str
    description: str


class UnifiedDesign(BaseModel):
    """`design.json`'s `unified_design` -- ADR-0010's real shape for what ADR-0008 left untyped.

    `domain_entities` is deterministic (see `nodes/solution_architect.build_domain_entities`);
    `batch_jobs` and `rest_endpoints` are LLM-authored, informed by but never re-deriving the
    entity data.
    """

    domain_entities: list[DomainEntity]
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
SCHEMA_VERSION = "3.0.0"

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
