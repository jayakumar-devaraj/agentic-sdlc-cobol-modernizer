# ADR-0072: A step is ordered where its input exists

## Status

**Accepted** (2026-09-06). Closes the finding
[ADR-0071](0071-a-chunk-step-is-a-processor-step-and-the-job-names-only-those.md) left open in its
Consequences, and takes the same two-halves shape as
[ADR-0059](0059-a-field-name-is-required-and-a-nameless-declaration-is-refused.md),
[ADR-0062](0062-a-step-must-be-able-to-return-what-it-computes.md),
[ADR-0063](0063-an-accumulator-belongs-to-its-group-not-to-the-row.md) and
[ADR-0070](0070-a-step-that-changes-its-items-type-is-a-processor.md): the rule is stated in the
architect's prompt and refused by the contract that reads its output.

Leaves [ADR-0032](0032-a-rendered-job-names-every-step-and-stages-what-crosses-a-boundary.md)
untouched. This record moves the question *earlier*; it does not soften what happens to a design
that still arrives with a step nothing can supply.

## Context

Run `step55-cbact04c-20260906-090845` produced the first live design whose wiring rendered. Its job
still cannot start: `computeCategoryFees` is named in `STEP_NAMES` with no bean behind it, because
`plan_steps` correctly declined to render a step whose input nothing supplies.

ADR-0071 recorded that as a design-ordering finding and stopped there. It also stated the mechanism
slightly wrong, and the correction is what makes the rule decidable.

### What the design actually says

`step55`'s six chunk steps, in the order the model wrote them:

| # | step | in | out |
|---|---|---|---|
| 1 | `resolveAccountAndCardXref` | `TranCatBal` | `TranCatBalWithAccount` |
| 2 | `resolveInterestRate` | `TranCatBalWithAccount` | `RatedCategoryBalance` |
| 3 | `computeMonthlyInterest` | `RatedCategoryBalance` | `AccruedCategoryInterest` |
| 4 | `writeInterestTransaction` | `AccruedCategoryInterest` | `Tran` |
| 5 | `computeCategoryFees` | `AccruedCategoryInterest` | `AccruedCategoryInterest` |
| 6 | `postAccountInterest` | `AccountInterestPosting` | `Account` |

ADR-0071 and `test_a_live_design_wires`'s docstring both said *"nothing supplies the
`AccruedCategoryInterest` it consumes"*. Step 3 supplies it. **Step 4 consumes it and returns a
`Tran`**, so by the time the chain reaches step 5 the item has changed type. That is a fan-out --
two steps consuming one type -- not a missing producer, and the difference is the whole decision: a
missing producer is unfixable by reordering, and a fan-out whose extra consumer is a passthrough is
fixable by nothing else.

### Why the model put it there

The COBOL, and the design is faithful to it:

```
214    IF DIS-INT-RATE NOT = 0
215      PERFORM 1300-COMPUTE-INTEREST
216      PERFORM 1400-COMPUTE-FEES
217    END-IF

462   1300-COMPUTE-INTEREST.
468        PERFORM 1300-B-WRITE-TX.        <- nested

518   1400-COMPUTE-FEES.
519   * To be implemented
520       EXIT.
```

At the top level, fees follows interest. `1300-B-WRITE-TX` is a **nested** PERFORM inside
`1300-COMPUTE-INTEREST`. The model flattened those three paragraphs into three sibling steps and
kept paragraph order, which inserted the type-changing step between the producer and the second
consumer. Every paragraph is in source order and the result cannot be wired.

Nor did the model misunderstand the paragraph. Its own `description` for the step:

> Explicit no-op extension point preserved from the empty COBOL fee stub, invoked once per
> non-zero-rate category balance and passing the item through unchanged.

It knew the step was a passthrough over a category balance. It ordered by paragraph number, which is
what it was asked for -- `v1_4_0` says nothing about ordering.

### The rule is decidable, and the fix is one move

`plan_steps` already asks exactly this question at render time, and answers it against the pinned
design with no model call:

| | renderable | staged | named with no bean |
|---|---|---|---|
| as the model wrote it | 5 of 6 | 3 types | `computeCategoryFees` |
| `computeCategoryFees` moved ahead of `writeInterestTransaction` | **6 of 6** | same 3 | **none** |

A valid order exists, one move away, and nothing else is perturbed. So this is not a design that
cannot be expressed -- it is a design ordered by the wrong key.

### A check that could not have fired

The first shape this refusal could have taken runs `plan_steps` over the design as parsed. That
check never fires, on any design:

