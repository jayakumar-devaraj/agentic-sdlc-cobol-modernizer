# Hand-written job wiring for `CBACT04C` — and what the design could not supply

**This is a stopgap, and it is labelled one on purpose** ([ADR-0030](../../../../docs/adr/0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md)).
`generate` renders domain records, `ItemProcessor`s and an equivalence test. It renders **no reader,
no writer, no step and no job**, which is why a generated project compiles and cannot run (gap
**G31**). Everything here is what nothing renders, written once, for one program.

## The three bounds ADR-0030 put on it

1. **It never enters `templates/target-spring-boot-baseline/`.** It lives here, under
   `tests/fixtures/`, and `tests/system/test_hand_written_round_trip.py` copies it into a throwaway
   project. In the template it would silently join every generated project and make every future
   round-trip claim ambiguous.
2. **Every result measured through it carries two qualifiers.** `describe_result` in that test
   emits them: *wiring hand-written*, and *bodies scripted rather than model-authored*. A green
   result does not mean this platform generated a working program — it means generated business
   logic matches COBOL's output when placed in wiring a human wrote.
3. **It is written against `design.json`, and every fact the design lacked is recorded below.**
   That is what turns a stopgap into a requirements list for the renderer ADR-0030 names as the
   target (option (c): render readers from the COBOL's own `FILE-CONTROL`), drawn from practice
   rather than guessed in a design session.

## Findings — what a rendered reader will have to carry

### F1 · `design.json` gives widths, never offsets or record lengths

`DomainField` carries a `PIC X(n)` length or a numeric precision and scale, in copybook order. It
carries **no byte offset, no record length, and no `FILLER`**. `CobolFixedWidth` and the layout
constants in `TranCatBalWithRateItemReader` therefore assume fields are contiguous from byte zero —
true for `CVTRA01Y`, `CVTRA02Y`, `CVACT01Y` and `CVACT03Y` only because every `FILLER` in them is
trailing, and false the moment a copybook has one in the middle.

Record lengths (50, 50, 300, 50) are in the copybook comments and in no contract.

### F2 · Nothing in the design says where data comes from

`CompositeType` declares that `TranCatBalWithRate` is composed of `TranCatBal`, `DisGroup`,
`Account` and `CardXref`. It does not say which is the driving stream, which are keyed lookups, or
what the keys are. All of that came from `CBACT04C`'s `FILE-CONTROL` — `TCATBAL-FILE` is
`ACCESS MODE IS SEQUENTIAL`, the other three are `RANDOM` with a declared `RECORD KEY` — which is
exactly the parse ADR-0030's option (c) proposes and `cobol_parser` does not do today.

The reader also reads flat files rather than PostgreSQL, and uses a `ResourcelessJobRepository`
rather than a `DataSource`. That is a divergence from ADR-0019's target, taken because a container
and a schema would add failure modes to a run whose subject is the generated logic. It does not
change what is measured: the processors receive the same records either way.

### F3 · A declared step chain with nowhere for the intermediate to live

`computeInterest` outputs a `TranWithContext` and `completeTransaction` consumes one, so the value
crosses a step boundary. `TranWithContext` corresponds to no copybook and no table, and ADR-0019's
target persists `Tran`. `TranWithContextStaging` holds it in memory, which is **not restartable** —
a real limitation of this stopgap, and a question a renderer has to answer rather than inherit.

### F4 · Business logic that the design leaves to wiring

Two behaviours in the reader are not infrastructure:

- **The `'DEFAULT'` disclosure-group fallback.** `1200-GET-INTEREST-RATE` reads on the account's own
  group and, on file status 23, re-reads under `'DEFAULT'`. That is a business rule, and it is here
  because the design hands `computeInterest` an already-resolved `DisGroup`.
- **Abend on a missed lookup.** The COBOL aborts when an account, xref or group is missing. The
  reader throws for the same reason: substituting a zero rate would silently suppress a transaction.

A rendered reader must carry both, or they are lost with no signal.

### F5 · Spring Batch 6 details that cost a build each, recorded so the renderer does not rediscover them

- `MapJobRegistry` registers every `Job` bean itself in `afterSingletonsInstantiated`; registering
  the job explicitly throws `DuplicateJobException`.
- `TaskExecutorJobOperator` refuses to initialise without a `JobRegistry`.
- `JobLauncher` is deprecated for removal; `JobOperator` replaces it.
- The configuration is gated behind the `handwritten-wiring` profile because `BatchApplication`
  component-scans `com.modernized.batch`, and without the gate this wiring joins the context of
  every Spring Boot test in the generated project — bound 1 defeated through a side door. The
  baseline's own `BaselineStackTest` failed on the first run for exactly that reason.

## What the first run found

`TRAN-SOURCE` is `PIC X(10)`. The completion body wrote a bare `"System"`, so **fifty records
disagreed with COBOL on one field** while every amount matched. The eval judge had flagged that same
defect in the real PR #44 body (audit R2.27) and the copybook had said so all along; this is the
first check in the repo that could fail on it, because the equivalence test asserts on `tranAmt`
alone.
