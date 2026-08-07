# 0008 - `design.json`'s schema, and `gate_items` as the concrete HITL-review contract

## Context

Per ADR-0001, this repo has no HITL gate of its own — control-plane's existing, durable,
`interrupt()`-backed gate machinery reviews `design.json` between the `design` and `generate`
invocations (ADR-0003). That's the *mechanism*. It says nothing about the *shape* of what a human
reviews, and today there isn't one: `cli.py`'s `design` subcommand still returns
`"not_implemented"`, and even once wired (Milestone C3 step 36), nothing yet consolidates "what in
this run actually needs a human's attention" into one place. A reviewer would otherwise have to
separately read four programs' `unsupported_fields`, four `SpecCritiqueResult.rule_confidence`
lists, and four `injection_flags` lists to find the handful of items that matter — exactly the
kind of scattered, easy-to-miss review surface that makes a gate a formality instead of a real
check. This is the concrete form of Pillar 10 (Strict Tool Contracts), previously a real, named
gap: "CLI I/O still ad-hoc; formal JSON Schema is Milestone C3 step 33."

Two things are true at once that shape this ADR's scope: `spec_extractor` and `spec_critic`
(Milestone C2) already produce everything a per-program gate review needs, today, for real.
`solution_architect` (Milestone C3 step 34) — the node that actually produces the unified
REST/domain model `design.json`'s name promises — does not exist yet. A schema for `design.json`
written now cannot honestly specify a shape for work that hasn't been designed.

## Decision

**1. `design.json`'s envelope is versioned and mostly-specified now; `unified_design` is
deliberately left as an untyped placeholder.** `core/contracts.py`'s `DesignDocument` has
`schema_version`, `generated_at`, `programs: list[ProgramDesignEntry]` (one real, fully-typed
entry per program — `SpecExtractionResult` plus `SpecCritiqueResult`, both already real), a real
`gate_items: list[GateItem]`, and `unified_design: dict | None = None`. Guessing
`solution_architect`'s output shape now, before that node's own design decisions exist, would be
exactly the kind of speculative design this repo's own conventions warn against — the field is
present (so `DesignDocument`'s envelope doesn't need a breaking schema change when step 34 lands)
but intentionally untyped until then.

**2. `gate_items` is the concrete answer to "what does a human actually review."** A new
`build_gate_items()` function deterministically consolidates, across every program in one
`design.json`:
   - one `GateItem` per `UnsupportedField` (every `REDEFINES`/unresolvable `PIC` clause — already
     real, from `spec_extractor`),
   - one `GateItem` per `SpecCritiqueResult.fidelity_issues` entry (every mechanically-proven
     narration defect — already real, from `spec_critic`, and per ADR-0007 these already force
     `overall_confidence` to `0.0`; each one is still surfaced individually here, not summarized
     away, since a human deciding whether to approve needs to know *what* is wrong, not just that
     something is),
   - one `GateItem` per rule in `rule_confidence` scoring below a threshold (**`0.7`, a tentative
     default in the same spirit as ADR-0004's tentative model tiers — not a benchmarked number,
     since no real critique run against a live model has happened yet to calibrate against; revisit
     once one has**),
   - one `GateItem` per `InjectionFlag` (already real, from `core/guardrails.py`).

   This module never decides what happens with a `GateItem` — no approve/reject, no blocking,
   consistent with `guardrails.InjectionFlag`'s own "flagged for a human or downstream gate to
   weigh, never used to block processing outright" posture (see that module's docstring). Building
   an opinion about gate policy here would be this repo quietly growing the second control plane
   ADR-0001 exists to prevent.

**3. The CLI's `--json` stdout contract is a summary envelope, not the full `design.json`.**
`DesignCliResult` reports `status` (`"ok"`/`"error"`), the programs processed, the real
`output_path` `design.json` was written to, and `gate_item_count` — enough for control-plane to
decide what to do next (including whether its own gate policy pauses) without control-plane having
to parse a potentially-large four-program JSON blob out of stdout. The full `design.json` is the
`--output` file's content, per the existing CLI contract. `status` reports only whether this
invocation itself succeeded — it is deliberately *not* `"gate_required"` or similar: whether
`gate_item_count > 0` should pause anything is control-plane's gate policy to decide, not a
judgment this repo's CLI contract should bake in. `GenerateCliResult` is defined with the same
minimal shape as the current stub (`status`, `phase`, `output_path`, `detail`) — its own
self-healing-loop-specific fields (attempt count, diagnosis) are Milestone C4 work, once
`build_validator` exists to define them; inventing them now would be the same speculative-design
mistake `unified_design` avoids.

**4. JSON Schema is generated from the Pydantic models, not hand-maintained separately.**
`schemas/*.schema.json` are committed artifacts produced by each model's own
`model_json_schema()` — Pydantic remains the single source of truth (consistent with Pillar 15,
"Structured Type Validation: Strong | Pydantic throughout every module built so far"). A test
regenerates each schema at run time and asserts it matches the committed file exactly, so a schema
change without a matching code change (or vice versa) fails CI rather than silently drifting —
external consumers (chiefly control-plane, which may not be Python) get a real, checkable contract
file, not a hand-transcribed copy that could rot.

## Consequences

A human reviewing a gate now sees one list, `gate_items`, spanning every program in the run, each
entry naming its category, program, and a human-readable summary — not four separate structures to
cross-reference. `0.7` as the low-confidence threshold is a real, disputable number stated plainly
rather than buried in code; revisiting it is expected once real critique runs exist to calibrate
against, the same posture ADR-0004 already took for `spec_critic`'s model tier.

`unified_design` being untyped is an honest, temporary gap, not a finished contract — `solution_architect`
(step 34) must give it a real shape, and that will be a schema change to `DesignDocument`, tracked
when it happens, not assumed away now. `cli.py` itself is still not wired to any of this — that
remains Milestone C3 step 36's job; this ADR and `core/contracts.py` define the contract that step
will fill in, not the wiring itself.
