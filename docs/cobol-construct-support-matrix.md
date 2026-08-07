# COBOL Construct Support Matrix

Scope boundary for what this repo parses and modernizes. A construct not marked in-scope for
Track C must be detected and routed to a human-in-the-loop gate, never guessed at
([ADR-0002](adr/0002-a-hand-rolled-parser-for-a-deliberately-bounded-grammar.md)).

Verified against the real source of the four Track C programs and every copybook they `COPY`
(`CBACT04C`, `CBTRN02C`, `CBACT01C`, `CBCUS01C` and `CVTRA01Y`, `CVTRA02Y`, `CVTRA05Y`,
`CVTRA06Y`, `CVACT01Y`, `CVACT03Y`, `CVCUS01Y`, `CODATECN`) fetched directly from
`carddemo-tenant-service`, not assumed from the wider CardDemo application.

| Construct | Track C | Track B | Notes |
|---|---|---|---|
| `WORKING-STORAGE` PIC clauses, numeric (`9`, `S9`) | In scope | In scope | Core of `pic_mapper`. |
| `WORKING-STORAGE` PIC clauses, alphanumeric (`X`) | In scope | In scope | Mapped to `String`. |
| Signed decimal (`PIC S9(n)V9(m)`) | In scope | In scope | Verified present: `ACCT-CURR-BAL` etc. (`CVACT01Y`, `S9(10)V99`), `TRAN-CAT-BAL`/`TRAN-AMT`/`DALYTRAN-AMT` (`S9(09)V99`), `DIS-INT-RATE` (`S9(04)V99`). All map to `BigDecimal`. |
| `COMP-3` packed decimal | **Not present in Track C's scope** | In scope if encountered | Verified by reading every copybook the four Track C programs `COPY`: none declares `COMP-3` or any explicit `USAGE` clause — every numeric field is default `DISPLAY` (zoned decimal). The construct-matrix draft assumed COMP-3 might appear here; it does not. `pic_mapper` must still detect and correctly handle it for Track B / any future program, but no Track C fixture exercises that path. |
| Sequential / VSAM read-only file I/O | In scope | In scope (full VSAM cluster ops in Track B) | Track C only reads existing files; no VSAM cluster definition/IDCAMS handling. |
| Straight `COPY` | In scope | In scope | All four Track C programs use straight `COPY`, no `REPLACING`. |
| `COPY ... REPLACING` | **Out of scope** | In scope | Not present in any Track C program; would require resolving pseudo-text substitution. |
| `MOVE` / `COMPUTE` / `IF` / `EVALUATE` / `PERFORM` | In scope | In scope | Core `PROCEDURE DIVISION` verbs, hand-rolled parser ([ADR-0002](adr/0002-a-hand-rolled-parser-for-a-deliberately-bounded-grammar.md)) covers these. |
| `EXEC CICS` (online transaction verbs) | **Out of scope** | In scope | None of the four Track C programs are CICS (`CB`-prefix = batch, confirmed by their filenames and the absence of `EXEC CICS` in their source). |
| BMS screen maps | **Out of scope** | In scope | No BMS involvement in batch programs. |
| JCL job-scheduling semantics | **Out of scope** (JCL exists to invoke these programs but is not itself parsed) | In scope | Track C treats "this program is invoked by JCL" as given context, not a parsed artifact. |
| Embedded SQL / DB2 | **Out of scope** | In scope | Not present in any Track C program. |
| `REDEFINES` | **Out of scope** | In scope, highest-risk module (Milestone B3) | Not present in the copybooks Track C's four programs use. If encountered elsewhere, must route to a human gate, never auto-resolve ([ADR-0001](adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md) consequences). |
| `OCCURS DEPENDING ON` | **Out of scope** | In scope, same module as `REDEFINES` | Same treatment as `REDEFINES`. |
| RACF security | **Out of scope** | In scope if relevant | Batch programs read/write files directly; no RACF interaction modeled in Track C. |

## Track C program inventory (verified)

| Program | Copybooks (`COPY`) | Role in the narrative |
|---|---|---|
| `CBCUS01C.cbl` | `CVCUS01Y` | Customer master file processing |
| `CBACT01C.cbl` | `CVACT01Y`, `CODATECN` | Account master file processing |
| `CBTRN02C.cbl` | `CVTRA06Y`, `CVTRA05Y`, `CVACT03Y`, `CVACT01Y`, `CVTRA01Y` | Transaction posting |
| `CBACT04C.cbl` | `CVTRA01Y`, `CVACT03Y`, `CVTRA02Y`, `CVACT01Y`, `CVTRA05Y` | Interest calculation |

`CBCUS01C` and `CBACT01C` share no copybook dependency with each other and are the parallel-branch
pair in Milestone C3, step 3. `CBTRN02C` and `CBACT04C` both depend on `CVACT01Y` (account) and
`CVTRA01Y`/related transaction-category copybooks, consistent with running after both parallel
branches join.
