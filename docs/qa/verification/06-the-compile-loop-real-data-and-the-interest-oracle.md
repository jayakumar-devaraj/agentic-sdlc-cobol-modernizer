# The compile loop, real data, and the interest oracle

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### The Java toolchain, and pinning the last unpinned dependency

**A local JDK and Maven now exist**, so the Java half of this repo is no longer CI-only. Installed
2026-08-09: **Eclipse Temurin 25.0.4** (`winget`) and **Apache Maven 3.9.16**, the latter downloaded
from Apache's CDN with its **SHA-512 verified against `downloads.apache.org`'s published checksum**
before extraction — a binary that compiles model-authored code is not one to take on trust.

```
mvn -B -ntp verify   # in templates/target-spring-boot-baseline
```

Real result: **`Tests run: 13, Failures: 0, Errors: 0, Skipped: 0` — `BUILD SUCCESS`** on JDK 25.0.4
against a **real PostgreSQL Testcontainer**, matching CI exactly. CI pins Temurin 25.0.3; the
difference is a patch within the same feature release, and `BaselineStackTest`'s own
`Runtime.version().feature() == 25` assertion is what actually guards this.

**Maven 3.9.16 rather than 4.0.0, deliberately.** ADR-0019 chose Maven over Gradle *because* its
compile diagnostics are straightforward to parse and auto-repair, and step 42's self-healing loop is
built on parsing exactly that output. Anchoring a diagnostic parser to a brand-new major whose
output format may have moved is avoidable risk that buys nothing.

**Host install rather than a Maven container, with Docker available.** In production the specialist
is a container (step 46 / gap G5). Giving *that* container a Docker socket so it could launch Maven
containers would grant root-equivalent host access to the component that compiles LLM-authored code
derived from untrusted COBOL — the wrong trust boundary for a repo whose standing rule is that COBOL
is data, never instructions. The right shape is JDK and Maven **inside** the specialist image with
`local_compiler` invoking `mvn` on `PATH`, and the development environment now mirrors it. It is
also far faster: the self-healing loop compiles up to 12 times per run, and a cold container with a
cold `~/.m2` on each one would dominate the loop's wall time.

**The gap this exposed.** CI ran `mvn` from whatever the runner image ships — **an unpinned
dependency in a template that pins its JDK, its Spring Boot version, its Testcontainers version, and
asserts the JVM's own feature version at runtime.** The build tool was the one thing left free to
change under an image update, and it is the tool whose output step 42 will parse.

Closed by adding the **Maven Wrapper**, pinned to 3.9.16, with CI switched to `./mvnw`:

- `distributionType=only-script`, so the repo carries **three text files and no committed jar** —
  an opaque binary in source control that nobody reviews and every scanner flags.
- Verified: `./mvnw -B -ntp -version` resolves `Apache Maven 3.9.16` from
  `~/.m2/wrapper/dists`, downloading it on first use.
- Three new invariants in `tests/system/test_target_template.py` — the version is an exact pin, no
  jar is committed, and **CI actually calls the wrapper rather than a bare `mvn`**, because pinning
  a version is pointless if the pipeline still runs whatever is on `PATH`.

### `tools/local_compiler.py` — a real Maven build, and diagnostics a loop can act on (step 40)

**The first module in this repo whose correctness could not be established without running it.**
Four defects were found by compiling the real template, and every one of them would have survived a
mocked `subprocess.run` untouched.

```
mvnw -B -ntp compile   # driven by compile_project, against a copy of the real template
```

Real result on the clean template: **`succeeded=True`, exit 0, 0 diagnostics, ~10s.** With
`setScale` typo'd to `setScaleTypo`, one located diagnostic comes back:

```
error: src/main/java/com/modernized/batch/cobol/CobolArithmetic.java:46:21: cannot find symbol
    symbol:   method setScaleTypo(int,java.math.RoundingMode)
    location: variable value of type java.math.BigDecimal
```

**The four defects, in the order they were found:**

