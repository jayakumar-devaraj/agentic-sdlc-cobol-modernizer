# COBOL Construct Support Matrix

Scope boundary for what this repo parses and modernizes. A construct not marked in-scope for
Track C must be detected and routed to a human-in-the-loop gate, never guessed at
([ADR-0002](adr/0002-a-hand-rolled-parser-for-a-deliberately-bounded-grammar.md)).

Verified against the real source of the four Track C programs and every copybook they `COPY`
(`CBACT04C`, `CBTRN02C`, `CBACT01C`, `CBCUS01C` and `CVTRA01Y`, `CVTRA02Y`, `CVTRA05Y`,
`CVTRA06Y`, `CVACT01Y`, `CVACT03Y`, `CVCUS01Y`, `CODATECN`) fetched directly from
`carddemo-tenant-service`, not assumed from the wider CardDemo application.

Two columns that are easy to conflate, kept distinct below: whether a construct is **in scope** for
Track C, and whether the parser **currently reaches** it. An earlier revision of this table
collapsed the two, which is how `COMP-3` came to be recorded as "not present in Track C" when
`CBACT01C` really does declare it — found by an exhaustive cross-check on 2026-08-07 and fixed in
ADR-0011. Keep them separate: "we decided not to handle this" and "we never looked" are different
claims, and only the first is a design decision.

| Construct | Track C | Track B | Notes |
|---|---|---|---|
| `WORKING-STORAGE` PIC clauses, numeric (`9`, `S9`) | In scope | In scope | Core of `pic_mapper`. |
| `WORKING-STORAGE` PIC clauses, alphanumeric (`X`) | In scope | In scope | Mapped to `String`. |
| Signed decimal (`PIC S9(n)V9(m)`) | In scope | In scope | Verified present: `ACCT-CURR-BAL` etc. (`CVACT01Y`, `S9(10)V99`), `TRAN-CAT-BAL`/`TRAN-AMT`/`DALYTRAN-AMT` (`S9(09)V99`), `DIS-INT-RATE` (`S9(04)V99`). All map to `BigDecimal`. |
| `COMP-3` packed decimal | In scope, **reached and mapped** | In scope | Corrected 2026-08-07 by an exhaustive numeric-field cross-check (`tests/system/test_numeric_field_coverage.py`). The earlier verdict here — "not present in Track C's scope" — was derived by reading every copybook the four programs `COPY`, where it is still true that none declares `COMP-3`. It does not hold for the programs' own source: `CBACT01C` declares `OUT-ACCT-CURR-CYC-DEBIT` and `ARR-ACCT-CURR-CYC-DEBIT` as `PIC S9(10)V99 USAGE IS COMP-3` in its `FILE SECTION`. `pic_mapper` always detected `COMP-3` correctly; nothing could reach it until ADR-0011. `OUT-ACCT-CURR-CYC-DEBIT` now maps (`BigDecimal`, precision 12, scale 2, signed); `ARR-ACCT-CURR-CYC-DEBIT` is isolated instead, for the unrelated reason in the `OCCURS` row below. Note for Milestone C4 ([ADR-0019](adr/0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md)): both live in `CBACT01C`'s scoped-out demo outputs, so **no `COMP-3` field is on the generation path** — the two business programs' copybooks declare none. Parsed and mapped is not the same as generated, and this row is about the former. |
| `FILE SECTION` (`FD`) record layouts and `LINKAGE SECTION` parameters | In scope, **parsed** (ADR-0011) | In scope | Previously read only from `WORKING-STORAGE`, leaving `FD` record layouts (the files each batch job reads and writes) and `LINKAGE SECTION` parameters (e.g. `CBACT04C`'s `EXTERNAL-PARMS`, named by its own `PROCEDURE DIVISION USING` clause) neither mapped nor flagged unsupported — absent. `extract_record_fields` now covers the whole `DATA DIVISION`. These fields do **not** become domain entities: `build_domain_entities` promotes only copybook-sourced fields (ADR-0010), and these are program-local. |
| `OCCURS` (fixed, no `DEPENDING ON`) | **Out of scope** — detected and gated | In scope | Reversed 2026-08-07 (ADR-0011); previously mapped, on the reasoning that a fixed `OCCURS` is unambiguous. It is — but `PicMapping` has no cardinality field, so mapping returns a correct precision and scale on a wrong shape: one scalar where the record holds N. Generating Java from that compiles and is wrong. Rejected with construct name `"OCCURS (fixed)"`, distinct from `OCCURS ... DEPENDING ON`. `CBACT01C`'s `ARR-ACCT-BAL OCCURS 5 TIMES` is the only occurrence in Track C. **Reaffirmed 2026-08-09 ([ADR-0019](adr/0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md))**: rather than reverse ADR-0011 to represent the array, Milestone C4 scopes `CBACT01C`'s `ARRY-FILE` and `OUT-FILE` outputs out of code generation entirely — the fields in question are assigned hard-coded literals (`CBACT01C.cbl:237,255-260`), so there is no business rule to preserve and a five-layer contract change would only reproduce constants. |
| Sequential / VSAM file I/O | In scope | In scope (full VSAM cluster ops in Track B) | **Corrected 2026-08-09 (ADR-0019).** This row previously read "read-only file I/O ... Track C only reads existing files", which is false: `ACCOUNT-FILE` is opened `I-O` and `REWRITE`n by both `CBACT04C` (line 356) and `CBTRN02C` (line 554), `CBTRN02C`'s `TCATBAL-FILE` is `WRITE`-or-`REWRITE` (an upsert, lines 510/528), and five files are opened `OUTPUT` and written. Of the 16 `SELECT`s across the four programs, 10 are `ORGANIZATION IS INDEXED` (VSAM KSDS) and 6 are `SEQUENTIAL`; 7 are `ACCESS MODE IS RANDOM`. What Track C genuinely does *not* do is define or delete clusters (no IDCAMS), which is the part of the original claim that survives. Full inventory in [ADR-0019](adr/0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md). |
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
