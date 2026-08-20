# ADR-0036: The generated jobs persist to PostgreSQL, loaded once from CardDemo's ASCII data files

## Status

**Accepted** (decision taken 2026-08-09, amended the same day; recorded here 2026-08-20).

Supersedes **decision 3** of
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md) and its
amendment, which bundled three independent decisions into one record. **Nothing here reverses that
decision.** ADR-0019's same-day amendment is folded into the decision below rather than trailing it,
because the framework behaviour it recorded is now a premise, not a correction.

This record carries the **file-access inventory** that all three split records rest on. Siblings:
[ADR-0034](0034-java-25-on-maven-with-the-framework-version-pinned-in-the-build.md) (stack) and
[ADR-0035](0035-fixed-occurs-stays-unrepresentable-and-cbact01c-demo-outputs-stay-out-of-generate.md)
(scope).

## Context

[ADR-0009](0009-generated-java-targets-a-new-repo-card-service.md) chose Spring Batch and left the
store open. Step 40a cannot load data into a store nobody has chosen, and the template's shape needs
to know whether `ItemProcessor` lookups hit a database or a file.

### What the real COBOL says about file access

Every `SELECT` in the four Track C programs, verified 2026-08-09 by reading the fixture source
(`tests/fixtures/tenant_repo_sample/app/cbl/`), not inferred from the programs' names:

| Program | File | `ORGANIZATION` | `ACCESS MODE` | `OPEN` | Verbs | Role |
|---|---|---|---|---|---|---|
| `CBCUS01C` | `CUSTFILE-FILE` | `INDEXED` | `SEQUENTIAL` | `INPUT` | `READ` | driving |
| `CBACT01C` | `ACCTFILE-FILE` | `INDEXED` | `SEQUENTIAL` | `INPUT` | `READ` | driving |
| `CBACT01C` | `OUT-FILE` | `SEQUENTIAL` | `SEQUENTIAL` | `OUTPUT` | `WRITE` | demo output |
| `CBACT01C` | `ARRY-FILE` | `SEQUENTIAL` | `SEQUENTIAL` | `OUTPUT` | `WRITE` | demo output |
| `CBACT01C` | `VBRC-FILE` | `SEQUENTIAL` | `SEQUENTIAL` | `OUTPUT` | `WRITE` | demo output |
| `CBACT04C` | `TCATBAL-FILE` | `INDEXED` | `SEQUENTIAL` | `INPUT` | `READ` | driving |
| `CBACT04C` | `XREF-FILE` | `INDEXED` | `RANDOM` | `INPUT` | `READ` | keyed lookup |
| `CBACT04C` | `DISCGRP-FILE` | `INDEXED` | `RANDOM` | `INPUT` | `READ` ×2 | keyed lookup |
| `CBACT04C` | `ACCOUNT-FILE` | `INDEXED` | `RANDOM` | `I-O` | `READ`, `REWRITE` | lookup + update |
| `CBACT04C` | `TRANSACT-FILE` | `SEQUENTIAL` | `SEQUENTIAL` | `OUTPUT` | `WRITE` | output |
| `CBTRN02C` | `DALYTRAN-FILE` | `SEQUENTIAL` | `SEQUENTIAL` | `INPUT` | `READ` | driving |
| `CBTRN02C` | `XREF-FILE` | `INDEXED` | `RANDOM` | `INPUT` | `READ` | keyed lookup |
| `CBTRN02C` | `ACCOUNT-FILE` | `INDEXED` | `RANDOM` | `I-O` | `READ`, `REWRITE` | lookup + update |
| `CBTRN02C` | `TCATBAL-FILE` | `INDEXED` | `RANDOM` | `I-O` | `READ`, `WRITE`, `REWRITE` | lookup + upsert |
| `CBTRN02C` | `TRANSACT-FILE` | `INDEXED` | `RANDOM` | `OUTPUT` | `WRITE` | output |
| `CBTRN02C` | `DALYREJS-FILE` | `SEQUENTIAL` | `SEQUENTIAL` | `OUTPUT` | `WRITE` | reject output |

Sixteen files: **10 `INDEXED` (VSAM KSDS), 6 `SEQUENTIAL`; 7 opened `RANDOM`, 3 opened `I-O`.**

**A correction, recorded rather than quietly fixed.** The first version of this analysis said *"these
are VSAM KSDS files, not flat files — every program uses `ORGANIZATION IS INDEXED`."* Counting the
`SELECT`s says otherwise. Every program has *at least one* indexed file, but `CBTRN02C`'s **driving
dataset is a plain sequential file** (`DALYTRAN-FILE`) and every output except `CBTRN02C`'s
`TRANSACT-FILE` is sequential too. The conclusion below survives unchanged — it never depended on
the sweeping version — but "not flat files" was false, and a template built on it would have had no
`FlatFileItemReader` at all for the one program that genuinely needs one.