1. **The wrapper path was relative to the caller's working directory** while the child ran with
   `cwd=project_dir`, so Maven never started. Symptom: `succeeded=False`, **zero diagnostics, 186ms**
   — byte-for-byte the shape of code that does not compile.
2. **Maven prints every compile error twice**, and only one copy carries javac's
   `symbol:`/`location:` lines. Two copies invite a repair loop to believe there are two problems;
   dropping the wrong duplicate discards the only part a repair can act on.
3. **Paths came back absolute**, on Windows in a `/C:/...` form nothing else in this pipeline
   recognises. Diagnostics are now project-relative and POSIX-separated — a model pays for those
   tokens and cannot act on a path whose base it does not know.
4. **A missing JDK was indistinguishable from broken code.** Found by running this module's own
   tests in a shell without one: the wrapper exits non-zero with no located diagnostic. `JdkNotFound
   Error` and `CompilerNotFoundError` (both `ToolchainNotFoundError`) make the two distinguishable,
   and `CompileTimeoutError` is kept out of `CompileResult` for the same reason — **a timeout says
   nothing about whether the source compiles.**

**A fifth was found by CI, and could not have been found here.** `resolve_build_command` chose the
wrapper by existence order, so on the Linux runner it picked `mvnw.cmd` — a Windows batch file with
no execute bit — and failed with `PermissionError: [Errno 13]`. Selection is now by platform, with a
test asserting the choice matches the platform and another asserting the POSIX script still carries
its execute bit. **This is the second time in one session that the Java toolchain behaved
differently on the runner than on the development machine** (the first being `mvnw`'s mode bit and
line endings), and it is the concrete argument for CI being the arbiter for anything touching it.

**The Python CI job now installs a JDK** so these tests run there rather than skipping forever —
the same standard `template-build` already applies to Docker. Had they skipped, defect 5 would have
shipped and surfaced inside step 42's heal loop, where a `PermissionError` reads as an unparsed
build failure: exactly the confusion `ToolchainNotFoundError` exists to prevent, arriving by a route
that was not yet guarded.

**CI result on this change: 513 passed, 4 skipped, 99% overall**, `local_compiler.py` at 98%. The
uncovered lines are `PATH`-based JDK discovery (shadowed by `JAVA_HOME` everywhere, including CI)
and the offline flag.

**What this does not establish.** `compile_project` has only ever compiled **hand-written** Java --
the template, and the template with a deliberate typo. **No model-generated file has been compiled**,
because nothing yet writes one to a project: `generate` still has no caller and `card-service` still
holds zero files. Wiring that is steps 41 and 42, and until then the round-trip count stays 0 of 4.

### The self-healing loop, the `generate` subcommand, and the round trip (step 42, ADR-0020)

**A `design.json` now produces a target project that compiles.** One design document yields five
domain records and a processor in `card-service`'s package layout, and `mvn compile` is green:

```
src/main/java/com/modernized/batch/domain/{Account,CardXref,DisGroup,Tran,TranCatBal}.java
src/main/java/com/modernized/batch/processor/ComputeMonthlyInterestProcessor.java
```

**The loop heals a real compile error, against real Maven.** Attempt 1 calls a method that does not
exist, javac says so, attempt 2 compiles — no human, two attempts, verified in
`test_generate_pipeline.py` rather than against a mocked compiler. A mock would let the loop
"recover" from failures a compiler would never report, and would have hidden the Spring Batch 6
package rename that step 41 caught.

**Refusing to retry is tested as carefully as retrying.** A `blocked` verdict produces exactly
**one** generation call: retrying a design defect burns the whole budget and yields three worse
versions of the same code. `MAX_HEAL_ATTEMPTS` (3) is asserted to differ from
`MAX_TRANSPORT_ATTEMPTS` (5) — they bound unrelated things and multiply if confused, which is
ADR-0013's stacking failure.

**Two defects found, both of the same silent shape:**

