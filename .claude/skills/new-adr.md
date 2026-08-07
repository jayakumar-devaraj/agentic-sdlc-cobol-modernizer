---
name: new-adr
description: Scaffold a new ADR in docs/adr/ following this repo's Context/Decision/Consequences format and sequential numbering.
---

Create a new file `docs/adr/NNNN-descriptive-sentence-slug.md`, where `NNNN` is the next sequential
number after the highest existing ADR in `docs/adr/`, and the slug states the decision as a short
assertion (e.g. `the-specialist-is-a-subprocess-not-a-second-control-plane`), matching the
convention already in use — not a generic topic name.

Use this structure:

```markdown
# NNNN - <Decision as a short assertion>

## Context

<What forced this decision. Name the real constraint, defect, or trade-off — not a generic
"we needed to decide X" framing.>

## Decision

<What was actually decided. If alternatives were rejected, name them and say why, on their
merits — not just "we chose X".>

## Consequences

<What this costs, not just what it buys. State explicitly what the decision does *not* solve.>
```

Only create an ADR when the decision has a cost someone could reasonably dispute, or a defect had
a design cause — per `.claude/agents/design.md`. Link the new ADR from the README or matrix
section it constrains once it's written.
