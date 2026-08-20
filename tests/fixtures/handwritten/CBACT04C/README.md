# Hand-written job wiring for `CBACT04C` — and what the design could not supply

**This is a stopgap, and it is labelled one on purpose** ([ADR-0030](../../../../docs/adr/0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md)).
`generate` renders domain records, `ItemProcessor`s and an equivalence test, and used to render no
reader, no writer, no step and no job -- which is why a generated project compiled and could not run
(gap **G31**).

**The reader is no longer here.** `rendering/java_reader.py` renders it from `design.json`, and the
round trip that measures this program runs on the rendered one: `500 of 500` transaction fields and
`598 of 600` account fields, the same numbers the hand-written reader produced before it was deleted.
The stopgap worked as ADR-0030 intended -- writing it by hand is what produced the findings below,
and each of those became a fact in the contract rather than a guess in a design session.

**The writers are no longer here either.** `rendering/java_writer.py` renders both -- an appending
writer for the interest transactions and an in-place `REWRITE` writer for the account master -- so
the candidate the round trip compares is now COBOL's own record format, produced by generated code
and parsed with the same layout the oracle is read with.

**What is still hand-written**, and what the qualifier on the round-trip number refers to: the job
bean, the three step beans, the staging between steps 1 and 2, and the aggregating reader for the
account-posting step.

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

## Findings — what a rendered reader had to carry

Each is marked with where it ended up. **F1, F2 and F4 are closed**: the renderer emits them from
design.json, and the round trip proves the emitted code produces COBOL's own answers.

### F1 · `design.json` gives widths, never offsets or record lengths — **closed**

`DomainField` carries a `PIC X(n)` length or a numeric precision and scale, in copybook order. It
carries **no byte offset, no record length, and no `FILLER`**. `CobolFixedWidth` and the layout
constants in `TranCatBalWithRateItemReader` therefore assume fields are contiguous from byte zero —
true for `CVTRA01Y`, `CVTRA02Y`, `CVACT01Y` and `CVACT03Y` only because every `FILLER` in them is
trailing, and false the moment a copybook has one in the middle.

Record lengths (50, 50, 300, 50) are in the copybook comments and in no contract.

**Answered by G31 stage 3 (PR #65).** `DomainField.byte_offset` and `DomainEntity.record_length`
now carry the layout, computed over every declaration including `FILLER` -- so an interior `FILLER`
shifts what follows instead of silently mis-slicing it, and a record whose width cannot be
determined (`COMP-3`, a `REDEFINES`) is refused whole rather than returned partial. The computed
offsets are checked against the differential's own hand-derived layouts, which COBOL's output has
already validated across 1,098 fields.

### F2 · Nothing in the design says where data comes from — **closed**

`CompositeType` declares that `TranCatBalWithRate` is composed of `TranCatBal`, `DisGroup`,
`Account` and `CardXref`. It does not say which is the driving stream, which are keyed lookups, or
what the keys are. All of that came from `CBACT04C`'s `FILE-CONTROL` — `TCATBAL-FILE` is
`ACCESS MODE IS SEQUENTIAL`, the other three are `RANDOM` with a declared `RECORD KEY` — which is
exactly the parse ADR-0030's option (c) proposes and `cobol_parser` does not do today.

**Answered by G31 stage 2 (PR #64).** `design.json` now carries `file_access_paths`: per program,
which file yields which entity, whether it is a stream or a keyed lookup, and the key it is actually
read by -- including the alternate-key case this finding is about. What remains unanswered here is
the *layout* half (F1) and the writer side, since only `READ ... INTO` is parsed.

The reader also reads flat files rather than PostgreSQL, and uses a `ResourcelessJobRepository`
rather than a `DataSource`. That is a divergence from ADR-0019's target, taken because a container
and a schema would add failure modes to a run whose subject is the generated logic. It does not
change what is measured: the processors receive the same records either way.

### F3 · A declared step chain with nowhere for the intermediate to live

`computeInterest` outputs a `TranWithContext` and `completeTransaction` consumes one, so the value
crosses a step boundary. `TranWithContext` corresponds to no copybook and no table, and ADR-0019's
target persists `Tran`. `TranWithContextStaging` holds it in memory, which is **not restartable** —
a real limitation of this stopgap, and a question a renderer has to answer rather than inherit.

### F4 · Business logic that the design leaves to wiring — **closed**

**Both are now rendered.** `LookupKeyPart` carries the `'DEFAULT'` retry as a marked second
assignment with a literal source, so the generated reader emits the fallback probe; and the abend on
a missed lookup is rendered as a throw, for the reason below. What follows is the original finding.

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

### F6 · The account half is where the design's step chain stops being enough

`CBACT04C` writes **two** files, and the second one needs the interest summed per account before
`1050-UPDATE-ACCOUNT` can post it. ADR-0027 makes that summation infrastructure -- so
`AccountInterestPostingItemReader` aggregates the first step's staged output by account key. Nothing
in `design.json` says that this step consumes an *aggregate* of an earlier step's output rather than
a stream of its own: the step declares `AccountInterestPosting` in and `Account` out, and the
grouping key, the summed field and the ordering are all facts the wiring supplied. The live model
reached the same conclusion from the COBOL alone, writing that the total *"has to be supplied by
whatever step implements the account-break/update logic"*.

## What the first run found

`TRAN-SOURCE` is `PIC X(10)`. The completion body wrote a bare `"System"`, so **fifty records
disagreed with COBOL on one field** while every amount matched. The eval judge had flagged that same
defect in the real PR #44 body (audit R2.27) and the copybook had said so all along; this is the
first check in the repo that could fail on it, because the equivalence test asserts on `tranAmt`
alone.


## What the account half found

`598 of 600` fields match, **with nothing excluded** -- the account record gives up no field the way
`transact.dat` gives up `TRAN-ID` and its timestamps. The two that differ are both on the **last
account**, and both are fields `1050-UPDATE-ACCOUNT` writes:

| field | candidate | COBOL |
|---|---|---|
| `ACCT-CURR-BAL` | 2060.06 | 2041.30 |
| `ACCT-CURR-CYC-CREDIT` | 0 | 1549.30 |

**One cause, and it is COBOL's.** The main loop is `PERFORM UNTIL END-OF-FILE = 'Y'` with the
account-break post in the `ELSE` of `IF END-OF-FILE = 'N'`, so that branch is unreachable and the
final account is never posted. The paragraph does exactly three things -- add the interest, zero the
cycle credit, zero the cycle debit -- and the divergence set is exactly its write set, minus the one
field that was already zero. That is what makes the diagnosis a measurement rather than a story.

**Reproducing the defect in the wiring was available and refused.** Skipping the last account would
have made this comparison green by encoding a bug, and the number would then have been an artifact
of the wiring rather than a fact about the generated logic. `assert_account_half_matches_except_the_last`
pins the shape instead: one record, fields limited to that paragraph's writes, the balance
difference equal to that account's uncredited interest as read from the transaction oracle.
