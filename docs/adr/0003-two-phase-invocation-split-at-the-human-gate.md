# 0003 - Two-phase invocation, split at the human gate

## Context

An earlier draft of this repo's architecture diagram showed one continuous flow: spec extraction,
critique, and solution design, then "return to control-plane for a gate," then codegen and the
self-healing compile loop — all inside a single CLI invocation. That contradicts ADR-0001 directly:
this repo has no durable checkpointer and is explicitly a *single bounded subprocess call*. A
process cannot pause partway through, hand control back to its caller, and later resume from where
it left off after a human approves something — once the process exits, whatever wasn't written to
its output is gone. The diagram described a capability this repo's own foundational ADR had already
ruled out.

The value the gate is meant to protect is real, though: reviewing `design.json` before spending
LLM calls (and sandbox compute) generating and compiling Java against a possibly-wrong design is
worth the pause. Removing the gate entirely — generating code straight through with no review —
was not an acceptable fix for the diagram's inconsistency.

## Decision

**The CLI has two subcommands, not one: `design` and `generate`, each a separate, independently
bounded process invocation.**

- `cobol-modernizer design --programs CBCUS01C CBACT01C CBTRN02C CBACT04C --tenant-repo <path>
  --output <path>` runs `spec_extractor` → `spec_critic` → `solution_architect` across all four
  programs and exits, having written `design.json` and each program's `spec.md` to `--output`.
  Nothing about this invocation assumes it will run again or that any later process can see its
  in-memory state — everything a later phase needs is in the written files.
- Control-plane receives this result, persists it, and pauses at its own existing
  `plan_approval`-shaped gate — durably, using infrastructure this repo doesn't have and doesn't
  need to build (ADR-0001).
- `cobol-modernizer generate --design <path to the approved design.json> --tenant-repo <path>
  --output <path>` is a **second, unrelated process invocation**, started fresh, with no memory of
  the `design` run beyond what's in the `design.json` file it's given. It runs
  `modernization_engineer` → the self-healing compile loop → `build_validator` and exits.

**`design.json` covers all four Track C programs together, not one file per program.** The four
programs form one coherent domain (customer, account, transaction, interest — one nightly batch
cycle, per the construct matrix), and the target is one Spring Boot module, not four disconnected
ones. Splitting design output per-program would force `generate` to somehow reconcile four
independent domain models into one coherent codebase with no shared context to do it from — the
unified file is what makes the two-phase split viable at all.

## Consequences

**`design.json` must be fully self-contained.** `generate` has zero access to anything `design`
reasoned about that didn't make it into the file — no shared memory, no re-invocable state. If a
future node in `generate` needs something `solution_architect` decided (a naming convention, a
rejected alternative, a confidence caveat), that has to be an explicit field in `design.json`, not
an assumption that context carries across the gate.

**A human approving `design.json` is approving a complete, static snapshot.** If the tenant
repository changes between the `design` and `generate` invocations (a concurrent modification to
the COBOL source, however unlikely in Track C's read-only flow), `generate` has no way to detect
that on its own — this is control-plane's concern (it owns the clone) and is out of this repo's
scope to solve, not silently assumed away.

**The CLI skeleton (Milestone C1) already reflects this** — `design` and `generate` are separate
subcommands from the start, not a single `run` command retrofitted later. Retrofitting after
`spec_extractor` existed would have been a larger, riskier change than getting the shape right
before any node logic depended on it.