1. **G21 was reported closed and was not.** `render_program_field_facts` was added, tested, and
   **never called** — `build_engineer_prompt` kept its old return because a string-replacement patch
   did not match and nothing failed. The test written for it exercised the helper directly, so it
   passed against unwired code the whole time. Now wired, with a guard asserting through the *real*
   prompt builder: `WS-MONTHLY-INT -- BigDecimal, precision 11, scale 2, signed` reaches the prompt
   a model actually receives.
2. **`run_generate` never rendered the domain records.** Processors are generated *against* those
   types, so nothing would have compiled regardless of ADR-0020. Found by running the pipeline, not
   by reading it.

**The contract gap ADR-0020 closes was found by building the subcommand.** An `ItemProcessor` is two
types and `BatchStepDesign` named neither. A real `solution_architect` run is what settled the fix:
it chains three processor steps (`resolveAccountContext` → `resolveInterestRate` →
`computeInterest`), and the values flowing between them are **not** in `domain_entities` because
they do not exist as entities. There was nothing to derive from, so composites are declared.

**What this does NOT establish**, stated plainly against a green suite:

1. **The compiling processor body is `return item;`** — a scripted pass-through, not translated
   business logic. No real model has written a body through this path.
2. **`0 of 4 programs round-trip` is unchanged.** That metric means COBOL → compiling Java →
   **passing differential test**, and step 45's equivalence test does not exist, nor does step 40a's
   data loader that it needs.
3. **The real `solution_architect` output predates this contract**, so a fresh `design` run is
   needed before any real design.json carries step types.

**A coverage regression caught before merge, worth recording as a pattern.** CI's first run on this
branch reported 96.6% against the usual 99% -- and every uncovered line was ADR-0020's own code:
`render_composite` at **69%**, plus the resolution helpers and the architect's composite parsing.
The round-trip test used a plain entity, so **no composite was ever constructed in a test**: the
feature the ADR exists for was the one part unverified, behind a green suite and a passing
`--cov-fail-under=90`. Closed with a step whose input type *is* a composite, generated and compiled
for real. `contracts.py`, `solution_architect.py` and `java_records.py` are now at 100%.

### `tools/data_loader.py` — CardDemo's real data, into a real PostgreSQL (step 40a)

Step 45's equivalence test compares generated Java against the COBOL it came from **on the same
inputs**, so reading those inputs correctly is a precondition. PR #26 recorded three things a naive
reader gets wrong; all three are now verified against real bytes and pinned by tests rather than
restated in prose.

**The fixture is byte-verified.** Each file's git blob SHA was checked against
`carddemo-tenant-service` before committing, the way PR #10 verified the copybooks — and it matters
more here, because two of the three defects *are properties of the bytes*. A fixture git had
silently normalised would make every test below vacuous. `.gitattributes`' `-text` rule already
covered the path.

| Finding | How it is now checked |
|---|---|
| `CVACT03Y` declares `RECLN 50`; `cardxref.txt` is **36 bytes/record** | Copybook-derived width vs measured width — **disagree** for `cardxref`, **agree** for `tcatbal` and `discgrp`. The contrast is what makes it a finding rather than an inability to derive widths |
| Signed numerics carry a **zoned-decimal sign overpunch** | `00000001940{` → `Decimal("194.00")`, not `19.40`. All twenty forms decode; an unrecognised one raises |
| `tcatbal.txt` mixes line endings | Asserted on raw bytes: **49 `CR`, 50 `LF`**, and all 50 records still read |

The overpunch is where money is lost: stripping the `{` costs a factor of ten **and** the sign,
silently, in the direction that makes a balance look smaller.

**Offsets are computed** from the copybook's declaration order via `pic_mapper`'s own
`precision`/`string_length`. A hand-written offset table drifts silently, and every field past the
drift reads one column off — plausible wrong numbers rather than an error.

