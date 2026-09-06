# ADR-0074: A mid-chain step reads its predecessor, not a file

## Status

**Accepted** (2026-09-06). Found by the first live run under
[ADR-0072](0072-a-step-is-ordered-where-its-input-exists.md)'s ordering rule — run
`step56-cbact04c-20260906-153713`, whose design obeyed that rule on the first attempt and whose
wiring was then refused.

Completes [ADR-0073](0073-a-staging-store-belongs-to-the-step-that-fills-it.md): that record gave
each staged edge its own store, and this one makes the steps actually read them.

## Context

`step56`'s architect placed `computeCategoryFees` as a `RatedCategoryBalance` passthrough between
`resolveInterestRate` and `computeMonthlyInterest`. Every step's input is its predecessor's output,
`plan_steps` planned all six, and `generate` refused:

```
steps 'computeCategoryFees' and 'computeMonthlyInterest' both need a
ItemReader<com.modernized.batch.domain.RatedCategoryBalance> bean, and Spring resolves
these by type -- so this job's wiring is ambiguous
```

`render_file_bindings` was right to refuse. It was refusing the shallower of two problems.

### What the rendered job actually looked like

`RatedCategoryBalance` is `TranCatBal + Account + CardXref + DisGroup` and declares no computed
field, so `_has_file_source` answers **True** — every component has a declared access path. A file
reader was therefore preferred for every step whose input type happened to be assemblable that way,
including steps in the middle of the chain. Rendering the *pinned* `step55` design showed it plainly:

| step | reader it was given | store it filled |
|---|---|---|
| `resolveAccountAndCardXref` | `ItemReader<TranCatBal>` | `ResolveAccountAndCardXrefStaging` |
| `resolveInterestRate` | **`ItemReader<TranCatBalWithAccount>`** | `ResolveInterestRateStaging` |
| `computeMonthlyInterest` | **`ItemReader<RatedCategoryBalance>`** | `ComputeMonthlyInterestStaging` |

`ResolveAccountAndCardXrefStaging` is written by the first step and **read by nobody**. The second
step re-derives its input from files instead, which means re-doing the work of the step before it —
and in `CBACT04C` that work is not incidental. `resolveInterestRate`'s own description:

> Looks up the disclosure group by account group ID plus transaction type and category codes,
> retrying under group ID `'DEFAULT'` when the primary read returns status `'23'`.

A file reader handed `RatedCategoryBalance` must reproduce that keyed read *and* its `'DEFAULT'`
fallback to build the composite at all. The step whose whole purpose is that lookup sits beside it,
its output discarded.

**So the ambiguity was a symptom.** Two steps sharing an input type is only a problem because both
were being given file readers; neither should have had one. It took a passthrough — two consecutive
steps with the same input type — to make a silent duplication surface as a loud refusal.

### Three modules, one question, all three wrong the same way

`_step_bean`, `render_file_bindings._reads_a_file` and `generate_pipeline` each decided this
independently, each spelled `aggregation_source(...) is None and _has_file_source(...)`, and each
agreed with the others. Agreement is what made it invisible. This is ADR-0071's defect exactly —
two halves of one decision, asked privately — with the twist that here they never diverged, so
nothing ever failed loudly.

**Nothing in 906 unit and contract tests caught the change when the precedence was flipped.** No
test asserted which reader a mid-chain step receives.

## Decision

**1. A step reads a file only when nothing upstream produced its input.** In order: an aggregating
step reads its rendered aggregation; a step whose immediately preceding chunk step outputs its input
type reads that step's store; only then is a file reader considered. The chain outranks the file
because the chain is what the design *states*, while `_has_file_source` reports what is merely
*possible*.

**2. One predicate, `reads_a_file`, and all three modules call it.** Not three consistent edits —
ADR-0071 is the bill for that shape, and this record is the second instance.

**3. `_produces_the_input_of` requires the type to match.** It returned the previous chunk step
unconditionally, which was safe only because its one caller had already established the match.
As the precedence check it must answer honestly on its own.

**4. `plan_steps` is unchanged.** `from_file or from_chain` asks whether an input is *obtainable*,
and both still count. Which of the two is used is a rendering decision, and it belongs where the
bean is rendered.

## Consequences

**`step56`'s design wires.** The two `RatedCategoryBalance` consumers take different stores —
`ResolveInterestRateStaging` and `ComputeCategoryFeesStaging` — and no bean type is claimed twice.

**Every design gets a real chain.** One file reader at the head of the job, one file writer at each
sink, and stores in between that are filled and read. The pinned `step55` design changes the same
way, which is why it is asserted here rather than only described.

**Duplicated lookups stop being rendered.** A step that re-derived its input from files now reads
what the step before it produced. This is a correctness change, not only a wiring one, though it
surfaced as neither: the round trip that builds and runs a generated job against the oracle passes
before and after, because its design has no step whose input was file-assemblable mid-chain.

**A known adjacent defect, recorded rather than fixed here.** In the pinned `step55` design,
`postAccountInterest` reads `ComputeCategoryFeesStaging` — the store of a step `plan_steps`
*skipped*, which nothing fills. `aggregation_source` walks back to the nearest type-compatible step
without asking whether that step is renderable. It does not affect `step56`, whose aggregation
resolves to a rendered step, and the pinned job cannot start regardless. It is its own decision and
gets its own record.

**Ninth defect found by pointing the pipeline at a design a model wrote** (ADR-0069), and the first
found by a *live run* rather than by pre-flighting one offline.

## Alternatives considered

**Refuse a design whose two steps share an input type.** Treats the symptom, and would refuse
`step56`'s design — which is correct under ADR-0072 and which a passthrough makes unavoidable. Any
no-op extension point produces two consecutive steps consuming one type.

**Tighten `v1_5_0` to say where a passthrough goes.** The position the ADR-0072 refusal names, on
`AccruedCategoryInterest`, happens not to collide, because that composite carries a computed field
and so is not file-assemblable. Steering the architect toward it would be steering it away from a
renderer defect using a rule about design — and the next composite without a computed field brings
the defect straight back.

**Make `_has_file_source` stricter.** It answers its own question correctly: those entities do have
access paths. The defect is in what was inferred from the answer, so the fix belongs at the call
site rather than in the fact.

**Fuse the passthrough into a neighbour.** Rejected for ADR-0032's reason, unchanged since
ADR-0073: it removes a step boundary a human approved at the gate.
