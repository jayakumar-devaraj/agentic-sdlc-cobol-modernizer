# ADR-0071: A chunk step is a processor step, and the job names only those

## Status

**Accepted** (2026-09-06). Completes
[ADR-0068](0068-a-steps-structure-is-read-from-the-design-not-inferred-from-its-types.md), which
taught `plan_steps` to read a step's `role` and left `render_job_configuration` naming every
declared step. Rests on
[ADR-0070](0070-a-step-that-changes-its-items-type-is-a-processor.md), which is what makes the
first half of this record's title true rather than merely convenient.

Refines [ADR-0032](0032-a-rendered-job-names-every-step-and-stages-what-crosses-a-boundary.md)'s
"the job names every step" in one narrow way, stated in the Decision.

## Context

Run `step55-cbact04c-20260906-090845` was the first live run whose wiring rendered. `generate`
reported `Wiring: rendered`, six processors compiled, and the equivalence test passed. The rendered
project also compiles.

**Its job cannot start.**

```
STEP_NAMES = List.of("openInterestCalculationFiles", "readTranCatBalance",
    "resolveAccountAndCardXref", "resolveInterestRate", "computeMonthlyInterest",
    "writeInterestTransaction", "computeCategoryFees", "postAccountInterest",
    "closeInterestCalculationFiles")        # nine names
```

Five `@Bean Step` methods were rendered. The job's own lookup then throws, naming the first missing
one:

```
job "interestCalculationJob" declares step "openInterestCalculationFiles"
and no bean named "openInterestCalculationFilesStep" supplies it
```

ADR-0068 taught `plan_steps` that a `tasklet` and a `reader` are not chunk steps.
`render_job_configuration` builds `STEP_NAMES` from `job.steps` and was not changed. **Two halves of
one decision, disagreeing** — which is the same shape as the defect ADR-0068 was written about, one
layer up.

### Compilation did not catch it, and was trusted to

The offline pre-flight for this run rendered the project and compiled it: `COMPILES: True`. That was
accurate and insufficient. A Spring bean lookup is a runtime failure, so javac cannot see it, and a
green build was read as "this project runs". The differential's refusal — *"the job is not fully
renderable, so no runnable project exists to compare"* — was correct, and was briefly written off as
over-strict.

### A second, latent hole in the same predicate

ADR-0068 excluded `tasklet` and `reader`. A planned step takes a `<Step>Processor`, and `generate`
renders a body for `role == "processor"` and nothing else — so a planned **`writer`** wires the job
to a class that will never exist. That is exactly the defect ADR-0070 refuses at design time, still
reachable through the renderer if a design ever carries one.

### Two fields, and the wrong one was used

`WiringVerdict` already distinguishes these:

> **`skipped_steps`** — "business logic present in the COBOL and **absent from the generated
> project**"
>
> "Separate from **`steps_not_generated`**, which counts steps this pipeline never renders **by
> role**."

ADR-0068 put role exclusions in `skipped_steps`. So the run's gate told a reviewer that three file
open/close/read paragraphs were missing business logic — false, and precisely the misreading that
field's docstring exists to prevent. They were already counted correctly by role elsewhere, with
their paragraphs named, so they were reported twice and wrongly once.

## Decision

**1. A chunk step is a processor step.** `_NOT_A_CHUNK_STEP` covers `tasklet`, `reader` and
`writer`. ADR-0070 requires every step whose input and output types differ to be a `processor`, so a
step of any other role transforms nothing — it is the reader's, the writer's or the job's lifecycle.
Adding `writer` closes a hole rather than adding a case.

**2. One predicate, `is_chunk_step`, used by both halves.** `plan_steps` and
`render_job_configuration` must answer this question the same way, and the only reason they diverged
is that each asked it privately.

**3. `STEP_NAMES` names every chunk step and only those.** ADR-0032 has the job name a step it does
not render so a missing bean fails loudly instead of leaving a shorter job that looks like it ran.
That is right for a step whose **business logic** could not be wired — `computeCategoryFees` stays
named, and the job still refuses to start until someone deals with it. It is wrong for a role
exclusion, which is not a step at all: naming one does not make it impossible to forget, it makes
the job impossible to start.

**4. A role exclusion is not a `skipped_step`.** It is reported through `steps_not_generated`, which
already carries it with the role and the paragraphs.

## Consequences

**The differential can return a verdict for a design whose processor steps all wire.** It could not
before, for any live design, because every real one declares a file open.

**`step55`'s job still cannot start**, and this record does not change that:
`computeCategoryFees` is ordered after `writeInterestTransaction`, whose output is a `Tran`, so
nothing supplies the `AccruedCategoryInterest` it consumes. That is a design-ordering finding, it is
named in `STEP_NAMES` with no bean, and ADR-0032's loud failure is doing its job. Its COBOL is
`* To be implemented` / `EXIT.` — but the pipeline cannot know that, and inferring it would be the
guess this repository refuses everywhere else.

**The pinned live-design fixture was replaced, and ADR-0069 was wrong about why it would go stale.**
That record said `step54b`'s design should stay after ADR-0070 changed what an architect produces.
Once `writer` is correctly excluded, that design's aggregation, control break and computed fields
all become unreachable — it would have been a test that passes while exercising none of what it
names. **A regression fixture has to still reach the code it regresses.** `step55`'s design, produced
under `v1_4_0`, replaces it.

**Six defects have now been found by pointing the pipeline at a real design**, four of them by the
test module ADR-0069 introduced, two of those *after* the first three were fixed. That is the
argument for the fixture, made by the fixture.

## Alternatives considered

**Render a no-op `Step` bean for a tasklet.** It would make the job start. Rejected: a file open is
not a step, and rendering an empty one puts a lie in the job's structure — the generated job would
claim to do something the original does elsewhere.

**Leave `STEP_NAMES` alone and let the operator supply the missing beans.** That is ADR-0032's
hand-written-step escape hatch, and it is real. It does not apply: nobody is going to hand-write a
bean for `1000-TCATBALF-GET-NEXT`, because Spring Batch's reader already is one.

**Exclude `computeCategoryFees` too, since its COBOL is empty.** Rejected on the fact that the
pipeline cannot see it is empty without reading the paragraph and judging it — and a step silently
dropped because a renderer thought it looked unimportant is the failure mode ADR-0023 and
`skipped_steps` both exist to prevent.
