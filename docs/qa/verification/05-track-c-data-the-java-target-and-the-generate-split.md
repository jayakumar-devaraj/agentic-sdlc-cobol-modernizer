# Track C data, the Java target, and the generate split

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### Track C's real file I/O, and CardDemo's real data files (ADR-0019)

**Verified**: the two factual premises ADR-0019 rests on, both of which had been asserted from
memory in a draft before being checked — and checking each one found the draft wrong.

**1. Every `SELECT` in the four programs, read from the real fixture source.** 16 files: 10
`ORGANIZATION IS INDEXED` (VSAM KSDS) and **6 `SEQUENTIAL`**, 7 `ACCESS MODE IS RANDOM`, 3 opened
`I-O` and `REWRITE`n. The full table is in ADR-0019. **This falsified the draft's claim that "these
are VSAM KSDS files, not flat files"**: `CBTRN02C`'s *driving* dataset (`DALYTRAN-FILE`) is a plain
sequential file, and five of six outputs are sequential. It also falsified a long-standing line in
`docs/cobol-construct-support-matrix.md` — *"Track C only reads existing files"* — contradicted by
`CBACT04C.cbl:356`, `CBTRN02C.cbl:510,528,554`, and five `OPEN OUTPUT` statements. Both are
corrected in this change rather than carried forward.

**Command**: `rg "^\s+(SELECT|ORGANIZATION|ACCESS MODE|RECORD KEY|OPEN|READ|WRITE|REWRITE)\s" tests/fixtures/tenant_repo_sample/app/cbl/`
**Result**: as tabulated in ADR-0019.

**2. CardDemo's real data files, against `carddemo-tenant-service` itself.** The plan carried this
as an explicitly *unverified* precondition ("this repo's fixture is source only"). It is now checked
and it changed what step 40a has to build:

- `app/data/ASCII/` holds nine fixed-width `.txt` files; `app/data/EBCDIC/` holds the mainframe
  `.PS` datasets. Five of the six files a Track C program reads match their copybook's own `RECLN`
  exactly: `acctdata.txt` 50 records × 300 bytes (`CVACT01Y`), `tcatbal.txt` 50 × 50 (`CVTRA01Y`),
  `dailytran.txt` 300 × 350 (`CVTRA06Y`), `custdata.txt` 50 × 500 (`CVCUS01Y`), `discgrp.txt`
  51 × 50 (`CVTRA02Y`).
- **The sixth does not, and that was the point of counting rather than assuming.** `cardxref.txt`
  is **36 bytes per record against `CVACT03Y`'s declared `RECLN 50`** — exactly the 16 + 9 + 11 of
  its three real fields, with the trailing `FILLER PIC X(14)` simply absent from the file. A reader
  built to the copybook's record length would misalign every record after the first. `XREF-FILE` is
  a random keyed lookup in **both** business programs (`CBACT04C`, `CBTRN02C`), so this is on the
  critical path, not in a demo output.
- **Signed numerics carry a zoned-decimal sign overpunch.** The first `acctdata.txt` record's
  `ACCT-CURR-BAL` is the twelve characters `00000001940{`, not `000000019400`; the trailing byte
  encodes the last digit *and* the sign. `dailytran.txt`'s `DALYTRAN-AMT` shows `0000005047G`
  (= 504.77) and contains real negatives. `new BigDecimal("00000001940{")` throws; dropping the
  last character instead loses a factor of ten and the sign. Decoding this is a required, testable
  part of step 40a, driven off `pic_mapper`'s existing signedness and scale.
- **`tcatbal.txt` has 49 `CR` bytes against 50 `LF`** — 49 of 50 records `CRLF`-terminated, one not;
  every other ASCII data file is pure `LF` (`acctdata` 0/50, `dailytran` 0/300, `discgrp` 0/51,
  `custdata` 0/50, `cardxref` 0/50). It is `CBACT04C`'s driving dataset, so this lands on the
  interest calculator specifically. Same defect class as `CODATECN.cpy`'s real `CRLF` (PR #10),
  same upstream repo, found the same way — by counting bytes rather than trusting a text file to be
  uniform.

