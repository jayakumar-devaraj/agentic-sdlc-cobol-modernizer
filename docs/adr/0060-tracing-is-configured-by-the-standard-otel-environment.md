# ADR-0060: Tracing is configured by the standard OTel environment, and emitted around one logical call

## Status

**Accepted** (2026-08-29). Implements [ADR-0046](0046-tracing-is-self-hosted-and-emitted-from-the-one-client-every-node-uses.md),
which decided the instrumentation point and the collector and deliberately implemented neither.
This records the decisions that implementing it actually required — the ones ADR-0046 left open,
each of which has a cost worth disputing.

## Context

ADR-0046 settled three things: spans come from `core/model_client.call_model` rather than from the
graph, because only half this repo is a LangGraph run; the collector is self-hosted rather than a
SaaS, because the prompts carry the tenant's proprietary COBOL; and emission is vendor-neutral, so
the backend stays a configuration change.

It settled nothing about *how*. Between that ADR and a working span there were six choices, and
the working reference that existed in the meantime — a monkey-patch loaded only under
`langgraph dev` — answered none of them, because a local harness is allowed to reach into a
package's internals and a package is not.

The gap that made this urgent: the container runs `main.py`. The harness patch loads through a dev
server that production never starts, so every containerised run has been completely untraced.

## Decision

### 1. Configuration is the standard `OTEL_*` environment, and nothing else

`OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT` turns tracing on; the
exporter reads its own endpoint, headers and timeouts from the same environment. This repo defines
no configuration keys of its own for tracing and names no backend anywhere in `src/`.

**The cost:** there is no config-file path, so a deployment must set environment variables, and a
typo in a variable name produces silence rather than an error. Mitigated by one startup log line
that states whether tracing is on and where it exports to — a run that silently produced no spans
is the failure this platform has spent the most time misdiagnosing.

**Why not the Langfuse SDK**, which is what the harness used and what the reference implementation
proves works: it would put the backend's name in every call site, which is precisely the coupling
ADR-0046 traded away when it chose an OTel-compatible collector. The swap it wanted to keep cheap
is only cheap if nothing in `src/` knows the answer.

### 2. Three OpenTelemetry packages are runtime dependencies, not an extra

**The cost, stated plainly:** three packages on every install, against ADR-0001's bounded-subprocess
premise, for a feature most invocations will not use.

An extra was the obvious alternative and is wrong here for a specific reason: control-plane
installs this repo from the pinned requirement string in its `config/scenario_specialists.yaml`,
which names no extras. An extra would mean the containerised deployment — the one that is untraced
today and the whole reason this work exists — is the one guaranteed not to have it. A dependency
whose only purpose is a deployment that cannot receive it is worse than no dependency.

The premise is honoured where it can be: `telemetry/tracing.py` imports the SDK lazily inside
`configure_tracing`, so an invocation with no endpoint configured constructs nothing and pays only
for three directory entries.

### 3. A parent's `TRACEPARENT` is read from the environment

This CLI is normally a subprocess of control-plane. A trace that began at this process boundary
would answer none of the cross-process questions tracing exists for, so the root span joins a W3C
`traceparent` when one is present in the environment.

**The cost:** the environment is the only channel a `subprocess.run` caller and this CLI already
share, and it is a channel the caller has to opt into. Nothing sets it today — control-plane's
specialist invocation does not yet export it — so this half of the contract is implemented and
unexercised. Stated rather than left to be discovered: **until control-plane exports
`TRACEPARENT`, a specialist run appears as its own trace rather than as part of the caller's.**
That is a control-plane change and belongs to its own pillar (ADR-0046's Consequences already
assign cross-repo tracing there).

### 4. One span per logical call, not per transport attempt

`call_model` retries up to `MAX_TRANSPORT_ATTEMPTS`. The span wraps the whole loop and carries
`gen_ai.request.attempts`.

**The cost:** a run that succeeded on its third attempt shows one span, not three, so per-attempt
latency is not recoverable from the trace. Accepted because the alternative reads worse for the
question actually being asked — *what did this node's call cost and how long did it take* — and
because the retry warnings already go to stderr with the same `run_id`. A call that struggled is
still visible: the attempt count is on the span.

The prompt is attached **before** the first attempt, so a call that never returns still shows what
was asked. That is the case where a trace is most useful and the one a success-only span misses.

### 5. Three `langfuse.*` attributes, named as a concession

Everything else is OpenTelemetry GenAI semantic conventions. Three attributes
(`langfuse.observation.type`, `.input`, `.output`) are renderer-specific.

**The cost:** the emission is not purely vendor-neutral, so ADR-0046's claim needs this footnote.
Accepted because the attributes are *additive* — a collector that does not recognise them ignores
three strings and still receives every `gen_ai.*` attribute, including all four token counts —
which is a different and much smaller coupling than emitting through a vendor SDK, where the whole
payload shape would be proprietary.

### 6. Prompt and completion capture is on by default, and switchable

Default on: ADR-0046 refused a SaaS backend specifically so this repo could record prompts that
carry the tenant's source, and a tracing implementation that then declined to record them would
have paid that ADR's cost for none of its benefit.

**The cost:** the tenant's proprietary COBOL leaves this process and lands in whatever collector
the environment names, which is a data-egress decision made by a variable. The standard
`OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=false` turns content off while keeping token
counts, timings and the call graph — a deployment exporting somewhere less trusted loses the
prompts, not the span.

## Consequences

- **The containerised consumer can be traced**, which it could not be before: the instrumentation
  is in the package, reached by `main.py` through the CLI like everything else, rather than in a
  harness only `langgraph dev` loads.
- **Gap G3 is not closed**, and this does not claim to close it. What is closed is this repo's
  half of it, for the two subcommands and every model call inside them. Cross-repo correlation
  needs decision 3's other end, which is control-plane's.
- **`_finish` is now the single exit for three things** — logging, usage accounting, and the span
  — rather than two. That was already its stated reason for existing: one helper so that a future
  third backend cannot do one and forget another.
- **Two defects were found by the tests rather than by a run**, and both would have been silent:
  the OTel SDK records an exception itself, so leaving its handling on logged every failure twice;
  and the global tracer provider can be set only once per process, so a second `configure_tracing`
  would have left `shutdown_tracing` holding a provider that was never used, flushing nothing.
- **A local harness proved the shape and is now redundant for this repo.** `_llm_spans.py` remains
  the only instrumentation for control-plane's own `claude` CLI call, which has no equivalent
  package-level implementation and needs its own decision there.
