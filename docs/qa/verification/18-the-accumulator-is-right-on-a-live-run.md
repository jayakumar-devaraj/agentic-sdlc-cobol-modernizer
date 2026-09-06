# 18. The accumulator is right on a live run

Verified 2026-09-06. Covers [ADR-0063](../../adr/0063-an-accumulator-belongs-to-its-group-not-to-the-row.md)
(now Accepted), [ADR-0070](../../adr/0070-a-step-that-changes-its-items-type-is-a-processor.md) and
[ADR-0071](../../adr/0071-a-chunk-step-is-a-processor-step-and-the-job-names-only-those.md).

Read [17 — the first design a model wrote](17-the-first-design-a-model-wrote.md) first. That entry
closed the wiring defects against a *pinned* design and left two things open: whether a live
architect would obey ADR-0070's rule, and whether the resulting project runs.

**The claim, in one line: run `step55-cbact04c-20260906-090845` produced a design that obeys both
rules with no repair attempt, and generated an account accumulator that is correct — which is the
condition ADR-0063 was waiting on since 2026-09-04.**

## The run

`cobol-modernizer v0.4.2`, architect prompt `v1_4_0`, control plane pinned and rebuilt, both gates
approved by the operator.

| Phase | |
|---|---|
| `design` | 386.6s, 9 gate items (all pre-existing `REDEFINES`/DB2 constructs), $1.12 |
| `generate` | 255.4s, **6 processor steps generated and compiled** |
| Wiring | **rendered**, 12 files |
| Equivalence test | `ComputeMonthlyInterestProcessorEquivalenceTest` **PASSED** |
| Published | `agentic-patch/step55-cbact04c-20260906-090845`, commit `7b605511` |

## ADR-0070's open question, answered

Every step whose input and output types differ came back `processor` — including
`writeInterestTransaction` and `postAccountInterest`, the two that `step54b` typed `writer` under
`v1_3_0`. **No repair attempt was spent**, so the prompt carried it rather than the refusal.

That is the thing ADR-0070 explicitly declined to claim when it was written: *"whether `v1_4_0` makes
an architect type these steps correctly is the next live run's question."* It does, once.

## The accumulator, which is what ADR-0063 was waiting for

The design is ADR-0063's shape: `AccruedCategoryInterest` carries `monthlyInterest <- WS-MONTHLY-INT`
and nothing else; `WS-TOTAL-INT` lives on `AccountInterestPosting`, the control-break step's input.

Generated `PostAccountInterestItemReader`:

```java
BigDecimal key = item.categoryBalance().trancatAcctId();
totals.merge(key, item.monthlyInterest(), BigDecimal::add);
...
new com.modernized.batch.domain.AccountInterestPosting(first.account(), total)
```

Generated `PostAccountInterestProcessor`:

```java
BigDecimal newCurrentBalance =
        CobolArithmetic.requireFits(account.acctCurrBal().add(item.totalInterest()), 12, 2);
```

`item.totalInterest()` is the group's sum, produced by the reader. **Step 51 set
`totalInterest = monthlyInterest`** and would have posted the last category's interest to an account
with four. That branch is still published as a labelled exhibit; this one is the corrected shape,
generated rather than argued.

## What this run also found, and it was the pre-flight that found it

**The job cannot start.** `STEP_NAMES` named nine steps; five `@Bean Step` methods were rendered:

```
job "interestCalculationJob" declares step "openInterestCalculationFiles"
and no bean named "openInterestCalculationFilesStep" supplies it
```

ADR-0071 records the cause — ADR-0068 taught `plan_steps` to exclude a role and left
`render_job_configuration` naming every declared step — and fixes it in `v0.4.3`.

**Two corrections belong in this entry, because both were mistakes made while reading this run.**

1. **A green compile was read as "it runs".** The offline pre-flight reported `COMPILES: True`, which
   was accurate. A Spring bean lookup fails at runtime, so javac cannot see it. Compilation is
   necessary and not sufficient, and this entry exists partly to say so where the next reader will
   find it.
2. **The differential's refusal was briefly written off as over-strict.** It reported *"the job is
   not fully renderable, so no runnable project exists to compare"*, and that was **correct** — the
   job could not have started. The genuine defect nearby was a *reporting* one: role exclusions were
   landing in `skipped_steps`, so the gate told a reviewer three file-handling paragraphs were
   missing business logic.

## What is not covered

**The record-level differential still has not run**, for this design or any other live one. After
ADR-0071 the remaining blocker is `computeCategoryFees`, which the design orders after
`writeInterestTransaction` so nothing supplies its input. Its COBOL is `* To be implemented` /
`EXIT.` — harmless in fact, and the pipeline cannot know that, so the step is named with no bean and
ADR-0032's loud failure stands. **A design-ordering finding, not a renderer one**, and the next thing
between this pipeline and an end-to-end verdict on a live design.

**One run, one program, one model, one day.** `CLAUDE.md`'s rule that a capability is complete when a
second instance exercises it is not met by this entry and is not claimed to be.
