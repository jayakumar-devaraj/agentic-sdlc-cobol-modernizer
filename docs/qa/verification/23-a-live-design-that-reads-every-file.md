# 23. A live design that reads every file, end to end

Verified 2026-09-07. Covers
[ADR-0076](../../adr/0076-a-keyed-lookup-belongs-in-the-reader-of-the-step-that-uses-it.md) on a live
run, and closes the gap
[22](22-the-job-opens-every-file-its-cobol-names.md) stated it left open.

Read [22](22-the-job-opens-every-file-its-cobol-names.md) first. That entry ended with the shape
refused and the folded shape compiling under Maven, and said plainly that no live run had been made
against a design binding all five files, so no correctness verdict existed for one.

**The claim, in one line: run `step59-cbact04c-20260907-203928` designed, generated, wired, ran and
published a `CBACT04C` job that opens all five of the program's files, and its differential is
`mismatched` on three fields that ADR-0066 documents as the correct outcome — 1097 of 1100 match.**

## The run

| | |
|---|---|
| run id | `step59-cbact04c-20260907-203928` |
| specialist | `cobol-modernizer` **v0.4.7**, interrogated 33/33 from a clean venv and inside the image |
| architect prompt | **`v1_6_0`** |
| tenant checkout | `carddemo-tenant-service` at `fb85c564` |
| design phase | 264.0s — 1 program, 9 gate items |
| generate phase | 184.1s — 4 processor steps |
| publish | commit `9bd40fc2` → `agentic-patch/step59-cbact04c-20260907-203928` |
| terminal state | `completed` |

## The refusal never fired, and that is the result

ADR-0076 shipped two halves: a refusal in `render_job_wiring` and the rule stated in prompt
`v1_6_0`. **The second half did the work.** The model wrote no resolve steps at all:

```
computeMonthlyInterest     RatedCategoryBalance -> AccruedCategoryInterest
computeCategoryFees        AccruedCategoryInterest -> AccruedCategoryInterest
writeInterestTransaction   AccruedCategoryInterest -> Tran
postAccountInterest        AccountInterestPosting -> Account

RatedCategoryBalance = [TranCatBal, Account, CardXref, DisGroup]
```

`plan_steps`: 4 renderable, **0 skipped**. Bindings:

```
cobol.file.tcatbalf  cobol.file.acctfile  cobol.file.xreffile
cobol.file.discgrp   cobol.file.transact
```

The design never took the shape that gets refused, so the refusal is a backstop rather than the
mechanism. Stated because a reviewer seeing no refusal in the logs could reasonably conclude the
release did nothing.

## The verdict

```
Wiring:      rendered and compiled; every renderable step is wired
Job run:     completed -- InterestCalculationJobRunTest started 'interestCalculationJob'
             and it reached COMPLETED having written roundtrip/output/transact.dat
Equivalence: MISMATCHED -- 3 field(s) differ over the same corpus
             records: 100   fields: 1100
             excluded: ['TRAN-ID', 'TRAN-ORIG-TS', 'TRAN-PROC-TS']

accounts: record 49 ACCT-CURR-BAL:         got 1964.64  want 1945.87
accounts: record 49 ACCT-CURR-CYC-CREDIT:  got 0.00     want 1501.75
accounts: record 49 ACCT-CURR-CYC-DEBIT:   got 0.00     want -47.88

Equivalence test: ComputeMonthlyInterestProcessorEquivalenceTest PASSED
```

**Those three are the documented correct outcome.** ADR-0066 measured exactly them:
`CBACT04C`'s `PERFORM UNTIL END-OF-FILE = 'Y'` puts the account-break post in the `ELSE` of
`IF END-OF-FILE = 'N'`, so the final account keeps a balance excluding the interest the same run
wrote for it, and a faithful translation reproduces that. The verdict is `mismatched` permanently,
and making it green would be encoding a defect to improve a number.

Re-run independently against the published project rather than read off the log line, which is how
the record counts above were obtained.

