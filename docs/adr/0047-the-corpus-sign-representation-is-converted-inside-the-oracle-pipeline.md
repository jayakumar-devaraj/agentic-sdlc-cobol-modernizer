# ADR-0047: The corpus's sign representation is converted inside the oracle pipeline, in both directions

## Status

**Accepted** (2026-08-21). Implements the fix [ADR-0043](0043-the-corpus-uses-ibm-sign-overpunches-and-the-oracle-runtime-does-not-read-them.md)
located but deliberately did not make. Supersedes the record counts in
[ADR-0037](0037-a-file-written-both-ways-renders-as-an-upsert.md) and
[ADR-0038](0038-the-reject-file-is-scoped-out-of-generation-not-faked.md), which were measured
against the oracle this record replaces.

## Context

ADR-0043 established, four independent ways, that **GnuCOBOL 3.1.2 does not recognise the corpus's
IBM trailing sign overpunches**: it reads the byte as digit `0` and drops the sign, so
`0000005047G` — 504.77 — arrives in the program's arithmetic as 504.70. It stopped there on purpose.
The disagreement it explained was seven decisions in `CBTRN02C`'s round trip, and the honest
response to *"our number is right and the oracle is wrong"* was to write down the evidence, not to
move a metric.

It also said where a fix belonged if one were wanted: **in the oracle pipeline, beside `LOADIDX` and
`DALYCONV`**, which already convert the corpus's other representation quirks. Not in this repo's
decoders, because changing a decoder that agrees with the standard so it agrees with a runtime that
does not would be fixing the measurement into the instrument.

**The reason to take it now is the reason ADR-0028 gave for trusting this oracle at all**: that it
is COBOL's own output. For these fields it demonstrably was not — it was COBOL's output computed on
amounts missing a digit. A known-wrong instrument that produces a green number is worth less than a
faithful one that produces an unknown number.

### What was measured before implementing anything

Per [ADR-0044](0044-a-capability-closes-on-a-second-instance-and-a-caveat-carries-a-status.md)'s
reconnaissance rule, every signed field in every corpus file was scanned first, rather than the one
field the defect surfaced in:

| file | signed fields | sign-position characters |
|---|---|---|
| `acctdata.txt` | five `S9(10)V99` | `{` only, all 250 positions |
| `tcatbal.txt` | `TRAN-CAT-BAL` | `{` only, all 50 |
| `discgrp.txt` | `DIS-INT-RATE` | `{` only, all 51 |
| `dailytran.txt` | `DALYTRAN-AMT` | **all twenty**, across all 300 |

`{` is `+0` — the one overpunch where the correct reading and the lossy one agree. So three of the
four files needed nothing, and that is a measurement rather than an assumption. **One field, in one
file, needed converting.**

### What the runtime uses instead, asked directly

`OPTEST.cbl` answered what this runtime *reads*. `SIGNTEST.cbl` was added for what it *writes*, and
the two halves are not symmetric:

- It **writes** plain digits for positives and `q`–`y` for −1..−9. It never writes a negative zero:
  `COMPUTE` collapses −0 to +0 before the store.
- It **reads** `p` as −0 regardless.

That asymmetry decided one mapping. The corpus's `}` is −0, and half (a) alone would have argued for
mapping it to `0` — which reads back as **+919.00** where the corpus means −919.00.

## Decision

**The conversion is bidirectional, and GnuCOBOL's representation never leaves the container.**

1. **`SIGNCONV`** converts `DALYTRAN-AMT` on the way in, after `DALYCONV` frames the records —
   deliberately after, because the record ends in `FILLER X(20)` of spaces and `LINE SEQUENTIAL`
   output trims trailing spaces. An unrecognised byte is a hard failure: there is no defensible
   guess about a sign, and the run would otherwise still exit 0.
2. **`SIGNBACK`** converts every output back, so **the oracle directory is in the corpus's
   representation. Full stop.** Only the negative half is rewritten (`p`–`y` → `}JKLMNOPQR`);
   positives are plain digits in both conventions, which is what the previous fixture already
   carried and what this repo's own `CobolRecord.zoned` writes.
