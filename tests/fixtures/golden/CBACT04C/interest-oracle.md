# `CBACT04C` interest — a hand-computed expected table

The oracle for step 45's equivalence test on `1300-COMPUTE-INTEREST`. Read this before trusting,
extending, or regenerating `interest-oracle.json`.

```cobol
1300-COMPUTE-INTEREST.
    COMPUTE WS-MONTHLY-INT
     = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200
```

| Field | `PIC` | Precision / scale | From |
|---|---|---|---|
| `TRAN-CAT-BAL` | `S9(09)V99` | 11, 2 | `CVTRA01Y` |
| `DIS-INT-RATE` | `S9(04)V99` | 6, 2 | `CVTRA02Y` |
| `WS-MONTHLY-INT` | `S9(09)V99` | 11, 2 | `WORKING-STORAGE` — the **receiving** field, so it sets the target scale |

## Why a table at all

Step 45 compares generated Java against the COBOL it came from, which needs COBOL's answer. There is
no mainframe here, no COBOL runtime in CI, and no recorded output in the CardDemo corpus. Of the
three ways to produce expected values, this is the middle one, chosen with its limits stated —
see `docs/adr/0021-hand-computed-oracle-for-the-interest-equivalence-test.md`.

**The option deliberately refused was deriving expected values in Python.** That would make step 45
compare generated Java against this repo's own re-implementation — two renderings of one
interpretation, agreeing with each other and with nothing external. Every value below is instead a
literal, with its arithmetic written out, so a reviewer can check any row with a pencil.

## What these numbers are evidence of, and what they are not

**They are evidence that the arithmetic is right**: truncation rather than rounding, toward zero
rather than toward negative infinity, at the receiving field's scale, on a divisor of 1200.

**They are not evidence that the program is right.** The table covers one `COMPUTE`. It says nothing
about the rate lookup, the `'DEFAULT'` group fallback, the per-account accumulation into
`WS-TOTAL-INT`, `1050-UPDATE-ACCOUNT`, or the contents of the transaction record `1300-B-WRITE-TX`
writes. A green step 45 means the interest arithmetic matches — no more than that, and the milestone
should not be reported as more.

## The one interpretive assumption, and why it is narrow

Everything rests on: **`COMPUTE` without `ROUNDED` truncates toward zero to the receiving field's
scale.** That is standard COBOL, it is already encoded and cited in `CobolArithmetic`, and ADR-0015's
four-model benchmark caught a model getting it wrong — so it is a documented failure mode rather than
a hypothetical one.

**The usual objection does not apply here.** Tables like this are normally fragile because COBOL's
intermediate precision is compiler-dependent, and a wider intermediate can change the answer. It
cannot change these: truncating toward zero at any scale ≥ 2 and then again at 2 gives exactly the
same result as truncating once at 2. **Every row is insensitive to intermediate precision.** That is
a property of truncation specifically, and it would *not* hold for a `ROUNDED` variant — double
rounding is real, and `CobolArithmetic.divideRounded` documents a case where it bites.

So the residual risk is one clearly-stated semantic, not an open-ended set of compiler behaviours.

## Two divergences recorded rather than asserted

**Overflow.** `WS-MONTHLY-INT` holds at most `999,999,999.99`, and the operands can exceed it: a
maximal balance times a maximal rate over 1200 is roughly `8.3e9`. COBOL without `ON SIZE ERROR`
silently discards **high-order** digits and continues; `CobolArithmetic.requireFits` deliberately
throws instead, and says so. That divergence is a decision this repo already made, not a defect —
but it means no overflow row can be both faithful and desirable, so **the table contains none**.
Asserting one would require observing a real COBOL runtime doing the silent truncation, which is the
oracle we do not have.

**The sign of zero (row R6).** `-0.00625` truncated toward zero is `-0`. COBOL's signed field can
carry that as a negative zero — the zoned-decimal overpunch distinguishes `}` (−0) from `{` (+0) —
and Java's `BigDecimal` has no negative zero, returning `0.00`. The *numeric value* is zero either
way, which is why R6 is marked `"compare": "numeric"`. It would matter only if step 45 ever compares
the written transaction record byte-for-byte rather than comparing values; if that day comes, this
row needs revisiting rather than reusing.

## The rows

Each carries its own derivation in the JSON. What the set is built to catch:

| Row | Balance × rate | Catches |
|---|---|---|
| R1 | `194.00` × `15.00` | rounding instead of truncating (exact tie, positive) |
| **R2** | `-194.00` × `15.00` | **rounding *and* floor, in one row** — a negative exact tie separates all three modes |
| R3 | `100.00` × `25.00` | a division with no scale and rounding mode — `BigDecimal` throws on this non-terminating quotient |
| R4 | `-100.00` × `25.00` | floor, on a non-terminating negative |
| R5 | `0.50` × `15.00` | inventing a cent from a sub-cent result |
| R6 | `-0.50` × `15.00` | floor on a sub-cent negative; also the sign-of-zero caveat above |
| R7 | `999.77` × `25.00` | rounding, at `dailytran.txt`'s largest real amount |
| R8 | `-998.33` × `15.00` | rounding and floor, at its smallest — without relying on an exact `.5` |
| R9 | `0.00` × `15.00` | conflating a zero *balance* with a zero *rate*; these are different control paths |
| R10 | `194.00` × `0.00` | **not an expected value.** The guard `IF DIS-INT-RATE NOT = 0` means the paragraph never runs — no interest, no accumulation, and **no transaction written**. An implementation returning `0.00` agrees numerically and is still wrong, because it emits a record COBOL does not |

R1, R7 and R8 use real values — a real `ACCT-CURR-BAL` from `acctdata.txt` and the extremes of
`dailytran.txt` — against the real rates in `discgrp.txt` (`15.00`, `25.00`, and `0.00` for R10).
The rest are chosen for discrimination, and are labelled as such rather than dressed up as samples.
