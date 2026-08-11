"""Tests for the real `design`-phase LangGraph run, against real fixture source for all four programs.

ADR-0001 said this pipeline is a bounded in-process LangGraph sub-graph; ADR-0012 is where that
became code. What needs verifying here is the wiring, not the nodes -- each node is already
covered exhaustively by its own test module. Specifically: that state flows correctly across a
sub-graph boundary, that the fan-out really is concurrent rather than merely drawn that way, that
concurrency does not leak into the output ordering, and that a failure in one branch fails the run
rather than producing a quietly partial `design.json`.

Every model call is injected, so the graph, the parallelism, and the state plumbing are all real
while the three Anthropic calls are not -- this environment has no credential. See
`docs/qa/verification-report.md`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

import pytest

from cobol_modernizer.core import model_client
from cobol_modernizer.core.contracts import DesignDocument
from cobol_modernizer.core.model_client import ModelCallResult
from cobol_modernizer.graph import design_graph
from cobol_modernizer.graph.design_graph import build_design_graph, run_design
from cobol_modernizer.telemetry.logging_config import bind_run_id, current_run_id
from cobol_modernizer.tools.tenant_repo import TenantRepoFileNotFoundError

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
ALL_PROGRAMS = ["CBACT04C", "CBCUS01C", "CBACT01C", "CBTRN02C"]

# CBCUS01C and CBACT01C share no copybook at all (docs/cobol-construct-support-matrix.md's own
# program inventory) -- the plan's designated parallel-branch pair for this milestone.
PARALLEL_PAIR = ["CBCUS01C", "CBACT01C"]


def faithful_narrate(model: str, system_prompt: str, user_content: str) -> str:
    """Reproduce the real Known Facts block verbatim -- the technique the node tests established.

    Splitting on the first wrapped source block (rather than on a named program) keeps this usable
    for any program, which matters here because these tests run all four at once.
    """
    return user_content.split('\n\n<untrusted-cobol-source')[0]


def confident_critique(model: str, system_prompt: str, user_content: str) -> str:
    return json.dumps([{"rule": "representative rule", "confidence": 0.9, "rationale": "matches source"}])


def make_architect(program_names: list[str]):
    """An architect response covering exactly `program_names`, which the node validates against."""

    def architect(model: str, system_prompt: str, user_content: str) -> str:
        return json.dumps(
            {
                "batch_jobs": [
                    {
                        "program_name": name,
                        "job_name": f"{name.lower()}Job",
                        "domain_entities": [],
                        "steps": [],
                    }
                    for name in program_names
                ],
                "rest_endpoints": [],
            }
        )

    return architect


def run(program_names: list[str], **overrides) -> DesignDocument:
    kwargs = {
        "narrate": faithful_narrate,
        "critique": confident_critique,
        "architect": make_architect(program_names),
    }
    kwargs.update(overrides)
    return run_design(FIXTURE_ROOT, program_names, **kwargs)


# --- The run produces a real, complete DesignDocument -------------------------------------------


def test_design_run_covers_every_requested_program_and_is_schema_valid():
    document = run(ALL_PROGRAMS)

    assert [entry.program_name for entry in document.programs] == ALL_PROGRAMS
    assert document.unified_design is not None
    # Round-trips through the real contract, so a graph that produced something un-serializable
    # fails here rather than at the point control-plane tries to read design.json.
    assert DesignDocument.model_validate_json(document.model_dump_json()) == document


def test_gate_items_aggregate_every_programs_real_unsupported_constructs():
    # The real per-program counts each node's own tests already pin: 9 + 2 + 32 + 9 = 52.
    # This is the first time they are summed across one run -- gate_items is the whole point of
    # the design phase (ADR-0008), so an aggregation bug here would be invisible everywhere else.
    document = run(ALL_PROGRAMS)
    unsupported = [item for item in document.gate_items if item.category == "unsupported_construct"]
    assert len(unsupported) == 52

    per_program = {}
    for item in unsupported:
        per_program[item.program_name] = per_program.get(item.program_name, 0) + 1
    assert per_program == {"CBACT04C": 9, "CBCUS01C": 2, "CBACT01C": 32, "CBTRN02C": 9}


def test_a_faithful_narration_produces_no_fidelity_issues_through_the_whole_graph():
    # Confirms the extraction each branch produced is the same one its critic checked -- if the
    # sub-graph handed spec_critic a different program's extraction, every field reference would
    # mismatch and this would be full of fidelity issues.
    document = run(ALL_PROGRAMS)
    assert [item for item in document.gate_items if item.category == "fidelity_issue"] == []
    for entry in document.programs:
        assert entry.critique.fidelity_issues == []


def test_each_branchs_critique_is_paired_with_its_own_programs_extraction():
    document = run(ALL_PROGRAMS)
    for entry in document.programs:
        assert entry.spec_extraction.program_name == entry.program_name


# --- Concurrency is real, and does not leak into the output --------------------------------------


def test_program_branches_actually_run_concurrently():
    """The C3 gate's "real parallel-branch trace" -- fan-out that overlaps, not just topology.

    Asserted by observed overlap rather than by wall-clock total alone: a loaded CI machine can
    make a total-time threshold flaky, but one branch starting before another finishes is not
    something a sequential executor can ever do.
    """
    spans: dict[str, tuple[float, float]] = {}
    threads: set[str] = set()
    lock = threading.Lock()

    def slow_narrate(model: str, system_prompt: str, user_content: str) -> str:
        started = time.perf_counter()
        time.sleep(0.5)
        result = faithful_narrate(model, system_prompt, user_content)
        with lock:
            # The program name is on the Known Facts header line: "# Known Facts for CBACT04C".
            name = result.splitlines()[0].removeprefix("# Known Facts for ").strip()
            spans[name] = (started, time.perf_counter())
            threads.add(threading.current_thread().name)
        return result

    run(ALL_PROGRAMS, narrate=slow_narrate)

    assert len(spans) == len(ALL_PROGRAMS), f"expected one span per program, got {spans.keys()}"
    latest_start = max(start for start, _ in spans.values())
    earliest_end = min(end for _, end in spans.values())
    assert latest_start < earliest_end, (
        f"no overlap between branches -- they ran sequentially. spans={spans}"
    )
    assert len(threads) > 1, f"all branches ran on one thread: {threads}"


def test_concurrent_branches_are_capped(monkeypatch):
    """Fan-out is bounded, so a large `--programs` list cannot open unlimited concurrent calls.

    Asserted by peak observed concurrency rather than by reading the constant back: a cap that is
    configured but not actually passed to `invoke` would still pass a constant check, and that is
    exactly the bug worth catching. Uses 8 branches against a cap of 2, so the two numbers cannot
    be confused for each other.
    """
    monkeypatch.setattr(design_graph, "MAX_CONCURRENT_PROGRAMS", 2)

    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def counting_narrate(model: str, system_prompt: str, user_content: str) -> str:
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.15)
        with lock:
            state["active"] -= 1
        return faithful_narrate(model, system_prompt, user_content)

    # Repeat the real programs to get more branches than the cap without inventing fixtures.
    programs = ALL_PROGRAMS * 2
    run_design(
        FIXTURE_ROOT,
        programs,
        narrate=counting_narrate,
        critique=confident_critique,
        architect=make_architect(programs),
    )

    assert state["peak"] <= 2, f"cap not enforced: {state['peak']} branches ran at once"
    assert state["peak"] > 1, "nothing ran concurrently; the test would pass even if serialized"


def test_output_order_follows_the_requested_order_under_staggered_branch_timing():
    """`design.json`'s program order is an output contract: identical runs, identical bytes.

    **This test passes with or without `run_design`'s explicit re-ordering, and that is worth
    stating rather than leaving as a trap for the next reader.** The re-ordering was written
    expecting concurrent branches to fan in by completion order. They don't: LangGraph applies a
    reducer's writes in `Send` order, so the raw state is already in requested order regardless of
    timing -- confirmed by deleting the re-ordering and watching this still pass, then measured
    directly (see `test_langgraph_fans_in_by_send_order_not_completion_order` below).

    So this is a contract test, not a proof that the normalization is load-bearing. It pins the
    property callers depend on; the test below pins the mechanism currently providing it, so an
    upgrade that changes the mechanism is caught rather than silently making this the only thing
    standing between us and a nondeterministic artifact.

    Delays are deliberately inverse to the requested order, so completion order is the exact
    reverse of what the caller asked for.
    """
    delays = {name: 0.05 * (len(ALL_PROGRAMS) - index) for index, name in enumerate(ALL_PROGRAMS)}

    def staggered_narrate(model: str, system_prompt: str, user_content: str) -> str:
        result = faithful_narrate(model, system_prompt, user_content)
        name = result.splitlines()[0].removeprefix("# Known Facts for ").strip()
        time.sleep(delays[name])
        return result

    document = run(ALL_PROGRAMS, narrate=staggered_narrate)
    assert [entry.program_name for entry in document.programs] == ALL_PROGRAMS


def test_langgraph_fans_in_by_send_order_not_completion_order():
    """Pins the LangGraph behavior the test above relies on, so an upgrade that changes it is loud.

    Reaches into the raw graph state rather than `run_design`'s output, precisely because
    `run_design` normalizes the order and would mask a change here. If this ever fails, nothing is
    broken for callers -- `run_design`'s re-ordering becomes genuinely load-bearing instead of
    belt-and-braces -- but the reasoning in ADR-0012 needs updating, and that should not be
    discovered by reading a stale docstring.
    """
    delays = {name: 0.05 * (len(ALL_PROGRAMS) - index) for index, name in enumerate(ALL_PROGRAMS)}

    def staggered_narrate(model: str, system_prompt: str, user_content: str) -> str:
        result = faithful_narrate(model, system_prompt, user_content)
        time.sleep(delays[result.splitlines()[0].removeprefix("# Known Facts for ").strip()])
        return result

    graph = build_design_graph(
        narrate=staggered_narrate,
        critique=confident_critique,
        architect=make_architect(ALL_PROGRAMS),
    )
    final_state = graph.invoke(
        {
            "worktree_root": str(FIXTURE_ROOT),
            "program_names": ALL_PROGRAMS,
            "program_entries": [],
        }
    )
    raw_order = [entry.program_name for entry in final_state["program_entries"]]
    assert raw_order == ALL_PROGRAMS, (
        "LangGraph no longer fans in by Send order -- run_design's re-ordering is now the only "
        "thing keeping design.json deterministic. Update ADR-0012's reasoning."
    )


def test_a_single_program_still_runs_through_the_same_graph():
    # The fan-out is dynamic, so one program is not a special case -- it is one branch.
    document = run(["CBACT04C"])
    assert [entry.program_name for entry in document.programs] == ["CBACT04C"]


def test_the_parallel_pair_the_plan_names_runs_as_two_branches():
    document = run(PARALLEL_PAIR)
    assert [entry.program_name for entry in document.programs] == PARALLEL_PAIR
    # These two genuinely share nothing: no copybook in common, so no merged domain entity.
    entity_sources = {e.source_copybook for e in document.unified_design.domain_entities}
    assert entity_sources == {"CVCUS01Y", "CVACT01Y"}
    for entity in document.unified_design.domain_entities:
        assert len(entity.used_by_programs) == 1


# --- Failure policy ------------------------------------------------------------------------------


def test_one_failing_program_fails_the_whole_run_rather_than_producing_a_partial_document():
    # Per ADR-0012: a DesignDocument silently covering three of four requested programs is
    # indistinguishable, at the review gate, from a complete one.
    with pytest.raises(TenantRepoFileNotFoundError):
        run(["CBACT04C", "NOSUCHPGM"])


# --- Graph structure ------------------------------------------------------------------------------


def test_the_compiled_graph_exposes_the_named_specialist_nodes():
    # ADR-0001 names the specialists; keeping them as distinct graph nodes (rather than one node
    # doing extraction and critique together) is what makes a run traceable per specialist.
    graph = build_design_graph(narrate=faithful_narrate, critique=confident_critique)
    assert {"program_branch", "solution_architect"} <= set(graph.get_graph().nodes)

    # xray=True descends into the compiled sub-graph, which is where the two per-program
    # specialists live -- the flat view only shows the branch as one opaque node.
    expanded = " ".join(graph.get_graph(xray=True).nodes)
    assert "spec_extractor" in expanded
    assert "spec_critic" in expanded


# --- ADR-0018: run_id and usage must cross LangGraph's thread pool ------------------------------


def test_run_id_reaches_every_concurrent_branch():
    """A `ContextVar` bound before `invoke` must be visible inside each branch thread.

    This is the assertion that would have caught a broken implementation. `contextvars` copies the
    calling context into each worker, so binding *before* fan-out works and binding inside a node
    would not -- and a sequential test cannot tell those apart, because with one thread every
    variant passes.
    """
    bind_run_id("run-under-test")
    seen: dict[str, str] = {}
    seen_threads: set[int] = set()
    lock = threading.Lock()

    def narrate_capturing_run_id(model, system_prompt, user_content):
        program = user_content.split("\n", 1)[0]
        with lock:
            seen[program] = current_run_id()
            seen_threads.add(threading.get_ident())
        return faithful_narrate(model, system_prompt, user_content)

    run(ALL_PROGRAMS, narrate=narrate_capturing_run_id)

    assert len(seen) == len(ALL_PROGRAMS)
    assert set(seen.values()) == {"run-under-test"}, seen
    # If everything ran on one thread the assertion above proves nothing about propagation.
    assert len(seen_threads) > 1, "branches did not actually run on separate threads"


def test_run_cost_sums_usage_across_concurrent_branches(monkeypatch):
    """`DesignDocument.cost` must total every real `call_model`, including from branch threads.

    The accumulator is a mutable object behind a `ContextVar` precisely so child threads mutate the
    parent's instance. Had it been a `ContextVar` of running integers, each branch would have
    incremented a private copy and this total would come back as zero from the branch calls --
    passing a single-threaded test and failing in production (ADR-0018).
    """
    fake = ModelCallResult(
        text="[]", model="fake-model", backend="anthropic_sdk", attempts=1,
        input_tokens=100, output_tokens=10,
        cache_creation_input_tokens=5, cache_read_input_tokens=2,
        notional_cost_usd=0.25,
    )
    monkeypatch.setattr(
        model_client, "_call_anthropic_sdk",
        lambda *a, **k: fake,
    )

    def via_call_model(node_name, fallback):
        def call(model, system_prompt, user_content):
            model_client.call_model(node_name, "fake-model", system_prompt, "probe")
            return fallback(model, system_prompt, user_content)
        return call

    document = run(
        ALL_PROGRAMS,
        narrate=via_call_model("spec_extractor", faithful_narrate),
        critique=via_call_model("spec_critic", confident_critique),
        architect=via_call_model("solution_architect", make_architect(ALL_PROGRAMS)),
    )

    # 4 extractor + 4 critic (concurrent branches) + 1 architect (after fan-in).
    expected_calls = 2 * len(ALL_PROGRAMS) + 1
    assert document.cost is not None
    assert document.cost.model_calls == expected_calls
    assert document.cost.input_tokens == 100 * expected_calls
    assert document.cost.output_tokens == 10 * expected_calls
    assert document.cost.cache_creation_input_tokens == 5 * expected_calls
    assert document.cost.cache_read_input_tokens == 2 * expected_calls
    assert document.cost.notional_cost_usd == pytest.approx(0.25 * expected_calls)
    assert document.cost.calls_without_reported_cost == 0


def test_run_cost_reports_a_partial_total_when_a_backend_gives_no_cost(monkeypatch):
    """Token counts stay exact while the dollar figure goes absent, not wrong.

    The SDK backend reports no cost by design (`model_client` keeps no rate card so it cannot go
    stale), so a consumer must be able to tell "nothing cost anything" from "nobody said".
    """
    fake = ModelCallResult(
        text="[]", model="fake-model", backend="anthropic_sdk", attempts=1,
        input_tokens=7, output_tokens=3, notional_cost_usd=None,
    )
    monkeypatch.setattr(
        model_client, "_call_anthropic_sdk", lambda *a, **k: fake
    )

    def call(model, system_prompt, user_content):
        model_client.call_model("spec_extractor", "fake-model", system_prompt, "probe")
        return faithful_narrate(model, system_prompt, user_content)

    document = run(PARALLEL_PAIR, narrate=call)

    assert document.cost is not None
    assert document.cost.model_calls == len(PARALLEL_PAIR)
    assert document.cost.input_tokens == 7 * len(PARALLEL_PAIR)
    assert document.cost.notional_cost_usd is None
    assert document.cost.calls_without_reported_cost == len(PARALLEL_PAIR)


def test_run_cost_is_logged_even_when_the_run_fails(monkeypatch, caplog):
    """A failed run has still spent money, and that is when the question gets asked.

    No `design.json` is written on the failure path and `DesignCliResult` carries no cost field
    (ADR-0008), so the stderr line is the only surviving record. Before this was moved into a
    `finally`, an invocation that died on its fourth program reported nothing about the three it
    had already paid for.
    """
    fake = ModelCallResult(
        text="[]", model="fake-model", backend="anthropic_sdk", attempts=1,
        input_tokens=11, output_tokens=2, notional_cost_usd=0.5,
    )
    monkeypatch.setattr(model_client, "_call_anthropic_sdk", lambda *a, **k: fake)

    def narrate_then_fail(model, system_prompt, user_content):
        model_client.call_model("spec_extractor", "fake-model", system_prompt, "probe")
        return faithful_narrate(model, system_prompt, user_content)

    with (
        caplog.at_level(logging.INFO, logger="cobol_modernizer.graph.design_graph"),
        pytest.raises(TenantRepoFileNotFoundError),
    ):
        run(["CBCUS01C", "NOSUCHPROGRAM"], narrate=narrate_then_fail)

    cost_lines = [r.message for r in caplog.records if "design run cost" in r.message]
    assert cost_lines, "a failed run must still report what it spent"
    # The valid program's branch really did call a model before the invalid one raised.
    assert "model_calls=1" in cost_lines[0]
    assert "input_tokens=11" in cost_lines[0]


# --- The populatability gate item, through the wiring rather than the helper ----------------------


def _entry(program_name: str):
    from cobol_modernizer.core.contracts import ProgramDesignEntry
    from cobol_modernizer.nodes.spec_critic import critique_spec
    from cobol_modernizer.nodes.spec_extractor import extract_spec

    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{program_name}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, program_name, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return ProgramDesignEntry(
        program_name=program_name, spec_extraction=extraction, critique=critique
    )


def _narrow_design():
    """The design as it stood when a real model could not populate the `Tran` it was asked for."""
    from cobol_modernizer.core.contracts import (
        BatchJobDesign,
        BatchStepDesign,
        CompositeComponent,
        CompositeType,
        UnifiedDesign,
    )
    from cobol_modernizer.nodes.solution_architect import build_domain_entities

    entry = _entry("CBACT04C")
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    design = UnifiedDesign(
        domain_entities=entities,
        composite_types=[
            CompositeType(
                name="TranCatBalWithRate",
                components=[
                    CompositeComponent(field_name="balance", entity_name="TranCatBal"),
                    CompositeComponent(field_name="disclosureGroup", entity_name="DisGroup"),
                ],
            )
        ],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name="CBACT04C",
                description="Monthly interest.",
                domain_entities=[e.name for e in entities],
                steps=[
                    BatchStepDesign(
                        step_name="computeInterest",
                        source_paragraphs=["1300-COMPUTE-INTEREST"],
                        role="processor",
                        description="Computes monthly interest.",
                        input_type="TranCatBalWithRate",
                        output_type="Tran",
                        guard_condition="IF DIS-INT-RATE NOT = 0",
                    )
                ],
            )
        ],
        rest_endpoints=[],
    )
    return entry, design


def test_an_unpopulatable_step_becomes_a_gate_item_a_reviewer_sees():
    """The wiring, not the helper -- which is the distinction G21 was closed twice over.

    `unreachable_entities` had tests of its own while nothing exercised the path that turns its
    answer into something a human at control-plane's gate actually reads. CI's coverage falling
    from 98.59% to 98.37% is what surfaced that, and every uncovered line was this function.
    """
    entry, design = _narrow_design()
    items = design_graph.unpopulatable_gate_items(FIXTURE_ROOT, [entry], design)

    assert len(items) == 1
    item = items[0]
    assert item.program_name == "CBACT04C"
    assert "Account" in item.summary and "CardXref" in item.summary
    assert "1300-COMPUTE-INTEREST" in item.detail


def test_the_gate_item_reaches_the_design_document():
    # A gate item nobody assembles into `design.json` is a gate item nobody sees.
    from cobol_modernizer.core.contracts import build_design_document

    entry, design = _narrow_design()
    document = build_design_document(
        [entry],
        unified_design=design,
        design_gate_items=design_graph.unpopulatable_gate_items(FIXTURE_ROOT, [entry], design),
    )
    assert any("cannot reach" in gate_item.summary for gate_item in document.gate_items)


def test_a_design_that_never_ran_the_architect_yields_no_items():
    assert design_graph.unpopulatable_gate_items(FIXTURE_ROOT, [_entry("CBACT04C")], None) == []


def test_a_job_for_another_program_is_not_analysed_against_this_ones_source():
    # The program filter matters: analysing CBACT04C's steps against CBCUS01C's source would
    # report every entity as unreachable, which is a false alarm rather than a finding.
    entry, design = _narrow_design()
    design.batch_jobs[0].program_name = "CBCUS01C"
    assert design_graph.unpopulatable_gate_items(FIXTURE_ROOT, [entry], design) == []


def test_a_populatable_step_produces_no_gate_item():
    """The other half of a check that can fail: it must also go quiet when the design is right.

    This is PR #40's widened composite. Without this case the suite only ever saw the check
    complain, which is how a check that always fires looks exactly like a check that works.
    """
    from cobol_modernizer.core.contracts import CompositeComponent

    entry, design = _narrow_design()
    design.composite_types[0].components.extend(
        [
            CompositeComponent(field_name="account", entity_name="Account"),
            CompositeComponent(field_name="cardXref", entity_name="CardXref"),
        ]
    )
    assert design_graph.unpopulatable_gate_items(FIXTURE_ROOT, [entry], design) == []