Three things the table settles that a naive
`FlatFileItemReader → ItemProcessor → FlatFileItemWriter` shape gets wrong:

1. **Only the driving dataset is an `ItemReader`.** `CBACT04C` reads `TCATBAL-FILE` sequentially and
   then does **four random keyed reads per record** — `XREF-FILE`, `ACCOUNT-FILE`, and
   `DISCGRP-FILE` twice (the second is the hard-coded `'DEFAULT'` group fallback on
   `DISCGRP-STATUS = '23'`). `CBTRN02C` does the same shape from a flat driving file. Those lookups
   are **repositories injected into the `ItemProcessor`**, not four more readers: a reader is
   positional and a keyed lookup is not.
2. **Three files are read-modify-write.** `ACCOUNT-FILE` is opened `I-O` and `REWRITE`n by both
   business programs; `CBTRN02C`'s `TCATBAL-FILE` is `WRITE`-or-`REWRITE` — an upsert. The
   long-standing claim in `docs/cobol-construct-support-matrix.md` that *"Track C only reads
   existing files"* was false and was corrected against this table.
3. **`CBACT01C` writes nothing anyone reads.** All three of its outputs are sequential dumps, which
   is what [ADR-0035](0035-fixed-occurs-stays-unrepresentable-and-cbact01c-demo-outputs-stay-out-of-generate.md)
   scopes out.

## Decision

The Spring Batch jobs run against **PostgreSQL**. A loader (step 40a) reads CardDemo's ASCII data
files into it once. Reasons, strongest first:

1. **Spring Batch's `JobRepository` needs a relational store anyway.** Restart-from-checkpoint is
   the entire reason ADR-0009 chose Spring Batch, and the `JobRepository` is what provides it.
   Postgres is therefore running regardless — so keeping domain data in files means operating *two*
   stores and getting transactional consistency between them wrong at some point.
2. **There is no credible Java VSAM story** for what the programs actually do. Seven `RANDOM` keyed
   reads and three `I-O` read-modify-writes across the two business programs is not "read a file";
   `RECORD KEY` → primary key and `ALTERNATE RECORD KEY` → secondary index is one line of DDL each,
   against hand-rolling keyed B-tree access over a fixed-width file.
3. **The REST layer ADR-0009 already committed to needs the same indexed random access.** Point
   queries over a flat file are a table scan.
4. **`pic_mapper`'s precision and scale map straight onto `NUMERIC(p, s)`.** The zero-drift property
   this repo exists to guarantee becomes a database constraint rather than an application-layer
   convention — `ACCT-CURR-BAL PIC S9(10)V99` is `NUMERIC(12, 2)`, and a defect that would silently
   truncate becomes an error at the boundary.

**A weak argument was withdrawn, and the conclusion survived it.** The first version led with
*"Postgres collapses the packed-decimal problem."* Once `CBACT01C`'s demo outputs are excluded there
is no packed decimal on C4's critical path whatsoever, so that leg was solving a problem which does
not exist here. The conclusion held on its other four legs. Recorded because an argument that
survives on reasons other than the one it was sold on is worth re-deriving in public, not quietly
re-labelling.

The schema is **derived from the copybooks via `pic_mapper`, never hand-written** — a hand-written
schema is a second, independently-maintained description of a record layout the repo already
computes, and the two would diverge.

### Schema ownership belongs to step 40a, not to a framework default

ADR-0019 originally left schema ownership open, on the assumption that Spring Boot's
`spring.batch.jdbc.initialize-schema` could create Spring Batch's metadata tables in the meantime.
**Spring Boot 4 removed `spring.batch.jdbc.*` from `BatchProperties` entirely.** There is no
`initialize-schema` any more, and because unknown configuration keys are silently ignored, setting
one looks decided and does nothing.

Found by running it, not by reading release notes: `BaselineStackTest`'s first version set
`spring.batch.jdbc.initialize-schema=always` and then counted **zero** batch tables against a real
PostgreSQL container. Every pre-Boot-4 Spring Batch example sets that property, which makes it a
trap the code generator will walk into by default — the same shape as the `@EnableBatchProcessing`
annotation that now switches auto-configuration *off*.

So the metadata schema ships as `org/springframework/batch/core/schema-postgresql.sql` inside
`spring-batch-core` and is applied by whatever applies the domain schema `pic_mapper` derives. That
makes migration tooling a **step 40a** concern: the loader already has to create the domain tables,
and this is one more script alongside them. The template's `application.yml` states the property's
absence and why, rather than carrying a dead key.

## Consequences

### Byte-level output equivalence is off the table — and never was on it

Migrating to a database means the generated app cannot produce byte-identical VSAM output for
comparison against a mainframe run. That sounds like a real loss and is not: **no COBOL runtime
exists anywhere in this platform** — no GnuCOBOL, no z/OS, no emulator — so there has never been a
reference execution to compare bytes against. Step 45 already specified **numeric** equivalence
(JUnit tests over the interest math), which is what the equivalence gate actually tests.
[ADR-0029](0029-the-differential-compares-fields-and-an-excluded-field-is-reported.md) rests on this
directly: the target does not produce a file to compare, so the differential compares fields.

