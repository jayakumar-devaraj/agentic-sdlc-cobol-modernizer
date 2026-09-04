# 15. The gate that catches half a class

Verified 2026-09-04. Covers ADR-0065: `generate` rendering the JUnit equivalence test, resolving its
result field from the design, running it, and reporting a verdict.

Read [14 — the value that had nowhere to go](14-the-value-that-had-nowhere-to-go.md) first. That
entry closed the design-language half of the same defect; this one closes the half that checks
whether the generated arithmetic is right.

**What this entry claims, in one line: a HALF_UP rounding defect and a discarded interest value now
come back from `generate` labelled, and a wrong per-account accumulator still does not.** The second
half is as much the finding as the first.

## Why a functional entry rather than a coverage number

Every component here already had unit tests, and the whole point is that they proved nothing.
`rendering/java_equivalence_test.py` was fully written, fully tested, and **called by nothing but one
integration test** — the exact shape of ADR-0063, which shipped inert while 1251 tests passed. A
green suite was compatible with the feature doing absolutely nothing, twice in a row.

So the verification here is: run the real entry point, with a real Maven build, against a scripted
defect, and read the verdict a release gate would read.

## The finding that changed the work, established by executing it

The plan was to wire the renderer unchanged. That was checked before it was built, against the real
step-51 design:

```
$ .venv/Scripts/python.exe scratch/probe.py
step: computeMonthlyInterest | RatedCategoryBalance -> AccruedCategoryInterest
REFUSED: 'AccruedCategoryInterest' has no 'tran' component;
         components are ['account', 'cardXref', 'categoryBalance', 'disclosureGroup']
```

Step 51's arithmetic was **correct**. Wiring the renderer as written would have shipped a gate that
blocks working code, which gets switched off within two runs. The oracle's `component: "tran"`
describes one decomposition, not a fact about COBOL — and in step 51's design `1300-B-WRITE-TX` is a
separate step, so the interest never reaches `TRAN-AMT` inside the step under test.

The design already declared the answer, so no guess was needed:

```
AccruedCategoryInterest -> computed_fields: [
  {"field_name": "monthlyInterest", "cobol_field_name": "WS-MONTHLY-INT"},
  {"field_name": "totalInterest",   "cobol_field_name": "WS-TOTAL-INT"}]
```

After the resolver, both live shapes resolve as they should:

```
step51 (real):  RENDERED -> BigDecimal actual = result.monthlyInterest();
step49 (shape): REFUSED  -> 'RatedCategoryBalance' carries the interest nowhere this renderer
                can assert on: it declares no computed field for 'WS-MONTHLY-INT'
                (computed fields are []) and has no 'tran' component
```

## The second finding: the heal loop never ran tests

The renderer's docstring states the test is *"compiled and run by the same Maven the heal loop
already drives."* **That sentence is false**, and it was repeated into the first draft of ADR-0065
before the loop was checked:

```
$ grep -rn 'goal=' src/cobol_modernizer/graph/generate_pipeline.py
325:        result = compile_project(project_dir, goal="compile")
```

`mvn compile` does not compile test sources. `verify` appears **only in tests**, never in the
production path. Rendering the file and stopping there would have been the third inert mechanism in
a row — caught by reading the caller rather than by trusting a docstring, which is the habit entry
14 records under a different name.

An unfiltered `verify` was not the fix either: the baseline template ships `BaselineStackTest`, which
is `@SpringBootTest @Testcontainers`, so it would have made the gate's correctness signal into a
Docker-availability signal. The run is narrowed to the rendered class.

## The command, and what it proves

```
JAVA_HOME=/c/Program\ Files/Eclipse\ Adoptium/jdk-25.0.4.7-hotspot \
  .venv/Scripts/python.exe -m pytest tests/integration/test_generate_renders_equivalence_test.py -q
```

```
.....                                                                    [100%]
5 passed in 150.81s (0:02:30)
```

Every one of the five goes through `run_generate` — the real entry point — with a real Maven build.
None calls the renderer directly, deliberately: that coverage already existed and is what let an
unwired renderer look finished.

| Test | Scripted body | Verdict | What it proves |
|---|---|---|---|
| `renders_the_test_beside_the_processor` | correct | `passed` | the file lands in `src/test/java/...` and names the generated processor, so it is an equivalence test and not a test of `CobolArithmetic` |
| `a_correct_body_passes_and_the_verdict_states_its_limit` | correct | `passed` | a green verdict that names what it does *not* cover |
| **`a_wrong_rounding_mode_fails_the_rendered_test`** | `divideRounded` | **`failed`** | **the load-bearing one.** A gate that only ever reports `passed` is indistinguishable from one reporting nothing |
| `a_zero_rate_transaction_fails_the_rendered_test` | emits a zero-amount `Tran` | `failed` | every arithmetic row still passes; only the `assertNull` case catches it |
| `a_design_carrying_the_interest_nowhere_is_refused` | step 49's body verbatim | `refused` | the processor **compiles cleanly** and the money is gone; caught at render time, before any Java exists |

The last row asserts `outcome.outcomes[0].succeeded` before asserting the verdict, because the point
is that the compiler had no objection. That assertion was added after the first version of the test
failed for the wrong reason.

## The probe that corrected the test rather than confirming it

The refusal test originally removed `Tran` from the *output composite*. It failed:

```
AssertionError: assert 'not_rendered' == 'refused'
```

Removing the component made the model-authored **body** fail to compile, so the step never reached
the renderer at all. That is a different finding from the one being claimed, and reporting it as a
refusal would have been wrong. Two changes came out of it:

1. The test was rebuilt as step 49's actual shape — output type equal to input type, body returning
   the item unchanged — which compiles, and refuses.
2. `run_generate` now distinguishes *"no step declares the paragraph the oracle covers"* from
   *"the step that does is `blocked`/`exhausted`, so nothing could be run against it."* Both were
   `not_rendered` with one reason before, and they are different facts for a reviewer.

## What is verified, and what is not

**Verified.** For the one `COMPUTE` this oracle covers: truncation versus rounding on the rows chosen
to separate them, the zero-rate control-flow case, and that the computed value reaches a field the
design declares. Plus the refusal when it does not.

**Not verified, stated rather than left to be discovered.** `WS-TOTAL-INT` — step 51's actual defect.
It is an accumulator across the rows of an account, the oracle has no expected values for it, and a
per-row `@CsvSource` cannot express one, since the expected total depends on which rows preceded
this one. Catching it needs a sequence-of-rows test with a control-break fixture. ADR-0065's
Consequences table carries the same statement, and the verdict `reason` string names the accumulator
in every passing run so a reviewer reading only the gate sees the limit too.

**Also not verified.** The end-to-end differential (`compare_project_output`) still reports `not_run`.
`generate` renders no readers, writers or job configuration (ADR-0019), so no project it produces
runs. This entry closes the unit-granularity half of ADR-0064's gap and leaves the other half open.
