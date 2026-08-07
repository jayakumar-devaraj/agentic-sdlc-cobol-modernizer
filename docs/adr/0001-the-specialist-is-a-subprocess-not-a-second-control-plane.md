# 0001 - The specialist is a subprocess, not a second control plane

## Context

A code review of `agentic-sdlc-control-plane` (not its README — the actual source) found a
Postgres-checkpointed `StateGraph`, five durable human-in-the-loop gate types, a hash-chained
audit log, and a 91%-coverage-gated test suite, all verified working, including restart-resume
proven with a real killed worker process. Building a second orchestration engine for COBOL
modernization — its own checkpointer, its own gates, its own audit log — would duplicate
infrastructure that already works, and do it with less rigor than the original took to get right:
control-plane's own ADRs record five real defects (0002-0006) found only by running against real
infrastructure, each while a green test suite was passing.

Control-plane's ADR 0001 also constrains the option of putting COBOL logic directly inside
`graph.py`: the repo is domain-agnostic by requirement, enforced with a CI grep for tenant
vocabulary, not by convention. Reverse-engineering COBOL, mapping `PIC` clauses to `BigDecimal`,
and reasoning about CardDemo's copybooks is exactly the kind of tenant vocabulary that check
exists to keep out.

Separately, within whatever this repo builds, there is a question of how the COBOL-to-Java
pipeline itself should be structured: one long prompt handling extraction, design, and code
generation together, several independent agents negotiating directly, or a supervised sequence of
narrow specialists.

## Decision

**This repo is invoked as a subprocess, the same shape as control-plane's existing `coder` node
invoking the `claude` CLI.** It has no ingress API, no Postgres checkpointer, no HITL gates, and no
audit ledger of its own. Durability and human approval live in control-plane, at the granularity
of one specialist invocation — control-plane pauses at its own existing gate types before and
after calling this CLI; this repo does not add new ones.

Internally, the pipeline is a **bounded, in-process LangGraph sub-graph with an in-memory
checkpointer**, not a durable one — one process, one invocation, no cross-process resume. It is a
supervisor-plus-specialist pattern, not a single monolithic prompt and not decentralized
peer agents:

- **Rejected: a single prompt covering extraction, design, and code generation.** COBOL-source
  text, an intermediate design, and Maven compiler output would all accumulate in one context
  window, and a guardrail meant to apply only to code output (reject unsafe generated syntax)
  cannot be scoped separately from one meant to apply only to extraction (treat COBOL comments as
  untrusted text, not instructions) if both live in the same call.
- **Rejected: independent agents negotiating directly.** Nothing here needs negotiation — spec
  extraction, design, and code generation are a strict pipeline, each stage consuming the
  previous stage's output. Peer negotiation buys flexibility this problem doesn't have, at the
  cost of a harder-to-reconstruct decision trail — control-plane's own audit design treats "who
  decided what, and why" as a first-class requirement, and this repo's output feeds that same
  ledger.
- **Chosen: five narrow specialist nodes under one supervisor** — `spec_extractor`,
  `spec_critic`, `solution_architect`, `modernization_engineer`, `build_validator` — each with one
  auditable responsibility, connected by conditional edges the supervisor owns (the self-healing
  compile retry, capped at three attempts, is one such edge). `spec_critic` exists specifically
  because the other four are extraction, design, generation, and diagnosis — none of them
  re-examine their own output for completeness before a human sees it. It does, computing a
  confidence score per extracted business rule from the source it was extracted from.

## Consequences

**A crash mid-invocation loses that invocation's progress**, not just a step of it — there is no
partial-resume the way control-plane resumes a killed worker from its last checkpoint. This is an
accepted cost: one invocation is bounded (one COBOL program's extraction-through-compile cycle,
not a multi-day workflow), and control-plane already retries a failed node at the coarser level, so
a crashed specialist invocation is recovered by control-plane re-invoking the CLI, not by anything
this repo builds. Building resume-from-mid-pipeline here would be exactly the duplicated
infrastructure this decision exists to avoid.

**Every specialist node's output must be self-contained JSON**, since nothing is held in a shared
durable store between this process and control-plane — the CLI's structured stdout (spec, design,
generated files, compile diagnostics) is the entire contract. If a future stage needs something
an earlier stage decided, that decision has to be in the JSON, not inferred by re-running an
earlier node.

**`spec_critic`'s confidence score is the only risk signal control-plane's HITL gate sees for this
scenario type** — there is no second, independent review. If extraction and critique share a
systematic blind spot, nothing here catches it before the human does. Track B's `REDEFINES`/
`OCCURS DEPENDING ON` alias analysis is the first place this repo adds a second, independent
check (a mandatory gate on ambiguity, not just a confidence score) — because that specific failure
mode is a plausible-wrong-answer risk, not a not-noticed-yet one.
