# ADR-0028: What the round-trip metric requires, and why five closed gaps did not move it

## Status

**Proposed** (2026-08-12). Does not close a gap. It answers a question that has been implicit since
the metric was adopted and never stated: **what would have to be true for `0 of 4` to become
`1 of 4`?**

Depends on [ADR-0021](0021-a-hand-computed-oracle-for-the-interest-equivalence-test.md), whose
refused option (a) turns out to be the binding constraint, and on
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md)'s
generation scope, which decides the metric's reachable maximum.

## Context

The platform tracks one headline number: **programs that round-trip COBOL → compiling Java →
passing differential test.** It was adopted deliberately (audit R2.4, finding 2) as *"a single
headline number that cannot be moved sideways"*, because a thirty-pillar scorecard rewards breadth
and this does not.

It has read `0 of 4` for every revision since. In one session five gaps closed — G9, G30, G29, G27,
and the eval harness — and it did not move. That is not an execution failure; it is the number doing
its job. But it is worth knowing **why**, because "keep closing gaps" is only a strategy if closing
gaps eventually moves it.

### Finding 1 — no program-level differential test exists, for any program

Step 45 is the only differential check in the repo, and it covers **one `COMPUTE` of one paragraph**.
ADR-0021 states that ceiling in three places on purpose. Nothing asserts that `CBACT04C` as a program
produces what `CBACT04C` produces.

So the metric is not blocked on generation quality. `CBACT04C` now has generated, tested Java for its
interest arithmetic (10 of 10 against the oracle), its transaction-record population (PR #44), and its
account posting (ADR-0027). **The missing artifact is the test, not the code.**

### Finding 2 — the corpus ships no expected output, verified across all nine files

A differential test needs COBOL's answer. `app/data/ASCII/` holds nine files and **every one is
input or reference data**:

| Read by a Track C program | Reference/master, not read by Track C |
|---|---|
| `acctdata` `tcatbal` `dailytran` `custdata` `discgrp` `cardxref` | `carddata` `trancatg` `trantype` |

There is no `transact`/`tranfile` output. `tcatbal.txt` is `CBTRN02C`'s **pre-posting input state**
(established at audit R2.9), not its output. Checked against all nine rather than the six previously
enumerated, because R2.9's own lesson was a conclusion generalised past three of nine files.

**So no oracle for whole-program output exists anywhere in this platform** — not in the corpus, not
in CI, not recorded.

### Finding 3 — the denominator is not four comparable programs

Gap G17 established that only `CBACT04C` and `CBTRN02C` carry real business logic, and ADR-0019
scopes three of `CBACT01C`'s four files out of generation, leaving it a sequential read and a print
— like `CBCUS01C`.

**The metric's reachable maximum is therefore `2 of 4` on the current scope**, and the denominator
has been quietly overstating what is achievable. Worth saying plainly rather than discovering it at
`2 of 4` and wondering why it stalled.

### Finding 4 — ADR-0021's trigger 2 has arrived without being declared

ADR-0021 named four events that would force option (a), a real COBOL runtime. The second was
*"a decision to compare written transaction records byte-for-byte, which makes the sign of zero
load-bearing."*

**That decision was never made, and the work has been behaving as though it were.** G28 exists
because *"an empty string and 50 blanks differ byte for byte"*. `CobolText.pad`/`spaces` exist to
produce exact widths. `fixed_width_text` is one of four judge criteria. The judge's most recent
finding was `TRAN-SOURCE` written six characters into a `PIC X(10)` field — a defect that **only
matters under byte comparison**. Three sessions of work have been aimed at byte-fidelity by
implication.

If byte-fidelity is the target, trigger 2 landed some time ago and **G23 should reopen to 🔴**.

## Decision

**Do not redefine the metric. State its price, and pay it once, for one program.**

### 1. The metric stays as it is

Redefining "passing differential test" to something reachable — self-consistency, a property check,
a field-level subset — would move the number without moving the capability. That is precisely the
sideways movement R2.4 adopted this metric to prevent, and the metric is more valuable than the
satisfaction of moving it. **`0 of 4` is correct and should stay until a real differential passes.**

### 2. The price is a recorded oracle, not a CI toolchain

ADR-0021 refused option (a) because it puts a second toolchain in CI and answers *a* COBOL rather
than the tenant's. Both objections are about **running COBOL continuously**. Neither applies to
running it **once**.

The proposal is a scoped spike: execute `CBACT04C` under GnuCOBOL against the shipped inputs, and
commit its output as a fixture with its provenance — the compiler, its version, the inputs' blob
hashes, and the dialect caveat stated in the file. That converts a runtime dependency into the thing
the corpus lacks: **a recorded expected output.** CI then compares generated Java against a committed
file, exactly as it compares against ADR-0021's committed literals today.

It is the same artifact ADR-0021 chose, at whole-program scale and machine-produced rather than
hand-derived — which is the only way it scales past nine rows.

### 3. The dialect caveat is recorded, not resolved

GnuCOBOL is not IBM Enterprise COBOL. For this program the exposure is narrow and nameable:
truncating `COMPUTE` is standard and already verified 10 of 10 against hand-derived values, so the
arithmetic is corroborated independently. What is *not* corroborated is `FUNCTION CURRENT-DATE`
formatting, `STRING ... DELIMITED BY SIZE` padding at the edges, and the sign of zero. Those three go
in the fixture header as known-unverified, and any disagreement in them is a finding to investigate
rather than a failure to fix.

## Consequences

**Good.** The headline number becomes movable for the first time, and by a route that does not
weaken it. One spike produces an oracle for a whole program; the existing generated code can then be
measured against it rather than against one `COMPUTE`. It also settles Finding 4 deliberately instead
of by drift.

**The cost, stated.** A GnuCOBOL run is a real dependency for the duration of the spike, and its
output is only as trustworthy as the dialect caveat allows. If `CBACT04C`'s generated Java disagrees
with it, the first question is which of the two is wrong — and answering that is exactly the work
ADR-0021 was avoiding. **That is the price of a whole-program oracle and there is no cheaper version
of it.**

**What this ADR does not do.** It generates nothing, tests nothing, and closes no gap. `TRAN-ID`
remains unpopulated by ADR-0026's decision, so even a passing differential would have to record that
field as excluded — and whether an excluded field disqualifies a round-trip is a question this ADR
raises and does not answer.

**The reachable maximum is `2 of 4`** until CICS or the out-of-scope constructs are addressed, and
the metric's denominator should be read with G17 beside it.

**If this is declined**, the honest consequence is that `0 of 4` cannot move, and the platform should
stop treating it as a progress metric and start treating it as a **statement of what has not been
attempted**. Continuing to close gaps against an unmovable number is the depth-first pattern the
audit has diagnosed seven times, wearing its most convincing disguise: real work, really finished,
that cannot reach the goal it is nominally serving.
