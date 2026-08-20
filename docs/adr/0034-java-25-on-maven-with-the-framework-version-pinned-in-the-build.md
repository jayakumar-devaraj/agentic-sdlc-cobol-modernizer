# ADR-0034: Java 25 on Maven, with the framework version pinned in the build and not in an ADR

## Status

**Accepted** (decision taken 2026-08-09; recorded here 2026-08-20).

Supersedes **decision 1** of
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md), which
bundled three independent decisions into one record. **Nothing here reverses that decision** — the
target stack has not changed. What changed is that ADR-0019's four mitigations were written as
*gates* on step 38 and have since been *run*, so this record states outcomes where the original
could only state intentions.

Sibling records from the same split:
[ADR-0035](0035-fixed-occurs-stays-unrepresentable-and-cbact01c-demo-outputs-stay-out-of-generate.md)
(scope) and
[ADR-0036](0036-the-generated-jobs-persist-to-postgresql-loaded-once-from-carddemo-ascii-files.md)
(persistence).

## Context

[ADR-0009](0009-generated-java-targets-a-new-repo-card-service.md) decided *where* generated Java
lives (`card-service`) and *what execution model* it uses (Spring Batch), and deliberately left the
language level open. Step 38 cannot write a `pom.xml` without one: a `pom.xml` needs a
`maven.compiler.release`, a persistence driver and a test stack, none of which is derivable from
ADR-0009.

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

## Decision

### 1. Java 25, with the ecosystem risk gated rather than intended

Four mitigations, all gates on step 38:

- **Prove Hibernate/ByteBuddy, Mockito and Testcontainers work on 25 before pinning them** — by
  running them on 25, not by reading release notes.
- **`--enable-preview` stays off.** Preview features change between releases; generated code using
  one is brittle in exactly the way the self-healing loop cannot recover from.
- **Set `maven.compiler.release=25` and put no 25-specific instruction in the codegen prompt.**
  Target the runtime; do not chase the syntax.
- **CI compiles on 25 from the template's first commit**, so an unsupported transitive dependency
  surfaces at step 38 rather than at step 42 inside a self-healing retry loop.

### 2. Maven, not Gradle, and the reason is step 42

The self-healing loop reads a build tool's diagnostics and patches source. Maven's XML is
predictable to generate and its compiler output is mechanically parseable; Gradle's Kotlin DSL would
make the build script itself a second codegen surface, with build-script failures the loop would
have to diagnose alongside Java ones. This is a choice made *for* the agent, not despite it.

### 3. The framework version is pinned in the `pom.xml`, never named in an ADR

An ADR that hardcodes a framework version is wrong within a quarter and stays wrong, because nobody
re-reads an accepted ADR to bump a number. The pin lives in the build, where a real build verifies
it, and `tests/system/test_target_template.py` asserts the pin is exact rather than a range.

### 4. `BigDecimal` plus one `CobolArithmetic` helper

COBOL `COMPUTE` without `ROUNDED` **truncates**; Java's `BigDecimal.divide` throws on a
non-terminating quotient unless told what to do. Encoding that once in a helper, rather than leaving
`setScale` calls scattered through generated code, is the whole point:
[ADR-0015](0015-compute-model-selection-from-a-priced-evidence-gated-catalog.md)'s benchmark caught
Haiku 4.5 missing exactly this semantic when narrating `CBACT04C`, so it is demonstrably a thing a
model gets wrong.

## Consequences

### The gates were executed, and the numbers are real

ADR-0019 could only promise these. Run in CI (`template-build`), reported in full in
[the QA spoke for this phase](../qa/verification/05-track-c-data-the-java-target-and-the-generate-split.md):

```
openjdk version "25.0.3" 2026-04-21 LTS
Apache Maven 3.9.16
Compiling 3 source files with javac [debug parameters release 25] to target/classes
Tests run: 13, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

The Spring Boot 4.1.0 BOM manages **Hibernate 7.4.1, ByteBuddy 1.18.10, Mockito 5.23.0 and
Testcontainers 2.0.5** — the four libraries named above as the actual risk — and each is exercised
at runtime against a real PostgreSQL container rather than merely resolved. `BaselineStackTest`
asserts `Runtime.version().feature() == 25` from inside the JVM, because `maven.compiler.release`
constrains the bytecode target and says nothing about what ran. **Zero tests skip**: a container
test that skipped without Docker would turn the gate into decoration.

### The pin is a decision, not a refactor

Changing the language level means changing `pom.xml`, `test_target_template.py`'s asserted constant
and this record together. That friction is deliberate. The same applies to `--enable-preview`: it is
asserted absent, so re-enabling it cannot happen quietly.

### What this record deliberately does not decide

- **The Spring Boot version.** Pinned in the `pom.xml`, verified by a real build, not asserted here.
- **Whether `card-service` runs one deployable or several.** ADR-0009 already decided one.
- **Anything about persistence or generation scope** — those are ADR-0036 and ADR-0035.
