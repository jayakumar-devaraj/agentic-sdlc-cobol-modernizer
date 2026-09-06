# ADR-0073: A staging store belongs to the step that fills it

## Status

**Accepted** (2026-09-06). Found while verifying
[ADR-0072](0072-a-step-is-ordered-where-its-input-exists.md) — the design that record tells the
architect to produce did not compile, so the two ship together.

Refines [ADR-0032](0032-a-rendered-job-names-every-step-and-stages-what-crosses-a-boundary.md),
which introduced the in-memory staging bean and keyed it by the type it carries.

## Context

ADR-0072 refuses `step55`'s design and names the move that fixes it: `computeCategoryFees` ahead of
`writeInterestTransaction`. `plan_steps` confirms that move renders every chunk step. Rendering the
moved design and running `javac` says otherwise:

```
InterestCalculationJobConfiguration.java:147:44: variable accruedCategoryInterestStaging
is already defined in method computeCategoryFeesStep(
    JobRepository, PlatformTransactionManager,
    AccruedCategoryInterestStaging, AccruedCategoryInterestStaging)
```

`computeCategoryFees` is a passthrough: `AccruedCategoryInterest` in, `AccruedCategoryInterest` out,
neither end file-backed. `_step_bean` asks for a store by its item's type on both ends, so both ends
named the same bean and the method declared one parameter twice.

### Deduplicating the parameter would have been much worse

The obvious fix — notice the two are equal, emit one — compiles. It also hands the step a single
store it both reads and writes, and `render_staging` emits a list with a read cursor:

```java
public void write(Chunk<? extends T> chunk) { staged.addAll(chunk.getItems()); }
public T read() { return next < staged.size() ? staged.get(next++) : null; }
```

Every item read is written back, so the list grows by one for each item the cursor consumes and
`read()` never returns `null`. **The job would not terminate.** A green compile hiding a
non-terminating job is worse than the compile error it replaced, and this is the third time in this
repository that compilation has been mistaken for a job that runs (ADR-0071, verification 18).

### The key was wrong, not the value

A store carries **one edge of the chain**, and an edge is identified by the step that fills it. A
type identifies an edge only while no type appears on two consecutive edges — which held for every
design the pipeline had seen, because none contained a passthrough step. The reordered chain has
two:

| edge | carries | filled by |
|---|---|---|
| … → `computeCategoryFees` | `AccruedCategoryInterest` | `computeMonthlyInterest` |
| `computeCategoryFees` → `writeInterestTransaction` | `AccruedCategoryInterest` | `computeCategoryFees` |

Keyed by type those are one store; keyed by producer they are two, which is what the chain means.

This is the shape ADR-0068 and ADR-0071 each recorded once already — **a fact keyed by something
that happened to be unique in every case so far** — and CLAUDE.md's rule about the *n*-th defect of
a kind applies: the change worth making is the one that makes the *n+1*-th impossible. Producer
keying is total. There is no conditional to forget, because two edges cannot share a producer.

## Decision

**1. `staging_class_name` takes the producing step, not a type name.** `computeMonthlyInterest` →
`ComputeMonthlyInterestStaging`. The signature change is the enforcement: a caller cannot pass a type
name by accident, because the parameter is a `BatchStepDesign`.

**2. `render_staging` takes the producing step** and reads the item type from its `output_type`. One
store, one producer, one type — stated in one place instead of agreeing in three.

**3. `plan_steps` returns the steps whose output needs a store**, not the type names. Both places
that recorded a store already had the producing step in hand: the chain edge records `step`, and the
aggregation records the step it groups over.

**4. A step reading from the chain takes its predecessor's store**, resolved through
`_produces_the_input_of` using `is_chunk_step` — the same predicate `plan_steps` walks the chain
with, so the renderer and the planner cannot disagree about which step comes before which
(ADR-0071).

## Consequences

**Generated bean names change.** `tranWithContextStaging` becomes `computeInterestStaging`. Nothing
outside the generated project refers to them — the job looks its steps up by name, not its stores —
and five tests that pinned the old names now pin the new ones.

**A passthrough step renders.** It was unreachable before: no design the pipeline had seen put the
same type on two consecutive edges, which is why ADR-0032's keying survived this long.

**The name says what the store is for.** `ComputeInterestStaging` names the step that fills it;
`TranWithContextStaging` named the item and left "which handoff?" unanswered as soon as there were
two.

**ADR-0072's named move now produces a project that compiles**, verified rather than argued —
see `docs/qa/verification/19-the-move-the-refusal-names.md`.

## Alternatives considered

**Deduplicate the parameter when both ends resolve to the same store.** Compiles, and does not
terminate. Rejected on the mechanism above; it is the option that looks cheapest and is the only one
that produces a wrong program rather than a loud failure.

**Refuse a passthrough step in `plan_steps`.** Honest, and it would leave ADR-0072 naming a move
that renders a job which still cannot start — closing one gap by opening another in the same PR.

**Fuse a passthrough into its neighbour.** One step, one store, no collision. Rejected for
ADR-0032's original reason: fusing removes a step boundary a human approved at the gate, and the
generated job would no longer have the shape the design was signed off as.

**Key the store by the edge's position in the chain.** Equivalent, and worse to read: `Staging3`
says nothing about what fills it, and every insertion renumbers the stores after it.
