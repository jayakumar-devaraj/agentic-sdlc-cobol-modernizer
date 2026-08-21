# ADR-0039: `CBTRN02C`'s posting path is declared and refused, because its decision reads its own writes

## Status

**Accepted** (2026-08-21). The result of taking the second Track C program with real business logic
(**G17**) as far as the renderer would carry it, after
[ADR-0037](0037-a-file-written-both-ways-renders-as-an-upsert.md) fixed its write modes and its
transaction master was captured as an oracle.

## Context

`CBACT04C` computes interest per balance row. Nothing it decides depends on what it has already
written, which is why [ADR-0027](0027-the-account-break-becomes-a-second-pass-over-pre-aggregated-items.md) could resolve its
one accumulation by **resizing the item**: make the item an account with its interest already
summed, and the accumulation becomes intra-item.

`CBTRN02C` is not that kind of program.

**`1500-B-LOOKUP-ACCT` decides whether a transaction is accepted from state the job is writing.**

```cobol
COMPUTE WS-TEMP-BAL = ACCT-CURR-CYC-CREDIT - ACCT-CURR-CYC-DEBIT + DALYTRAN-AMT
IF ACCT-CREDIT-LIMIT >= WS-TEMP-BAL   CONTINUE
ELSE  MOVE 102 TO WS-VALIDATION-FAIL-REASON
```

Those cycle fields are exactly what `2800-UPDATE-ACCOUNT-REC` `ADD`s to and `REWRITE`s for every
accepted transaction. The decision for transaction *n* reads what *1..n-1* wrote.
`2700-UPDATE-TCATBAL` has the same shape against a second file — `ADD DALYTRAN-AMT TO TRAN-CAT-BAL`
on the row it just read, which is also where ADR-0037's `upsert` comes from.

**Measured, from committed artifacts rather than from a reading of the source.**
`transact-stage1.dat` holds exactly the transactions the program wrote, each carrying its
`DALYTRAN-ID`, so the rejected set is known rather than modelled:

| | |
|---|---|
| daily transactions in the corpus | **300** |
| written by `CBTRN02C` | **257** |
| rejected | **43** — every one of them reason `0102 OVERLIMIT TRANSACTION` |
| rejected, yet passing the limit check against the account's **initial** state | **30** |

So a stateless, order-independent implementation writes **287** records where the program writes
257, **and every one of the 287 is individually correct**. A field-level differential sees nothing;
only the count does — the same blindness ADR-0037 found in the writer, one level up.

The 30 are not an edge case in a quiet corpus. They are 70% of all rejections, and there are no
other rejection causes in this data at all.

## Decision

**`CBTRN02C`'s posting path is declared and refused by name. It is not generated, and the round-trip
metric stays at `1 of 4`.**

The refusal is `not_generated` with a stated reason, the reporting path
[ADR-0023](0023-a-step-this-pipeline-does-not-render-is-reported-not-dropped.md) exists for: a step
this pipeline cannot render is reported, never silently dropped. The reason is *"the acceptance
decision reads account state this job writes, so a stateless processor cannot reproduce which
transactions are accepted"*.

**What is refused is the posting path, not the program's file handling.** The reader, the three
writers and the job wiring all render correctly (ADR-0037) and remain covered by tests. What cannot
be produced is the *decision*.

## Options considered

**(a) Generate it anyway and compare.** Rejected. It produces 287 records that each look right, and
reporting that as a round trip would be the overclaim this platform has repeatedly identified as its
characteristic failure — with the added cost that the number would be *defended* by a green field
comparison.

**(b) Decompose into aggregating steps** — group by account, sum, post once. Rejected on the
measurement rather than on taste: aggregation computes sums over a transaction set whose
*membership* is what the ordering decides. Every order-independent decomposition has the same
defect, so this is not a decomposition problem.

**(c) A sequential step with step-scoped state** — chunk size 1, an in-memory account and balance
cache, transactions in file order. This is a faithful translation, and it is the standing upgrade
rather than a rejected option. It is not taken here because it needs a fact `design.json` does not
carry — *this step reads state it writes* — and inventing that field while also writing the first
consumer for it is how a contract gets shaped around one program. It wants its own decision, with
this ADR's numbers as its evidence.

**(d) Scope the comparison to the order-independent part.** There is no such part: acceptance is
what the ordering decides, so every one of the three output files inherits it.

## Consequences

- **`2 of 4` is not reachable by generating more of this program.** The reachable maximum stated in
  G17 stands, and the reason it is not yet reached is now specific: not "the wiring", not "the
  renderer", but one property of this program's control flow.
- **The finding generalises past this corpus**, and that is the part worth carrying: a batch program
  whose validation reads a file it updates cannot be translated item-by-item, however good the
  renderer is. `CBACT04C` gave no sign of this because its one accumulation was per-group and
  self-contained.
- **A mechanical check is deliberately not built here.** "Reads a file it writes back" is already
  derivable from `FileAccessPath` — but it is true of `CBACT04C`'s account writer too, which is
  correct and shipped, so that condition alone would fire on a green round trip. The distinguishing
  fact is whether two input items can reach the same written key, and no parse in this repo
  establishes it. Building a detector that is right about `CBTRN02C` and wrong about `CBACT04C`
  would be worse than the prose here. Named as the open question rather than half-answered.
- The measurement is pinned by `tests/system/test_cbtrn02c_order_dependence.py`, including a
  discrimination case, so a corpus change that alters these numbers fails rather than quietly
  invalidating this record.
