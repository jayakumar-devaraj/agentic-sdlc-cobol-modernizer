# ADR-0043: The corpus carries IBM sign overpunches, and the oracle's runtime does not read them

## Status

**Accepted** (2026-08-21). Settles the disagreement `CBTRN02C`'s round trip surfaced (PR #80): seven
decisions differ from the oracle, every one of them traceable to an amount whose final byte is a
sign overpunch.

## Context

`DALYTRAN-AMT` is `PIC S9(09)V99` — eleven digit positions, `DISPLAY`, sign carried on the trailing
digit. In the ASCII rendering of an EBCDIC signed field the last byte is the low-order digit **and**
the sign: `{` is `+0`, `A`–`I` are `+1`…`+9`, `}` is `-0`, `J`–`R` are `-1`…`-9`.

`CBTRN02C`'s generated run and its GnuCOBOL oracle disagree about what those amounts are worth. The
question is about a number, so it gets [ADR-0021](0021-a-hand-computed-oracle-for-the-interest-equivalence-test.md)'s
treatment: **derive the answer by hand from the source and the standard, write the literals down,
and make the runtime match them.**

### The derivation

Every one of the twenty overpunch characters appears in this corpus, so the table is exhaustive
rather than sampled. Four of the twenty (the full set is in
`tests/system/test_overpunch_derivation.py`):

| raw `DALYTRAN-AMT` | last byte | derived value |
|---|---|---|
| `0000003250{` | `{` = +0 | **+325.00** |
| `0000005047G` | `G` = +7 | **+504.77** |
| `0000000567P` | `P` = −7 | **−56.77** |
| `0000009190}` | `}` = −0 | **−919.00** |

**The corpus corroborates the table from its own construction**, which is what makes twenty hand
literals more than twenty assertions of one person's arithmetic: in every record the digit
immediately *before* the overpunch equals the digit the overpunch carries, so the cents read as a
repeated pair — `...4161A` is 416.**11**, `...5047G` is 504.**77**, `...0709R` is −70.**99**. A
wrong table would break that pattern for eighteen of the twenty characters. It breaks for none.

### What the oracle's runtime does instead

Asked directly (`tools/cobol-oracle/OPTEST.cbl`, same image and dialect as the oracle run):

```
$ cobc -x -std=ibm OPTEST.cbl && ./optest
0000005047G (G = +7) ->        504.70
0000000567P (P = -7) ->         56.70
0000000294D (D = +4) ->         29.40
0000009190} (} = -0) ->        919.00
0000003250{ ({ = +0) ->        325.00
```

**GnuCOBOL 3.1.2 reads the overpunch byte as digit `0` and drops the sign**, even under `-std=ibm`.
Where the carried digit *is* zero (`{`, `}`) the magnitude survives and only the sign is lost;
everywhere else a digit is gone.

**It happens on input, not on output.** Account `00000000030` has exactly one posted transaction —
`0000000294D`, which is 29.44 — and starts the run with a cycle-credit of zero. GnuCOBOL's run ends
it at `000000002940`, and its balance moves 2.00 → 31.40. The lost digit is inside a *computed
total*, so the value that entered the `ADD` was already 29.40.

**This is not a bug in GnuCOBOL so much as a mismatch of conventions.** CardDemo is a mainframe
application and its corpus is written the way an IBM runtime writes a signed `DISPLAY` field;
GnuCOBOL in ASCII mode expects a different representation and does not recognise these bytes as
signed digits at all.

## Decision

**The overpunch table is correct as this repo implements it, and both decoders are pinned to
hand-written literals** — `data_loader.decode_zoned_decimal` and the template's
`CobolRecord.number`, the latter because it ships inside every generated project and is what a
migrated program reads its own input with.

**`transact-stage1.dat` is not evidence about `TRAN-AMT`.** It is a faithful record of what
GnuCOBOL did, and what GnuCOBOL did was compute on amounts missing a digit. The same applies to
which transactions its run accepted: the credit-limit comparisons behind those 43 rejections were
made against wrong values.

**Where a fix belongs, when one is wanted: in the oracle pipeline, not in this repo's decoders.**
`run-oracle.sh` already normalises the corpus's *other* representation quirks before either program
sees it — `LOADIDX` frames records, `DALYCONV` converts the daily file — and converting overpunched
fields into whatever GnuCOBOL reads as signed belongs beside them. Changing a decoder that agrees
with the standard, in order to agree with a runtime that does not, would be fixing the measurement
into the instrument.

## Consequences

- **`CBACT04C` is unaffected, and that is checked rather than assumed.** Its signed inputs all end
  in `{` (positive zero) or are not overpunched at all, and those are exactly the cases where the
  lossy reading and the correct one agree. **`500 of 500` and `598 of 600` stand.**
- **`CBTRN02C` has no usable oracle for its transaction amounts**, so it cannot reach `2 of 4` on
  the strength of this fixture. What it *has* is a run that builds, completes, and produces 256 of
  the oracle's 257 records, with the remaining difference explained down to a byte.
- **The round-trip metric stays `1 of 4`.** Stated plainly because the temptation runs the other
  way: the generated pipeline is now the side more likely to be right, and "our number is correct
  and the oracle is wrong" is exactly the claim that needs the strongest evidence rather than the
  loudest assertion. The evidence is four independent lines — the standard, the corpus's own
  construction, a computed total carrying the loss, and the compiler saying so directly — and it
  still does not license moving a metric that is supposed to mean *measured against COBOL*.
- `OPTEST.cbl` is committed beside the oracle tools so the probe is reproducible. It is a
  diagnostic, not part of the pipeline, and nothing depends on it.
