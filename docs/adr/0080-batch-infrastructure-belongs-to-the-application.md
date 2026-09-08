# ADR-0080: Batch infrastructure belongs to the application, not to the generated job

## Status

**Accepted** (2026-09-08). Found by the first CI run inside the repository a generated project was
delivered to — the experiment that produced
[ADR-0079](0079-ci-belongs-to-the-output-repository-not-the-generated-project.md).

## Context

The rendered job configuration declared four beans of its own: `JobRepository`,
`PlatformTransactionManager`, `JobRegistry` and `JobOperator`, the first two resourceless. Running
`./mvnw -B verify` on a delivered project for the first time:

```
BeanDefinitionOverrideException: bean 'jobRepository'
  BatchAutoConfiguration$SpringBootBatchDefaultConfiguration wants to register it
  ... already bound from interestCalculationJobConfiguration
```

**The delivered artifact could not start as a Spring Boot application**, and the baseline says why
that matters. `BatchApplication`'s own Javadoc:

> *Deliberately carries no `@EnableBatchProcessing`: Spring Boot's own auto-configuration builds the
> `JobRepository` and `JobLauncher` against the configured DataSource, and adding the annotation
> switches that auto-configuration **off** — a mistake that is easy for a code generator to make.*

The generator made the equivalent mistake by a different route. It did not add the annotation; it
declared the beans directly, which collides with the same auto-configuration and, where it wins,
substitutes a `ResourcelessJobRepository` that persists no job metadata for one backed by Postgres.

### Why it survived from G31 until now

Three contexts each hid it, and no context exercised the combination that fails:

| context | why it stayed green |
|---|---|
| the pipeline's job runner | a plain `AnnotationConfigApplicationContext` — no auto-configuration at all, deliberately (ADR-0075) |
| the template's `BaselineStackTest` | a bare template has no generated job configuration to collide with |
| the hand-written round trips | render with `profile="handwritten-wiring"`, so the whole configuration is inactive under Boot |
| **a delivered project** | renders ungated — the only place both halves are live |

The beans arrived with G31 and nothing had started a full Boot context on a generated project since.

### What Boot actually supplies, checked rather than assumed

`SpringBootBatchDefaultConfiguration extends DefaultBatchConfiguration`, and `javap` on the shipped
6.0.4 jar says that class exposes `jobRepository()` and `jobOperator(JobRepository)` as beans, while
`getJobRegistry()` and `getTransactionManager()` are `protected` accessors rather than beans. So the
delivered application gets its `JobRepository` and `JobOperator` from Boot, its
`PlatformTransactionManager` from `DataSourceTransactionManagerAutoConfiguration` against the real
`DataSource`, and needs no `JobRegistry` bean once the generated `jobOperator` is gone.

## Decision

**The rendered job configuration declares no batch infrastructure.** It declares the job, its steps,
`STEP_NAMES` and `CHUNK_SIZE`, and takes `JobRepository` and `PlatformTransactionManager` as
parameters — supplied by whoever runs it.

**The contexts that have no auto-configuration supply their own.** The rendered runner carries a
nested `@Configuration static class Infrastructure` with the four resourceless beans, and registers
it beside the configuration and its bindings. It lives in the runner, which is deleted after the run
(ADR-0075), so nothing resourceless reaches the artifact the tenant ships. The two hand-written
round-trip fixtures gained the same four beans in the wiring class they already use for exactly this
purpose.

**Rejected: `@ConditionalOnMissingBean` on the generated beans.** Boot's batch auto-configuration is
itself conditional, so both sides can back off and leave no bean at all. A conditional that depends
on evaluation order is a worse defect than the one being fixed, because it fails intermittently.

**Rejected: extending `DefaultBatchConfiguration`.** It would make Boot back off cleanly, and it is
the idiomatic Spring Batch answer — but it switches off exactly the auto-configuration
`BatchApplication` documents itself as preserving, which is the same mistake in a third disguise.

## Consequences

**The collision is gone, and that is all this record claims.** Measured on the delivered `step61`
project, re-rendered by the fixed renderer and built with its own `./mvnw -B verify`:
`BeanDefinitionOverrideException` occurrences went from 5 to **0**, and 41 of its 46 tests pass.

**A delivered project still does not start under a full Boot context**, for a second and unrelated
reason found by the same build:

```
computeMonthlyInterestItemReader -> NoSuchFileException: data\TCATBALF
```

Boot's `jobOperator` eagerly resolves the `Job`, which resolves the steps, which construct the file
readers -- and the rendered `application.properties` defaults `cobol.file.base=data` while the
delivered project ships no `data/` directory. **A generated job opens its files when the context
starts rather than when the step runs.** That is its own defect and wants its own record; it is
stated here rather than folded in, because bundling a second fix into a change is how this
repository shipped a broken one earlier the same day.

So what this record buys is narrower than "it starts": the generated configuration no longer fights
the auto-configuration it was documented to rely on, and a `JobRepository` backed by Postgres is now
reachable rather than shadowed by one that forgets every execution.

**The pipeline's own verdict is unaffected.** The runner's context is unchanged in what it contains;
the four beans moved from a class it registers into a class it registers.

**Two committed Java fixtures changed**, which is worth stating because fixtures are normally
evidence. These are wiring stand-ins rather than pinned model output: `HandWrittenRemainder` and
`PostingWiring` exist to supply what a generated remainder does not, and this is one more thing in
that category.

**This was invisible to every check this repository had**, including the one that renders a live
design and builds it with Maven. What found it was running the delivered artifact's own build in the
repository it was delivered to — which is the thing ADR-0079 makes possible and ADR-0077 was reaching
for. The lesson is narrower than "test more": a context that never enables auto-configuration cannot
tell you whether your beans collide with it, however much of it you run.