No test pins these yet, deliberately: the data files are not in this repo (the fixture is source
only), and a test that reaches GitHub at run time would be a network dependency in a suite that has
none. They become falsifiable when step 40a lands a byte-verified data fixture, the same way
`tests/fixtures/tenant_repo_sample/` did for source.

**Command**: `gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/data/ASCII --jq '.[].name'`,
then each file's base64 `content` decoded locally and its bytes counted (record length, `CR`/`LF`
totals, and the literal characters at the signed-field offsets).
**Result**: as stated above.

### `templates/target-spring-boot-baseline/` — the Java 25 stack, compiled and run on Java 25 (step 38)

**Verified**: the four mitigations ADR-0019 wrote as *gates* on step 38, executed rather than
intended. This repo has no JDK, so all of it runs in CI (`.github/workflows/ci.yml`, job
`template-build`).

**The toolchain is the pinned one, confirmed from the build's own output** — not from the workflow
file that asked for it:

```
openjdk version "25.0.3" 2026-04-21 LTS
Apache Maven 3.9.16
Compiling 3 source files with javac [debug parameters release 25] to target/classes
Tests run: 13, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

`BaselineStackTest` asserts `Runtime.version().feature() == 25` from inside the JVM as well, so a
workflow that silently resolved a different JDK fails rather than going green —
`maven.compiler.release` constrains the bytecode target and says nothing about what ran.

**Each named ecosystem risk is exercised against a real PostgreSQL container**, because all three
fail at *runtime*, where a compile-error-driven self-healing loop cannot help:

- **Hibernate/ByteBuddy**: the `EntityManagerFactory` is built and opened. Building it is what
  drives ByteBuddy, the library that historically breaks first on a new class-file version.
- **Mockito's instrumentation agent**: a `final` class is mocked, deliberately — an interface would
  be proxied by plain JDK reflection and would pass with the agent completely broken. Surefire runs
  with `-XX:+EnableDynamicAgentLoading` so the dependency on self-attach is explicit in the build
  rather than one JDK release from an unpredicted failure.
- **Testcontainers 2.0.5** starts the container at all.

**Zero tests skip.** A container test that skips without Docker would turn this gate into
decoration — the failure this repo already corrected once, when `test_knowledge_store.py` could
have skipped in CI forever.

**ADR-0019's fourth reason for choosing PostgreSQL is checked, not restated**: a `NUMERIC(12,2)`
column accepts `9999999999.99` and **rejects** `10000000000.00` with `numeric field overflow`,
where a COBOL `MOVE` would silently discard the high-order digit. That is the zero-drift property
becoming a database constraint rather than an application convention.

**A real defect was found by the first CI run, and it is a trap for the code generator.**
`BaselineStackTest` initially set `spring.batch.jdbc.initialize-schema=always` and then counted
**zero** batch metadata tables. **Spring Boot 4 removed `spring.batch.jdbc.*` from
`BatchProperties` entirely**; unknown configuration keys are silently ignored, so the key looked
decided and did nothing. Every pre-Boot-4 Spring Batch example sets it — the same shape as
`@EnableBatchProcessing`, which in Boot 3+ switches auto-configuration *off*. The test now applies
`org/springframework/batch/core/schema-postgresql.sql` itself, `application.yml` states the absence
and the reason instead of carrying a dead key, and ADR-0019 gains an amendment moving schema
ownership to step 40a. **Twelve of thirteen tests passed on that first run**, so the JDK 25 gate
itself was clear before this was fixed.

**Two coordinate facts verified against Maven Central before writing the pom**, rather than
discovered by a failed build: Testcontainers 2.x renamed `org.testcontainers:postgresql` to
`org.testcontainers:testcontainers-postgresql` (the 1.x coordinates resolve to nothing), and the
Spring Boot 4.1.0 BOM manages Hibernate 7.4.1, ByteBuddy 1.18.10, Mockito 5.23.0 and Testcontainers
2.0.5 — the four libraries ADR-0019 names as the actual risk.

**`CobolArithmetic`'s 8 tests pin the rules a literal translation gets wrong**, each with the wrong
answer asserted alongside the right one so the difference is visible rather than claimed:
truncation vs rounding (`194.995` → `194.99`, not `195.00`); truncation toward zero rather than
toward negative infinity (`-194.999` → `-194.99`, where `FLOOR` gives `-195.00` — the two agree on
every positive number, so this is the case that can tell them apart); `BigDecimal.divide` throwing
on `100/3` where the helper returns `33.33`; single rounding (`20099/20000` = `1.00495` → `1.00`,
where an intermediate-precision implementation gives `1.01`); and `requireFits` raising on
`10000000000.00` against `PIC S9(10)V99` instead of losing the high-order digit in silence.

**The template's textual invariants are pinned from this repo's own suite**
(`tests/system/test_target_template.py`, 6 tests, 0.02s, no JDK) because compiling proves the
ecosystem supports the pin but not that the pin still says 25, that no preview flag appeared, or
that the scaffold stayed free of this tenant's vocabulary. **Writing them found a real defect
immediately**: XML forbids a double hyphen inside a comment, so the pom comment explaining that the
preview flag stays off was making the file not well-formed — with no JDK on this machine *at the
time*, nothing else would have caught it before CI. (A JDK and Maven were installed locally on
2026-08-09; the reasoning stands as the reason these Python invariants exist, since they run in
0.02s against no toolchain at all and CI is still the only place the Java build is authoritative.) **Confirmed falsifiable**: flipping
`maven.compiler.release` to 21 fails exactly that one test and nothing else, with the substitution
asserted to have applied before the result was believed.

**Command**: `mvn -B -ntp verify` (CI job `template-build`, `temurin` 25) and
`pytest tests/system/test_target_template.py -v`
**Result**: `Tests run: 13, Failures: 0, Errors: 0, Skipped: 0` / `BUILD SUCCESS`; 6/6 passed.

### `nodes/modernization_engineer.py` + `rendering/` — the generate split, against real Track C data (step 39)

**What was verified, and what deliberately was not.** The node's whole design is that a model
writes one method body and everything around it is rendered. Both halves are exercised against the
real four-program corpus with the model call injected. **The live call has not run** — no real model
has yet written a line of this Java, and nothing below claims otherwise.

```
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/system/test_knowledge_store.py \
  --cov=cobol_modernizer --cov-report=term