3. **`DALYCONV` keeps its own claim literally true.** Its header said it *"does not touch anything
   else"*, so the content change is a separate program rather than an addition to it. Framing and
   content are different claims and the pipeline listing shows both.
4. **A sign fingerprint of the corpus is asserted every run**, per file and per field, before
   anything is loaded.

### Why `SIGNBACK` exists at all, which is the least obvious part

Converting on the way in is enough to make the oracle *correct*. It is not enough to make it
*usable*, and the reason is that two of these files are not comparison targets:
`tcatbal-posted.dat` and `acctdata-stage1.dat` are **inputs to the generated Java**.

Leaving GnuCOBOL's signs on them would have forced `CobolRecord.number` to learn `p`–`y` — putting a
test harness's representation inside every migrated program this platform ships. That is ADR-0043's
own objection in mirror image, and it is a worse version of it: the decoder it would corrupt is not
a test tool but the production artifact.

The alternative considered and rejected was **teaching `data_loader` the union of both alphabets**.
They are disjoint, so it would have been unambiguous, and it is roughly ten lines against roughly two
hundred of COBOL. It was rejected because the split it creates is subtle — Python may read both,
Java may read one — and a subtle rule about which component speaks which representation is exactly
the kind that rots. One rule with no exceptions was worth the extra program.

## Consequences

**The numbers moved, and every one of them moved toward what the generated pipeline already
produced.** This is the part worth stating plainly, because it is also the part that would be
easiest to present as a triumph:

| | before | after | note |
|---|---|---|---|
| daily transactions rejected | 43 | **38** | five were rejected on amounts missing a digit |
| transaction master records | 257 | **262** | 262 is what the Java pipeline produced all along |
| `TCATBAL` rows after posting | 94 | **100** | 100 is what the Java pipeline produced all along |
| rows `CBTRN02C` creates | 44 | **50** | a suppressed acceptance suppresses its balance row too |
| `CBTRN02C` exit code | 4 | **0** | the warning code was itself a symptom |

**Two independent counts the pipeline was previously "wrong" about now agree exactly**, and they
were not fitted: 262 and 100 were both recorded in `test_cbtrn02c_round_trip` before this change,
as the numbers a *failing* comparison produced.

**`CBACT04C` was re-verified first, and its own logic was never in question.** Its inputs are
regenerated by the same run, so its measurement is against a new oracle rather than the old one.

**287 did not move**, and that is a check rather than a curiosity. The count of transactions a
stateless implementation would write is computed from the corpus through `decode_zoned_decimal`,
which was correct all along; only the split between *"rejected by COBOL"* and *"rejected only by
ordering"* moved, from 30-of-43 to 25-of-38. A change in the total would have meant this module's
own inputs had shifted.

**The order-dependence finding is unaffected.** ADR-0039's refusal stands: order dependence is a
property of the program, not of how its amounts were read.

**The zoned-decimal sign representation leaves the known-unverified list**, and the run's provenance
says so rather than quietly dropping it. `docs/qa/oracle-caveats.md` keeps its history, including
that the probe failed — the register's convention is that an uncomfortable answer is worth more than
the reassurance would have been.

**A cost, stated rather than hidden**: the oracle now depends on two more programs of this repo's
own COBOL, so the claim *"both tenant programs run unmodified"* carries more conversion around it
than it did. That claim is still exactly true, and it is the one that matters — nothing in `SIGNCONV`
or `SIGNBACK` touches a tenant program. But the surface where a conversion bug could hide is larger,
which is why both programs' counts are asserted per call, the corpus fingerprint is asserted before
the run, and all three refusals were shown to fire on deliberately damaged input.

**What this does not do**: it does not make GnuCOBOL the tenant's compiler. Three caveats remain on
the unverified list, and the conversion table is this runtime's, established by probing this runtime.
A different oracle compiler would need its own table and its own probe.
