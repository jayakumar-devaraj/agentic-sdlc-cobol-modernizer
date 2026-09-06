# 20. The first live run under the ordering rule

Verified 2026-09-06. Covers [ADR-0072](../../adr/0072-a-step-is-ordered-where-its-input-exists.md)'s
prompt half and [ADR-0074](../../adr/0074-a-mid-chain-step-reads-its-predecessor-not-a-file.md).

Read [19 — the move the refusal names](19-the-move-the-refusal-names.md) first. That entry verified
the refusal and the repair instruction offline, and said in as many words that whether an architect
obeys `v1_5_0` unprompted was unmeasured and was the next run's question.

**The claim, in one line: the architect obeyed the ordering rule on its first attempt with no repair,
and the job it designed was still refused — by a defect that had been rendering silently into every
job the pipeline had ever produced.**

## The run

`step56-cbact04c-20260906-153713`. Specialist `v0.4.4`, architect prompt `v1_5_0`, control plane
pinned and both images rebuilt, design gate approved by the operator.

| Phase | |
|---|---|
| `design` | 338s, 9 gate items, **3 model calls**, $1.15 |
| `generate` | 275s, **6 processor steps generated and compiled** |
| Wiring | **REFUSED** |
| Equivalence test | `ComputeMonthlyInterestProcessorEquivalenceTest` **PASSED** |
| Differential | `NOT RUN` — the project produced no output to compare |

## ADR-0072's open question, answered

Three model calls is the no-repair count. The refusal never fired; the design arrived ordered.

| # | step | in | out |
|---|---|---|---|
| 2 | `resolveAccountAndXref` | `TranCatBal` | `TranCatBalWithAccount` |
| 3 | `resolveInterestRate` | `TranCatBalWithAccount` | `RatedCategoryBalance` |
| 4 | **`computeCategoryFees`** | `RatedCategoryBalance` | `RatedCategoryBalance` |
| 5 | `computeMonthlyInterest` | `RatedCategoryBalance` | `AccruedCategoryInterest` |
| 6 | `writeInterestTransaction` | `AccruedCategoryInterest` | `Tran` |
| 7 | `postAccountInterest` | `AccountInterestPosting` | `Account` |

Every step's input is its predecessor's output. Offline `plan_steps`: 6 of 6 renderable, 0 skipped,
0 named without a bean.

**It chose a different position than the refusal would have named.** ADR-0072's message says to move
the passthrough ahead of `writeInterestTransaction`, on `AccruedCategoryInterest`. The architect put
it on `RatedCategoryBalance`, two steps earlier. Both satisfy the rule. Only one of them wires — and
that is the finding.

The nine gate items are the known pre-existing `REDEFINES` on `CBACT04C`'s DB2 timestamp working
storage (ADR-0002), unchanged from `step55`.

## What `generate` said

```
Wiring: REFUSED -- steps 'computeCategoryFees' and 'computeMonthlyInterest' both need a
ItemReader<com.modernized.batch.domain.RatedCategoryBalance> bean, and Spring resolves
these by type -- so this job's wiring is ambiguous
```

The guard is in `java_file_bindings.py`, predates this session, and was refusing correctly. It was
refusing the shallower of two problems.

## The prediction that was wrong, and why

Entry 19 and the run brief both said to expect `equivalence: mismatched`. It came back `not_run`
again. The reasoning behind the prediction — that ordering was the last thing between the pipeline
and a verdict — was the same reasoning ADR-0071 used one release earlier, and wrong the same way:
each fix revealed the next thing standing behind it, and calling any of them *the* last one is a
claim nobody had evidence for.

## The defect the ambiguity was hiding

`RatedCategoryBalance` is `TranCatBal + Account + CardXref + DisGroup` and carries no computed
field, so `_has_file_source` answers **True**. A file reader was preferred for any step whose input
was assemblable that way, mid-chain or not. Rendering the **pinned `step55`** design showed it
without any passthrough involved:

| step | reader it was given | store it filled |
|---|---|---|
| `resolveAccountAndCardXref` | `ItemReader<TranCatBal>` | `ResolveAccountAndCardXrefStaging` |
| `resolveInterestRate` | **`ItemReader<TranCatBalWithAccount>`** | `ResolveInterestRateStaging` |
| `computeMonthlyInterest` | **`ItemReader<RatedCategoryBalance>`** | `ComputeMonthlyInterestStaging` |

The first store is written and read by nobody. `resolveInterestRate` rebuilds its input from files
instead — and its input requires the disclosure-group keyed read *with* the `'DEFAULT'` fallback on
status `'23'`, which is the entire job of the step whose output it is discarding.

So two steps sharing an input type was only a problem because **neither should have had a file
reader**. It took a passthrough to turn a silent duplication into a loud refusal.

## After ADR-0074

Both designs render as real chains — one file reader at the head, stores filled and read after it:

```
LIVE (step56)
  resolveAccountAndXref      ItemReader<dom.TranCatBal> reader
  resolveInterestRate        ResolveAccountAndXrefStaging
  computeCategoryFees        ResolveInterestRateStaging
  computeMonthlyInterest     ComputeCategoryFeesStaging
  writeInterestTransaction   ComputeMonthlyInterestStaging
  postAccountInterest        ComputeMonthlyInterestStaging
```

The two `RatedCategoryBalance` consumers now take different stores and no bean type is claimed twice.

## The tests exist because nothing caught this

**906 unit and contract tests passed with the precedence flipped.** No test asserted which reader a
mid-chain step receives, so a change rerouting every mid-chain reader in every generated job was
invisible. Four tests now pin it, against `cbact04c-design-step56.json` — the design that obeys
ADR-0072 and is the only fixture that reaches this defect.

Proved to fail without the fix rather than assumed to. With `_produces_the_input_of` stubbed back to
`None`:

```
--- with the old precedence ---
  job config RAISED: UnrenderableJobError
  bindings RAISED: UnrenderableJobError steps 'computeCategoryFees' and 'computeMonthlyInterest'
                   both need a ItemReader<dom.RatedCategoryBalance> bean
```

That is the live run's message, reproduced offline.

## The suites

```
$ pytest tests/unit tests/contract -q
911 passed, 3 skipped in 78.55s

$ pytest tests/integration/test_hand_written_round_trip.py tests/integration/test_generate_renders_the_wiring.py -q
16 passed, 1 skipped in 193.12s

$ pytest tests/integration/test_a_live_design_wires.py tests/integration/test_account_break_posting.py tests/integration/test_cbtrn02c_round_trip.py -q
31 passed, 1 skipped in 344.60s

$ ruff check src tests   All checks passed!
$ mypy                   no issues in 57 source files
```

The round trip builds a generated project and **runs** it against the oracle. It passes before and
after, because its design has no step whose input is file-assemblable mid-chain — which is why it
never caught this, and why it is still the safety net that says the change did not break what worked.

## What this entry does not claim

**No verdict yet.** The differential has still never returned `mismatched` on a live design. This
entry verifies the ordering rule is obeyed and the wiring defect behind it is fixed; whether the
next run's job runs to completion and compares is unmeasured.

**A known adjacent defect, recorded and not fixed.** In the pinned `step55` design
`postAccountInterest` reads `ComputeCategoryFeesStaging` — the store of a step `plan_steps` skipped,
which nothing fills. `aggregation_source` does not ask whether the step it resolves to is
renderable. It does not affect `step56`, and the `step55` job cannot start regardless.
