# 25. The delivered artifact builds

Verified 2026-09-08. Closes the item
[24](24-the-artifact-that-could-not-start.md) left open, on a live run rather than an offline render.

Read [24](24-the-artifact-that-could-not-start.md) first. It measured
[ADR-0081](../../adr/0081-a-generated-reader-and-writer-open-their-files-when-the-step-runs.md) on a
project rendered offline and said plainly that no run had been made, so *"a delivered branch whose own
CI passes"* remained open and was not claimed. This entry is that claim, and the evidence for it.

**The claim, in one line: run `step62` designed, generated, ran, delivered and opened a pull request
on `card-service`, and that branch's own `verify` is green — 46 of 46 tests, BUILD SUCCESS, on
GitHub's runner.**

## The run

| | |
|---|---|
| run id | `step62-cbact04c-20260908-212245` |
| specialist | `cobol-modernizer` **v0.4.10**, interrogated 47/47 from a clean venv and inside the image |
| tenant checkout | `carddemo-tenant-service` at `fb85c564` — the same commit as `step59` |
| design phase | **405.0s** — 1 program, 12 gate items |
| generate phase | **201.6s** — 4 processor steps |
| publish | commit `3afe8e03` → `agentic-patch/step62-cbact04c-20260908-212245` |
| terminal state | **`completed`** |

## What `verify` says on the delivered branch

[card-service PR #2](https://github.com/jayakumar-devaraj/card-service/pull/2), on the pipeline's own
commit, unedited:

```
Started BaselineStackTest in 3.873 seconds
BaselineStackTest                            Tests run: 5,  Failures: 0, Errors: 0
ComputeMonthlyInterestProcessorEquivalenceTest Tests run: 10, Failures: 0, Errors: 0
                                             Tests run: 46, Failures: 0, Errors: 0
BUILD SUCCESS
```

ADR-0080 measured this same suite at **41 of 46**, the five failures being `BaselineStackTest`. It is
now **46 of 46**, and the difference is that a delivered project starts.

**The one remaining `ERROR` line is the point of the record, not a blemish:**

```
ERROR o.s.batch.core.step.AbstractStep : Encountered an error executing step
      computeMonthlyInterest in job monthlyInterestCalculationJob
```

There is no `data/` directory, so the job correctly cannot run. Under `v0.4.9` that same condition
killed the application context and took five unrelated tests with it. It is now a step-level failure
naming what it wanted, and the suite passes — which is exactly what ADR-0081 decided and what
`@Lazy` was documented as achieving and never did.

## The differential is the same three fields, for the third time

```
record 49 ACCT-CURR-BAL:        got 1964.64  want 1945.87
record 49 ACCT-CURR-CYC-CREDIT: got    0.00  want 1501.75
record 49 ACCT-CURR-CYC-DEBIT:  got    0.00  want  -47.88
```

`mismatched` on 3 of 1100, identical to `step59` and `step61`, and ADR-0066's documented-correct
outcome. `ComputeMonthlyInterestProcessorEquivalenceTest` passed, so the per-row interest arithmetic
matches COBOL's own answers. **Three independent designs now produce identical correct output.**

## The ninth thing, and it is not code

`publish` pushed the branch and then failed:

```
INFO  publish: pushed to agentic-patch/step62-cbact04c-20260908-212245
ERROR publish: could not open a pull request: GitHub returned 403
      {"message":"Resource not accessible by personal access token"}
```

The PAT has no `pull_requests: write`. `scenario_specialists.yaml` listed that grant as *"still
unproven"*; it is now proven, and it is absent. **The repo-level `permissions` probe does not cover
it** — that probe reports `admin/maintain/pull/push/triage` and says nothing about opening pull
requests, so "full permissions" was never a claim about this.

**The PR was opened by hand rather than by widening the token**, for the reason the same file already
gives: the control plane creating pull requests and CI means CI running model-generated code with the
repository's secrets. The branch content is exactly what the pipeline committed.

**The push itself succeeded, which is `step61`'s failure fixed.** The delivered tree contains
`.github/workflows/build.yml` — inherited from `card-service`'s `main` and *unmodified*, so the
`workflow`-scope refusal never arises. That is ADR-0079 working as designed.

## What this still does not close

**ADR-0078 remains unproven by the pipeline.** It is reached only by a design whose *head* step is a
passthrough. `step62`'s head is `computeMonthlyInterest`, type-changing — the same shape as `step59`
and `step61`. The three passthroughs it does contain are two tasklets and a reader, which
`plan_steps` does not plan as chunk steps, plus `computeFees` mid-chain. Four runs, four
type-changing heads. It stays confirmed by hand and by synthetic checks only.

**`step60` is still parked**, and PR #1 is still red — it was rendered under `v0.4.7` and carries
neither ADR-0080 nor ADR-0081. Nothing here changes a branch already committed; PR #2 is the
delivered artifact that carries both.

**The working set still has ADR-0081's defect**, unreachable and recorded rather than fixed.

## What pre-flight bought this time

The design was rendered offline before the gate was approved — 9 wiring files rendered and compiled,
nothing skipped, five of five files bound, and the rendered reader carrying `open` with an empty
constructor. That is not a check the pipeline performs before spending the generate phase, and it
turned gate approval from a judgement into a reading. Bite 8 of the `step-62` brief holds: offline
rendering could not have found the original defect, and it is still the best ratio available.
