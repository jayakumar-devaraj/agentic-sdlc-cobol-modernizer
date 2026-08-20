# ADR-0019: PostgreSQL persistence, a bounded `generate` scope, and the target stack for `card-service`

## Status

**Superseded** (2026-08-20) by three records, one per decision. Accepted 2026-08-09; amended the
same day. Prerequisite for Milestone C4 — step 38 cannot write a `pom.xml` without a language
level, and step 40a cannot load data into a store nobody has chosen.

**Nothing below was reversed.** This record bundled three independent decisions, so a reference to
"ADR-0019" could not say which one it meant, and none of the three could be superseded without
reopening the other two. The text below is left exactly as accepted — it is the historical record,
and the corrections it contains (the withdrawn VSAM claim, the withdrawn packed-decimal argument)
are the part worth not editing. The decisions **in force** live here:

| This ADR's decision | Now recorded in |
|---|---|
| 1. Java 25, Maven not Gradle, framework version pinned in the build | [ADR-0034](0034-java-25-on-maven-with-the-framework-version-pinned-in-the-build.md) |
| 2. Fixed `OCCURS` scoped out of `generate`; `PicMapping` unchanged | [ADR-0035](0035-fixed-occurs-stays-unrepresentable-and-cbact01c-demo-outputs-stay-out-of-generate.md) |
| 3. PostgreSQL, loaded once from CardDemo's ASCII files — and the amendment on schema ownership | [ADR-0036](0036-the-generated-jobs-persist-to-postgresql-loaded-once-from-carddemo-ascii-files.md) |

The `SELECT` inventory in the Context below is carried forward verbatim into ADR-0036, which is the
record that rests on it. ADRs 0021, 0023, 0026–0030 and 0032 cite this one as it stood on their own
dates and are deliberately not rewritten; live pointers in code, config and the construct matrix
were moved to the successor that owns the decision they name.

Builds on [ADR-0009](0009-generated-java-targets-a-new-repo-card-service.md), which decided *where*
generated Java lives (`card-service`) and *what execution model* it uses (Spring Batch), and
deliberately left everything else open. Depends on
[ADR-0011](0011-parse-every-data-division-section-and-reject-fixed-occurs.md), whose fixed-`OCCURS`
rejection this ADR declines to reverse.

## Context

ADR-0009 is a year-zero decision: a new repo, Spring Batch, one deployable app, a provenance
convention across the repo boundary. It answers none of the three questions step 38 actually
blocks on — what Java, what the batch jobs read and write, and whether all four Track C programs
are in `generate`'s scope at all. Those are decided here, from the real source rather than from the
shape a Spring Batch tutorial assumes.

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
`TRANSACT-FILE` is sequential too. The design conclusion below survives unchanged — it never
depended on the sweeping version — but "not flat files" was false, and a template built on it would
have had no `FlatFileItemReader` at all for the one program that genuinely needs one.

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
   existing files"* is false and is corrected in this change.
3. **`CBACT01C` writes nothing anyone reads.** All three of its outputs are sequential dumps, and
   what they contain is the subject of Decision 2.

### What blocks step 38

`templates/target-spring-boot-baseline/` is a `pom.xml` plus a shape. A `pom.xml` needs a
`maven.compiler.release`, a persistence driver, and a test stack; the shape needs to know whether
`ItemProcessor` lookups hit a database or a file. Neither is derivable from ADR-0009.

## Decision

### 1. Java 25, Spring Boot pinned at build time, Maven not Gradle

**Java 25 is a user decision, and the reason is positioning, not performance.** For a Spring Batch
application 25 over 21 buys close to nothing functionally — Spring Boot 4's own baseline is 17. The
honest statement of the reason is that a COBOL-modernization showcase landing on a four-year-old LTS
undercuts its own story. That is a real reason. It is not a technical one, and dressing it up as one
would be the kind of claim this repo's ADRs exist to avoid.

**The risk is not the language.** A model that does not know Java 25 writes Java 17 idioms, which
compile. The risk is the **ecosystem's bytecode tooling** — Hibernate/ByteBuddy, Mockito, and any
agent-based instrumentation historically lag a new JDK by months, and they fail at *runtime*, in a
generated test, in a way the self-healing compile loop (step 42) is not built to diagnose because
the compile succeeded.

Four mitigations, all gates on step 38 rather than intentions:

