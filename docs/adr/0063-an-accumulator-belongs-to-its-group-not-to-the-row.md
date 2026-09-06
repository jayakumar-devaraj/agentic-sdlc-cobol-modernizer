# ADR-0063: An accumulator belongs to its group, not to the row that feeds it

## Status

**Accepted** (2026-09-06, proposed 2026-09-04). Corrects
[ADR-0062](0062-a-step-must-be-able-to-return-what-it-computes.md), which is live in
`v0.2.0` and whose refusal **requires** the defect described below.

Held at *Proposed* deliberately, and against this repository's habit — all 62 prior records were
Accepted. ADR-0062 was written as Accepted before anything had exercised it against a live model,
and the hole it left was found one run later. This record set two conditions instead: the check
exists, **and** a run produces a correct `postAccountInterest` item.

**Both are now met.** The check landed with this record. The second condition was met by run
`step55-cbact04c-20260906-090845` (`v0.4.2`, architect prompt `v1_4_0`), which produced the correct
design with no repair attempt — `AccruedCategoryInterest` carrying `monthlyInterest` alone, the
accumulator on `AccountInterestPosting` — and generated this:

```java
BigDecimal key = item.categoryBalance().trancatAcctId();
totals.merge(key, item.monthlyInterest(), BigDecimal::add);
...
new AccountInterestPosting(first.account(), total)          // the group's sum

// and in the processor:
account.acctCurrBal().add(item.totalInterest())             // ADD WS-TOTAL-INT TO ACCT-CURR-BAL
```

Step 51's branch set `totalInterest = monthlyInterest` and would have posted one category's interest
where an account has four. Measured, not inferred: see
[verification 18](../qa/verification/18-the-accumulator-is-right-on-a-live-run.md).

**What is still not proven** is that the resulting job *runs*. It cannot yet, for a reason unrelated
to this record — see [ADR-0071](0071-a-chunk-step-is-a-processor-step-and-the-job-names-only-those.md)
and that verification entry. The accumulator being correct in the generated source is what this
record claimed, and that is what has been shown.

Restores the boundary [ADR-0027](0027-the-account-break-becomes-a-second-pass-over-pre-aggregated-items.md)
already drew and ADR-0062 unknowingly crossed.

## Context

Run `step51-cbact04c-20260904-091713` was the first live exercise of ADR-0062. The design half
worked exactly as intended: `solution_architect` produced the corrected design **with no repair
attempt**, `computeMonthlyInterest` was typed `RatedCategoryBalance -> AccruedCategoryInterest`, and
`WS-MONTHLY-INT` reached the generated code as a returned value rather than a discarded local.

Then `generate` produced this:

```java
BigDecimal monthlyInterest = CobolArithmetic.requireFits(
        CobolArithmetic.divide(
                item.categoryBalance().tranCatBal().multiply(interestRate),
                new BigDecimal("1200"), 2),
        11, 2);
BigDecimal totalInterest = monthlyInterest;          // <-- wrong money
return new AccruedCategoryInterest(
        item.categoryBalance(), item.account(), item.cardXref(),
        item.disclosureGroup(), monthlyInterest, totalInterest);
```

`WS-TOTAL-INT` is not this row's interest. It is a **running total across every category balance of
one account**:

```cobol
200:   MOVE 0 TO WS-TOTAL-INT              *> reset at each ACCOUNT break
467:   ADD WS-MONTHLY-INT TO WS-TOTAL-INT  *> accumulate, once per category
352:   ADD WS-TOTAL-INT TO ACCT-CURR-BAL   *> post the SUM, once per account
```

An account with four category balances would be posted the **last** category's interest instead of
the sum of four. The generated javadoc again claims an accumulation that does not happen — the same
sentence as the step-49 defect, for a different reason.

### The model had no correct option

A Spring Batch `ItemProcessor` is stateless and sees one item. There is no previous accumulator value
reachable from a `RatedCategoryBalance`. Handed a per-item field named `totalInterest` and told to
fill it, `= monthlyInterest` is the only expressible answer. As with step 49, the model behaved
correctly inside a contract that could not express the truth.

### ADR-0062 does not merely miss this. It requires it.

This is the finding that matters, and it was verified by running the check rather than reasoning
about it. Given the design that is actually **correct** — the row-grain item carrying only the
row-grain value:

```python
AccruedCategoryInterest(components=[categoryBalance:TranCatBal],
                        computed_fields=[monthlyInterest <- WS-MONTHLY-INT])
```

`undeliverable_computed_values` returns:

```
['WS-TOTAL-INT']        # i.e. REFUSED
```

`WS-TOTAL-INT` is computed in `1300-COMPUTE-INTEREST`, which `computeMonthlyInterest` owns; it
escapes to `1050-UPDATE-ACCOUNT`; and it is `MOVE`d into no record, so the landing-field escape does
not apply. ADR-0062's rule therefore demands that `computeMonthlyInterest`'s output type carry it,
and the only way to satisfy that at row grain is to fabricate a total. **The architect was obeying
the contract, and the contract was wrong.**

### ADR-0027 had already settled this

> **Why this could not simply be generated.** A stateless `ItemProcessor` cannot hold `WS-TOTAL-INT`
> across items, and Spring Batch's chunk boundaries do not align with COBOL's account breaks.

ADR-0027 considered exactly this and chose option (d): a second pass whose reader yields **one item
per account, already summed**, so the generated body is a stateless per-item transform and *the
summation lives in infrastructure*. It even names the item: `(account, totalInterest)`.

The live design produced that item correctly, alongside the wrong one:

