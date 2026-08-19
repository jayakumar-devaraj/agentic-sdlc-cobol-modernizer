"""The `design` phase as a real LangGraph run: extract and critique per program, then architect once.

ADR-0001 decided this pipeline is "a bounded, in-process LangGraph sub-graph with an in-memory
checkpointer... five narrow specialist nodes under one supervisor". Until now that was a decision
with no code behind it -- `langgraph` was a declared dependency this repo never imported, and the
three `design`-phase nodes existed only as functions nothing composed. This module is where that
decision becomes real for the `design` half; `generate`'s two nodes (and the self-healing compile
cycle, which is the case where LangGraph's conditional edges genuinely earn their keep) are
Milestone C4.

The shape, and why (see ADR-0012 for the full reasoning):

- **Per-program work fans out and runs concurrently.** `spec_extractor` and `spec_critic` are
  per-program and independent across programs -- `CBCUS01C` and `CBACT01C` share no copybook at
  all. Fan-out is dynamic (`Send`), driven by whatever `--programs` the caller passed, not a
  hardcoded branch per known program name.
- **`solution_architect` runs once, after every branch joins**, because its entire job is looking
  across all programs at once (ADR-0010).
- **The per-program branch is its own compiled sub-graph with two nodes**, not one node doing both
  jobs, so `spec_extractor` and `spec_critic` stay the separately-named, separately-traceable
  specialists ADR-0001 describes.

Two properties worth stating because they are easy to get wrong and neither is free:

1. **`program_entries` is explicitly re-ordered to the caller's requested program order**, even
   though measurement says it does not currently need to be. The expectation going in was that
   concurrent branches would fan in by completion order; they do not. LangGraph applies a
   reducer's writes in task-creation (`Send`) order, so the result is already deterministic --
   verified directly, including with randomized per-branch delays across repeated runs, not
   assumed from docs. The normalization stays anyway: `design.json`'s ordering is an output
   contract (two identical runs must produce byte-identical files, or the provenance and diffing
   story `CLAUDE.md` asks for quietly dies), and resting a contract on an internal scheduling
   detail of a dependency means a future LangGraph upgrade could change it with nothing failing
   loudly. Two cheap lines make the guarantee ours. See ADR-0012, and
   `test_design_graph.py`'s own note that this makes the ordering test pass either way.
2. **A failing program fails the whole invocation**, deliberately. See `run_design`.

The model-calling functions are injected the same way each node already injects its own, so this
module's own tests exercise the real graph, real parallelism, and real state plumbing without a
live API credential -- which this development environment does not have. See
`docs/qa/verification-report.md` for exactly what that leaves unverified.
"""

from __future__ import annotations

import logging
import operator
import os
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from cobol_modernizer.core.contracts import (
    DesignDocument,
    GateItem,
    ProgramDesignEntry,
    RunCost,
    UnifiedDesign,
    build_design_document,
)
from cobol_modernizer.core.model_client import RunBudget, UsageAccumulator, collect_usage
from cobol_modernizer.nodes.solution_architect import (
    ArchitectFn,
    design_solution,
    unobtainable_inputs,
    unreachable_entities,
)
from cobol_modernizer.nodes.spec_critic import CritiqueFn, critique_spec
from cobol_modernizer.nodes.spec_extractor import NarrateFn, SpecExtractionResult, extract_spec
from cobol_modernizer.tools.tenant_repo import resolve_program

logger = logging.getLogger(__name__)

#: Upper bound on program branches running at once. Fan-out was previously unbounded: one thread
#: and two model calls per `--programs` entry, with nothing stopping a 200-program invocation from
#: opening 400 concurrent calls and being rate-limited into a retry storm. The cap is the real
#: backpressure mechanism (plan pillar 25); `core/model_client.py`'s jittered backoff handles what
#: still gets throttled. Four is a deliberate starting point, not a benchmarked number -- it
#: matches Track C's own program count, so today's full run is unthrottled while a larger one is
#: bounded. Raise it once a real run shows headroom.
MAX_CONCURRENT_PROGRAMS = int(os.getenv("COBOL_MODERNIZER_MAX_CONCURRENCY", "4"))


