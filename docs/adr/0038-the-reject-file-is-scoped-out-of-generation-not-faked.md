# ADR-0038: `CBTRN02C`'s reject file is scoped out of generation rather than given an invented type

## Status

**Accepted** (2026-08-20). Found alongside
[ADR-0037](0037-a-file-written-both-ways-renders-as-an-upsert.md), by rendering `CBTRN02C`'s five
write targets. Four render; this is the fifth.

**Counts superseded in part by [ADR-0047](0047-the-corpus-sign-representation-is-converted-inside-the-oracle-pipeline.md)** (2026-08-21). The corpus produces **38**
rejects, not the 43 this record cites: five of those rejections were decided on amounts missing a
digit. The scoping decision is unchanged — a real output of the program is still a real output at
38 — and nothing else here depends on the number.

## Context

`CBTRN02C` writes rejected daily transactions to `DALYREJS-FILE`:

```cobol
WRITE FD-REJS-RECORD FROM REJECT-RECORD          *> line 451
```

`REJECT-RECORD` is a `WORKING-STORAGE` `01` (line 176) — `REJECT-TRAN-DATA PIC X(350)` followed by
`VALIDATION-TRAILER PIC X(80)`, the failed record plus the reason it failed. It is **not a
copybook**, and [ADR-0010](0010-unified-design-shape-and-the-deterministic-llm-split.md) promotes
copybook-sourced fields only, so `build_domain_entities` produces no `Reject` entity and
`render_item_writer` refuses:

```
UnrenderableWriterError: the design has no domain entity 'Reject'
```

**The refusal is correct and is not the problem.** The file access path names `Reject` as the
written entity because the parse can see the `WRITE`; the design has no such type because the merge
invariant excludes it. The renderer declines rather than inventing a shape, which is the designed
behaviour and is why this is actionable at all.

**It is a real output, not an edge case.** The corpus's 300 daily transactions produce 43 rejects —
`CBTRN02C: TRANSACTIONS PROCESSED :000000300; TRANSACTIONS REJECTED :000000043` in the oracle's own
provenance. A generated job that silently dropped them would be wrong about one transaction in
seven.

## Decision

**The generated job posts transactions and does not write the reject file. This is recorded as a
stated divergence, in the manner ADR-0021 requires and
[ADR-0026](0026-job-parameters-reach-a-processor-and-the-per-run-counter-does-not.md) already
used for `TRAN-ID`: named, bounded, and not faked.**

Concretely: no step is declared for the reject path, so nothing is silently skipped inside a step
that claims to be complete — [ADR-0023](0023-a-step-this-pipeline-does-not-render-is-reported-not-dropped.md)'s
rule that a non-generated step reports itself continues to apply if one is ever declared.

## Options considered

**(a) Render it as an opaque 430-byte record.** `REJECT-RECORD` really is just the input record plus
a trailer, so a writer could pass bytes through with no typed entity. Rejected: it introduces an
untyped record to a renderer whose whole guarantee is that every field it writes traces to a `PIC`,
and the first thing anyone would ask of the output — *why was this rejected* — lives in the 80-byte
trailer that this option leaves as undifferentiated bytes.

**(b) Promote program-local `FD` and `WORKING-STORAGE` `01` layouts to domain entities.** This is
the real fix and it closes the master plan's open issue 11 (`FD` record layouts are parsed but do
not become domain entities). Rejected **for now, not on the merits**: it amends ADR-0010's merge
invariant, which is load-bearing for every program's entity set, and doing that as a side effect of
wanting one output file is how an invariant gets weakened without anyone deciding to weaken it. It
wants its own decision with its own evidence.

**(c) Give `Reject` a hand-authored entity in the design.** Rejected outright: a type hand-written
into `design.json` to make a renderer succeed is a fact no parse produced, and the artifact would no
longer be derivable from the source — the property the whole provenance chain rests on.

## Consequences

- `CBTRN02C`'s generated job covers the posting path. **The 43 rejects are not written**, and any
  round-trip claim for this program must say so alongside the number, the way "wiring hand-written"
  travels with `1 of 4`.
- The comparison targets for a `CBTRN02C` round trip are therefore the **balance file, the account
  file and the transaction master** — three of its four outputs. `dalyrejs` is out of scope for the
  measurement by this decision rather than by omission.
- **Validation still runs.** What is scoped out is *writing the reject record*, not deciding that a
  transaction is invalid: a rejected transaction must still be excluded from the three files that
  are compared, or the posting totals would be wrong. This is the sharp edge of the decision and the
  reason it is written down rather than assumed.
- Option (b) stays the standing upgrade. If a second program needs a program-local record as an
  output — or if the reject reasons become something a demo has to show — that is the trigger to
  take it, and it should cite this ADR.