**Schema and load, against a real container** (`docker compose up postgres`, the same instance
`knowledge_store`'s tests use):

- `NUMERIC(p, s)` is derived from the same `pic_mapper` numbers the generated Java carries.
  PostgreSQL rounds into a narrower `NUMERIC` **silently rather than raising**, so a separately
  written schema would corrupt balances with no error anywhere.
- 50 real `tcatbal` records load; `information_schema` confirms the column really is
  `NUMERIC(11, 2)`.
- `cardxref` is **refused, not partly loaded** — its leading fields would read correctly, so a
  partial load looks like success while dropping whatever follows. A refused load leaves no table.
- Spring Batch's six metadata tables are created from DDL **read out of the jar on the classpath**.
  This step owns that at all because PR #27 found Spring Boot 4 removed `spring.batch.jdbc.*`, so
  `initialize-schema=always` is accepted and ignored.

**Two behaviours found by running it against a real database**, both now documented and pinned:
`apply_spring_batch_schema` is **not idempotent** (Spring Batch's DDL uses bare `CREATE`, and
creates *sequences* as well as tables — a cleanup dropping only tables leaves
`BATCH_STEP_EXECUTION_SEQ` behind); and a test asserting on a raising statement leaves the
transaction aborted, so cleanup must roll back first.

**The finding that matters most, and it is about step 45 rather than this module.** Every
`TRAN-CAT-BAL` in the shipped CardDemo data is **zero**. `CBACT04C` computes
`(TRAN-CAT-BAL * DIS-INT-RATE) / 1200`, so every interest it computes on this data is zero — and an
equivalence test run against these files alone **would pass for any implementation that returns
zero**, including a badly wrong one. It would be a test that cannot fail. Step 45 needs non-zero
balances: fabricated input, or a second dataset. Pinned by a test that fails the moment this stops
being true. `discgrp.txt` is genuinely varied by contrast (rates `0.00`, `15.00`, `25.00`), so the
reader is not merely returning zero for everything.

Also recorded: ~~**every signed field in the shipped files ends `{`** (+0), so the other nineteen
overpunch forms are covered by construction only. A real file containing one would be new
information.~~

> **Superseded 2026-08-10 — this was false, and it was false when written.** It generalised from
> the only two data files this step loaded. See the next section: a third real file contains all
> twenty forms.

41 tests, **100%** on the module.

### `dailytran.txt` — the third real file, and the claim above being wrong

**The check.** The sentence struck through above said a real file carrying a non-`{` overpunch
"would be new information". One exists, and it had been in the corpus the whole time.
`app/data/ASCII/` holds nine files; step 40a loaded three, and the two it drew that conclusion from
(`tcatbal`, `discgrp`) are both `CBACT04C`'s. Both really do only ever carry `{`, so nothing in the
suite was wrong — the *scope* of the conclusion was.

**What `dailytran.txt` contains**, measured through the real code path (`derive_layout` over
`CVTRA06Y`, not a hand-written offset table):

| | |
|---|---|
| Records / width | 300 · derived 350 = measured 350, so **no width discrepancy** here, unlike `cardxref` |
| `DALYTRAN-AMT` final byte | **all twenty forms** — `{ABCDEFGHI` positive, `}JKLMNOPQR` negative |
| Values | 299 distinct across 300 records, **−998.33 to +999.77**, 50 negative, sum `104801.54` |
| Fixture | byte-verified — staged blob SHA `848919fe…` matches the remote |

The negative half of `decode_zoned_decimal` had never been reached by a real byte: every negative
test was a literal written in this repo. It is now exercised at real offsets in real records, and
the values survive a load into the derived `NUMERIC(11, 2)` and a read back — asserted on
PostgreSQL's own `min`/`max`/`sum` rather than on a row count, because a column narrower than the
copybook rounds cents away **silently**.

**Why the balances are zero, which turns out not to be arbitrary.** `CBTRN02C` is the program that
writes `tcatbal` — `ADD DALYTRAN-AMT TO TRAN-CAT-BAL`, on both its create and update paths. The
shipped file is therefore the state **before posting has run**, and the non-zero balances step 45
needs are stage one of CardDemo's own pipeline over `dailytran.txt`, not fabricated input.

That claim is pinned against the COBOL source, deliberately **not** by computing the posted balances
in Python and comparing. Such an oracle would be one reading of `CBTRN02C`, and step 45 checking
generated Java against it would be comparing two renderings of the same interpretation — a fifth
check that cannot fail, arriving by the route the first four came by.

**What this does and does not unblock.** It supplies step 45's *input* side from real data and
removes the fabrication question for it. It does not supply an *oracle*: expected outputs for
`CBACT04C` still have to come from somewhere other than this repo's own implementation. *(Decided
in ADR-0021 — see the next section.)*

