# ADR-0075: The pipeline runs the job it generated

## Status

**Accepted** (2026-09-07). Closes the gap
[ADR-0064](0064-the-differential-becomes-a-gate-verdict.md) left open: the differential has
been able to return a verdict since that record and had never returned one, on any run, because
nothing gave it output to compare.

Same family as [ADR-0065](0065-the-equivalence-test-is-rendered-and-its-result-field-is-resolved-from-the-design.md) (renders
the per-row equivalence test) and
[ADR-0066](0066-generate-renders-the-job-wiring-and-the-stopgap-retires.md) (renders the wiring). Those two
made a job that compiles; this one starts it.

## Context

Run `step58-cbact04c-20260907-085235` rendered **6 of 6** steps and reported *"Wiring: rendered and
compiled; every renderable step is wired"* — the first live design ever to get there. It still
returned:

```
Equivalence: NOT RUN -- the project produced no output to compare:
roundtrip/output/transact.dat, roundtrip/input/acctdata-stage1.dat
```

`generate` called `compile_project` with `goal="compile"` and stopped. Nothing staged the oracle's
inputs and nothing executed the built job.

### The mechanism existed and had never been in the pipeline

`tests/integration/test_hand_written_round_trip.py`'s `wire_build_and_run` does three things
`generate` did not: copies in a job-run test, stages inputs into `roundtrip/input/`, and builds with
`goal="verify"`, which is what runs the job *there*.

**What that test proves is narrower than it looks.** It reaches `roundtrip/` only because
`HandWrittenRemainder.java` hardcodes `Path.of("roundtrip", "input")` and constructs the writers
itself — bypassing the rendered bindings entirely. The rendered bindings resolve every path against
`cobol.file.base`, which defaults to `data`. So the harness and the generated job have never pointed
at the same directory, and the round trip's green result is about the hand-written wiring.

### Why not simply `goal="verify"`

Because the baseline template ships `BaselineStackTest`, which is `@SpringBootTest @Testcontainers`.
An unfiltered `test` or `verify` phase makes the job-run verdict depend on Docker being present on
the specialist host and on failures that say nothing about this job. `run_equivalence_test` already
solved this one level down, with `goal="test"` plus `-Dtest=<class>` and
`-Dsurefire.failIfNoSpecifiedTests=false`, and the same narrowing is what this uses.

## Decision

**`generate` stages the oracle's inputs, renders a runner, and executes the built job — narrowed to
that runner.**

1. `equivalence/staging.py` stages exactly the files a job binds, read from `file_binding_properties`,
   and returns the `cobol.file.*` overrides that point the job at what it wrote. **One function
   returns both**, because deriving "what to stage" and "where to tell the job to look" separately is
   how the two start disagreeing — and the disagreement would surface as `not_run` with nothing
   saying which half was wrong.
2. `rendering/java_job_run.py` renders the runner. Rendered rather than copied because the classes it
   registers are named from the design: the two live designs produce
   `InterestCalculationJobConfiguration` and `MonthlyInterestCalculationJobConfiguration`.
3. It asserts the **run**, never the values ([ADR-0029](0029-the-differential-compares-fields-and-an-excluded-field-is-reported.md)):
   `COMPLETED`, and that output exists. Whether the arithmetic matches COBOL is the Python
   differential's judgement, and duplicating it here would give it a second place to disagree with
   itself.
4. A new `JobRunVerdict` carries the outcome, separate from `EquivalenceVerdict`. That one's
   `not_run` branch can say only "there is no output here" — a job that abended, a context that could
   not start, and a pipeline that never tried all collapse into one sentence.

The runner builds a plain `AnnotationConfigApplicationContext`, not `@SpringBootTest`: the rendered
configuration carries its own `ResourcelessJobRepository` and `ResourcelessTransactionManager`, so
Boot's autoconfiguration would only add a `DataSource` requirement that is not what is being
measured.

## Consequences

**`compare_project_output` is unchanged.** Staging points the job at `roundtrip/input/` and
`roundtrip/output/` because that is where the harness looks; the harness's constants are documented
as deliberately non-configurable — *"a caller that could point this anywhere could point it at the
oracle"* — and weakening that to meet the job halfway would have been the wrong direction.

**The staged paths are baked into the rendered runner rather than passed as `-D`.** Surefire does not
forward command-line system properties into the forked JVM without being configured to, and
configuring the generated project's POM to make one test work would change the artifact the tenant
ships.

**So the runner does not survive the run that used it.** Baking the paths in means the file holds one
machine's temp directory, and an ordinary test in `src/test/java` joins every later unfiltered `test`
or `verify`. Left in place it is therefore a test that fails for every tenant who builds this
artifact, and it takes the build with it. Measured rather than reasoned: the round-trip integration
test runs `verify`, and it went red the first time this ran. The runner is deleted in a `finally`,
and what a reviewer needs survives in the verdict — `test_class` names what ran, `staged_inputs`
names every file it was pointed at.

**A green build is not evidence the job ran.** `-Dsurefire.failIfNoSpecifiedTests=false` is what stops
a project with no matching test from failing, and it makes a runner surefire never picked up exit 0
exactly like one that passed. `completed` is therefore conditioned on the output file existing, not
on the exit code — otherwise the one verdict in this pipeline that claims the program executed would
be the easiest in it to fake.

**This is the fourth "last thing in the way" in four records, and it is not the last one either.**
ADR-0071 said the remaining gap was design ordering; ADR-0072 closed that and ADR-0073 was behind it;
ADR-0074 was behind that; running the job was behind that. Pre-flighting this change found the next
one, stated here rather than discovered again later:

> **Both live designs bind three of `CBACT04C`'s five files.** `XREFFILE` and `DISCGRP` are declared
> in `file_access_paths`, resolvable, and bound to nothing; `ACCTFILE` is bound only as a writer. The
> wiring hands every processor a no-argument constructor, and a rendered processor's only injectable
> state is job parameters — so `ResolveAccountAndCardXrefProcessor` must return a
> `TranCatBalWithAccount(categoryBalance, account, cardXref)` with nothing to build the last two
> from, and `ResolveInterestRateProcessor` must attach a disclosure group it cannot read.
>
> The renderer supports these joins: a step whose `input_type` is the composite gets a reader taking
> all four paths, and `_order_lookups` and `_lookup` exist for exactly that. Both models instead
> wrote the bare entity as `input_type` and the composite as `output_type`, putting the join in the
> processor. **Nothing refuses it** — `reads_a_file` answers yes, a one-path reader renders, it all
> compiles, and the wiring reports every step wired.

That is the same signature as ADR-0072's and ADR-0074's defects and wants its own record. It is
stated here because a `JobRunVerdict` of `failed` on `CBACT04C` should be read against it rather than
as a defect in the generated arithmetic.

**A test records the gap rather than prose.**
`test_both_live_designs_strand_the_two_lookup_files` asserts the lookups are declared and unbound. It
is written to fail when a design binds them, which is the direction that wants attention.

**An `ASSIGN TO` absent from `SOURCES` is not the same question as an output.** The first
implementation treated it as one, which made the refusal unreachable: every unrecognised *input* was
silently pointed at an empty path under `roundtrip/output/`, and the job would have started and died
on a file nothing staged. The design's own sink set decides it now. Recorded because it is bite 4
from this repository's own list — *prove a new check can fail before trusting it* — and the check was
written the obvious way and could not.
