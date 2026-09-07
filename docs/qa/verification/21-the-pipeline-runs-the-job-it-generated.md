# 21. The pipeline runs the job it generated

Verified 2026-09-07. Covers
[ADR-0075](../../adr/0075-the-pipeline-runs-the-job-it-generated.md).

Read [20 — the first live run under the ordering rule](20-the-first-live-run-under-the-ordering-rule.md)
first. That entry ended with a job that rendered 6 of 6 steps, compiled, and still returned
`Equivalence: NOT RUN` — because no phase ran it.

**The claim, in one line: a runner rendered from the design compiled against the generated class
names, started the generated job, and it reached `COMPLETED` — and the differential, pointed at the
project afterwards, returned a comparison instead of `not_run` for the first time.**

## What was run

```bash
export JAVA_HOME="C:/Program Files/Eclipse Adoptium/jdk-25.0.4.7-hotspot"
export PATH="$JAVA_HOME/bin:$PATH"
.venv/Scripts/python.exe -m pytest tests/integration/test_hand_written_round_trip.py \
  -k "wiring_produces_one_record or matches_the_cobol_oracle"
```

`2 passed, 7 deselected in 125.61s`. That test calls `run_generate`, so it now exercises the new
phase on a real Maven build rather than a fixture.

### The runner ran, and that is read off surefire rather than the exit code

```
Test set: com.modernized.batch.job.InterestJobRunTest
Tests run: 1, Failures: 0, Errors: 0, Skipped: 0, Time elapsed: 1.062 s
```

**The distinction matters more than the result.** The build is invoked with
`-Dsurefire.failIfNoSpecifiedTests=false`, so a run that matched *no test at all* also exits 0. The
first version of `stage_and_run_job` returned `completed` on the exit code and would have reported a
green on a runner surefire never picked up — a false claim that the program executed, on the one
verdict that asserts it. It now requires the output file to exist, and this report is what confirms
the check is measuring the right thing.

### The differential, pointed at the project

```
status : mismatched
reason : 3 field(s) differ from what CBACT04C wrote over the same corpus
records: 100   fields: 1100
excluded: ['TRAN-ID', 'TRAN-ORIG-TS', 'TRAN-PROC-TS']

accounts: record 49 ACCT-CURR-BAL:         got 1964.64  want 1945.87
accounts: record 49 ACCT-CURR-CYC-CREDIT:  got 0.00     want 1501.75
accounts: record 49 ACCT-CURR-CYC-DEBIT:   got 0.00     want -47.88
```

**Three account fields on one record is the documented correct outcome**, not a defect.
[ADR-0066](../../adr/0066-generate-renders-the-job-wiring-and-the-stopgap-retires.md) already
measured exactly this and says so: `CBACT04C`'s `PERFORM UNTIL END-OF-FILE = 'Y'` puts the
account-break post in the `ELSE` of `IF END-OF-FILE = 'N'`, so the final account keeps a balance
excluding the interest the same run wrote for it, and a faithful translation reproduces that. Two
briefs in a row predicted `mismatched` and got `not_run`; this is the first run where the prediction
was testable, and reading it as a translation defect is the misreading that record exists to prevent.

*Correction to the session brief that scoped this work*: it attributed this divergence to ADR-0043.
That record is about the corpus's IBM sign overpunches and `CBTRN02C`'s oracle, which is a different
finding entirely. The account-break divergence is ADR-0066's.

## What this does not verify

**A live-design run.** This exercised the round-trip fixture's design — `interestJob`, three
processor steps, bodies from the scripted author. The two live `CBACT04C` designs have a different
shape, and the difference is not cosmetic:

| | round-trip fixture | `step56` / `step58` |
|---|---|---|
| files bound | **4** — `TCATBALF`, `ACCTFILE`, `XREFFILE`, `DISCGRP` | **3** — `TCATBALF`, `TRANSACT`, `ACCTFILE` |
| lookups reach a reader | yes | **no** |

So the corpus-staging path and the framing of `cardxref.dat`/`discgrp.dat` at 50 bytes were exercised
here and would not have been by either live design. **Whether a live design's job completes is
unmeasured**, and there is a stated reason to doubt it — see below.

## What pre-flight found, before a phase was spent

Rendering both live designs offline, before writing any of this:

- **`XREFFILE` and `DISCGRP` are declared, resolvable, and bound to nothing** in both. `ACCTFILE` is
  bound only as a writer. The wiring constructs every processor with `new XProcessor()`, and a
  rendered processor's only constructor parameters are job parameters — so
  `ResolveAccountAndCardXrefProcessor` must return a `TranCatBalWithAccount(categoryBalance, account,
  cardXref)` with nothing to build the last two from.
- **The renderer supports the join and the design shape bypasses it.** Rendering the same step with a
  widened input:

  | `input_type` | reader paths |
  |---|---|
  | `TranCatBal` (as designed) | `TCATBALF` |
  | `TranCatBalWithAccount` | `TCATBALF`, `ACCTFILE`, `XREFFILE` |
  | `RatedCategoryBalance` | `TCATBALF`, `ACCTFILE`, `XREFFILE`, `DISCGRP` |

  Both models wrote the bare entity as `input_type` and the composite as `output_type`, putting the
  join in the processor. **Nothing refuses it** — the same signature as ADR-0072's and ADR-0074's
  defects.

`test_both_live_designs_strand_the_two_lookup_files` asserts this so it cannot regress quietly. It is
written to fail when a design binds them.

## Two defects this work introduced and the runs found

Recorded because both were written the obvious way and both were wrong in the direction that reads as
success.

1. **An `ASSIGN TO` absent from `SOURCES` was treated as an output.** That made the refusal branch
   unreachable: every unrecognised *input* was silently pointed at an empty path under
   `roundtrip/output/`, and the job would have started and died on a file nothing staged. Caught by
   `test_a_bound_file_with_no_source_is_refused`, which failed on first run — bite 4 from this
   repository's own list, *prove a new check can fail before trusting it*.
2. **The rendered runner shipped in the generated project.** It holds the absolute staged paths of
   one machine's temp directory and, being an ordinary test in `src/test/java`, joins any later
   unfiltered `test` or `verify`. The round-trip test runs `verify`, and it went red:

   ```
   NoSuchBeanDefinitionException: No qualifying bean of type
   'org.springframework.batch.core.job.Job' available
   ```

   The runner is now deleted in a `finally`, and `JobRunVerdict.staged_inputs` carries what a
   reviewer would have opened the file for. Not caught by any unit test — only by running the thing.

## The habit that paid, again

Offline pre-flight against the two real designs cost seconds and produced the file-binding table, the
`input_type` finding, and the shape of the whole change before a single Maven build ran. It did not
catch either defect above. **Both halves of that sentence are the point**: pre-flight is still the
best ratio available, and it is not sufficient — the same conclusion entry 20 reached, reached again
by a different route.