```

Real result locally: **466 passed, 4 skipped in 21.33s**, with the container suite excluded because
Docker's daemon was not running on this machine *when that run was made*. (It was started later the
same day; the run is left as recorded rather than re-run, because the point of the entry is CI's
number being the authoritative one, which does not change.)

**CI ran the same suite with a real Postgres service container and reported 478 passed, 4 skipped,
99.11% coverage** (run `31346976317`, both `test` and `template-build` green). The twelve-test
difference is exactly `tools/knowledge_store.py`'s suite, which skips nothing there — worth stating
because a local run of this repo is *structurally* weaker than CI, not merely slower, and a number
quoted from the wrong one would understate coverage while sounding more careful.

Per-module, on the modules this work added:

| Module | Coverage | The uncovered part |
|---|---|---|
| `rendering/java_names.py` | 100% | — |
| `rendering/java_records.py` | 100% | — |
| `rendering/java_processor.py` | 100% | — |
| `nodes/modernization_engineer.py` | 99% | `_default_author`'s body — the live model call, the same honest gap every other node carries and the reason it is injectable |

**Rendered against real copybooks, not hand-built entities.** `build_domain_entities` over the real
`CBACT04C`/`CBCUS01C`/`CBACT01C`/`CBTRN02C` fixtures produces seven entities; all seven render.
`Account` (from the real `CVACT01Y`, used by three real programs) renders twelve components with
`pic_mapper`'s computed shapes carried as documented fact — `acctCurrBal` as
`BigDecimal ... precision 12, scale 2, signed`. **`ACCT-EXPIRAION-DATE` renders as
`acctExpiraionDate`**: the typo is upstream in the real copybook, and a mechanical transform that
silently corrected it would break the trace back to source.

**The review boundary is real and is checked.** Everything outside the `BEGIN/END model-authored
logic` markers is a pure function of `design.json`; a test asserts no `@Override`, `@Component`,
`public class`, `package` or `import` ever appears inside the region. A body containing either
marker is refused (`GeneratedBodyForgeryError`) rather than escaped — the mirror of
`DelimiterForgeryError`, which refuses forged delimiters on the way *in*.

**Prompt ordering, measured rather than asserted.** Two steps of `CBACT04C` produce prompts sharing
**70,547 of 70,688 characters (99.8%) as a genuine common prefix**, leaving a 141-character
per-step tail. This was wrong when first written — the step facts led, making the ~68k shared span
a suffix behind a variable prefix, which is exactly the shape ADR-0017 corrected in `spec_critic`
after G13 measured it at ~26% of a run. The test asserts the shared span *is* a prefix, so
reordering the sections fails it.

**Falsifiability confirmed by breaking things on purpose**, not by reading the assertions:

- Renaming `generate`'s `--run-id` to `--corr-id` fails the parser-parity test with exactly
  `generate is missing ['--run-id']`.
- A step named `1300-COMPUTE-INTEREST` yields the class `1300ComputeInterestProcessor` and raises
  `UnrenderableJavaNameError: ... is not a legal Java identifier`, naming the COBOL source.
- Per-line `strip()` on the body flattens a wrapped expression's continuations onto their
  statement's column. It still compiles, so an explicit indentation assertion catches it where a
  build would not.

**The run budget, under real concurrency.** Eight threads racing a ceiling of four: every trial
gives exactly eight recorded calls and exactly four `RunBudgetExceededError`s, with no run of 500
producing any other outcome. Enforcement lives inside `UsageAccumulator`'s existing lock — outside
it, several branches would read the same pre-increment total and all pass, which is the
lost-update race the lock exists for, reintroduced in the check rather than the counter.

**What this does not establish**, stated plainly so a green suite does not imply it:

1. **No Java produced here has ever been compiled.** ~~There is no JDK on this machine and Docker's
   daemon is not running, so `javac` has not seen any of it.~~ **The stated cause is superseded
   (2026-08-09): a JDK and Maven are now installed and the template builds green locally.** The
   claim itself still holds and is the one that matters — **generated** output does not reach a
   build at all yet, because nothing writes it to a project `javac` is pointed at. That is step 40,
   and the missing toolchain was never the reason.
2. ~~**No real model has written a body.**~~ **Superseded the same day — one real call has now run.
   See the entry below.** What remains true is narrower: no real model has yet produced a *working*
   implementation, and quality across programs is unmeasured.
3. **Nothing is written to `card-service`**, which still holds 0 Java files. The node has no
   caller: `cli.py`'s `generate` subcommand still returns `"Not implemented"`.

### The first real `modernization_engineer` call — and the design defect it found (step 39)

**One live model call**, `claude-opus-5` via the `claude_cli` backend, 2026-08-09. Real COBOL
source, real copybooks, real `pic_mapper` output, real routing, real prompt. The narration was the
hand-verified golden `spec.md` (step 32) rather than a fresh extraction, so the call measured the
new node instead of re-proving nodes that already have measurements. The `BatchStepDesign` was
**constructed by hand** from real `CBACT04C` paragraph names — `solution_architect`'s batch design
is LLM-authored and running it would have been a second call. That hand-construction is what this
run turned out to be a test of.

**The model refused to implement the step.** Asked for
`BigDecimal process(TranCatBal item)` from `1300-COMPUTE-INTEREST`, it threw
`IllegalStateException` with a diagnostic instead of returning a number, on the grounds that the
paragraph needs `DIS-INT-RATE` and nothing reachable from a `TranCatBal` supplies it.

**Every factual claim in its response was checked against the real source. All of them hold:**

| Claim | Verified against |
|---|---|
| `COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200`, no `ROUNDED` | `CBACT04C.cbl:464-465` — verbatim |
| `WS-MONTHLY-INT` is `PIC S9(09)V99` | `CBACT04C.cbl:168` — exact |
| The paragraph also does `ADD WS-MONTHLY-INT TO WS-TOTAL-INT` and `PERFORM 1300-B-WRITE-TX` | `CBACT04C.cbl:467-468` |
| `DISCGRP-STATUS` `'23'` triggers a `'DEFAULT'` group re-read | Golden fixture's hand-verified business rules |
| `DIS-INT-RATE` is not reachable from `TranCatBal` | `TranCatBal` really has exactly four components |

**Zero invented identifiers.** The four accessors it emitted — `trancatAcctId()`,
`trancatTypeCd()`, `trancatCd()`, `tranCatBal()` — are all four real components of the real
`TranCatBal` entity, spelled correctly. This is the specific failure mode the Known Facts section
exists to prevent, and on this call it did.

**It also flagged two behaviours that would otherwise be silently lost**: the caller only performs
the paragraph when `DIS-INT-RATE` is non-zero, and the paragraph accumulates into `WS-TOTAL-INT`
and writes a transaction — neither expressible in a step whose signature returns one `BigDecimal`.

**The defect is in the design, not the model.** A step design of
`TranCatBal -> BigDecimal` for this paragraph is genuinely insufficient, and the hand-constructed
input was wrong in exactly the way the feasibility assessment's § 3.3 predicted in the abstract:
*"`design.json` is too thin to generate from... the generator will infer most of the architecture
from prose."* **That prediction is now measured rather than argued** — and the failure mode was the
good one: refusal with a diagnostic, not a confident invention.

**Measured cost and token profile — the first real numbers for this node:**

| | Placeholder in `model_routing.yaml` | Measured |
|---|---:|---:|
| Input tokens | 50,000 | **39,862** (39,860 cache-creation + 2) |
| Output tokens | 30,000 | **2,894** |
| Notional cost | — | **$0.302311** |
| Duration | — | 40.9s |

**Output came in at roughly a tenth of the placeholder**, which is the render-don't-generate split
showing up in the measurement: the model writes a method body, not a file. The profile is
deliberately **not** updated from this single call — it is one sample and an atypical one, since a
refusal is not a representative implementation. It is recorded so the next call has something to
be compared against rather than replacing evidence with a single point.

**Note for the caching work**: `cache_creation_input_tokens` was 39,860 against `cache_read` of 0,
so the CLI backend did write a cache entry for this prefix. Whether a second call against the same
99.8% prefix reads it is the obvious next measurement and has not been made.

### The second real call — the same step with the design fixed, and correct arithmetic out (step 39)

Run 1's refusal named two ways to make the step implementable. Option (a) — an input record
carrying both the category balance and the resolved rate — is the one the processor renderer can
currently express, so that is what this run supplied. **`TranCatBalWithRate` was hand-constructed**,
standing in for a corrected `solution_architect` design; its component names and numeric shapes are
copied verbatim from the real entities, but the joining is a human's, not a model's. The step
description explicitly scoped out the accumulate-and-write that run 1 flagged, so this call tests
one thing: **given an adequate design, is the COBOL arithmetic translated correctly?**

**It is.** The generated body:

```java
BigDecimal quotient = categoryBalance.multiply(annualRatePercent)
        .divide(new BigDecimal("1200"), MathContext.DECIMAL128);