- **Prove Hibernate/ByteBuddy, Mockito, and Testcontainers work on 25 before pinning them** — by
  running them on 25, not by reading release notes.
- **`--enable-preview` stays off.** Preview features change between releases; generated code using
  one is brittle in exactly the way the self-healing loop cannot recover from.
- **Set `maven.compiler.release=25` and put no 25-specific instruction in the codegen prompt.**
  Target the runtime; do not chase the syntax.
- **CI compiles on 25 from the template's first commit**, so an unsupported transitive dependency
  surfaces at step 38 rather than at step 42 inside a self-healing retry loop.

**Spring Boot's version is pinned in the `pom.xml` at build time, never named in this ADR.** An ADR
that hardcodes a framework version is wrong within a quarter and stays wrong, because nobody
re-reads an accepted ADR to bump a number.

**Maven, not Gradle, and the reason is step 42.** The self-healing loop reads a build tool's
diagnostics and patches source. Maven's XML is predictable to generate and its compiler output is
mechanically parseable; Gradle's Kotlin DSL would make the build script itself a second codegen
surface, with build-script failures the loop would have to diagnose alongside Java ones. This is a
choice made *for* the agent, not despite it.

**`BigDecimal` plus one `CobolArithmetic` helper.** COBOL `COMPUTE` without `ROUNDED` **truncates**;
Java's `BigDecimal.divide` throws on a non-terminating quotient unless told what to do. Encoding
that once in a helper, rather than leaving `setScale` calls scattered through generated code, is
the whole point: ADR-0015's benchmark caught Haiku 4.5 missing exactly this semantic when narrating
`CBACT04C`, so it is demonstrably a thing a model gets wrong.

### 2. Fixed `OCCURS` is scoped out of `generate`; `PicMapping` does not change

`CBACT01C`'s `ARRY-FILE` and its `OUT-FILE` `COMP-3` field are **excluded from Milestone C4**.
ADR-0011's rejection of fixed `OCCURS` stands.

**The evidence is decisive and was nearly missed.** The fields inside that `OCCURS` group are
assigned hard-coded literals, not computed values (`CBACT01C.cbl:255-260`):

```cobol
MOVE   ACCT-CURR-BAL   TO   ARR-ACCT-CURR-BAL(1).
MOVE   1005.00         TO   ARR-ACCT-CURR-CYC-DEBIT(1).
MOVE   ACCT-CURR-BAL   TO   ARR-ACCT-CURR-BAL(2).
MOVE   1525.00         TO   ARR-ACCT-CURR-CYC-DEBIT(2).
MOVE   -1025.00        TO   ARR-ACCT-CURR-BAL(3).
MOVE   -2500.00        TO   ARR-ACCT-CURR-CYC-DEBIT(3).
```

`OUT-FILE`'s `COMP-3` field is the same (`CBACT01C.cbl:237`, `MOVE 2525.00`). **There is no business
rule in there to preserve.** `CBACT01C` is a COBOL feature demonstration wearing the shape of an
account-listing program.

Reversing ADR-0011 to represent the array would ripple through `pic_mapper`, `DomainField`,
`UnifiedDesign`, the generated schemas, and the `CBACT04C` golden fixture — a contract change across
five layers, to reproduce four constants. The alternative considered and rejected was doing it
anyway "for completeness"; completeness of an artifact nobody reads is not a reason to widen a
contract.

Fixed `OCCURS` stays unsupported and stays Track B's B3, which already has its own stricter gate.
`CBACT01C`'s driving read of `ACCTFILE-FILE` remains in scope — it is only the three sequential
demo outputs that are excluded.

### 3. PostgreSQL, with CardDemo's data files as a one-time migration source

The Spring Batch jobs run against PostgreSQL. A loader (step 40a) reads CardDemo's data files into
it once. Reasons, strongest first:

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

The schema is **derived from the copybooks via `pic_mapper`, never hand-written** — a hand-written
schema is a second, independently-maintained description of a record layout the repo already
computes, and the two would diverge.

## Consequences

### Byte-level output equivalence is off the table — and never was on it

Migrating to a database means the generated app cannot produce byte-identical VSAM output for
comparison against a mainframe run. That sounds like a real loss and is not: **no COBOL runtime
exists anywhere in this platform** — no GnuCOBOL, no z/OS, no emulator — so there has never been a
reference execution to compare bytes against. Step 45 already specified **numeric** equivalence
(JUnit tests over the interest math), which is what the equivalence gate actually tests. This
decision makes explicit a trade the plan had already made implicitly, which is the only reason it is
worth writing down.