def _optional(name: str, value: object | None) -> dict:
    """`{name: value}` when a callable was injected, `{}` when it wasn't.

    Each node already owns its own live-Anthropic default (`_default_narrate` and friends, private
    to their modules). Rather than importing those privates here just to re-supply them as
    defaults -- or making them public, which this repo only does when a second caller genuinely
    needs the value itself (`render_known_facts`) -- an un-injected callable is simply not passed,
    and the node applies its own default. One place decides what the default call is: the node.
    """
    return {} if value is None else {name: value}


class ProgramBranchState(TypedDict, total=False):
    """One program's branch state. `program_entries` is the *only* key shared with `DesignState`.

    LangGraph propagates a sub-graph's output to its parent by matching state keys, so
    `program_entries` carries the same `operator.add` reducer in both schemas -- that is what makes
    every branch's single-element list accumulate into one parent list instead of overwriting.

    Every other key here is deliberately named so it does **not** collide with the parent's, and
    `branch_worktree_root` is the one that looks like it should just be `worktree_root`. It can't
    be. A sub-graph returns its whole state to the parent, so a shared key that is not reducer-
    backed gets written once per concurrent branch within a single superstep, and LangGraph rejects
    that: `InvalidUpdateError: At key 'worktree_root': Can receive only one value per step`. Every
    branch would be writing the identical value, which makes the failure look absurd until you
    notice the channel has no way to know that. Found by running the graph, not by reading docs.
    """

    branch_worktree_root: str
    program_name: str
    extraction: SpecExtractionResult
    program_entries: Annotated[list[ProgramDesignEntry], operator.add]


class DesignState(TypedDict, total=False):
    """The whole `design` run's state."""

    worktree_root: str
    program_names: list[str]
    program_entries: Annotated[list[ProgramDesignEntry], operator.add]
    unified_design: UnifiedDesign


def build_design_graph(
    *,
    narrate: NarrateFn | None = None,
    critique: CritiqueFn | None = None,
    architect: ArchitectFn | None = None,
    model_routing_config: Path | None = None,
):
    """Compile the `design`-phase graph, with the three model calls injected.

    A factory closing over the callables rather than carrying them in the graph state: LangGraph
    state is meant to be serializable data (and is, here -- every value is a `str`, a `list`, or a
    Pydantic model), and stuffing function objects into it would break that for no benefit, since
    nothing in the graph needs to *change* which callable it uses mid-run.

    Args:
        narrate/critique/architect: Override the live Anthropic calls -- tests only; real callers
            leave these unset, and each node then applies its own default (see `_optional`).
        model_routing_config: Overrides `core/model_routing.py`'s default config path -- tests only.

    Returns:
        A compiled graph. No checkpointer is attached: ADR-0001 is explicit that this pipeline is
        bounded and non-durable, and control-plane recovers a crashed invocation by re-invoking the
        CLI, not by resuming mid-pipeline.
    """

    def run_spec_extractor(state: ProgramBranchState) -> dict:
        program_name = state["program_name"]
        logger.info("spec_extractor: start program=%s", program_name)
        extraction = extract_spec(
            Path(state["branch_worktree_root"]),
            program_name,
            model_routing_config=model_routing_config,
            **_optional("narrate", narrate),
        )
        logger.info(
            "spec_extractor: done program=%s fields=%d unsupported=%d paragraphs=%d",
            program_name,
            len(extraction.field_mappings),
            len(extraction.unsupported_fields),
            len(extraction.paragraph_names),
        )
        return {"extraction": extraction}

    def run_spec_critic(state: ProgramBranchState) -> dict:
        program_name = state["program_name"]
        extraction = state["extraction"]
        logger.info("spec_critic: start program=%s", program_name)
        critique_result = critique_spec(
            Path(state["branch_worktree_root"]),
            extraction,
            model_routing_config=model_routing_config,
            **_optional("critique", critique),
        )
        logger.info(
            "spec_critic: done program=%s confidence=%.2f fidelity_issues=%d",
            program_name,
            critique_result.overall_confidence,
            len(critique_result.fidelity_issues),
        )
        # The branch's single contribution to the shared list -- see ProgramBranchState.
        return {
            "program_entries": [
                ProgramDesignEntry(
                    program_name=program_name,
                    spec_extraction=extraction,
                    critique=critique_result,
                )
            ]
        }

    branch = StateGraph(ProgramBranchState)
    branch.add_node("spec_extractor", run_spec_extractor)
    branch.add_node("spec_critic", run_spec_critic)
    branch.add_edge(START, "spec_extractor")
    branch.add_edge("spec_extractor", "spec_critic")
    branch.add_edge("spec_critic", END)
    branch_app = branch.compile()

    def fan_out_per_program(state: DesignState) -> list[Send]:
        """One concurrent branch per requested program -- the supervisor's only routing decision."""
        program_names = state["program_names"]
        logger.info("design graph: fanning out %d program branch(es)", len(program_names))
        return [
            Send(
                "program_branch",
                {"branch_worktree_root": state["worktree_root"], "program_name": name},
            )
            for name in program_names
        ]

    def run_solution_architect(state: DesignState) -> dict:
        entries = state["program_entries"]
        logger.info("solution_architect: start programs=%d", len(entries))
        unified = design_solution(
            Path(state["worktree_root"]),
            entries,
            model_routing_config=model_routing_config,
            **_optional("architect", architect),
        )
        logger.info(
            "solution_architect: done entities=%d batch_jobs=%d rest_endpoints=%d",
            len(unified.domain_entities),
            len(unified.batch_jobs),
            len(unified.rest_endpoints),
        )
        return {"unified_design": unified}

    graph = StateGraph(DesignState)
    graph.add_node("program_branch", branch_app)
    graph.add_node("solution_architect", run_solution_architect)
    graph.add_conditional_edges(START, fan_out_per_program, ["program_branch"])
    graph.add_edge("program_branch", "solution_architect")
    graph.add_edge("solution_architect", END)
    return graph.compile()