return CobolArithmetic.truncate(quotient, 2);
```

Checked against the three rules a literal translation gets wrong: truncation not rounding
(`CobolArithmetic.truncate`, not `setScale`), toward zero not `FLOOR`, and `BigDecimal` constructed
from a **string literal**, never a `double`. All three correct, on a `COMPUTE` with no `ROUNDED`
whose receiving field is `S9(09)V99`.

**The model flagged an assumption, and the assumption was right.** Note 1 said the Known Facts did
not give it `CobolArithmetic`'s signatures, so it had called `truncate(BigDecimal, int)` and a
reviewer should substitute the real name if different. That method exists with exactly that
signature (`CobolArithmetic.java:45`). Guessing correctly is not the point — **saying it was
guessing is**.

**Two real gaps that same honesty exposed, both in this repo's prompt rather than in the model:**

1. **`CobolArithmetic.divide(dividend, divisor, scale)` already exists** (`:68`) and does the
   direct truncating divide in one step. The model described exactly that as the formulation it
   would prefer *"for a reviewer who wants zero intermediate rounding at all"* — and could not use
   it, because nothing told it the method was there. The prompt made the model write a
   second-choice implementation it had itself identified as second-choice.
2. **`CobolArithmetic.requireFits(value, precision, scale)` exists** (`:101`) and is precisely the
   size-guard note 3 asked for: *"If CobolArithmetic has a checked MOVE/store helper for a declared
   precision/scale, the return should go through it so an oversized interest amount throws rather
   than being written 10x too small."* It does. The model could not know.

**Fix implied and not yet made**: the Known Facts must carry the target's own helper API. This is
a cheap, well-evidenced prompt change, and it is exactly the kind of finding a single real call
buys that no amount of injected-fake testing can.

**Measured, run 2:**

| | Run 1 | Run 2 |
|---|---:|---:|
| Input tokens (fresh) | 39,860 cache-creation | 31,833 cache-creation |
| **Cache read** | **0** | **49,290** |
| Output tokens | 2,894 | 4,682 |
| Notional cost | $0.302311 | $0.295966 |

**Cross-invocation prompt caching is confirmed working on the `claude_cli` backend** — 49,290
tokens served from cache on the second call. This is the first direct evidence that the
stable-prefix-first ordering pays off in `generate`, and it corroborates the R1.5 probe's finding
from the other direction. Cost barely moved only because run 2's output was 62% larger (six
substantive notes rather than one).

**What is still not established**: none of this Java has been compiled. `TranCatBalWithRate` does
not exist as a type, `card-service` still holds zero files, and no test has executed the arithmetic
against real CardDemo data. Correct-looking arithmetic reviewed by a human is not a passing
differential test, and the distinction is the whole reason Phase 1 exists.

### The third real call — the prompt fix, and what it changed (step 39)

Run 2's two findings were that `CobolArithmetic.divide(dividend, divisor, scale)` and
`requireFits(value, precision, scale)` existed and the model had never been told. `rendering/
target_api.py` now extracts the class's public API **from the real source file** and puts it at the
head of the prompt. Run 3 is the identical step and design, re-run against that prompt.

**The generated body changed to exactly what run 2 had asked for and could not write:**

```java
// Run 2 -- named by the model itself as second-choice
BigDecimal quotient = categoryBalance.multiply(annualRatePercent)
        .divide(new BigDecimal("1200"), MathContext.DECIMAL128);
