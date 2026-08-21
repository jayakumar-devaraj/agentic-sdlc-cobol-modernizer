# ADR-0046: Tracing is self-hosted and emitted from the one client every node already uses

## Status

**Accepted** (2026-08-21). Decides the tool for pillar 21 / step 57 (Track P5), which the plan had
named only as *"distributed tracing (OpenTelemetry)"* with no comparison recorded. Written under
ADR-0044's rule: a default with a cost is still a decision. **Nothing is implemented by this ADR** —
it decides what will be, and why not the alternatives.

## Context

Gap **G3** is open and 🔴: *"No runtime observability platform-wide. A cross-repo request cannot be
followed end to end."* Verified at every audit revision — **zero** OpenTelemetry, Prometheus or span
references in either repository.

What exists is **correlation, not tracing**: `run_id` flows through both CLI subcommands (ADR-0018,
closing G18) and structured logs go to stderr. That lets someone join records after the fact. It
does not let them follow one request across a process boundary.

Two facts about this repo decide most of what follows, and both were checked rather than assumed:

1. **Only half the pipeline is a LangGraph run.** `graph/design_graph.py` builds a real `StateGraph`
   (ADR-0012); `graph/generate_pipeline.py` is plain Python. So the auto-instrumentation that
   LangGraph-native tracing vendors sell covers `design` and gives **nothing** for `generate` — the
   half carrying the self-healing loop, the compile retries, and what G18 itself called *"the
   highest telemetry volume in the system"*.
2. **Every model call in both halves goes through one function.** All five nodes call
   `core/model_client.call_model` (ADR-0013), which already owns backend choice, retry, timeout and
   usage capture.

## Decision

**Emit OpenTelemetry spans from `call_model` and the two CLI entrypoints. Collect them in
self-hosted Langfuse. Do not use a hosted SaaS tracing backend.**

### Why not LangSmith

**It is a data-egress decision wearing a tool choice's clothes.** The prompts this repo sends carry
the tenant's proprietary COBOL — that is the whole payload. Exporting them to a third-party service
is a decision about a customer's source code leaving their estate, and it is not one a renderer
choice should make silently. This repo also has no credential story to hang it on: ADR-0005's
`--db-credentials-file` is decided and still unimplemented.

### Why Langfuse rather than a bare collector

Self-hostable in Docker beside the Postgres this repo already composes, and **OTel-compatible**, so
choosing it does not overwrite the plan's existing answer — spans are emitted in a vendor-neutral
format and Langfuse is where they land. Swapping the backend later costs a configuration change, not
an instrumentation rewrite. It also understands LLM-shaped traces (prompt, completion, token counts,
cost) which a generic collector renders as opaque attributes.

### Why the instrumentation point is `call_model`, not the graph

Because instrumenting the graph would cover half the system. One decorator at the client covers
**100% of model calls in both halves**, with `run_id` already flowing as the correlation key. The
LangGraph-native auto-instrumentation that vendors advertise is, for this architecture, the smaller
half of the value.

### Two constraints that are not optional

- **`stdout` stays clean.** The `--json` contract is one object on stdout; an exporter writes to
  stderr or a socket, never stdout. This has broken before and is the reason logging was put on
  stderr in the first place.
- **This repo is a subprocess that exits.** Spans must flush on exit, and a collector that is
  unreachable must not fail the run — telemetry that can abort a migration is worse than no
  telemetry.

## Consequences

- **G3 is not closed by this repo alone**, and the ADR does not pretend otherwise. Pillar 21 is
  assigned to control-plane, and *cross-repo* tracing needs the receiving side, which is Track P5 —
  blocked behind **G20**, whose extraction has never been sized. What this decides is the half this
  repo can own.
- **A dependency this repo does not have yet.** `opentelemetry-sdk` and an exporter are new
  packages, against ADR-0001's bounded-subprocess premise. Justified because they are optional at
  runtime — no collector configured means no exporter constructed — and refused if that stops being
  true.
- **The alternative stays open and is named**: a bare OTel collector with no Langfuse, if LLM-shaped
  trace rendering turns out not to earn its keep. Because emission is vendor-neutral, that is a
  config change.
- **Nothing here improves the `design.json` a human reviews.** Tracing is for diagnosing a run, not
  for the gate. The gate's payload is `gate_items` (ADR-0008), and that is deliberately unchanged.