def run_design(
    worktree_root: Path,
    program_names: list[str],
    *,
    narrate: NarrateFn | None = None,
    critique: CritiqueFn | None = None,
    architect: ArchitectFn | None = None,
    model_routing_config: Path | None = None,
    budget: RunBudget | None = None,
) -> DesignDocument:
    """Run the whole `design` phase and return the `design.json` contract object.

    Every exception each node documents propagates unchanged: a missing program source, a
    `COPY ... REPLACING`, a forged prompt delimiter, a malformed model response. **One program
    failing fails the entire invocation**, rather than producing a partial `design.json` covering
    the programs that happened to succeed. That follows this repo's established
    fail-loudly-on-the-unambiguous posture, and it matters more here than usual: `gate_items` is
    the payload control-plane's human gate reviews, and a reviewer given a document that silently
    covers three of four requested programs has no way to tell that from a complete one. Note the
    contrast with per-*field* handling, which is the opposite on purpose (ADR-0006): an
    unmappable field is isolated so the other 92 still get narrated. A whole missing program is not
    ambiguous the way one `REDEFINES` field is.

    Returns:
        A `DesignDocument` whose `programs` are ordered to match `program_names` -- an explicit
        guarantee of this function, not an inherited property of LangGraph's fan-in. See the
        module docstring for what was measured.
    """
    app = build_design_graph(
        narrate=narrate,
        critique=critique,
        architect=architect,
        model_routing_config=model_routing_config,
    )
    # The accumulator is bound *before* invoke so every branch thread inherits it in its copied
    # context (ADR-0018). Binding it inside a node would be too late for the branches already
    # running, and would give each its own instance.
    with collect_usage(budget if budget is not None else RunBudget()) as usage:
        try:
            final_state = app.invoke(
                {
                    "worktree_root": str(worktree_root),
                    "program_names": list(program_names),
                    "program_entries": [],
                },
                config={"max_concurrency": MAX_CONCURRENT_PROGRAMS},
            )
        finally:
            # In a `finally` because a failed run has still spent money: a run that dies on its
            # fourth program has already paid for the first three, and the failure path is
            # exactly when someone asks what it cost. No `design.json` is written on that path
            # and `DesignCliResult` carries no cost field (ADR-0008), so this stderr line is the
            # only record that survives.
            cost = _summarize_cost(usage)
            _log_run_cost(cost)

    entries_by_name = {entry.program_name: entry for entry in final_state["program_entries"]}
    ordered_entries = [entries_by_name[name] for name in program_names]

    return build_design_document(
        ordered_entries,
        unified_design=final_state["unified_design"],
        cost=cost,
        design_gate_items=unpopulatable_gate_items(
            worktree_root, ordered_entries, final_state["unified_design"]
        ),
    )