return CobolArithmetic.truncate(quotient, 2);

// Run 3
BigDecimal product = item.tranCatBal().multiply(item.disIntRate());
BigDecimal monthlyInterest = CobolArithmetic.divide(product, new BigDecimal("1200"), 2);
return CobolArithmetic.requireFits(monthlyInterest, 11, 2);
```

The intermediate `MathContext.DECIMAL128` quotient is gone in favour of the single truncating
divide at the target scale, and the overflow guard is present. `java.math.MathContext` dropped out
of the import list on its own.

**Cost fell as well: $0.295966 -> $0.243181, an 18% reduction for better code.** The prompt grew by
~2,800 characters of stable, cached prefix and the model stopped spending output tokens reasoning
its way around an API it could not see. Run 2 emitted six notes; run 3 emitted four, and none of
them is about a guessed method signature.

**A new finding, from the same honesty that produced the last two.** Run 3 flagged that
`WS-MONTHLY-INT` — the *receiving* field of the `COMPUTE`, and therefore the thing that determines
the target precision and scale — **is not in the Known Facts at all.** It appears only in the
untrusted narration and in the step description. The model inferred `precision 11, scale 2` and got
it right (`PIC S9(09)V99` is 9 integer digits plus 2 decimals), but it said plainly that it was
inferring: *"If a reviewer wants this beyond dispute, WS-MONTHLY-INT should be added to the
pic_mapper fact list rather than left to be read off the PIC clause here."*

It is correct, and this is the same defect class as the last one. `build_domain_entities` merges
**copybook-sourced** fields only (ADR-0010), so `WORKING-STORAGE` fields — which `cobol_parser`
already parses, per ADR-0011 — never reach the prompt as deterministic facts. **A `COMPUTE`'s
target scale is precisely the kind of number that must be computed and handed over, never
narrated**, for the same reason `pic_mapper` may not call a model: a wrong scale on a currency
field looks exactly like a right one. Recorded as an open gap rather than fixed in this PR.

**Three calls, $0.84 notional, $0 billed.** Each one found a defect the injected-fake test suite
could not: an under-specified design, a missing target API, and a missing class of deterministic
fact. None of that Java has been compiled yet.
