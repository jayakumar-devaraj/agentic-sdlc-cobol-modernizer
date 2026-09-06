# ADR-0070: A step that changes its item's type is a processor

## Status

**Accepted** (2026-09-06). The design-time half of the finding
[ADR-0068](0068-a-steps-structure-is-read-from-the-design-not-inferred-from-its-types.md) recorded
and deliberately did not fix: that record is about the wiring reading what the design says, and this
one is about the design saying something wrong.

Same two-halves shape as [ADR-0059](0059-a-step-name-is-refused-where-it-is-produced.md),
[ADR-0062](0062-a-step-must-be-able-to-return-what-it-computes.md) and
[ADR-0063](0063-an-accumulator-belongs-to-its-group-not-to-the-row.md): stated in the
`solution_architect` prompt (`v1_4_0`) and enforced on the way out through `parse_with_repair`.

## Context

With ADR-0068's four renderer defects fixed, the live design from run
`step54b-cbact04c-20260905-211254` renders its whole wiring and still does not compile:

```
InterestCalculationJobConfiguration.java:153: cannot find symbol
  symbol:   class WriteInterestTransactionProcessor
InterestCalculationJobConfiguration.java:169: cannot find symbol
  symbol:   class PostAccountInterestProcessor
```

Both steps are typed `"writer"`. `generate` renders a body for `role == "processor"` and for nothing
else (ADR-0023, G27), while `render_step_bean` injects a `<Step>Processor` into every chunk step it
wires. So a `writer` step that is planned is a step whose processor class will never exist.

**The model's reading was reasonable.** `1300-B-WRITE-TX` ends in a `WRITE` and
`1050-UPDATE-ACCOUNT` ends in a `REWRITE`; calling them writers is what the paragraph names say. The
prompt asked for a `role` from four options and never said which one a type-changing step must take.

**And this repository had already answered it twice, in prose, without ever checking it.**

`unobtainable_inputs` narrows to processors and gives the reason:

> A reader's and a writer's outputs are bound by `READ ... INTO` and `WRITE ... FROM`, and a tasklet
> has no item at all.

`tests/support/interest_design.py` types `1300-B-WRITE-TX` a processor and spends a paragraph on why:

> The paragraph is mostly per-item field population — fourteen `MOVE`s and two `STRING`s — which is
> what an `ItemProcessor` is for. […] Typing the whole paragraph as a `writer` would leave its field
> population ungenerated for the sake of the three statements that are genuinely not translatable.

ADR-0027 said the same about the posting step: once the item is pre-aggregated, it is *"an ordinary
per-item transform"*.

So the fact was settled, written down three times, and enforced nowhere — which is the class
`CLAUDE.md` counts, arriving this time as prose rather than as a contract field.

## Decision

**If a step's `input_type` differs from its `output_type`, its `role` must be `"processor"`.**

Decidable from the design alone, and that is why this rule rather than a judgment about what a
paragraph "really does": different input and output types mean *something transformed the item*, and
in a Spring Batch chunk step only the processor can. A reader's output is what it read, a writer
writes what it was given, a tasklet has no item.

`"reader"`, `"writer"` and `"tasklet"` remain correct where input and output types are the same —
the file open and close, and the plain record read that drives the job. ADR-0068 already excludes
those three from the wiring for their own reason.

Stated in the prompt as **v1_4_0** and enforced by `_refuse_a_transform_that_is_not_a_processor`,
reached through `parse_with_repair` so a model gets one repair attempt carrying the message. The
message names the role to use, because a model told only that its answer is wrong has three
remaining options and two of them are also wrong.

## Consequences

**A compile error moves to before the human gate.** The reviewer who approved `step54b`'s design
would have met these two missing classes in `generate`, after approving — the same sequence ADR-0062
and ADR-0063 each found once, and the reason both are enforced at the design boundary.

**`writer` becomes a narrow role, and that is a real change in what the design language means.** It
now describes only a step that hands on what it was given. Nothing in the corpus loses by it:
`CBACT04C`'s two writer-typed steps both transform, and the fixture design already types their
equivalents `processor`.

**It is not yet proven against a live model.** ADR-0063 was written `Proposed` for exactly this
reason and this record is not, because the difference is what is being claimed. ADR-0063 claimed a
model *would place a value correctly* once told; this claims only that a design violating a stated
rule is refused before it reaches a gate, which is verified by its own test. Whether `v1_4_0` makes
the architect type these steps correctly on the first attempt is the next live run's question, and
the repair attempt exists because the honest answer is "not necessarily".

**One artifact carries the defect**, deliberately: `tests/fixtures/live_designs/cbact04c-design.json`
is a design this rule now refuses, kept as the input that found ADR-0068's four defects (ADR-0069).
Its test asserts the two missing classes are the *only* remaining compile errors, so when a design
produced under `v1_4_0` replaces it, that assertion is what has to change.

## Alternatives considered

**Render a pass-through processor for a `writer` step.** `return item;` compiles and would make the
project build. Rejected: for `1300-B-WRITE-TX` it would silently discard fourteen `MOVE`s of field
population, which is precisely the step-49 defect — generated code that runs, looks complete, and
has lost its business logic.

**Skip `writer` steps in `plan_steps`, symmetrically with `reader` and `tasklet`.** Considered
seriously, since it needs no design change. Rejected: it would drop `postAccountInterest`, which
carries the control break, so an account total would go silently unposted — the outcome ADR-0063
exists to prevent. A writer step carries an item; a reader step's item is its own arrival.

**Infer the role from the paragraph instead of from the types.** More faithful in principle and
undecidable in practice: it asks whether a paragraph's `MOVE`s are business logic or record
assembly, which is the judgment the model is there to make. The type change is an observable
consequence of that judgment and needs no second opinion about it.
