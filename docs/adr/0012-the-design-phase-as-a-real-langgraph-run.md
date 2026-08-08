# ADR-0012: The `design` phase as a real LangGraph run

## Status

Accepted (2026-08-08).

## Context

ADR-0001 decided that this repo's internal pipeline is "a bounded, in-process LangGraph sub-graph
with an in-memory checkpointer... five narrow specialist nodes under one supervisor". That decision
had no code behind it. `langgraph` was a declared dependency this repo had never imported, and the
three `design`-phase nodes (`spec_extractor`, `spec_critic`, `solution_architect`) existed only as
functions that nothing composed — each verified thoroughly in isolation, none ever run together.
`cli.py`'s `design` branch returned `status: "not_implemented"`.

So the largest real gap was not a missing node. It was that nothing produced the artifact this
whole phase exists for: a `design.json` with `gate_items`, which per ADR-0008 is the payload
control-plane's durable human gate actually reviews.

## Decision

**1. The `design` phase is a compiled LangGraph, not a for-loop.**

A plain loop over programs plus a final call would be less code today. LangGraph is chosen anyway,
on three grounds: it is what ADR-0001 already decided; an unused declared dependency is its own
defect; and Milestone C4's self-healing compile loop (a conditional edge with a retry cap) is a
genuine cycle that a hand-rolled orchestration would end up reimplementing badly. Establishing the
pattern now, on the simpler half, is cheaper than establishing it under the harder one.

**2. Per-program work fans out dynamically and runs concurrently; `solution_architect` runs once
after the join.**

`spec_extractor` and `spec_critic` are per-program and independent across programs. Fan-out uses
`Send`, driven by whatever `--programs` the caller passed, rather than a branch per known program
name; a hardcoded topology would make the graph a liar the first time control-plane invoked it with
a different program set. `solution_architect` joins because its entire job is looking across all
programs at once (ADR-0010).

**This drops a serialization the README's diagram had carried since Milestone C1**, and the reason
is worth recording. That diagram ran `CBCUS01C` and `CBACT01C` as the parallel pair and chained
`CBTRN02C` and `CBACT04C` after the join, because those two "both depend on account data". They do
— but the dependency is on the `CVACT01Y` *copybook*, which each branch reads independently from
the read-only tenant worktree. No branch consumes another branch's output, so there was never
anything to serialize. The original topology confused a shared input with a data dependency. All
programs now fan out together; the README's diagram is corrected to match rather than left
describing an intent the code does not have.

Concurrency here is real and was measured, not assumed: LangGraph's sync `invoke()` dispatches
independent branches onto a `ThreadPoolExecutor`, and the branches genuinely overlap.

**3. The per-program branch is its own compiled sub-graph with two nodes**, rather than one node
calling both functions. That keeps `spec_extractor` and `spec_critic` separately named and
separately traceable, which is what ADR-0001's "narrow specialist nodes" buys and the only reason
to prefer a graph over a function call in the first place.

**4. `design.json`'s program ordering is guaranteed by this repo, not inherited from LangGraph.**

This one reversed during implementation and the reversal is the point. The re-ordering step in
`run_design` was written on the assumption that concurrent branches fan in by completion order.
**They do not.** LangGraph applies a reducer's writes in task-creation (`Send`) order, so the state
is already deterministic regardless of how long each branch takes — confirmed by deleting the
re-ordering and watching the ordering test still pass, then measured directly with randomized
per-branch delays over repeated runs.

The normalization stays regardless. `design.json`'s ordering is an output contract: two identical
runs must produce byte-identical files, or the provenance and diffing story `CLAUDE.md` asks this
repo to maintain quietly dies. Resting that contract on an internal scheduling detail of a
dependency means a LangGraph upgrade could change it with nothing failing loudly. Two lines make
the guarantee ours. The ordering test now says explicitly that it passes with or without those two
lines, and a second test pins LangGraph's current behavior so that a change becomes a loud failure
rather than a silent transfer of responsibility.

**5. One failing program fails the whole invocation.**