def unpopulatable_gate_items(
    worktree_root: Path,
    entries: list[ProgramDesignEntry],
    unified_design: UnifiedDesign | None,
) -> list[GateItem]:
    """One gate item per step whose COBOL reads data its declared types cannot reach (G26).

    ADR-0020 checks a step's types **resolve**; this reports whether they are **populatable**. The
    two failed apart once already: every type name real, and a model unable to reach `ACCT-ID` or
    `XREF-CARD-NUM` for the record it was asked to build. It left them null and said so, which is
    the only reason anyone found out.

    A fact, not a refusal. A referenced entity may be legitimately absent -- mentioned in a
    DISPLAY, or read by a paragraph whose logic belongs to a different step -- so this surfaces the
    finding and lets the reviewer weigh it, per the specialist contract's rule 5.
    """
    if unified_design is None:
        return []

    items: list[GateItem] = []
    for entry in entries:
        source_text = resolve_program(worktree_root, entry.program_name).source_text
        for job in unified_design.batch_jobs:
            if job.program_name != entry.program_name:
                continue
            # Every paragraph any step of this job claims. A step is answerable for what it
            # reads itself, not for what it hands to a sibling step (G26's split).
            owned = frozenset(
                paragraph for other in job.steps for paragraph in other.source_paragraphs
            )
            for step in job.steps:
                missing = unreachable_entities(
                    step,
                    source_text=source_text,
                    entities=unified_design.domain_entities,
                    composites=unified_design.composite_types,
                    owned_elsewhere=owned,
                )
                unobtainable = unobtainable_inputs(job, step, unified_design)
                if unobtainable:
                    items.append(
                        GateItem(
                            category="fidelity_issue",
                            program_name=entry.program_name,
                            summary=(
                                f"step {step.step_name!r} consumes "
                                f"{', '.join(unobtainable)}, which nothing supplies"
                            ),
                            detail=(
                                f"The step declares input {step.input_type!r}, which carries "
                                f"{', '.join(unobtainable)}. No earlier step of job "
                                f"{job.job_name!r} outputs those, and {entry.program_name}'s "
                                "FILE-CONTROL declares no file that is READ INTO them -- so a "
                                "rendered reader would have nowhere to get them (G31). Either the "
                                "step order is wrong, the composite carries an entity it does not "
                                "need, or this program genuinely does not read that data."
                            ),
                        )
                    )
                if not missing:
                    continue
                items.append(
                    GateItem(
                        category="fidelity_issue",
                        program_name=entry.program_name,
                        summary=(
                            f"step {step.step_name!r} reads {', '.join(missing)}, which "
                            f"{step.input_type}/{step.output_type} cannot reach"
                        ),
                        detail=(
                            f"Paragraph(s) {', '.join(step.source_paragraphs) or '(none)'} -- and "
                            f"anything they PERFORM -- reference fields of {', '.join(missing)}. "
                            f"The step declares input {step.input_type!r} and output "
                            f"{step.output_type!r}, and neither reaches those entities, so a "
                            f"generator has no way to read or populate them. Either widen the "
                            f"composite, or split the paragraph's work into a step that can reach "
                            f"them. Confirm before approving: a reference may be incidental."
                        ),
                    )
                )
    return items

def _summarize_cost(usage: UsageAccumulator) -> RunCost:
    """Snapshot the accumulator into the immutable contract type."""
    return RunCost(
        model_calls=usage.model_calls,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        notional_cost_usd=usage.notional_cost_usd,
        calls_without_reported_cost=usage.calls_without_reported_cost,
    )


def _log_run_cost(cost: RunCost) -> None:
    logger.info(
        "design run cost: model_calls=%d input_tokens=%d output_tokens=%d "
        "cache_creation=%d cache_read=%d notional_cost_usd=%s calls_without_cost=%d",
        cost.model_calls,
        cost.input_tokens,
        cost.output_tokens,
        cost.cache_creation_input_tokens,
        cost.cache_read_input_tokens,
        cost.notional_cost_usd,
        cost.calls_without_reported_cost,
    )