**The transaction half matches exactly**, and that is the evidence worth more than the count. If
`XREFFILE` or `DISCGRP` were being read wrongly the disclosure group would yield the wrong rate and
every interest transaction would be wrong. They are not merely *bound* — they are read and applied
correctly.

## The correction this run forced, and it is the interesting part

**The lookups were not always stranded. They became stranded at v0.4.5, and ADR-0076 closes a gap
ADR-0074 opened.**

This was found by checking a claim rather than asserting it: the session that produced this run
stated that every previously published branch carried a job reading three of five files. That is
false, and the artifacts say so. `agentic-patch/step55-cbact04c-20260906-090845`, generated under
**v0.4.4**, ships three file readers over one driving stream:

```java
ResolveAccountAndCardXrefItemReader(Path tcatbalf)
ResolveInterestRateItemReader(Path tcatbalf, Path acctfile, Path xreffile)
ComputeMonthlyInterestItemReader(Path tcatbalf, Path acctfile, Path xreffile, Path discgrp)
```

Its `application.properties` binds all five files. So the lookups *were* read — by three separate
steps, each re-iterating `TCATBALF` from the start and redoing the keyed reads the step before it
had already done. That is precisely the defect
[ADR-0074](../../adr/0074-a-mid-chain-step-reads-its-predecessor-not-a-file.md) names in its own
words: *"a file reader handed the same input type would redo that step's keyed lookups instead of
reading its output… The store the producing step fills was rendered and never read."*

ADR-0074 fixed that correctly — a mid-chain step reads its predecessor. **And for the split design
shape it left the only file-reading step as the first resolve step, with a one-path reader.** That
is when `XREFFILE` and `DISCGRP` stopped reaching a reader at all. `git show v0.4.4:docs/adr` does
not contain ADR-0074; `v0.4.5` does.

So the sequence is: v0.4.4 read the files redundantly and wrongly; v0.4.5 fixed the redundancy and
stranded them; v0.4.7 reads each of them once, in one reader, at the step that uses them.

**Comparing published branches across releases is not apples-to-apples**, and that is the trap this
correction fell into first. A rendered `application.properties` means different things under
different renderers — before ADR-0074 it reflected every step that got a file reader. The design and
its plan under the *current* renderer are the comparable artifacts:

| design | binds today | skipped today |
|---|---|---|
| `step55` | `acctfile`, `transact` | 3 steps |
| `step56` / `step58` | `acctfile`, `tcatbalf`, `transact` | 2 steps |
| **`step59`** | **all five** | **none** |

## What this does not verify

- **One program.** `CBACT04C` only. `config/scenario_specialists.yaml` still hardcodes
  `args: [design, --programs, CBACT04C]`, deliberately.
- **One design.** The prompt produced the right shape on its first attempt, once. A second live run
  is what would make this a property of `v1_6_0` rather than of one sample — the standard
  `CLAUDE.md` sets for declaring a capability complete.
- **The account accumulator.** `ComputeMonthlyInterestProcessorEquivalenceTest` covers one
  `COMPUTE`, not the accumulator (ADR-0065). The account half of the differential is what covers
  that, and it is the half carrying ADR-0066's three.
- **Nothing about `CBTRN02C`**, whose oracle remains untrustworthy (register #14, ADR-0043) and
  whose `reads_own_writes` rendering is still refused.

## A defect in the record, found while verifying this run

`config/scenario_specialists.yaml` says the routing table and the image *"must agree: a routing
table naming a tag the image did not install is a run that reaches `SpecialistInvocationError` after
a human has already approved a design."*

**That is false.** `distribution` is documentation — `ExternalSpecialist.distribution` is optional,
and the only place it is read is a string interpolated into an error message when `command` is
absent from `PATH`. The image's baked `/app/config/scenario_specialists.yaml` has read `v0.4.5`
through every run since, including this one, because `Dockerfile.specialist` layers on a base image
and never copies config. Diffing the baked file against the repo's, ignoring comments, the
`distribution` line is the *only* difference — every phase, arg, command and timeout is identical.

Recorded here rather than fixed, per the rule that an unverified caveat gets a probe or an owner:
this one now has a measurement.