### A weak argument was withdrawn, and the conclusion survived it

The first version of this analysis led with *"Postgres collapses the packed-decimal problem."* That
was much weaker than it was presented as. **Track C's only two `COMP-3` fields are both in
`CBACT01C`** — the program Decision 2 scopes out — and none of the copybooks the two business
programs use declares `COMP-3` at all. Once `CBACT01C`'s demo outputs are excluded there is no
packed decimal on C4's critical path whatsoever, so "it collapses packed decimal" was solving a
problem that does not exist here.

The conclusion held on its other four legs. Recorded because an argument that survives on reasons
other than the one it was sold on is worth re-deriving in public, not quietly re-labelling.

### The data-file precondition was verified, not assumed — and it found three things

This ADR was drafted with the precondition stated as unverified: CardDemo's *real* data files had
never been looked at, since this repo's fixture is source only. It has now been checked against
`carddemo-tenant-service` directly (GitHub Contents API, 2026-08-09), and three facts change what
step 40a has to build. All three are the same shape — **the file does not look like the copybook
says it does** — which is itself the finding worth carrying into step 40a:

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

### `CBACT01C` contributes almost nothing to the generated application

Decision 2 excludes three of `CBACT01C`'s four files. What remains is a sequential read of the
account master and a print — a listing. Combined with `CBCUS01C`, which is also a listing, **only
two of the four Track C programs carry business logic into `card-service`**: `CBACT04C` (interest
calculation) and `CBTRN02C` (transaction posting). That is a real limit on what a Track C demo can
claim, tracked in the architecture audit as gap G17, and it argues for a fifth genuinely-transactional
program in Track B rather than for widening C4.

### `FD` record layouts now need a decision that ADR-0010 deferred

`build_domain_entities` promotes only copybook-sourced fields, so the program-local `FD` layouts
(`FD-ACCTFILE-REC` and friends) are parsed and visible in `spec.md` but are not domain entities.
Choosing PostgreSQL makes this concrete rather than theoretical: the driving `ItemReader` needs a
type to read into, and the `FD` layout is what describes the record on disk. This ADR does not
answer it — it belongs with step 39 (`modernization_engineer`), where the generated type actually
appears — but it is no longer an open question that can be left alone.

### What this ADR deliberately does not decide

- **The Spring Boot version.** Pinned in the `pom.xml`, verified by a real build, not asserted here.
- **Whether `card-service` runs one deployable or several.** ADR-0009 already decided one; nothing
  here changes it.
- **How the loader is invoked in production.** Step 40a builds it as a one-time migration; whether
  it ever runs outside a demo is a Milestone C5 question.
- **Schema migration tooling.** `CREATE TABLE IF NOT EXISTS` is not a migration — this repo already
  paid for learning that (PR #22, ADR-0016) — but choosing Flyway or Liquibase belongs with the
  first schema that has to change, not with the first one that has to exist. **See the amendment
  below: the framework no longer offers the shortcut this was deferring against.**

## Amendment (2026-08-09, same day, found by building the template)

Decision 3 left schema ownership open on the assumption that Spring Boot's own
`spring.batch.jdbc.initialize-schema` could create Spring Batch's metadata tables in the meantime.
**Spring Boot 4 removed `spring.batch.jdbc.*` from `BatchProperties` entirely.** There is no
`initialize-schema` any more, and because unknown configuration keys are silently ignored, setting
one looks decided and does nothing.

Found by running it, not by reading release notes: `BaselineStackTest`'s first version set
`spring.batch.jdbc.initialize-schema=always` and then counted **zero** batch tables against a real
PostgreSQL container. Every pre-Boot-4 Spring Batch example sets that property, which makes it a
trap the code generator will walk into by default — the same shape as the `@EnableBatchProcessing`
annotation that now switches auto-configuration *off*.

Two consequences. The template's `application.yml` states the absence and why, rather than carrying
a dead key. And schema ownership is no longer deferrable to a framework default that does not
exist: the metadata schema ships as `org/springframework/batch/core/schema-postgresql.sql` inside
`spring-batch-core` and has to be applied by whatever applies the domain schema `pic_mapper`
derives. That makes migration tooling a **step 40a** concern rather than a later one — the loader
already has to create the domain tables, and this is one more script alongside them.
