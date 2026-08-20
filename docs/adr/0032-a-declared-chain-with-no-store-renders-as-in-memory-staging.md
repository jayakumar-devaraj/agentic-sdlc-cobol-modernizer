# ADR-0032: A declared step chain with no store renders as in-memory staging, and an aggregate is refused

## Status

**Accepted** (2026-08-20). Addresses the last part of gap **G31** — the job and step wiring — after
[ADR-0030](0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md)'s stopgap and the
reader/writer renderers that grew out of its findings.

## Context

The reader and both writers are now rendered from `design.json`. What is still hand-written is the
job bean, the step beans, and the handoff between them — and two of the stopgap's findings say why
that last part is not simply more of the same mechanical work.

**Finding F3.** `computeInterest` outputs a `TranWithContext` and `completeTransaction` consumes
one, so the value crosses a step boundary. `TranWithContext` corresponds to no copybook and no file:
`file_access_paths` has no entry for it, because the COBOL never writes it anywhere — in the original
it is a few `WORKING-STORAGE` fields between two `PERFORM`s. **The design declares a chain and no
store for it.**

**Finding F6.** `postAccountInterest` consumes an `AccountInterestPosting`, which is an *aggregate*
of the earlier steps' output — one account with its interest already summed (ADR-0027). Nothing in
the design says the grouping key, the summed field, or the ordering. Those facts are in the COBOL as
a control break, and no parse this repo has produces them.

Both are the same shape of question — *what happens between steps* — and they have different
answers, which is why they get one ADR rather than two.

## Options for the chain (F3)

**(a) Fuse the chain into one chunk step** with a `CompositeItemProcessor`. Mechanical, removes the
question entirely, and matches how a Spring Batch developer would write it. The cost is that the
design's step boundary disappears from the artifact: restart granularity, step metrics and the
`not_generated` reporting all become coarser than the design says, and a reviewer comparing
`design.json` to the running job finds a different number of steps than the one they approved.

**(b) Render an in-memory staging bean** — a class that is both the first step's writer and the
second's reader. Keeps the declared steps, and is exactly what the hand-written stopgap did. The
cost is real and is not hidden: it is **not restartable**. A job that fails after step 1 restarts
with an empty staging bean and step 2 processes nothing.

**(c) Render a staging table.** The restartable answer, and the one ADR-0019's PostgreSQL target
implies. It needs a schema for a type that corresponds to no copybook, a migration, and a decision
about lifecycle — none of which the design carries, and all of which would be invented here.

## Decision

**Take (b) for the chain, and refuse the aggregate.**

**The chain renders as in-memory staging, and the generated class says so.** The limitation is
written into the artifact's own Javadoc rather than into a document nobody reads next to it —
`ADR-0030`'s bound 2 applied one level down. (b) over (a) because the design's steps are what a human
approved at the gate, and a renderer that quietly delivers a different number of them makes the
approved artifact and the running job two different things. (b) over (c) because a staging table is
a schema decision, and inventing one is the class of guess this repo refuses everywhere else.

**A step whose input is an aggregate is not rendered at all.** `unrenderable` is the honest state:
the grouping key, the summed field and the ordering are facts the design does not carry, and a
renderer that picked them would be choosing business semantics. The rendered job **still names the
step in its chain**, so the missing bean is a startup failure that names it rather than a step that
silently does not run.

**The job requires every step it names.** The rendered configuration takes the available `Step`
beans and looks each declared step up by name, failing loudly on a missing one. That is what lets a
rendered job include a hand-written step without the renderer knowing anything about it — and what
makes an unrendered step impossible to forget.

**Chunk size is not a COBOL fact.** It is rendered as a constant with a comment saying so. Nothing
in the source implies one, and a batch size is a performance decision for whoever runs the job.

## Consequences

**Good.** The job, its steps, the infrastructure beans and the chain handoff are all rendered for
`CBACT04C`, leaving one hand-written step — the one with an unanswered design question behind it.
The round trip continues to measure the same thing and the qualifier narrows again.

**Accepted cost, stated where it bites.** The rendered staging bean makes the chain
non-restartable. For a job over 94 records that is a theoretical cost; for a real migration it is
not, and (c) becomes the right answer the moment restartability is a requirement rather than a
property. The generated Javadoc carries that sentence so it is visible at the place it applies.

**What this does not decide.** Whether the aggregate ever becomes renderable. That needs a way for
the design to express a control break — which is a `solution_architect` contract question, not a
rendering one, and is the honest remainder of G31 rather than a detail of it.
