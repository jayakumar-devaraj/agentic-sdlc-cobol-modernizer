# ADR-0048: `CBTRN02C`'s round trip counts, and its exclusions are earned rather than inherited

## Status

**Accepted** (2026-08-21). Moves the headline round-trip metric from `1 of 4` to **`2 of 4`, wiring
hand-written**. Depends on [ADR-0047](0047-the-corpus-sign-representation-is-converted-inside-the-oracle-pipeline.md),
without which the oracle this measures against was computing on amounts missing a digit.

## Context

`1 of 4` has always meant something specific, and the qualifier is machine-enforced: *generated logic
matches COBOL's own output, field-for-field at full declared width, inside wiring a human wrote.*
[ADR-0029](0029-the-differential-compares-fields-and-an-excluded-field-is-reported.md) fixes the comparison and
[ADR-0030](0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md) fixes the qualifier.

`CBTRN02C` has built and run since PR #79 and, after ADR-0047, agrees with its oracle on every
record. It was still not counted, for a reason worth stating plainly: **agreement on record identity
and one field is not the measurement the number names.** Reporting `2 of 4` on that would have been
a weaker measurement wearing a stronger one's name.

Three things had to be true, and each was a place the claim could have quietly narrowed.

### 1. The exclusions are this program's, not `CBACT04C`'s

`CBACT04C` excludes three fields under [ADR-0026](0026-job-parameters-reach-a-processor-and-the-per-run-counter-does-not.md).
**Two of the three do not transfer**, and inheriting them is the exact shape of exclusion creep
ADR-0029 names as the way a differential goes toothless:

| field | `CBACT04C` | `CBTRN02C` | transfers? |
|---|---|---|---|
| `TRAN-ID` | `STRING`s one from a per-run counter | `MOVE DALYTRAN-ID TO TRAN-ID` (`:425`) | **no** — compared |
| `TRAN-ORIG-TS` | reads the clock | `MOVE DALYTRAN-ORIG-TS TO TRAN-ORIG-TS` (`:436`) | **no** — compared |
| `TRAN-PROC-TS` | reads the clock | `PERFORM Z-GET-DB2-FORMAT-TIMESTAMP` per transaction, and that paragraph does `MOVE FUNCTION CURRENT-DATE TO COBOL-TS` (`:438`, `:693`) | **yes** |

So `CBTRN02C` carries **one** exclusion where `CBACT04C` carries three, and the one it carries was
established from its own source rather than by precedent. Its comparison is *stricter* than the one
the count was previously reported against, not weaker.

A test asserts the shape of that list, not merely its contents: exactly one field, and `TRAN-ID` and
`TRAN-ORIG-TS` explicitly absent. Widening it fails there first.

### 2. Every file the program writes is compared

`CBACT04C` writes two files and both are compared — that is what `1 of 4` has always covered.
`CBTRN02C` writes **four**: the transaction master, the account file, `TCATBAL`, and `DALYREJS`.
Comparing two of the three in scope would have narrowed the claim without saying so.

`DALYREJS` is out of scope for generation by [ADR-0038](0038-the-reject-file-is-scoped-out-of-generation-not-faked.md)
— refused by name in the job, which is a decision rather than an omission. A coverage test asserts
the job's output directory holds exactly the file compared here, so a fourth output appearing fails
rather than being read past.

### 3. A model wrote the body

Every comparison in the module runs on a *scripted* body transcribed from the COBOL statement for
statement. That measures the rendered wiring and the four contract facts behind it, which is what it
was written to measure. **It does not measure whether this pipeline's model writes a body that
reproduces COBOL**, and the round-trip count is a claim about generated logic.

`CBACT04C` has carried both halves since ADR-0030. Counting `CBTRN02C` on the scripted half alone
would have made the two halves of `2 of 4` mean different things — the shape of claim `CLAUDE.md`'s
*"a capability closes against a named instance"* rule exists to catch.

## Decision

**`CBTRN02C` counts, and the metric is `2 of 4`, wiring hand-written.** The qualifier is unchanged
and still enforced against `README.md` in the same paragraph as the number.

**Record ordering is framing and is sorted away before comparing.** The oracle is an indexed file
unloaded in key order; the candidate is a sequential write in arrival order. On this corpus the two
coincide — `dailytran.txt` is already `TRAN-ID`-sorted — but depending on that would turn a future
unsorted corpus into thousands of mismatches that say nothing about the logic. ADR-0029 already puts
record framing out of scope; ordering is framing.

## Consequences

**The measurement, on the same terms for both programs:**

| | scripted bodies | model-authored |
|---|---|---|
| `CBTRN02C` transactions (262 records, 12 of 13 fields) | **3144 of 3144** | **3144 of 3144** |
| `CBTRN02C` accounts (50 records, 12 of 12) | **600 of 600** | **600 of 600** |
| `CBTRN02C` balances (100 records, 4 of 4) | **400 of 400** | **400 of 400** |

**4,144 fields, one exclusion, nothing else skipped.** The live run took **one model call, first
attempt, no heal**, at a notional $0.35.

**`2 of 4` is the ceiling G17 names, and reaching it makes that gap's own claim testable.** `CBCUS01C`
and `CBACT01C` contribute a sequential read and a print (ADR-0035), so the remaining two cannot
round-trip in this sense. **Step 52 — "full recorded Track C dry run, all four programs" — is
therefore the open question, not this number.** As worded it may be unreachable, and it is Track C's
completion criterion.

**What is still not claimed.** The wiring is hand-written for both programs — file paths only
(ADR-0030), and the qualifier is why the number is never quoted bare. Nothing has been written to
`card-service`. `2 of 4` is a statement about a differential inside this repository.

**A cost worth naming**: the live test spends real money and is skipped unless
`COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, so CI proves the scripted half only. The model-authored
half is reproducible on demand rather than continuously, which is the same bargain ADR-0030 struck
for `CBACT04C` and is why both numbers are recorded here rather than only in a test's output.

**The model's own notes were evidence, not decoration.** Unprompted, the run derived the
`TRAN-PROC-TS` exclusion from the same two source lines this record cites, and identified that
rejected transactions vanish because `DALYREJS` has no owning step — ADR-0038's scoping, restated
from the source by something that had not read the ADR.
