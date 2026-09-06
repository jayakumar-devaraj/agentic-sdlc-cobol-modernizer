# 17. The first design a model wrote

Verified 2026-09-06. Covers [ADR-0068](../../adr/0068-a-steps-structure-is-read-from-the-design-not-inferred-from-its-types.md)
(a step's structure is read from the design), [ADR-0069](../../adr/0069-a-pipeline-that-consumes-model-output-is-verified-against-model-output.md)
(a pipeline consuming model output is verified against model output) and
[ADR-0070](../../adr/0070-a-step-that-changes-its-items-type-is-a-processor.md) (a transform is a
processor).

Read [16 — a generated project that runs](16-a-generated-project-that-runs.md) first. Every number in
it is correct and every one of them was measured against **the design this repository wrote**.

**The claim, in one line: `generate` now wires a job from a design a live `solution_architect`
produced, and the five defects between those two sentences are named, fixed and held by a test.**

## What "verified against the fixture" was hiding

Verification 16's own bean signature says which design it measured:

```java
ItemReader<...TranCatBalWithRate> computeInterestItemReader(...)
```

`TranCatBalWithRate` is `tests/support/interest_design.py` — three steps, written here. No design a
model produced had ever reached `render_job_wiring`. The next one that did refused, and kept
refusing: **each fix uncovered the next defect**, because every one of them sat behind the one in
front.

| # | Where | What it did |
|---|---|---|
| 1 | `aggregation_blockers` | Asked for the record column the value *lands in*, resolved against entity fields only. A design obeying ADR-0063 carries the value itself, so `aggregation_source` returned `None` for a step that declares a control break and the step fell through to a file reader, which correctly refused an in-memory aggregate. |
| 2 | `plan_steps` | Never read `role`. A `tasklet` of five file OPENs and a `reader` of `1000-TCATBALF-GET-NEXT` were planned as chunk steps and each demanded its own `ItemReader<TranCatBal>` beside the step actually driving the file. |
| 3 | `_has_file_source` | Reported a composite carrying `computed_fields` as file-readable. `render_item_reader` then emitted three arguments for a four-component record — **uncompilable Java with no diagnostic**. |
| 4 | `render_item_reader` | Wrapped a plain entity in its own constructor: `new TranCatBal(toTranCatBal(record))`. Every step of the fixture design takes a composite, so nothing had ever passed it an entity. |
| 5 | the design | Types `writeInterestTransaction` and `postAccountInterest` as `writer`; `generate` renders a body for `processor` only, so the job wired itself to two classes nothing would produce. |

Four of the five are one mistake: **the wiring answered a structural question by inspecting type
shapes when the design already stated the answer.** The fifth is its mirror — the design stating
something the pipeline cannot honour, refused now where it is produced.

## The measurement

```
JAVA_HOME=... .venv/Scripts/python.exe -m pytest tests/integration/test_a_live_design_wires.py -q
```

```
5 passed in 66.66s (0:01:06)
```

| What was checked | Result |
|---|---|
| The wiring reaches the compiler for a live design | no missing driving stream, no ambiguous bean |
| Every item-carrying step is planned | 5 of 8 — `resolveAccountAndCardXref`, `resolveInterestRate`, `computeMonthlyInterest`, `writeInterestTransaction`, `postAccountInterest` |
| The file lifecycle is skipped **with a reason** | 3 of 8, each naming its role |
| The control break renders its aggregating reader | groups on `item.categoryBalance().trancatAcctId()`, sums `item.monthlyInterest()`, builds `AccountInterestPosting(first.account(), total)` |
| No file reader for an item carrying a computed value | `WriteInterestTransactionItemReader` absent; `AccruedCategoryInterestStaging` present |
| What still fails to compile | **exactly** the two `writer`-typed steps, and nothing else |
| Unit + contract tiers | 901 passed, 3 skipped; ruff and mypy clean |

The rendered aggregation is ADR-0063's shape generated for the first time — the accumulator filled by
summing rather than copied from a row, on a stream that carries no column named for the total.

## The last row is the honest one

`test_what_remains_is_exactly_the_two_steps_typed_writer` asserts the build **fails**, and that every
diagnostic in it names one of the two classes. That is deliberate, and it is the same discipline
`assert_account_half_matches_except_the_last` applies to the account half: a known divergence pinned
to its cause, so a *different* cause cannot pass. A fifth defect appearing in this project fails the
test rather than hiding behind a known one.

It also states what to change when ADR-0070's refusal has produced a design under prompt `v1_4_0`:
the assertion becomes `assert build.succeeded` and the module docstring loses its caveat.

## What is not covered

**The design-time refusal is not proven against a live model.** ADR-0070 is enforced and tested in
both directions — the `writer`-typed transform is refused, the same step typed `processor` is
accepted — but whether `v1_4_0` makes an architect type these steps correctly is the next live run's
question. The `parse_with_repair` attempt exists because the honest answer is *not necessarily*.
ADR-0063 was written `Proposed` for this reason; ADR-0070 is not, because it claims only that a
design breaking a stated rule is refused before a human sees it.

**The project does not run.** It does not compile, for the reason above, so there is no differential
here and none is claimed. Correctness stays where it is measured, in
`test_generate_renders_the_wiring.py` against the oracle.

**One design, one program, one day.** `CLAUDE.md`'s standing rule is that a capability is complete
when a *second* instance exercises it. This is one design, from one model, for `CBACT04C`. The
pinned fixture will go stale when the refusal changes what an architect produces, and it stays
anyway: it is the input that found four defects, and a regression suite's job is to keep failing on
inputs that once broke something.

## The method, since it is the transferable part

**The design was interrogated before anything was spent on it.** A twenty-second `plan_steps` probe
against the real design found defect 1; re-running it after each fix found 2, then 3. Defects 4 and 5
came from rendering the project and handing it to javac. None of this needed a model call, a run, or
an approval — and the alternative was learning the same five things one paid phase at a time, each
after a human had approved a design.
