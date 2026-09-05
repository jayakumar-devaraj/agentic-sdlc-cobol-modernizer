# ADR-0066: `generate` renders the job wiring, and the hand-written stopgap retires

## Status

**Accepted** (2026-09-05). Written before the code, per the practice
[ADR-0065](0065-the-equivalence-test-is-rendered-and-its-result-field-is-resolved-from-the-design.md)
adopted after two records written afterwards both shipped wrong.

Reaches the target [ADR-0030](0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md)
named and deferred: its option **(c)**, *"render readers from the COBOL's own file declarations"*,
called there **"the architecturally right answer."** This record does not overturn that decision — it
executes it, and retires the stopgap ADR-0030 explicitly bounded so it could not become the answer.

Paired with [ADR-0067](0067-a-files-path-is-deployment-configuration-and-lives-in-spring-properties.md),
which decides the one fact this work needs and the design does not carry.

## Context

**Nearly all of (c) is already built.** ADR-0030 priced it as *"a parser change, a contract change
and a renderer"*; `parsing/file_control.py`, `FileAccessPath` and four renderers have all landed
since, each verified in its own entry of `docs/qa/verification-report.md`:

```
java_job.py         render_staging, render_job_configuration
java_reader.py      render_item_reader
java_writer.py      render_item_writer
java_aggregation.py render_aggregating_reader
```

**`generate_pipeline.py` calls none of them.** It calls `render_record` and `render_composite`, and
since ADR-0065 the equivalence-test renderer. Everything else is reached only from
`tests/integration/test_hand_written_round_trip.py`, through thin wrappers that do exactly what
`generate` would have to do.

So the situation is not the one ADR-0030 described. It is the situation ADR-0065 found one level
down: **a complete, tested renderer wired into nothing.** That is now this repository's most
frequently repeated defect, and the third instance in a month.

### A correction carried forward, so it is not re-derived

An earlier reading of `plan_steps` claimed three *steps* of `CBACT04C`'s design were unrenderable and
concluded a renderer was missing. That was wrong in a way worth recording: `6 of 9 renderable` is a
property of **that design**, whose three skipped steps have outputs going nowhere. It is
`plan_steps` correctly reporting a design defect, not a gap in `rendering/`. The renderers are
complete.

### What is genuinely missing is one fact, and it is not a design fact

`tests/fixtures/handwritten/CBACT04C`'s `HandWrittenRemainder.java` is the measured answer to *"what
does not render"*. It defines exactly **three beans**, and all three do the same thing — bind a path
to an already-rendered class:

```java
ItemReader<TranCatBalWithRate> tranCatBalWithRateItemReader()  // new ComputeInterestItemReader(paths…)
ItemWriter<Tran>               tranItemWriter()
ItemWriter<Account>            accountItemWriter()
```

Its own docstring states why, and states it correctly:

> A rendered reader takes `Path` arguments … because the COBOL says `ASSIGN TO TCATBALF` — an
> environment name — and nothing anywhere says what that resolves to. **Binding them to locations is
> deployment, and arguably never belongs in a design at all.**

That is ADR-0030's point 3 working exactly as intended: the stopgap was written to discover what the
contract is missing, and this is the single thing it found. ADR-0067 decides it.

## Decision

### 1. `generate` renders the staging, the aggregating readers, the readers, the writers and the job configuration

For every job in the design, using `plan_steps` to decide what is renderable, and reporting what is
not — which it already does, with reasons, and which is why a design defect surfaces as a named
skipped step rather than as a mysteriously empty project.

The renderers are called unchanged. This is a change to `generate_pipeline.py` and to nothing in
`rendering/`, which is the strongest evidence that (c) was already built.

### 2. `plan_steps`'s `skipped` list reaches the gate rather than only the logs

A skipped step means business logic present in the COBOL is absent from the generated project.
ADR-0023 already established that such a step is *reported, not dropped*; this extends that from the
processor loop to the wiring, because a job rendered with three of nine steps runs, produces output,
and is not the program.

### 3. The stopgap is retired, not left beside the renderer

`tests/fixtures/handwritten/CBACT04C/` stays where it is — as a **fixture**, exercising the same
round trip so the two paths can be compared — but it stops being the thing that makes a round trip
possible. ADR-0030's requirement that every round-trip result carry the qualifier *"the wiring was
hand-written"* ends for runs that go through `generate`, and that qualifier's removal is exactly the
claim this record is accountable for.

**Retired is not deleted.** ADR-0030's point 1 kept it out of
`templates/target-spring-boot-baseline/` so it could never silently join every generated project;
that constraint is unchanged, and deleting the fixture would throw away the only independent check
that the rendered wiring produces the same answer as wiring a person wrote.

## Consequences

**The round-trip metric becomes measurable for the first time.** `compare_project_output` has
reported `not_run` for every real design since ADR-0064, with the reason naming this gap. It now has
something to compare, and `GenerateCliResult.equivalence` can report `matched` or `mismatched` on a
project the pipeline produced end to end.

**A green run will claim much more than ADR-0065's does, and that is the point and the risk.** The
unit equivalence test covers one `COMPUTE`; the differential compares every field of every written
record, and the account half excludes nothing (ADR-0029). It is the check that would have caught step
51's accumulator, which ADR-0065 could not. It is also the check whose failure modes are least
understood, because it has never run against generated wiring.

**`generate` gets materially slower and can now fail for deployment reasons.** It renders more
files, and a job that will not start is a new failure class in a phase whose failures have all been
compile failures until now. Reported as facts for the gate, never as a decision — the specialist
contract's rule 5, unchanged.

**One program, and the claim is scoped to it.** `CBACT04C` is the only program this has been run
against, and `CLAUDE.md`'s standing rule is that a capability is complete when a *second* instance
exercises it — `CBTRN02C` is maintenance rule 6 and stays open. The honest closure is *"the wiring
renders for `CBACT04C`"*, and G31's closure is written that way rather than as *"wiring is
rendered"*, which is the exact overclaim that rule exists to prevent.

## Alternatives considered

**Keep the stopgap and wire only the differential.** Rejected: the differential would then measure
generated logic inside hand-written wiring — precisely the qualifier ADR-0030 attached to it, and the
reason `0 of 4` was never a number about this pipeline.

**Render the wiring but keep it behind a profile, as the fixture does.** The profile exists so the
hand-written beans do not join every Spring Boot test's context in the generated project. Rejected
for the rendered path: a job that only runs under a non-default profile is not the program, and the
generated project's own context is where it belongs. The rendered configuration keeps its optional
`profile` parameter, unused by `generate`, because the fixture round trip still needs it.

**Wait for `CBTRN02C` and render both at once.** Rejected on ADR-0030's own ordering principle —
sequence by risk retired per unit of effort. The risk here is that generated logic is wrong, and one
program measuring it retires far more of that than a second program measuring nothing yet.
