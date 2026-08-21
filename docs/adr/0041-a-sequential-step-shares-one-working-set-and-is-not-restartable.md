# ADR-0041: A sequential step shares one working set, and is not restartable

## Status

**Accepted** (2026-08-21). The rendering half of
[ADR-0040](0040-a-step-that-reads-its-own-writes-declares-it-rather-than-being-detected.md), which
added the declaration and left this open deliberately.

## Context

A step declaring `reads_own_writes` cannot run the way every other rendered step runs. Today a
reader loads its lookups into private maps and a writer holds its output in another, and the two
never meet — which is correct exactly as long as what a step decides for one item is independent of
what it decided for the ones before.

`CBTRN02C` is not independent in that way. `1500-B-LOOKUP-ACCT` reads `ACCOUNT-FILE` to decide
whether a transaction fits under the credit limit; `2800-UPDATE-ACCOUNT-REC` rewrites that same
record when it posts. `2700-UPDATE-TCATBAL` does the same against `TCATBAL-FILE`. Item *n*'s
decision depends on items *1..n-1*'s writes, and ADR-0039 measured what ignoring that costs: 287
records written where the program writes 257, each of them individually correct.

## Decision

**One store per sequential step, seeded from the input files, shared by the reader and the writer,
flushed once at the end** — `rendering/java_working_set.py`. It holds every file the program both
reads by key and writes back, keyed where that file's own record key sits.

**Which files those are is derived; *that* the step needs them shared is declared** (ADR-0040). The
derivation is safe here in a way it is not as a detector: once a human has said this step reads its
own writes, "which of its files are read-modify-written" is a fact about the program rather than a
judgement about the step.

**The store interprets nothing.** Records go in and out as text; it parses no field and compares no
value. What a record *becomes* stays the processor's work, which is ADR-0019's line, and the tests
assert it rather than trusting it — no `BigDecimal`, no `CobolRecord.number`, no `compareTo` in the
rendered source.

**Chunk size becomes a correctness constraint for these steps, not a performance knob.**
`java_job`'s `CHUNK_SIZE` is documented today as "not a COBOL fact… a performance decision for
whoever runs the job". That stays true of every ordinary step and stops being true here: each item's
write has to be visible to the next item's read, so a sequential step renders at **chunk size 1**.
Stated as a consequence rather than left for someone to discover by raising a number and watching a
comparison drift.

## Options considered

**(a) A `Tasklet`.** COBOL's program *is* one sequential loop, and a tasklet is "run this code
once" — arguably the closest structural match, with no shared-state plumbing and no ordering
subtleties at all. Rejected because it dissolves the boundary this pipeline is built on: the reader,
the writers and the job stay rendered from `design.json` and only the body is model-authored, and a
tasklet makes the whole step one model-authored blob. The renderer would have nothing left to
guarantee, and the step's I/O would stop being reviewable as rendered code.

**(b) State in the writer only.** Cheapest change, and wrong: the writer runs at chunk end, so with
any chunk size above 1 the decisions inside the chunk never see the writes. It would appear to work
on small inputs and diverge silently on large ones — the exact failure profile ADR-0039 exists to
prevent.

**(c) State in the processor.** Puts mutation inside the model-authored region, where a body could
hold or drop it without the renderer knowing. It also makes the processor stateful, which is the
property ADR-0019 relies on everywhere else.

## Consequences

- **Not restartable, and it is written on the class.** The state lives for the length of the step;
  a job that fails half way through has written nothing. That is a real regression against Spring
  Batch's normal chunk semantics, accepted because the alternative is not "restartable" but
  "wrong" — COBOL's own run is not restartable mid-file either, and reproducing its output is what
  this is for.
- **No parallelism for these steps.** Partitioning a step whose items are order-dependent changes
  its output by definition.
- **Memory is the whole file, per read-modify-written entity.** Fine for this corpus (50 accounts,
  94 balance rows) and a real limit worth naming before someone meets it on a tenant-sized file.
- **`CBACT04C` is untouched.** Its steps do not declare `reads_own_writes`, so nothing about its
  rendering, its chunk size or its round-trip numbers changes.
- **The store exists and nothing consumes it yet.** Wiring the reader, the writer and the job to it
  is the next change; until then a sequential step is still reported `not_generated` by ADR-0040's
  refusal. Stated because this repo has shipped a helper that was written, tested and called by
  nothing before (G21), and the honest version of that is to say which half has landed.
