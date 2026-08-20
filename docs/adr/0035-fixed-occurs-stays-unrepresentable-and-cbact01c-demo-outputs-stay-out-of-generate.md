# ADR-0035: Fixed `OCCURS` stays unrepresentable, and `CBACT01C`'s demo outputs stay out of `generate`

## Status

**Accepted** (decision taken 2026-08-09; recorded here 2026-08-20).

Supersedes **decision 2** of
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md), which
bundled three independent decisions into one record. **Nothing here reverses that decision.**

Reaffirms [ADR-0011](0011-parse-every-data-division-section-and-reject-fixed-occurs.md) rather than
reversing it. Sibling records from the same split:
[ADR-0034](0034-java-25-on-maven-with-the-framework-version-pinned-in-the-build.md) (stack) and
[ADR-0036](0036-the-generated-jobs-persist-to-postgresql-loaded-once-from-carddemo-ascii-files.md)
(persistence).

## Context

ADR-0011 rejects fixed `OCCURS`: `PicMapping` has no cardinality field and `DomainField` cannot
express a collection, so mapping an array returns a correct precision and scale on a **wrong shape**
— one scalar where the record holds N. Java generated from that compiles and is wrong.

Milestone C4 forces the question again from the other side. `CBACT01C` declares
`ARR-ACCT-BAL OCCURS 5 TIMES` — Track C's only occurrence — and if `generate` is to cover all four
Track C programs, either the array becomes representable or the program's array-bearing outputs
leave the scope. Deciding by default would mean silently generating the wrong shape.

## Decision

`CBACT01C`'s `ARRY-FILE` and its `OUT-FILE` `COMP-3` field are **excluded from Milestone C4**.
ADR-0011's rejection of fixed `OCCURS` stands, and `PicMapping` does not change.

**The evidence is decisive and was nearly missed.** The fields inside that `OCCURS` group are
assigned hard-coded literals, not computed values (`CBACT01C.cbl:255-260`):

```cobol
MOVE   ACCT-CURR-BAL   TO   ARR-ACCT-CURR-BAL(1).
MOVE   1005.00         TO   ARR-ACCT-CURR-CYC-DEBIT(1).
MOVE   ACCT-CURR-BAL   TO   ARR-ACCT-CURR-BAL(2).
MOVE   1525.00         TO   ARR-ACCT-CURR-CYC-DEBIT(2).
MOVE   -1025.00        TO   ARR-ACCT-CURR-BAL(3).
MOVE   -2500.00        TO   ARR-ACCT-CURR-CYC-DEBIT(3).
```

`OUT-FILE`'s `COMP-3` field is the same (`CBACT01C.cbl:237`, `MOVE 2525.00`). **There is no business
rule in there to preserve.** `CBACT01C` is a COBOL feature demonstration wearing the shape of an
account-listing program.

**The rejected alternative was doing it anyway "for completeness".** Reversing ADR-0011 to represent
the array would ripple through `pic_mapper`, `DomainField`, `UnifiedDesign`, the generated schemas
and the `CBACT04C` golden fixture — a contract change across five layers, to reproduce four
constants. Completeness of an artifact nobody reads is not a reason to widen a contract.

Fixed `OCCURS` stays unsupported and stays Track B's B3, which already has its own stricter gate.
**`CBACT01C`'s driving read of `ACCTFILE-FILE` remains in scope** — it is only the three sequential
demo outputs that are excluded.

## Consequences

### `CBACT01C` contributes almost nothing to the generated application

Three of `CBACT01C`'s four files are excluded. What remains is a sequential read of the account
master and a print — a listing. Combined with `CBCUS01C`, which is also a listing, **only two of the
four Track C programs carry business logic into `card-service`**: `CBACT04C` (interest calculation)
and `CBTRN02C` (transaction posting).

That is a real limit on what a Track C demo can claim. It is tracked in the architecture audit as
gap **G17**, and it argues for a fifth genuinely-transactional program in Track B rather than for
widening C4. [ADR-0028](0028-what-the-round-trip-metric-requires-and-why-it-has-not-moved.md)
depends on this: the round-trip metric's reachable maximum is `2 of 4`, and that ceiling is this
decision's direct consequence rather than a defect in the pipeline.

### No `COMP-3` field is on the generation path

Track C's only two `COMP-3` fields are both in `CBACT01C`, and neither business program's copybooks
declare `COMP-3` at all. Excluding the demo outputs therefore removes packed decimal from C4's
critical path entirely. `pic_mapper` still maps `OUT-ACCT-CURR-CYC-DEBIT` correctly (`BigDecimal`,
precision 12, scale 2, signed) — **parsed and mapped is not the same as generated**, and this record
is about the latter. `docs/cobol-construct-support-matrix.md` carries the distinction per row.

### The capability gap is real and is not closed by scoping it out

A fixed `OCCURS` still cannot be represented, only flagged: the group is routed to the human gate
rather than mapped. That is the correct behaviour given the current types — flattening an array to a
scalar would be a wrong answer that looks right — but it remains a capability gap, tracked as such
in the QA report's
[open-gaps spoke](../qa/verification/11-not-yet-covered-open-gaps.md). Revisit it with Track B's B3
alias-analysis module, or if a Track C program ever needs an array on the generation path. Neither
condition holds today.

### What this record deliberately does not decide

- **Whether `FD` record layouts become domain entities.** That question is real and is left to
  ADR-0036's consequences, where the `ItemReader`'s type makes it concrete.
- **Anything about the target stack or persistence** — those are ADR-0034 and ADR-0036.