### The data-file precondition was verified, not assumed — and it found three things

This decision was drafted with the precondition stated as unverified: CardDemo's *real* data files
had never been looked at, since this repo's fixture is source only. It was checked against
`carddemo-tenant-service` directly (GitHub Contents API, 2026-08-09), and three facts change what
step 40a has to build. All three are the same shape — **the file does not look like the copybook says
it does** — which is itself the finding worth carrying into step 40a:

**Real data exists, in both encodings.** `app/data/ASCII/` holds nine fixed-width `.txt` files and
`app/data/EBCDIC/` holds the mainframe `.PS` datasets. The ASCII set is the loader's source; the
EBCDIC set is not, and no EBCDIC transcoding is in C4's scope.

**Record length cannot be taken from the copybook.** Five of the six files a Track C program reads
match their copybook's declared `RECLN` — `acctdata.txt` 50 × 300 (`CVACT01Y`), `tcatbal.txt`
50 × 50 (`CVTRA01Y`), `dailytran.txt` 300 × 350 (`CVTRA06Y`), `custdata.txt` 50 × 500 (`CVCUS01Y`),
`discgrp.txt` 51 × 50 (`CVTRA02Y`). **`cardxref.txt` is 36 bytes per record against `CVACT03Y`'s
declared `RECLN 50`**: exactly the 16 + 9 + 11 of its three real fields, with the trailing
`FILLER PIC X(14)` absent from the file. A reader built to the copybook's length misaligns every
record after the first, and `XREF-FILE` is a keyed lookup in **both** business programs. The
loader's record length is therefore a property of the data file, verified per file, not a number
read off a comment.

**Signed numeric fields carry a zoned-decimal sign overpunch, so they are not parseable as digits.**
`ACCT-CURR-BAL` in the first `acctdata.txt` record is `00000001940{`, not `000000019400` — the
trailing byte encodes both the final digit and the sign (`{` = +0, `}` = −0, `A`–`I` = +1…+9,
`J`–`R` = −1…−9). `dailytran.txt` contains real negatives. **`Integer.parseInt` or
`new BigDecimal(String)` on those twelve characters throws**, and a loader that strips the last
character instead would be off by a factor of ten with the sign dropped — wrong money that looks
right, which is this platform's headline risk. Decoding overpunch is a required, testable part of
step 40a, derived from `pic_mapper`'s existing signedness and scale rather than hand-coded per
field.

**And one file's line endings are inconsistent.** `tcatbal.txt` has **49 `CR` bytes against 50
`LF`** — 49 of its 50 records are `CRLF`-terminated and one is not; every other ASCII data file is
pure `LF` (verified by byte count, not by opening it in an editor). `tcatbal.txt` is `CBACT04C`'s
driving dataset, so this lands on the interest calculator specifically. A loader that reads
fixed-width slices without normalising will carry a `\r` into the last field of 49 records out of
50. This is the same defect class as `CODATECN.cpy`'s real `CRLF` line endings (PR #10), from the
same upstream repo, found the same way: by counting bytes instead of trusting that a text file is
uniform.

### `FD` record layouts now need a decision that ADR-0010 deferred

`build_domain_entities` promotes only copybook-sourced fields, so the program-local `FD` layouts
(`FD-ACCTFILE-REC` and friends) are parsed and visible in `spec.md` but are not domain entities.
Choosing PostgreSQL makes this concrete rather than theoretical: the driving `ItemReader` needs a
type to read into, and the `FD` layout is what describes the record on disk. This record does not
answer it — it belongs with step 39 (`modernization_engineer`), where the generated type actually
appears — but it is no longer an open question that can be left alone.

### Generation is scoped to processors, and downstream records depend on that

Because the lookups are repositories injected into an `ItemProcessor` rather than readers, `generate`
renders **`ItemProcessor`s only**. That scope is the premise of
[ADR-0023](0023-a-step-this-pipeline-does-not-render-is-reported-not-dropped.md),
[ADR-0026](0026-job-parameters-reach-a-processor-and-the-per-run-counter-does-not.md),
[ADR-0027](0027-the-account-break-becomes-a-second-pass-over-pre-aggregated-items.md) and
[ADR-0030](0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md); all four continue
to cite ADR-0019 as written on their own dates, and none of them is reopened by this split.

### What this record deliberately does not decide

- **How the loader is invoked in production.** Step 40a builds it as a one-time migration; whether
  it ever runs outside a demo is a Milestone C5 question.
- **Which migration tool.** Choosing Flyway or Liquibase belongs with the first schema that has to
  *change*, not with the first one that has to exist.
- **Anything about the target stack or the `OCCURS` scope** — those are ADR-0034 and ADR-0035.
