# ADR-0068: A step's structure is read from the design, not inferred from its types

## Status

**Accepted** (2026-09-06). Extends
[ADR-0066](0066-generate-renders-the-job-wiring-and-the-stopgap-retires.md), whose wiring is live in
`v0.4.1` and whose verification never met a design a model wrote — see
[ADR-0069](0069-a-pipeline-that-consumes-model-output-is-verified-against-model-output.md), which is
how these defects were found and what stops the next one.

Does not disturb [ADR-0027](0027-the-account-break-becomes-a-second-pass-over-pre-aggregated-items.md),
[ADR-0032](0032-a-rendered-job-names-every-step-and-stages-what-crosses-a-boundary.md) or
[ADR-0062](0062-a-step-must-be-able-to-return-what-it-computes.md); it is the wiring layer catching
up with what they already put in the contract.

## Context

`generate` reported `wiring: refused` for run `step54b-cbact04c-20260905-211254`:

```
UnrenderableReaderError: step 'postAccountInterest' needs exactly one driving stream
and its input resolves to 0 (none)
```

The message is accurate and names nothing that mattered. Behind it were three separate defects, and
they are the same defect: **the wiring layer answered structural questions by inspecting type
shapes, and the design already stated the answers.**

### 1. What an aggregation sums

`aggregation_blockers` asked whether the upstream type could reach the break key *and the record
column the accumulated value lands in*, resolving both against entity fields only.

ADR-0062 gave a composite `computed_fields`. ADR-0063 requires the row-grain item to carry
`WS-MONTHLY-INT` and **not** the account total. A design obeying both returns the value itself, and
puts nothing called `TRAN-AMT` on that stream:

| upstream type | blockers, before |
|---|---|
| `AccruedCategoryInterest` — has the break key **and** the value | `['TRAN-AMT']` |
| `Tran` — has the column, not the account id | `['TRANCAT-ACCT-ID']` |

So `aggregation_source` returned `None` for a step declaring a control break, `render_job_wiring`
fell through to `_has_file_source`, and `render_item_reader` correctly refused to iterate an
in-memory aggregate. **The check refused the design ADR-0063 exists to require** — that ADR's own
words: *a check that refuses a correct design is worse than no check.*

### 2. What a step is

`plan_steps` has never read `role`. The live design decomposes `CBACT04C` the way the COBOL is
written — a `tasklet` of five file OPENs and a `reader` of `1000-TCATBALF-GET-NEXT`, both typed
`TranCatBal -> TranCatBal`, around the steps that do the work. Planned as chunk steps, each demanded
its own `ItemReader<TranCatBal>` beside the step actually driving the file, and `render_file_bindings`
refused the job for the collision — naming the two colliding steps and unable to name why they were
both there.

`unobtainable_inputs` already states both limits in one sentence — *"a reader's and a writer's
outputs are bound by `READ ... INTO` and `WRITE ... FROM`, and a tasklet has no item at all"* — and
already names this exact shape, the open/close tasklets of `CBACT01C` and `CBCUS01C`.

### 3. What a file can supply

`_has_file_source` asked only whether every entity a step's input carries has an access path. A
`computed_fields` entry is a working-storage value a step computes, and **no file holds one**. So
`AccruedCategoryInterest` was reported file-readable and `render_item_reader` produced:

```java
new AccruedCategoryInterest(toTranCatBal(record), toAccount(accountRecord), toCardXref(cardxrefRecord))
```

three arguments for a four-component record — uncompilable Java, emitted with no diagnostic, by the
module whose docstring says it refuses everything it cannot derive.

### The class, counted honestly

`CLAUDE.md` records *a fact the deterministic layer already held, dropped one step before its
consumer* five times (G21, G24, G26, G28, G30); ADR-0062 was the sixth and ADR-0063 the seventh.
These are three more, and ADR-0063 already said what the rule demands: **adding an n-th instance fix
does not close the class.** What is different here is that all three were in one layer, answering
one kind of question, so there is a boundary to draw rather than another patch.