6 tests, module still at **100%**.

### The interest oracle — a hand-computed expected table (ADR-0021)

**What was verified, and how.** Nine expected values for `1300-COMPUTE-INTEREST`, each derived by
hand from `COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200` and committed as a
literal with its arithmetic beside it, in `tests/fixtures/golden/CBACT04C/interest-oracle.json`.
The receiving field `WS-MONTHLY-INT` is `PIC S9(09)V99`, so the target scale is 2 and the rule is
truncation toward zero.

**The table is built to fail, and that was checked rather than assumed.** Mutating `R2`'s expected
value from `-2.42` to the rounded answer `-2.43` fails **three** tests: the literal guard, the
independent recompute, and the teeth check — which noticed that R2's *rejected* value had become
its expected one, i.e. that the row had stopped discriminating. Command:

```
python -m pytest tests/system/test_interest_oracle.py -q
→ 3 failed, 9 passed   (with R2 mutated)
→ 12 passed            (restored)
```

**What each row is for.** `R2` (`-194.00` × `15.00`) is the one that earns its place: a negative
exact tie where truncation gives `-2.42`, rounding gives `-2.43` and floor gives `-2.43`, so one
input separates all three modes. `R3` is a non-terminating quotient (`25/12`), which is where a
`BigDecimal.divide` missing its scale and rounding mode throws. `R5`/`R6` are sub-cent results a
rounding implementation would inflate into a cent.

**`R10` is deliberately not an expected value.** `IF DIS-INT-RATE NOT = 0` skips the paragraph
entirely, so a zero rate computes no interest, accumulates nothing, and **writes no transaction
record**. An implementation returning `0.00` agrees numerically and is still wrong. It is held
outside the `rows` list so a harness reading that list cannot consume it by accident, and a test
asserts it stays there.

**Provenance is checked, not asserted.** `R1`'s `194.00` is a real `ACCT-CURR-BAL` in
`acctdata.txt` (added to the fixture here, blob `50f88936…` matching the remote); `R7`/`R8` are
`dailytran.txt`'s real maximum and minimum; every rate used is one that really occurs in
`discgrp.txt`. Each is a test, so the table cannot drift away from the data it claims to come from.

**Two divergences recorded rather than asserted**, because asserting either would require the
oracle we do not have. Overflow is reachable — a maximal balance times a maximal rate over 1200
exceeds `WS-MONTHLY-INT` — and COBOL discards high-order digits silently where
`CobolArithmetic.requireFits` throws by deliberate design, so no row can be both faithful and
desirable and none exists. And `-0.00625` truncates to a negative zero a COBOL signed field can
carry and `BigDecimal` cannot; confirmed against a real JDK 25 that Java yields `0.00`, so `R6` is
marked to compare numerically.

**The honest limit, stated here as well as in the ADR.** This covers one `COMPUTE`. It says nothing
about the rate lookup, the `'DEFAULT'` fallback, the accumulation into `WS-TOTAL-INT`,
`1050-UPDATE-ACCOUNT`, or the transaction record's contents — and it cannot discover a semantic
nobody anticipated, which is what a real COBOL runtime would be for. **A green step 45 means the
interest arithmetic matches, and no more.**

12 tests.
