# ADR-0040: A step that reads its own writes declares it, rather than being detected

## Status

**Accepted** (2026-08-21). Schema **3.8.0**. Acts on
[ADR-0039](0039-cbtrn02c-posting-is-declared-and-refused-because-its-decision-reads-its-own-writes.md),
which measured the problem and named the missing contract fact as the reason it stopped there.

## Context

ADR-0039 established, from `CBTRN02C`'s own output, that its acceptance decision reads account state
its posting writes: judged per item, **30 of its 43 rejections disappear** and it writes 287 records
where the program writes 257 — every one of them individually correct, so only a count sees the
difference.

That ADR refused the step in **prose**. A refusal that lives in a document is one nobody's pipeline
enforces: `run_generate` would still have rendered the step for any design that declared it, and the
result would have compiled, run, and produced a plausible file. The gap between "we decided not to
do this" and "this cannot happen" is exactly the gap G21 kept reappearing in.

Making it mechanical needs a fact, and the question is where the fact comes from.

## Decision

**`BatchStepDesign.reads_own_writes: bool = False`, declared on the step — not derived from the
program.** `run_generate` reports such a step `not_generated` with a reason naming the cause and its
paragraphs, through [ADR-0023](0023-a-step-this-pipeline-does-not-render-is-reported-not-dropped.md)'s
existing reporting path.

**Why declared.** The derivable condition — a file that is both a keyed lookup and written back — is
already expressible from `FileAccessPath` alone, and it is **also true of `CBACT04C`'s account
writer**, which is correct as it stands because ADR-0027 aggregates its input to one item per
account. A detector on that condition would refuse a step that works today and is measured green at
500 of 500 and 598 of 600.

The condition that actually separates them is *can two input items reach the same written key*, and
that depends on the driving stream and any aggregation between it and the write. It is derivable in
the two cases this repo has, and deriving it from two cases is how a rule gets fitted to a sample.
ADR-0039 declined to build that detector; this ADR declines for the same reason and puts the fact
where a human states it and a reviewer can check it against the source.

**Why the default is `False`.** A step that says nothing renders exactly as it did before the field
existed, so `CBACT04C` is untouched — no regression risk to a working round trip from a field added
for a different program. This is deliberately *not* `guard_condition`'s required-but-nullable shape
(ADR-0022): there, silence and "unconditional" had to look different because the field records LLM
judgment about every step. Here the safe reading and the common reading are the same one, and making
every existing design restate it would be churn without a finding behind it.

## Consequences

- **The refusal is now enforced rather than documented.** A design declaring such a step gets an
  outcome, a count and a gate item; it cannot silently produce the wrong set of records.
- **It is a refusal, not a capability.** Nothing renders these steps yet. The round-trip metric is
  unchanged at `1 of 4`, and `CBTRN02C` still has no generated posting path.
- **The flag is only as good as whoever sets it.** A design that omits it on a step that needs it
  gets the 287-record failure ADR-0039 measured, and nothing in the pipeline will say so. That is
  the price of declaring over detecting, and it is the reason the field's docstring carries the
  numbers rather than a description: whoever sets it should see what it costs to get wrong.
- **A rendering decision is deliberately not made here.** How such a step *should* run — a shared
  working set the reader consults and the writer updates, at a chunk size that makes each write
  visible to the next read — is the next decision, and it changes what `CHUNK_SIZE` means from a
  performance knob into a correctness constraint. It gets its own record.