## Decision

**Where the design states a structural fact, the wiring reads it. Where it does not, the wiring
refuses and names what was missing.** Concretely:

1. **`role` decides whether a step is a chunk step.** `tasklet` and `reader` are skipped with the
   role in the reason, and dropped from the chain as well as from the plan — leaving one as
   someone's `previous` would let a step read from a reader nothing is rendering. `processor` and
   `writer` are planned as before. Skipped, not refused: the design is recording real COBOL, and in
   Spring Batch a file open and a record read are the item reader's own lifecycle.
2. **A composite carrying `computed_fields` is not file-readable.** `_has_file_source` answers
   `False`, so `plan_steps`, `render_job_configuration` and `java_file_bindings` all route the step
   to the stream that computes the value. `render_item_reader` additionally refuses outright and
   names the computed fields, because the two fail differently: the predicate keeps the pipeline
   correct, and the refusal keeps a *new caller* from re-creating a constructor that silently loses
   an argument.
3. **The value an aggregation sums has the two declared forms it actually has** — carried as a
   `computed_fields` entry, or landed in a record column — tried in that order. Same number, two
   declarations. A design carrying it both ways resolves exactly as it did before.

**One locator serves all of them.** `locate_item_field` moves to `java_reader`, the lowest of the
three layers, and both callers use it instead of `java_job` and `java_aggregation` each walking a
composite's components their own way. That duplication is what let the two disagree about whether a
type could reach a field.

## Consequences

**A control-break step's aggregating reader renders for a live design**, and the total goes into the
group item's accumulator computed field where one is declared — no record to copy and no column to
overwrite. Rendered from `step54b`'s design:

```java
BigDecimal key = item.categoryBalance().trancatAcctId();
totals.merge(key, item.monthlyInterest(), BigDecimal::add);
...
new AccountInterestPosting(first.account(), total)
```

**A row-grain computed field on a group item is now refused** rather than filled from the group's
first record. That is step 51's defect arriving from the other direction, and it would have looked
right: one row's number in a field named for a total.

**The rendered javadoc no longer claims a `MOVE` the render did not use.** The old text always said
the value is moved into `TRAN-AMT` and that the sum of a group's `TRAN-AMT` is the accumulator. True
of the program, and no longer a description of the reader underneath it — the same class of wrong
sentence as the accumulation javadoc ADR-0063 was written about.

**Three steps of `CBACT04C` are now reported as skipped where they were previously rendered wrong.**
`skipped_steps` is not a failure list and a reviewer must still read it; what changed is that the
entries now carry a structural reason (*"its role is 'tasklet', which has no item"*) rather than a
type-shaped one.

**This does not make the live design's project compile.** It types `writeInterestTransaction` and
`postAccountInterest` as `writer`, and `generate` renders a body only for `processor` (ADR-0023,
G27), so the job configuration injects two classes the pipeline will never produce. ADR-0027 settled
that a pre-aggregated posting step is an ordinary per-item transform. **That is a refusal to make
where the design is produced**, the same shape as ADR-0059, ADR-0062 and ADR-0063, and it is
deliberately not made here: this record is about the wiring reading what the design says, and that
one is about the design saying something wrong.

## Alternatives considered

**Teach `render_item_reader` to fill computed fields.** Rejected on the fact rather than on taste: a
computed value is produced by a processor and is in no file, so there is nothing for a reader to
read. Filling it would mean inventing a value or re-deriving the arithmetic, and the second is the
processor the step already has.

**Skip `writer`-role steps too, for symmetry with `reader`.** Rejected: it would drop
`postAccountInterest`, which is the control break, and losing an account total silently is precisely
the failure ADR-0063 records. A writer step carries an item; a reader step's item is its own arrival.

**Keep two locators and add a test that they agree.** That is the shape already used for
`aggregating_reader_class_name`, where two lines of naming are cheaper than a lazy import. It does
not transfer: this walk is thirty lines with two ways to reach a field, and the test would assert
agreement on the cases someone thought of.