`attach_control_breaks` runs *after* `parse_with_repair`, so at validation time no step carries a
`control_break`, and `aggregation_source` -- the only path that makes an aggregating step renderable
-- returns `None` for every step. Against the unattached `step55` design, `plan_steps` strands
**both** `computeCategoryFees` and `postAccountInterest`; no single move fixes a pair like that, so
the refusal falls silent and every live design passes. Measured, not reasoned: with control breaks
stripped, the candidate-move search returns `None`.

`contracts.py`'s `accumulator_owners` records another rule making this exact mistake, its unit tests
passing over it because they built the post-attachment state. Verification 18 records a second: a
wait condition that matched a string already in the log and so could not fail. **A check that cannot
fail is not a check**, and this one is two lines away from being one.

## Decision

**1. A step's input must exist where the step sits.** Readable from a file the program declares, or
the `output_type` of the step immediately before it, or -- for an aggregating step -- the output of
the step it groups over. Stated in architect prompt **`v1_5_0`**, because enforcing a rule the
prompt never stated punishes a model for following the contract it was given (ADR-0059's shape).

**2. Where two steps consume the same type, the step that changes it comes last.** This is the rule
in the form a model can act on. Two paragraphs performed from the same loop against the same working
storage have no order between them in COBOL; in a typed chain they do.

**3. Refused by `_refuse_a_step_ordered_before_its_input_exists`, through `parse_with_repair`** --
so a design carrying this fault buys one repair attempt at design time rather than a `generate` and
an approval later.

**4. The oracle is `plan_steps` itself, not a second implementation of its question.** ADR-0071 is
the bill for two functions answering "is this a chunk step?" privately. A design-time rule checked
by other means would drift from the renderer the first time either moved.

**5. Refused only where a single step move renders every chunk step, and silent otherwise.** The
message names that move. A design that cannot be ordered at all has a genuine fan-out the chain
cannot express; ADR-0054 grants one repair attempt, and spending it on "reorder this" that no
reordering satisfies buys nothing and loses the attempt. Such a design proceeds and fails loudly at
startup, which is what ADR-0032 is for.

**6. The validator sees the design the renderer will see.** Control breaks are attached and file
access paths supplied before the check runs. Both are deterministic and neither depends on the
model's response.

## Consequences

**Nothing is reordered on the model's behalf.** The pipeline names the move; the model makes it.
Reordering side-effecting steps is a business-semantics choice -- `writeInterestTransaction` writes
a file -- and this repository does not make those (ADR-0032).

**ADR-0071's Consequences and `test_a_live_design_wires`'s docstring are corrected here** on the
mechanism, not the outcome. Both said nothing supplies the step's input; a step three positions back
does, and an intervening step consumes it. The outcome each describes was right.

**The pinned live-design fixture stays exactly as the model wrote it**, and now regresses two things
instead of one: the renderer's handling of a stranded step, and the design-time refusal that would
have caught it. A fixture corrected to the rule it is testing would test nothing (ADR-0071).

**The pipeline still cannot tell that `1400-COMPUTE-FEES` is empty**, and still does not try. The
step is designed, ordered, rendered and wired as the passthrough the model described. ADR-0071's
rejection of "drop it because its COBOL looks unimportant" stands unchanged.

**Seven defects have now been found by pointing the pipeline at a design a model wrote** (ADR-0069),
and this is the first found by reading what the model *said* about a step rather than what the
renderer did with it.

## Alternatives considered

**Reorder the steps automatically.** Mechanical, and a topological sort would have fixed this design
without a second model call. Rejected: it is only safe because this particular stranded step is a
passthrough. Two steps that both transform, or that both write, have an order the COBOL fixes and a
sort does not know, and a pipeline that silently reorders those produces a program that is wrong in
a way nothing downstream would flag.

**Refuse whenever any step is stranded, without looking for a move.** Simpler, and it fires on more
designs. Rejected: it fires hardest on aggregating designs, where no move exists and the message
would be unfollowable -- see *A check that could not have fired*, where that is also the version
that never fires at all.

**Fix it in the renderer by dropping the step.** ADR-0071 already rejected this and the reason is
unchanged: the pipeline would be deciding a step is unimportant by reading its paragraph, which is
what `skipped_steps` and ADR-0023 exist to prevent.

**State the rule in the prompt and leave the contract alone.** Half of every ADR listed in the
Status, and the half that decays: a rule nothing enforces is a rule the next model revision silently
stops following.
