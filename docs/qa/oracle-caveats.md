# Oracle caveats — what the recorded oracle is not known to get right

The oracle in `tests/fixtures/golden/CBACT04C/oracle/` is what the **unmodified** tenant programs
wrote under GnuCOBOL 3.1.2 (ADR-0028). GnuCOBOL is not the tenant's compiler, so its own
`PROVENANCE.md` carries a *Known-unverified against IBM Enterprise COBOL* list.

**That list is an obligation, not a footnote.** One of its entries — the zoned-decimal sign
representation — sat in prose for four revisions while every downstream document treated the oracle
as ground truth, and it came due as seven wrong decisions in `CBTRN02C`'s round trip (ADR-0043,
audit G33). This file exists so that cannot happen quietly again: every caveat the provenance names
has a row here, and `tests/unit/test_oracle_caveats.py` fails if one does not.

Each row is one of two things, and says which:

- **Probed** — an executable check exists, is named, and can be re-run.
- **Accepted, untested** — with the consequence written out: what would be wrong, and how anyone
  would notice.

"We are aware of it" is neither, and is not a status.

| # | Caveat | Status | Evidence, or the consequence of being wrong |
|---|---|---|---|
| 1 | **The zoned-decimal sign representation** | **Probed — and it failed** | `tools/cobol-oracle/OPTEST.cbl` reads the same eleven bytes as `S9(09)V99` under the same image and dialect. GnuCOBOL returns **504.70** for `0000005047G`, which the standard makes **504.77**: it reads the overpunch byte as digit `0` and drops the sign. Corroborated three further ways in ADR-0043, including a computed total that carries the loss. **Consequence, realised**: `transact-stage1.dat` was not evidence about `TRAN-AMT`, and neither were the 43 rejections its run recorded. `CBACT04C` was unaffected — its signed inputs end in `{` or carry no overpunch, exactly where the two readings agree — and that was checked, not assumed. Tracked as audit **G33**. **Resolved 2026-08-21 (ADR-0047)**: `tools/cobol-oracle/SIGNTEST.cbl` probed the other half — what this runtime writes (plain digits positive, `q`–`y` for −1..−9) and reads back (`p` is −0, though it never writes one) — and `tools/cobol-oracle/SIGNCONV.cbl` now converts the corpus on the way in, `tools/cobol-oracle/SIGNBACK.cbl` back on the way out. The oracle's own counts moved to 38 rejects and 262 records, both of which the generated pipeline had already produced. The row stays *probed and it failed* because that is what happened; the fix is the consequence, not a re-grading of the answer. |
| 2 | **`FUNCTION CURRENT-DATE` formatting** | Accepted, untested | Only reaches `TRAN-ORIG-TS` and `TRAN-PROC-TS`, both excluded from the differential by ADR-0026 because a generated processor is handed one instant per run where COBOL reads the clock per record. **If the format differed from IBM's**, nothing in the comparison would see it, and a migrated program would write a timestamp the tenant's downstream readers reject. Would surface the first time a real consumer parses one. Cheap to probe with the same technique as caveat 1, and not yet worth it while both fields are excluded. |
| 3 | **`STRING ... DELIMITED BY SIZE` padding at the edges** | Accepted, untested | `CBACT04C` builds `TRAN-ID` this way, and `TRAN-ID` is excluded by ADR-0026 for an unrelated reason (a per-run counter a stateless processor cannot reproduce). **If the padding differed**, the field would be wrong in a way this corpus's exclusion currently hides. **`CBTRN02C` does not build `TRAN-ID` this way** — it copies `DALYTRAN-ID` — so the exclusion is inherited rather than earned there, and this caveat becomes live the moment that program's transactions are compared. |
| 4 | **The sign of zero** | Accepted, untested | ADR-0021's hand-derived table includes a negative-zero row and GnuCOBOL agreed with it 9 of 9, so the *arithmetic* is corroborated. What is not is how a zero-valued signed field is **written**: `{` (+0) and `}` (−0) are distinct bytes carrying the same value. **If they differ from IBM's choice**, a field-for-field comparison still passes — ADR-0029 compares values — and a byte-for-byte one would not. Byte fidelity is declined (ADR-0029), so this stays accepted until something asks for bytes. |

## When this file changes

- **A new caveat appears in `PROVENANCE.md`** — add a row in the same change. The test will fail
  until you do, which is the point.
- **A caveat is probed** — record the probe by name and what it returned, whether or not the answer
  was the comfortable one. Caveat 1 is the example: the probe failed, and saying so is worth more
  than the reassurance would have been.
- **A caveat becomes live** — when a program or a comparison starts depending on the field it
  covers, it stops being deferrable. Caveat 3 says exactly when that is.
- **A caveat is resolved** — record the fix *in the existing row*, and leave the original status and
  finding standing. Caveat 1 is the example: it still reads *probed — and it failed*, because it
  did, and a register that rewrote failures into successes once they were fixed would lose the only
  thing it is for. The row is also **not deleted**: the entry that came due as seven wrong decisions
  is the entry most worth being able to find again.
