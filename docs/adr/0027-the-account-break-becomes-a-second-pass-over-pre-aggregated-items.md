# ADR-0027: The account break becomes a second pass over pre-aggregated items

## Status

Accepted (2026-08-12). Closes the open half of gap **G27** — the generation half, which PR #39 /
ADR-0023 deliberately left after closing the *reporting* half.

Amends nothing in [ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md):
its processor-only generation scope is the constraint this decision is shaped around rather than an
obstacle to it. Builds on [ADR-0023](0023-a-step-this-pipeline-does-not-render-is-reported-not-dropped.md),
which made this step visible at the gate instead of invisible everywhere.

## Context

`CBACT04C` accumulates interest per account and posts it on an account break. Read from the source
rather than from the gap's summary, the structure has three properties that decide this:

```cobol
IF TRANCAT-ACCT-ID NOT= WS-LAST-ACCT-NUM          *> the break
   IF WS-FIRST-TIME NOT = 'Y'
      PERFORM 1050-UPDATE-ACCOUNT                 *> flush the PREVIOUS account
   ELSE
      MOVE 'N' TO WS-FIRST-TIME
   END-IF
   MOVE 0 TO WS-TOTAL-INT                         *> reset for the new one
   ...
ELSE
   PERFORM 1050-UPDATE-ACCOUNT                    *> and again at EOF, for the last account
```

1. **It is a classic control break, and it assumes its input is grouped by account.** The program
   never sorts. Given ungrouped input the same account is flushed repeatedly with partial totals,
   and nothing detects it.
2. **The flush is offset by one, plus a final flush at EOF.** A translation that posts on every
   record, or that forgets the EOF flush, loses the last account's interest **silently and by the
   full amount** — which is the severity G27 was raised at.
3. **`1050-UPDATE-ACCOUNT` is three concerns, not one**: `ADD WS-TOTAL-INT TO ACCT-CURR-BAL`, zeroing
   `ACCT-CURR-CYC-CREDIT`/`ACCT-CURR-CYC-DEBIT`, and the `REWRITE`. Only the first two are translated
   logic; the third is persistence.

**Why this could not simply be generated.** A stateless `ItemProcessor` cannot hold `WS-TOTAL-INT`
across items, and Spring Batch's chunk boundaries do not align with COBOL's account breaks — a chunk
of 100 items may end mid-account. That is the design question ADR-0023 named and declined to settle.

### The options

**(a) Render a stateful `ItemWriter` that does its own control break.** The literal translation: keep
per-balance items, hold the running total and the last account id in the writer, flush on change and
at `ItemStream#close`. It is faithful to the COBOL's shape and it fails on the two things COBOL never
had to survive — a chunk boundary landing mid-account, and a restart replaying one. It also breaks
ADR-0019's processor-only scope, and the state it holds is exactly the kind nothing downstream can
check.

**(b) Make the item an account instead of a balance row.** The elegant answer: if the unit of work is
a whole account, the accumulation is *intra*-item and the cross-item problem disappears rather than
being managed. **Blocked on the contract, not on preference** — a group item's output must carry many
transactions, and `CompositeType.components` names one entity each with no cardinality. Collections
are exactly what ADR-0019 scopes out and § 3.3 records as unresolved (`DomainField` has no
cardinality). Taking this route means solving array support first, which is a larger decision than
this one.

**(c) Leave it ungenerated.** Status quo. ADR-0023 already reports it honestly at the gate, so this
costs nothing new and closes nothing.

**(d) A second pass over pre-aggregated items.** ← proposed. Pass 1 is what already exists and works:
compute interest per balance, write a transaction per balance. Pass 2 is a separate step whose
**reader** yields one item per account, already summed, and whose **processor** applies the posting.

## Decision

**Take (d).** The step that owns `1050-UPDATE-ACCOUNT` reads items that are already aggregated, so the
generated logic is a stateless per-item transform and the summation lives in infrastructure.

**The item is `(account, totalInterest)`**, produced by a reader whose query groups the transactions
pass 1 wrote. The generated body is then exactly COBOL's translated logic and nothing else:

```java
// ADD WS-TOTAL-INT TO ACCT-CURR-BAL; MOVE 0 TO the two cycle fields
```

**Why the aggregate is provably the same number.** `WS-TOTAL-INT` sums `WS-MONTHLY-INT` per account;
every `WS-MONTHLY-INT` is written to `TRAN-AMT` in the same paragraph, under the same
`IF DIS-INT-RATE NOT = 0` guard — accumulate and write are both inside `1300-COMPUTE-INTEREST`, so
they cannot diverge. Therefore `SUM(tran_amt) GROUP BY account` **is** `WS-TOTAL-INT`, not an
approximation of it. That equality is what makes the second pass a re-ordering rather than a
re-implementation, and it is the reason to prefer (d) over (a) on correctness rather than on
convenience.

**What this buys against (a)**, stated as the properties that were failing:

| Property | (a) stateful writer | (d) second pass |
|---|---|---|
| Chunk boundary mid-account | Splits a total | Irrelevant — one item *is* one account |
| Restart replaying a chunk | Double-counts | Chunk is transactional; the item is recomputed from source |
| Input not grouped by account | Silently wrong | Irrelevant — the reader groups |
| ADR-0019's scope | Broken | Held: the generated part is a processor |

**The ordering assumption disappears**, which is worth stating separately: COBOL's correctness depends
on `tcatbal` arriving grouped, and nothing in the program checks it. A `GROUP BY` has no such
precondition, so this is one of the few places the target is *safer* than the source rather than
merely equivalent.

## Consequences

**Good.** The largest known correctness gap between a passing paragraph and a migrated `CBACT04C`
becomes generated, translated logic. The generated body stays a stateless processor, so ADR-0019
needs no amendment and the deterministic/LLM split is untouched. The off-by-one and the EOF flush —
the two ways a hand-written control break goes silently wrong — cannot be got wrong here, because
there is no break to implement.

**The divergence, recorded rather than discovered.** COBOL interleaves posting with transaction
writing in a single pass; this posts in a second pass after all transactions exist. **Final state is
identical**, but intermediate state is not: mid-run, COBOL has posted earlier accounts while this has
posted none. That binds if anything ever observes the target mid-job, and it means pass 2 is
meaningless unless pass 1 completed — an ordering dependency the job definition must express.

**Neither is idempotent across runs**, and that is not a divergence: `ADD ... TO ACCT-CURR-BAL` posts
again if re-run, and COBOL's `REWRITE` does the same. Stated so nobody reads "restart-safe" as
"re-runnable".

**The aggregating reader is infrastructure, not generated logic** — rendered or hand-written like the
`FlatFileItemReader`s, and reviewable once. That is the same line ADR-0026 drew for job parameters
and PR #44 drew inside `1300-B-WRITE-TX`: the model writes translated business rules, not wiring.

**What this does not do.** It does not make `CBACT04C` round-trip. `TRAN-ID` remains unpopulated by
ADR-0026's decision, so the transaction record is still incomplete, and whether the program
round-trips depends on what a differential test asserts over — which is a separate question from
whether this logic is generated. **Option (b) remains the more faithful long-term shape** and becomes
available if array support is ever added; this decision does not foreclose it.
