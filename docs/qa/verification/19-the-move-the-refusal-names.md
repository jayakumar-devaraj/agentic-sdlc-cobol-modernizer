# 19. The move the refusal names

Verified 2026-09-06. Covers [ADR-0072](../../adr/0072-a-step-is-ordered-where-its-input-exists.md)
and [ADR-0073](../../adr/0073-a-staging-store-belongs-to-the-step-that-fills-it.md).

Read [18 — the accumulator is right on a live run](18-the-accumulator-is-right-on-a-live-run.md)
first. That entry ends with a job that cannot start, and calls it a design-ordering finding.

**The claim, in one line: the design `step55` produced is refused at design time with the move that
fixes it named, and that move renders a project which compiles and gives every step it names a
bean — verified by building both designs, not by planning them.**

No model call anywhere in this entry. Every number below came from the pinned design and `javac`.

## What the design actually said

ADR-0071 and this module's own docstring both said *nothing* supplies the `AccruedCategoryInterest`
that `computeCategoryFees` consumes. Reading the design says otherwise:

| # | step | in | out |
|---|---|---|---|
| 3 | `computeMonthlyInterest` | `RatedCategoryBalance` | `AccruedCategoryInterest` |
| 4 | `writeInterestTransaction` | `AccruedCategoryInterest` | `Tran` |
| 5 | `computeCategoryFees` | `AccruedCategoryInterest` | `AccruedCategoryInterest` |

Step 3 supplies it and step 4 consumes it. A fan-out, not a missing producer — which is what makes
it fixable by reordering and nothing else.

The model was not confused about the paragraph. Its own `description`:

> Explicit no-op extension point preserved from the empty COBOL fee stub, invoked once per
> non-zero-rate category balance and passing the item through unchanged.

And the COBOL explains the placement. `1300-B-WRITE-TX` is a **nested** PERFORM inside
`1300-COMPUTE-INTEREST` (line 468); at the top level (lines 214–217) fees follows interest. Flatten
the nesting into three sibling steps, keep paragraph order, and the type change lands in the middle.

## The pre-flight, before anything was written

`plan_steps` against the pinned design and against a single-move variant, no model, about a second:

| | renderable | staged | named with no bean |
|---|---|---|---|
| as the model wrote it | 5 of 6 | 3 | `computeCategoryFees` |
| `computeCategoryFees` moved ahead of `writeInterestTransaction` | **6 of 6** | 3 | **none** |

That is what settled ADR-0072 in favour of an ordering rule rather than "the step has no place in a
chunk pipeline".

## The refusal fires, and stays silent on the design it asks for

```
$ .venv/Scripts/python.exe -m pytest tests/unit/test_solution_architect.py -q
43 passed in 14.26s
```

Against the pinned design, in the pre-attachment state the validator actually sees:

```
solution_architect job 'interestCalculationJob' orders step 'computeCategoryFees' where nothing
supplies its input 'AccruedCategoryInterest': ... Move 'computeCategoryFees' so it runs before
'writeInterestTransaction', which is where its input still exists. ...
```

Reordered: silent. Both asserted, because a refusal only checked against a bad design would keep
passing if it had become "refuse every design".

## The check that could not have fired

`attach_control_breaks` runs *after* `parse_with_repair`, so at validation time no step carries a
control break and `aggregation_source` answers `None` for every step. Measured against the
unattached design:

```
WITHOUT re-attaching control breaks, plan_steps skips:
   - computeCategoryFees
   - postAccountInterest
single move that fixes the UNATTACHED design: None
=> the refusal would have been SILENT
```

Two stranded steps, no single move fixing the pair, so the refusal returns nothing — **on every live
design, forever**. Not a false positive; a check that cannot fail. It is guarded by attaching the
breaks before planning, and `test_attaching_control_breaks_is_what_lets_the_refusal_fire` asserts the
same design one function apart.

This is the third instance in the register: `contracts.py`'s `accumulator_owners` records one, and
entry 18 records the wait condition that matched a string already in the log.

## The instruction, rendered — where the next defect was

`plan_steps` says the move wires the job. `javac` disagreed:

```
InterestCalculationJobConfiguration.java:147:44: variable accruedCategoryInterestStaging
is already defined in method computeCategoryFeesStep(
    JobRepository, PlatformTransactionManager,
    AccruedCategoryInterestStaging, AccruedCategoryInterestStaging)
```

**The unit test asserting the named move works passed while this was true**, because it asked
`plan_steps` — the same oracle that emitted the instruction. Asking the refusal to mark its own paper
is what it amounts to, and it is recorded in that test's docstring rather than quietly fixed.

Deduplicating the parameter would have compiled and produced a job that never terminates: the
staging class is one `ArrayList` with a read cursor, so a step reading and writing the same instance
appends one item for every item it drains. ADR-0073 re-keys the store by its producing step instead.

## After ADR-0073

```
$ .venv/Scripts/python.exe -m pytest tests/integration/test_a_live_design_wires.py -q
11 passed in 198.93s (0:03:18)
```

Both designs built. The moved one:

```
WIRING: rendered | 13 wiring file(s) rendered and compiled with every renderable step wired
skipped_steps: []
COMPILES: True

STEP_NAMES:   ['resolveAccountAndCardXref', 'resolveInterestRate', 'computeMonthlyInterest',
               'computeCategoryFees', 'writeInterestTransaction', 'postAccountInterest']
bean methods: ['computeCategoryFees', 'computeMonthlyInterest', 'postAccountInterest',
               'resolveAccountAndCardXref', 'resolveInterestRate', 'writeInterestTransaction']

NAMED WITH NO BEAN: []
```

Six named, six beans. The two stores that had collided are now
`ComputeMonthlyInterestStaging.java` and `ComputeCategoryFeesStaging.java`, and
`AccruedCategoryInterestStaging.java` is asserted absent.

## The whole suite

```
$ .venv/Scripts/python.exe -m pytest tests/unit tests/contract -q
907 passed, 3 skipped in 80.11s

$ .venv/Scripts/python.exe -m ruff check src tests
All checks passed!

$ .venv/Scripts/python.exe -m mypy
Success: no issues found in 57 source files
```

## What this entry does not claim

**No live run was spent.** Whether an architect obeys `v1_5_0`'s ordering rule unprompted is
unmeasured — this entry verifies the refusal and the repair instruction, not the model's compliance.
That is the next run's question.

**Nothing about equivalence.** The processor bodies in both builds are scripted `return null;`, so
nothing here claims the generated logic is right. `mismatched` remains the expected verdict for
`CBACT04C` for the reason the routing table gives at length.

**One pre-existing log line was investigated and left alone.** `local_compiler: build failed with no
located diagnostic` appears once in each `run_generate`, on the pinned design as well as the moved
one, and both projects compile. It predates this change and is noted here rather than chased.
