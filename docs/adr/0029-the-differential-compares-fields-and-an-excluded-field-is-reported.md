# ADR-0029: The differential compares fields, and an excluded field is reported rather than disqualifying

## Status

**Accepted** (2026-08-12), via the decision request in PR #55. Settles the two questions
[ADR-0028](0028-what-the-round-trip-metric-requires-and-why-it-has-not-moved.md) accepted without
answering, and does so **before** the spike rather than by it — a spike that diffs whole files
without saying so has decided the first question by accident, which is the drift ADR-0028's finding 4
identified.

> Decided through this pull request, per the mechanism ADR-0028 established: approving accepts,
> requesting changes rejects or amends, and the Status flips before merge.

## Context

ADR-0028 accepted paying for a recorded oracle and flagged two things it did not settle:

1. Is the comparison **byte-for-byte** or **field-for-field**?
2. Does an **excluded field** — `TRAN-ID`, unpopulated by ADR-0026's decision — disqualify a
   round-trip?

Both bite on the first comparison the spike performs, and each has a cheap wrong answer available.

### The finding that decides question 1

**The target does not produce a file to compare.** ADR-0019 chose PostgreSQL: *"The Spring Batch jobs
run against PostgreSQL"*, with CardDemo's data files as a one-time migration source. Nothing in
`rendering/` emits a `FlatFileItemWriter` or any fixed-width serialiser. COBOL writes 350-byte
`TRANSACT` records and rewrites 300-byte `ACCOUNT` records; the generated Java writes rows.

So byte-for-byte would require **building a serialiser that exists only for the test** — target code
whose sole consumer is the assertion about it. That is the shape of a check that cannot fail: it
would be written to match whatever the comparison needed.

**And byte equality is unreachable today by accepted decision, not by defect.** ADR-0026 leaves
`TRAN-ID` unpopulated and supplies **one run timestamp** where COBOL reads a per-record clock with
millisecond precision. While those decisions stand, byte-for-byte cannot hold. Choosing it is
therefore choosing that the metric can never move — which is a defensible position, but it should be
chosen knowingly rather than inherited from the fact that `CobolText.pad` exists.

## Decision

### 1. Field-for-field, against COBOL's parsed output records

The oracle is parsed with the same copybook-derived layout `tools/data_loader.py` already uses, and
compared field by field against the rows the generated job wrote.

**Each field is compared at its full declared width**, so `PIC X(50)` compares fifty characters and
padding still counts. What is excluded is the **record framing** — byte offsets, file layout, the
serialisation — not field contents. G28's width work, `CobolText.pad`, and the `fixed_width_text`
criterion all remain load-bearing; this decision does not soften them.

### 2. An excluded field is reported, not disqualifying — under three constraints

A field the pipeline cannot produce does not fail the round-trip, on ADR-0023's precedent: a step
this pipeline does not render is **reported, not dropped**. The same posture applied to a field.

Field-level comparison with exclusions is also exactly how a differential becomes toothless, so the
exclusions are constrained rather than trusted:

1. **Every exclusion cites an accepted ADR.** `TRAN-ID` cites ADR-0026. A field cannot be excluded
   because it is inconvenient — only because a decision on the record says it is unproducible.
2. **The exclusion list is committed data, reviewed like the oracle.** Not a flag, not a filter
   expression in a test.
3. **The result reports the ratio.** A pass says *"11 of 13 fields matched; 2 excluded by ADR-0026"*.

### 3. The metric carries its qualifier

`1 of 4` is never reported bare. It is **`1 of 4 (11/13 fields)`**, so the exclusion is visible
wherever the number is. A reader who sees only the headline still sees that something was excluded.

## Consequences

**Good.** The metric becomes movable without being weakened, and the weakening that field-level
comparison usually brings is priced instead of hidden. The spike now has an unambiguous target, and
cannot settle either question by writing a convenient assertion.

**ADR-0021's trigger 2 has NOT landed, and G23 stays 🟡.** This decision explicitly declines
byte-for-byte, which resolves the ambiguity ADR-0028 recorded. The byte-fidelity work already done is
not wasted — it is what makes *field* comparison meaningful at full width — but no decision to
compare records byte-for-byte has been taken.

**Accepted cost, and it is the real one.** A field-level pass is a weaker claim than byte equality.
If a downstream consumer ever reads the fixed-width file — another COBOL program, a mainframe
interface — this is insufficient, byte-fidelity becomes the target, trigger 2 lands, and G23 returns
to 🔴. **That is a change of requirement, not a defect discovered later**, and it is written here so
it reads that way when it happens.

**Exclusion creep is the failure mode to watch.** The ADR-citation rule is what stands between this
and a differential that passes by excluding whatever disagrees. If a future exclusion cannot name a
decision, the honest response is to fail the comparison and open the decision, not to add the field
to the list.

**What this does not do.** It generates nothing and asserts nothing. `TRAN-ID` remains unpopulated,
and the round-trip metric stays at `0 of 4` until the spike runs.
