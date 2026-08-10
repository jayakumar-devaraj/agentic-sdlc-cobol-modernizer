# ADR-0021: The interest equivalence test gets a hand-computed oracle, not a COBOL runtime

## Status

Accepted (2026-08-10). Unblocks the arithmetic half of step 45, which had real inputs and no source
of expected values.

Depends on [ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md)
for the data path the inputs travel, and shares its posture with
[ADR-0011](0011-parse-every-data-division-section-and-reject-fixed-occurs.md): a translation that
compiles and is wrong is the failure this repo is built to catch.

## Context

Step 45 compares generated Java against the COBOL it came from. That needs **COBOL's answer**, and
nothing in this platform supplies one: there is no mainframe, no COBOL runtime in CI, and no
recorded program output anywhere in the CardDemo corpus.

The input half is settled. PR #34 found every `TRAN-CAT-BAL` in the shipped data is zero, so an
equivalence test on it would pass for any implementation returning zero. PR #35 found why — the file
is the state before `CBTRN02C` posts to it — and found `dailytran.txt`, 300 real records with 299
distinct signed amounts. Inputs are real and no longer the problem.

The expected values are. Three options, and the reason this is an ADR rather than a commit message
is that each buys something different and the cheapest one is not obviously wrong.

### The options

**(a) Run the COBOL.** Put GnuCOBOL, or a real mainframe run, into the loop and diff its output.
This is the only option under which the word *equivalence* means what it says. It is also the only
one that can settle questions nobody has thought to ask — sign of zero in a signed field, high-order
truncation on overflow, whatever the next surprise is. The cost is a second toolchain in CI, and a
dialect question: GnuCOBOL is not IBM Enterprise COBOL, so it answers *a* COBOL rather than *the*
COBOL the tenant runs, and the difference is exactly in the arithmetic corners this test targets.

**(b) Hand-compute a small table.** Derive expected values from the `COMPUTE` by hand, write the
arithmetic down, commit the literals. Cheap, auditable by a reviewer with a pencil, no new
toolchain. Its ceiling is real and worth stating plainly: **it can only ever test arithmetic
someone already understood well enough to compute.** It cannot discover a semantic nobody thought
about, which is precisely what an oracle is most valuable for.

**(c) Derive expected values in Python.** Write the rule once, generate the table, compare Java to
it. Cheapest, scales to any number of rows — and it is the option to refuse. It makes step 45
compare generated Java against this repo's own re-implementation: two renderings of one
interpretation, agreeing with each other and with nothing external. This platform has produced four
checks that cannot fail in a week; this would be the fifth, and it would be the most expensive one,
because it would look like the strongest test in the suite.

## Decision

**Take (b) now, with its ceiling documented in the artifact itself. Keep (a) as the upgrade, and
record what would trigger it. Refuse (c) as an oracle.**

The oracle is `tests/fixtures/golden/CBACT04C/interest-oracle.json`: nine rows of literals, each
with its derivation, plus a tenth case that deliberately carries no expected value.

Three things make this more than a table of numbers:

1. **The rows discriminate rather than sample.** Each records what a named wrong implementation
   would produce, and a test asserts those really are what those modes produce — a wrong `rejects`
   entry makes a row decoration. `R2` (`-194.00` × `15.00`) separates truncate-toward-zero, round,
   and floor with one input.
2. **The zero-rate case is not written as `0.00`.** `IF DIS-INT-RATE NOT = 0` skips the paragraph,
   so no interest is computed, nothing accumulates, and **no transaction record is written**. An
   implementation returning `0.00` agrees numerically and is still wrong. It is held outside the
   `rows` list so a harness cannot consume it as an expected value.
3. **Two divergences are recorded rather than asserted.** Overflow is reachable — a maximal balance
   times a maximal rate over 1200 exceeds `WS-MONTHLY-INT` — and COBOL discards high-order digits
   silently where `CobolArithmetic.requireFits` throws by deliberate design. No row can be both
   faithful and desirable, so there is none. Separately, `-0.00625` truncates to a negative zero a
   COBOL signed field can hold and `BigDecimal` cannot.

### Why (b) is safer here than it looks

Tables like this are normally fragile because COBOL's intermediate precision is compiler-dependent.
**That objection does not apply to a truncating `COMPUTE`**: truncating toward zero at any scale
≥ 2 and then again at 2 equals truncating once at 2, so every row is insensitive to intermediate
precision. The property is specific to truncation and would not hold for `ROUNDED`, where double
rounding is real and `CobolArithmetic.divideRounded` already documents a case that bites.

So the residual risk is one clearly-stated semantic — *`COMPUTE` without `ROUNDED` truncates toward
zero to the receiving field's scale* — rather than an open-ended set of compiler behaviours. That
semantic is standard, is already encoded and cited in `CobolArithmetic`, and ADR-0015's four-model
benchmark caught a model getting it wrong, so it is a measured failure mode rather than a
hypothetical one.

### A recompute is allowed; an oracle is not

A test does recompute each row with exact rationals and compare. That is double-entry, not option
(c): the literals were derived first and by hand, the recompute catches a transcription slip in a
JSON file, and it is labelled in the test module as unable to validate the COBOL reading, because
both sides encode the same one. The distinction that matters is **what step 45 asserts against** —
a committed literal a human checked, never a value computed at test time.

## Consequences

**Good.** Step 45's arithmetic half can be built now, with no new toolchain and no fabricated data.
Every expected value is reviewable without executing anything. The table is built to fail: a
mutation changing `R2` to the rounded answer fails three tests.

**Accepted cost, stated rather than discovered later.** This tests one `COMPUTE`. It says nothing
about the rate lookup, the `'DEFAULT'` group fallback, the accumulation into `WS-TOTAL-INT`,
`1050-UPDATE-ACCOUNT`, or the transaction record's contents. **A green step 45 means the interest
arithmetic matches, and the milestone must not be reported as more than that.** It also cannot
discover a semantic nobody anticipated — the thing (a) would be for.

**What would trigger (a).** Any one of: a row whose expected value cannot be settled by reading the
standard; a decision to compare written transaction records byte-for-byte, which makes the sign of
zero load-bearing; overflow behaviour becoming something the target must reproduce rather than
reject; or a second tenant whose compiler dialect is not assumable. Until one of those lands, a
COBOL runtime in CI buys certainty this test does not need.

**This does not close the round-trip metric.** `0 of 4` needs COBOL → compiling Java → passing
differential test, and this ADR supplies the third leg's expected values for one paragraph of one
program. It is a prerequisite, not the milestone.
