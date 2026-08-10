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

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

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

#: design.json's own envelope version -- bump this on any breaking change to DesignDocument's
#: shape, e.g. once solution_architect gives `unified_design` a real type.
SCHEMA_VERSION = "1.0.0"

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
) -> DesignDocument:
    """Build a `DesignDocument`, deriving `gate_items` from `programs` so the two can't drift apart."""
    return DesignDocument(
        schema_version=SCHEMA_VERSION,
        generated_at=datetime.now(UTC),
        programs=programs,
        gate_items=build_gate_items(programs),
        unified_design=unified_design,
        cost=cost,
    )
