# target-spring-boot-baseline

The Maven project generated COBOL-to-Java batch code is seeded into. It is the `generate` phase's
starting point ([ADR-0009](../../docs/adr/0009-generated-java-targets-a-new-repo-card-service.md)),
and its stack is decided by
[ADR-0034](../../docs/adr/0034-java-25-on-maven-with-the-framework-version-pinned-in-the-build.md) and
[ADR-0036](../../docs/adr/0036-the-generated-jobs-persist-to-postgresql-loaded-once-from-carddemo-ascii-files.md).

**Domain-agnostic on purpose**: nothing tenant-specific belongs here, only what any modernized
batch program needs regardless of which one it is. `tests/integration/test_target_template.py` enforces
that as a real check rather than an intention — it fails if a program name or copybook name appears
anywhere under this directory.

## It is a real project, not a file of placeholders

Every file here compiles and every test here runs. That is a deliberate choice with a cost and a
reason:

- **The cost** is that the Maven coordinates are concrete (`com.modernized:modernized-batch-baseline`)
  rather than tokens, so seeding has to rewrite three lines of XML.
- **The reason** is that a template full of `${PLACEHOLDER}` cannot be compiled, so nothing would
  discover that it is broken until the self-healing compile loop (step 42) hit it — at which point
  the loop is diagnosing a scaffold defect it was never built to fix, inside a retry budget of
  three.

**The Java package never changes.** Generated code lands in sub-packages of `com.modernized.batch`,
so no `.java` file is ever string-substituted during seeding. Only `groupId`, `artifactId`, and
`<name>` differ between this template and the repository it is seeded into.

## What is pinned, and where

| Layer | Choice | Where the pin lives |
|---|---|---|
| Language | Java 25 | `pom.xml` `maven.compiler.release`, asserted by `test_target_template.py` |
| Framework | Spring Boot, exact version | `pom.xml` `<parent>` — deliberately **not** named in ADR-0034 |
| Batch | Spring Batch | `spring-boot-starter-batch` |
| Persistence | PostgreSQL + JPA | `spring-boot-starter-data-jpa`, `org.postgresql:postgresql` |
| Numerics | `BigDecimal` + `CobolArithmetic` | `src/main/java/com/modernized/batch/cobol/` |
| Testing | JUnit 5 + Testcontainers | `spring-boot-starter-test`, `org.testcontainers:testcontainers-postgresql` |
| Observability | Actuator + Micrometer/Prometheus | `spring-boot-starter-actuator` |

`--enable-preview` is off and stays off. A preview feature changes between releases, so generated
code using one compiles today and stops compiling on the next JDK — a failure the self-healing loop
cannot diagnose, because nothing about the source is wrong.

## The shape the COBOL actually implies

Verified against the real source, not assumed from a Spring Batch tutorial (ADR-0036 carries the
full 16-file inventory):

- **Only the driving dataset is an `ItemReader`.** One driving file is an indexed dataset read
  sequentially; another is a plain flat file. Both are readers.
- **Keyed lookups are repositories injected into the `ItemProcessor`**, not additional readers. A
  reader is positional; a lookup by key is not. Seven of the sixteen files are opened
  `ACCESS MODE IS RANDOM`.
- **Three files are read-modify-write** (`OPEN I-O` plus `REWRITE`), one of them an upsert. Those
  are repository writes, not `ItemWriter` appends.

## `CobolArithmetic`

The only real logic in the template, and the reason this directory is not just a `pom.xml`. Three
COBOL rules do not survive a literal translation into Java, and each produces a wrong number that
looks right:

- `COMPUTE` **without** `ROUNDED` **truncates**. A generated `setScale(2, HALF_UP)` is a defect.
  ADR-0015's four-model benchmark caught Haiku 4.5 missing exactly this, so it is a demonstrated
  failure mode of the generator rather than a hypothetical one.
- COBOL truncates **toward zero**, not toward negative infinity — `RoundingMode.DOWN`, not `FLOOR`.
  The two agree on every positive number, so a test suite using only positive amounts cannot tell a
  correct implementation from a wrong one.
- `BigDecimal.divide` **throws** on a non-terminating quotient where COBOL divides happily.

`requireFits` deliberately diverges from COBOL: a `MOVE` without `ON SIZE ERROR` silently discards
high-order digits, and reproducing that faithfully would mean generating code whose defined
behaviour is to lose an order of magnitude of money in silence. It raises instead.

## Building it

```bash
mvn -B verify
```

Needs **JDK 25** and a running **Docker** daemon — `BaselineStackTest` starts a real PostgreSQL
container and does not skip without one. A test that skips silently is how a gate becomes
decoration, which this repo has already had to correct once.

CI builds this on every push (`.github/workflows/ci.yml`, job `template-build`). That is the gate
ADR-0034 put on step 38: the Java 25 risk is not the language but the ecosystem's bytecode tooling
— Hibernate/ByteBuddy, Mockito's instrumentation agent, Testcontainers — all of which fail at
*runtime*, where a compile-error-driven self-healing loop cannot help. `BaselineStackTest` exercises
each of them, and asserts `Runtime.version().feature() == 25` so a workflow that silently resolved a
different JDK fails rather than going green.
