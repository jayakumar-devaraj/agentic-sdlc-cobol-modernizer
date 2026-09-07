# 22. The job opens every file its COBOL names

Verified 2026-09-07. Covers
[ADR-0076](../../adr/0076-a-keyed-lookup-belongs-in-the-reader-of-the-step-that-uses-it.md).

Read [21 — the pipeline runs the job it generated](21-the-pipeline-runs-the-job-it-generated.md)
first. That entry ended with a job that ran — and stated in its own Consequences that on both live
designs it would run without three of `CBACT04C`'s five files.

**The claim, in one line: the shape that stranded them is now refused with the move named, the
folded shape binds five of five and compiles under Maven, and the renderer needed no new
capability to do it.**

**What this does not verify: no live run has been made against a folded design, so there is still
no correctness verdict for one.** That is the next thing, and it is stated here rather than left to
be inferred from a green suite.

## What was measured, before anything was changed

Rendering all three live designs offline — `cbact04c-design.json`, `cbact04c-design-step56.json`,
`cbact04c-design-step58.json` — gave the same three properties and no others:

```
cobol.file.tcatbalf = ${cobol.file.base}/TCATBALF
cobol.file.acctfile = ${cobol.file.base}/ACCTFILE
cobol.file.transact = ${cobol.file.base}/TRANSACT
```

`XREFFILE` and `DISCGRP` declared, resolvable, bound to nothing. `plan_steps` skipped nothing and
the wiring reported every renderable step wired.

### The two stranded lookups are not one problem

The finding that changed the fix. Only one of the two enrichment steps reads a file:

| step | shape | reads | unsupplied |
|---|---|---|---|
| `resolveAccountAndCardXref` | `TranCatBal → TranCatBalWithAccount` | file | Account, CardXref |
| `resolveInterestRate` | `TranCatBalWithAccount → RatedCategoryBalance` | chain | DisGroup |

So *widening the reader* — the candidate that suggests itself, and the one the step-59 brief
proposed — binds `ACCTFILE` and `XREFFILE` and can never bind `DISCGRP`, whose step reads its
predecessor's store under [ADR-0074](../../adr/0074-a-mid-chain-step-reads-its-predecessor-not-a-file.md).
It would have bound four of five and looked like a fix.

### The renderer already supported the fix

Folding the resolve steps into the step that consumes their output, through `render_job_wiring`
with no code changed:

```
bound: acctfile, discgrp, tcatbalf, transact, xreffile
skipped: []
ComputeMonthlyInterestItemReader(Path tcatbalf, Path acctfile, Path xreffile, Path discgrp)
```

Constructor order derived, not declared: `DISCGRP`'s key comes from `ACCT-GROUP-ID`, an account
field, so the account read is ordered first.

## Why it is refused rather than skipped

Asserted as a measurement because the ADR rests on it. With the joins left out of the `step58`
design:

```
renderable: computeMonthlyInterest, computeFees, writeInterestTransaction, postAccountInterest
stores filled: ComputeFeesStaging, ComputeMonthlyInterestStaging
stores read  : ComputeFeesStaging, ComputeMonthlyInterestStaging, ResolveInterestRateStaging
UNFILLED stores read: ResolveInterestRateStaging
bound: cobol.file.acctfile, cobol.file.transact
```

The job loses `TCATBALF`, its own driving input, and reads a store whose producer is gone — and it
still compiles and starts. `test_skipping_the_join_would_leave_a_store_nothing_fills` pins both.

**The existing property test is blind to this.**
`test_every_store_a_step_fills_is_read_by_the_step_after_it` asserts no store is *filled* that
nothing reads; the dangerous direction is a store *read* that nothing fills, and it passes either
way. That is why a new test exists rather than a strengthened old one.

## The refusal was proven in both directions

```bash
# unsupplied_components neutered to return []
.venv/Scripts/python.exe -m pytest tests/unit/test_java_job.py -q \
  -k "join or stranded or folded or store_nothing"
```

`4 failed, 1 passed`. The four that fail are the check firing, the folded design binding five of
five, the skip naming the files, and the measurement above.

**The one that does not fail is stated rather than glossed.**
`test_a_component_the_step_computes_is_not_a_stranded_lookup` asserts the check does *not* fire on
`computeInterest`, which builds the `Tran` it carries out. Neutering cannot break it: it guards
against a broader predicate, not against no predicate. It earned its place anyway — the first version
of this check counted every added component and refused the interest calculation itself.

## The suite

```bash
.venv/Scripts/python.exe -m pytest tests/unit tests/contract -q
```

`938 passed, 3 skipped in 77.89s`.

```bash
export JAVA_HOME="C:/Program Files/Eclipse Adoptium/jdk-25.0.4.7-hotspot"
export PATH="$JAVA_HOME/bin:$PATH"
.venv/Scripts/python.exe -m pytest tests/integration/test_a_live_design_wires.py -q
```

`12 passed in 262.15s` — a real Maven build of the folded design, plus
`test_the_design_as_written_is_refused`, which holds the pinned design up to the refusal.

## What this cost elsewhere, reported rather than absorbed

**9 unit test cases changed behaviour, plus every test in
`tests/integration/test_a_live_design_wires.py` that depends on the pinned design rendering** --
7 test functions edited in all. None of them was wrong before. All three live designs carry the
refused shape, so every test that rendered one saw a skip that was not there previously.

An earlier, cruder predicate -- *any* component the output composite adds -- failed 39 tests and
refused the interest calculation itself. `is_keyed_lookup` is what took it to 9, and the difference
between those two numbers is the whole value of the distinction.

- The fixtures were **not** rewritten. They are the record of what models actually wrote, and
  rewriting one to make a test pass would destroy the only evidence for the shape this record
  refuses. `tests/support/joined_design.py` folds them in the open, and the fold is a *deletion*.
- `tests/integration/test_a_live_design_wires.py` now builds the folded design.
- ADR-0072's tests assert the *ordering* skips are cleared, not that nothing is skipped. A design can
  now be right about ordering and wrong about the join, and asserting an empty list would make those
  one claim.

### One regression this introduced and fixed

Adding a skip that no reordering clears made **ADR-0072's refusal go silent**.
`_a_move_that_strands_no_step` searches for a move after which `plan_steps` skips nothing, so a
permanent join skip meant no move ever qualified and the ordering refusal stopped firing on the very
design it was written for. Caught by `test_a_step_ordered_before_its_input_exists_is_refused`, which
failed with `DID NOT RAISE`. The search now weighs only the skips a move could fix.

**This is why the refusal is reported by `plan_steps` and raised by `render_job_wiring`, not raised
by both.** `plan_steps` is also the oracle `solution_architect` consults to tell a model what to
change, and that advice is the only thing that fixes this shape at its source.

## Corrections to the record

- **The step-59 brief's option 2 — "widen the reader" — does not reach `DISCGRP`.** Its own table
  gets all four files only by collapsing both joins into the first step, which is folding, not
  widening. Measured above.
- **Option 1 was described as cheap.** It is honest, and it is not cheap: 7 test functions, the
  integration module's fixtures, and the ADR-0072 regression. The cost is in the fixtures all
  carrying the same shape, which is also the strongest evidence that the shape is natural to write.