```
AccountInterestPosting   records: account:Account       computed: totalInterest <- WS-TOTAL-INT   ✅ group grain
AccruedCategoryInterest  records: ...4 records...        computed: monthlyInterest <- WS-MONTHLY-INT  ✅
                                                                   totalInterest   <- WS-TOTAL-INT   ❌ row grain
```

And the control break is attached to the right step:

```
postAccountInterest: control_break accumulator=WS-TOTAL-INT from=WS-MONTHLY-INT para=1050-UPDATE-ACCOUNT
```

So the design carries **two contradictory representations of the same value**, and nothing compares
them. ADR-0062 introduced the second one.

### The missing concept is grain

ADR-0062's refusal asks *"does this computed value have somewhere to go?"* It never asks *"is that
somewhere at the right grain?"* — because the word **grain** appears nowhere in the contract. A
`ComputedValue` records what a value is, which paragraphs compute it, which read it, and where it
lands. It does not record whether it is a property of **one row** or of **a group of rows**, and
those are different types of thing that a `BigDecimal` field cannot tell apart.

The fact needed to make the distinction is already present and already correct: a value that is some
control break's `accumulator_field` is group-scoped, by the definition of a control break. Nothing
consults it.

## Decision

### 1. A control break's `accumulator_field` is group-grain, and only a group-grain item may carry it

`UnifiedDesign` gains one derived question — *which computed values are accumulators, and for which
break* — read from the `ControlBreakDesign` entries already attached to steps. No new model-authored
field: the fact exists, it is deterministic, and this is the sixth time this repository has found a
fact it held and did not consult.

**A `CompositeType` may carry an accumulator as a `computed_fields` entry only if it is the output of
the step that owns the break's `performed_paragraph`.** `AccountInterestPosting` qualifies;
`AccruedCategoryInterest` does not.

### 2. The producing step is excused from carrying an accumulator

ADR-0062's rule stands unchanged for ordinary computed values and is **narrowed by one clause**: a
value that is an accumulator is not the producing step's to deliver. It belongs to the aggregation
(ADR-0027's pass 2), which obtains it by summing, not by receiving.

Concretely, in `undeliverable_computed_values`, a value whose name matches a control break's
`accumulator_field` for that job is skipped — a fourth narrowing beside processors-only,
escapes-its-paragraph, and lands-in-a-carried-record. With it, the correct design above is accepted
rather than refused.

### 3. Refused where it is produced, and the message names the mechanism

The same shape as ADR-0059 and ADR-0062: stated in the `solution_architect` prompt (`v1_3_0`) and
enforced on the way out through `parse_with_repair`. The message must name ADR-0027's item rather
than only the fault, because a model that is told *"do not put `WS-TOTAL-INT` here"* and not told
where it does belong will move it somewhere else that is also wrong.

### 4. `WS-MONTHLY-INT` is unaffected

It is a genuine per-row value, computed once per category balance, and ADR-0062 handles it correctly.
This decision must not disturb it — which is the test that separates a fix from a retreat.

## Consequences

**The refusal stops being wrong, which is worth more than it stops being incomplete.** A check that
refuses a correct design is worse than no check: it trains a producer to satisfy it incorrectly,
which is precisely what happened here in one run.

**One artifact already carries the defect**, deliberately.
`agentic-patch/step51-cbact04c-20260904-091713` was approved at the release gate as a labelled
before/after exhibit, with the accumulator fault written into the gate comment and
`Do not merge this branch to card-service main`. It stands beside step 49's branch: one discards its
central computation, the other returns it and gets the account total wrong. Two generations of the
same class, which is a more honest exhibit than a clean branch.

**The defect class is now named twice and should be counted once.** `CLAUDE.md` records *a fact the
deterministic layer already held, dropped one step before its consumer* five times (G21, G24, G26,
G28, G30); ADR-0062 was the sixth and this is the seventh. The rule it states — *when a fix is the
n-th of a kind, make the n+1-th impossible or loud* — is not being met by adding a seventh instance
fix. **The mechanism that would actually close this class is not another contract field: it is
running the generated code against ADR-0021's oracle before a human is asked to approve it.** The
accumulator would have been caught in seconds by comparing posted account balances. It was not,
because the oracle has never been pointed at pipeline output — it runs against hand-written Java in
this repository. That is the recommendation this ADR defers to and does not implement.

**Grain is introduced narrowly and deliberately does not generalise.** This decision recognises
exactly one group-grain concept — a control break's accumulator — because that is the one the
deterministic layer already identifies. A general notion of item grain (per-row, per-group, per-job)
would be a larger contract change, and inventing it from one instance is how ADR-0062 came to require
a defect.

## Alternatives considered

**Fix the generator instead.** Teach `modernization_engineer` that `totalInterest` cannot be filled
from one item. Rejected for ADR-0062's own reason: the generator would then be refusing a field the
design told it to populate, which moves the failure to the phase with the least context and after
the human gate.

**Let the processor accumulate with state.** ADR-0027 considered and rejected this as option (a): a
stateful writer fails on a chunk boundary landing mid-account and on a restart replaying one, holds
state nothing downstream can check, and breaks ADR-0019's processor-only scope.

**Refuse any composite carrying a computed value that another composite also carries.** Would catch
this instance — the two representations of `WS-TOTAL-INT` — and is wrong in general: a value
legitimately appearing on both a row item and the group item it feeds is exactly ADR-0027's design,
and `AccountInterestPosting` must keep it.

**Revert ADR-0062.** Rejected on the evidence. Its row-grain half is correct and was proven live:
`WS-MONTHLY-INT` is computed, returned and typed, with `precision 11, scale 2` handed over
deterministically rather than inferred off a `PIC` clause. The defect is one clause missing, not a
wrong idea.