No partial `design.json` covering the programs that happened to succeed. A reviewer handed a
document that silently covers three of four requested programs cannot tell it from a complete one,
and `gate_items` is precisely what they are there to weigh. Note this is the opposite of the
per-*field* policy in ADR-0006, deliberately: an unmappable field is isolated so the other 92 still
get narrated, because one ambiguous `REDEFINES` field is a bounded, visible fact. A missing program
is not.

**6. The CLI catches broadly at the subcommand boundary.**

Everywhere else this repo fails loudly. The CLI boundary is the exception, because its `--json`
stdout is a contract: an unhandled traceback leaves control-plane with nothing parseable in exactly
the situation where it most needs a machine-readable reason. The exception becomes
`status: "error"` with the exception type and message in `detail`, exit code 1, and the full
traceback still on stderr. Narrowing the `except` to the known node exception types was considered
and rejected — an unanticipated exception type is the case that most needs this.

**7. `run_id` correlation.** `DesignCliResult` gains a **required** `run_id`, stamped on every log
line and echoed on stdout. `--run-id` lets control-plane pass its own audit-log run id so both
sides share one identifier (the cross-repo provenance convention ADR-0009 sets out); absent one,
the CLI generates it, and echoing is how the caller learns it. Required rather than optional: a
result that could silently omit its correlation id is useless in the `status="error"` case, which
is when someone actually needs to find the matching stderr lines.

**8. On-disk layout**, owned by `core/design_outputs.py` — not by `cli.py` (argument parsing should
not decide a contract control-plane reads) and not by the graph (whose job ends at a
`DesignDocument`):

    <output>/design.json          the full DesignDocument, gate_items included
    <output>/<PROGRAM>/spec.md    one program's narration

`<PROGRAM>/spec.md` mirrors `tests/fixtures/golden/CBACT04C/spec.md` so a real run and the
hand-verified golden fixture are the same shape and can be diffed directly once a live credential
exists. `design.json` is pretty-printed with a trailing newline — it is read by a human at a review
gate and committed alongside generated code, so a single-line blob would make every review diff
useless. That is the opposite of the `--json` stdout, which is compact and machine-only.

## Consequences

**The `design` phase produces a real artifact.** A real four-program run writes a 413 KB
`design.json` with 52 `unsupported_construct` gate items and 7 unified domain entities, plus four
`spec.md` files — verified as an actual OS process, so the stdout/stderr split is the real one, not
pytest's in-process approximation.

**A sub-graph state key that collides with its parent's breaks concurrent fan-out.** A sub-graph
returns its whole state to the parent, so a shared key without a reducer gets written once per
branch inside a single superstep and LangGraph raises
`InvalidUpdateError: At key 'worktree_root': Can receive only one value per step` — even though
every branch writes an identical value. Hence `branch_worktree_root`. Found by running the graph.
Anything added to `ProgramBranchState` later needs the same care.

**The live model calls are still unexercised**, unchanged from every node before this. The CLI
tests patch `anthropic.Anthropic` itself rather than the nodes' injected callables, which puts the
fake as low as it can go: argument parsing, run-id handling, the graph, model routing, registry
prompt loading, guardrail wrapping, fidelity checks, gate-item aggregation, file writing, and the
stdout contract are all production code, and only the HTTP call is not. Two reasons for that
choice: `extract_spec(..., narrate=_default_narrate)` binds its default at definition time, so
patching the module attribute would not work anyway; and patching the injected callable would leave
the default path — the only one control-plane will ever take — untested.

**`spec_critic`'s model tier is still uncalibrated** (ADR-0004's deferred question), and
`LOW_CONFIDENCE_THRESHOLD` still has no real critique to calibrate against, so no
`low_confidence_rule` gate item has ever been produced by a real model.

**Milestone C3's gate is now partly met**: schema-valid `design.json` ✅ and a real parallel-branch
trace ✅; "a real control-plane gate exercised" remains open and belongs to Milestone C5, since it
needs control-plane's side wired (Track P1).

**No checkpointer is attached**, per ADR-0001 — a crash loses the invocation, and control-plane
recovers by re-invoking the CLI. The structured-output repair-retry loop (plan step 35) is
deliberately sequenced after this: with no live call anywhere in this repo, the failure modes such
a loop would handle are hypothesized rather than observed, and it now has a real path to be wired
into rather than a hypothetical one.
