# ADR-0030: Job wiring is rendered eventually, and hand-written once first

## Status

**Accepted** (2026-08-18), via the decision request in PR #57. Addresses gap **G31**. Decided through this pull request, per the
mechanism [ADR-0028](0028-what-the-round-trip-metric-requires-and-why-it-has-not-moved.md)
established: approving accepts, requesting changes rejects or amends, and the Status flips before
merge.

Follows [ADR-0029](0029-the-differential-compares-fields-and-an-excluded-field-is-reported.md), which
built the comparison, and PR #56, which built the oracle. Both are done and neither can be used.

## Context

`generate` produces domain records, `ItemProcessor`s, and a rendered equivalence test. It produces
**no reader, no writer, no step, no job**: `rendering/` contains `java_processor`, `java_records`,
`java_equivalence_test`, `java_names` and `target_api`, and the package contains no `JobBuilder`,
`StepBuilder`, `ItemReader`, `JobRepository` or `@Bean` anywhere.

**So the generated project compiles and cannot be run**, and the oracle now sitting in
`tests/fixtures/golden/CBACT04C/oracle/` has nothing to be compared against.

### How this happened, and why nothing here was a mistake

Every decision leading to it was individually correct:

| Decision | What it said |
|---|---|
| ADR-0010 | The model writes method bodies; everything structural is rendered |
| ADR-0019 | Generation is scoped to processors |
| ADR-0023 | A non-processor step is *reported*, not dropped |
| ADR-0027 | The aggregating reader is infrastructure, "rendered or hand-written" |

The gap is in the seam between them. *"Not written by a model"* was repeatedly established and
quietly read as *"not this pipeline's problem"* — but the alternative to model-authored is
**rendered**, not **absent**. The render/generate split has always had two categories; the artifact
needs three, and the third has no owner:

1. **Model-authored** — method bodies, inside markers.
2. **Rendered** — records, processor scaffolding, imports, provenance.
3. **Neither** — readers, writers, job and step configuration.

### The specific difficulty, which is not effort

Rendering a writer or a step bean is a mechanical transform of `design.json` in the same sense
`java_records.py` already is. **A reader is not**, because a reader needs to know *where the data
comes from*, and a type name does not carry that.

`computeInterest` takes `TranCatBalWithRate` — a composite of `TranCatBal`, `DisGroup`, `Account` and
`CardXref`. Reading one means a query joining four tables, and **nothing in the design says how they
join**. `CompositeType` declares components; it does not declare keys.

The COBOL does know. `CBACT04C` reads `TCATBAL-FILE` with `ACCESS MODE IS SEQUENTIAL` — the driving
file — and the other three with `ACCESS MODE IS RANDOM` and a declared `RECORD KEY`, which is exactly
"one driving stream, three keyed lookups". That is deterministic, parseable fact of the same grade as
a `PIC` clause.

**But `cobol_parser` does not read `FILE-CONTROL` at all.** It parses the DATA DIVISION; `SELECT`,
`ORGANIZATION`, `ACCESS MODE` and `RECORD KEY` are not extracted today.

## Options

**(a) Hand-write the wiring once, for `CBACT04C`.** Hours. Unblocks the measurement immediately. The
cost is that it is tenant-specific code that generalises to nothing, and any round-trip result must
be reported as *generated logic inside hand-written wiring* rather than as a generated program.

**(b) Render readers from an LLM-declared query per step.** `solution_architect` emits the SQL. Fast
to build and it puts a model in the one place this repo has been most careful to keep it out of: a
wrong join produces plausible rows and a silently wrong comparison. It is `pic_mapper`'s objection in
a new costume.

**(c) Render readers from the COBOL's own file declarations.** Extend `cobol_parser` to read
`FILE-CONTROL`, carry driving-vs-lookup and the record keys into the design, and render the reader
from that. **This is the architecturally right answer** — it is `pic_mapper`'s principle applied to
access paths, it is zero-token and unhallucinatable, and § 4b's "render, don't generate" lever says
mechanical transforms belong here. It is also a parser change, a contract change and a renderer, and
it lands well after the measurement it would enable.

**(d) Decline, and call the output a library of processors.** Honest, and it concedes the round-trip
metric permanently.

## Decision

**Take (a) now as an explicitly-labelled stopgap, and (c) as the target.**

This follows the platform's own stated ordering principle — *sequence by risk retired per unit of
effort; build the thinnest artifact that retires it* — and here the two are not the same work.

**The open question is whether the generated logic is correct.** That is what the oracle exists to
answer and what `0 of 4` has never measured. (c) is weeks of parser, contract and renderer work that
answers nothing about the logic; if the logic turns out to be wrong, (c) will have been built to run
code that needed changing anyway. (a) gets the measurement in hours, and the measurement is what
tells us whether (c) is worth building.

**The stopgap is bounded so it cannot quietly become the answer:**

1. It lives in `tests/fixtures/` or a clearly-named `handwritten/` directory — **never** in
   `templates/target-spring-boot-baseline/`, where it would silently become part of every generated
   project and make every future round-trip claim ambiguous.
2. Every round-trip result reports it. ADR-0029 already requires `1 of 4 (11/13 fields)`; this adds
   the second qualifier — **the wiring was hand-written**, so the claim is about generated *logic*.
3. It is written against `design.json`, not against the COBOL. If the wiring needs a fact the design
   does not carry, **that is a finding about the design** and gets recorded — which makes the stopgap
   a probe for exactly what (c) will have to render.

Point 3 is the reason (a) is not merely expedient: writing the wiring by hand is how we discover what
the contract is missing, and every gap it surfaces is a requirement for (c) that would otherwise be
guessed.

## Consequences

**Good.** The oracle and the comparison — both built, both shown to discriminate, both currently
unusable — get a candidate. The headline metric becomes measurable in hours rather than weeks, and
what it measures is stated precisely rather than implied.

**Accepted cost, and it is real.** A green round-trip after this does **not** mean "the platform
generated a working program". It means the generated business logic matches COBOL's output when
placed in wiring a human wrote. That is a genuinely weaker claim than the metric's name suggests, and
point 2 exists so nobody has to remember it.

**The risk is that the stopgap becomes permanent**, which is the normal fate of stopgaps. The
mitigation is point 3: the hand-written wiring is required to record every fact it needed that the
design lacked, so (c) starts with a requirements list drawn from practice instead of from a design
session. If that list is empty, (c) is a straightforward renderer. If it is long, that is the honest
size of the work and better known than assumed.

**What this does not decide.** Whether (c) is ever built. This ADR schedules the measurement, not the
factory — and G31 stays open until wiring is rendered, because a hand-written file for one program is
not a closed gap. **The reachable maximum remains `2 of 4`** (G17, ADR-0019), and `TRAN-ID` stays
excluded by ADR-0026.
