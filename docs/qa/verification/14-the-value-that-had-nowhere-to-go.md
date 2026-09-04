# 14 — The value that had nowhere to go, and the six probes that hold the fix

Part of [`docs/qa/verification-report.md`](../verification-report.md). Scope: the design-language
gap the first end-to-end run exposed ([ADR-0062](../../adr/0062-a-step-must-be-able-to-return-what-it-computes.md)) —
what the deterministic layer already knew and discarded, the refusal that now catches it, and the
probes showing each narrowing fires on the case it names and stays silent on the ones it must not.

## Functional verification

### The defect, and why no test could have caught it

The generated `ComputeMonthlyInterestProcessor` computes the monthly interest into a local and
returns the item unchanged. It compiles, `build_validator` passes it, and its own javadoc says it
"accumulates it into the account's running month total" — an accurate reading of the COBOL and an
inaccurate description of the method. Nothing downstream can see the difference between a value used
and a value discarded, because both are legal Java.

The design typed the step `input_type = output_type = RatedCategoryBalance`, which is a record with
no field for the result. Every component was correct; the design language had no word for the value.

### What the deterministic layer already held

Run against the real fixture, not asserted from reading:

```
$ ./.venv/Scripts/python -c "<build_computed_values over CBACT04C and CBTRN02C>"
CBACT04C WS-MONTHLY-INT     escapes_to=['1300-B-WRITE-TX']      lands_in=TRAN-AMT
CBACT04C WS-TOTAL-INT       escapes_to=['1050-UPDATE-ACCOUNT']  lands_in=None
CBACT04C WS-TRANID-SUFFIX   escapes_to=[]                       lands_in=None
CBTRN02C WS-TEMP-BAL        escapes_to=[]                       lands_in=None
```

and, from the same `pic_mapper` call `build_domain_entities` discards one line later:

```
WS-MONTHLY-INT: java=BigDecimal precision=11 scale=2 signed=True
WS-TOTAL-INT:   java=BigDecimal precision=11 scale=2 signed=True
```

`precision=11, scale=2` is exactly what step 39's run 3 inferred off the `PIC` clause and flagged as
an inference it would rather not be making. It was computed here the whole time.

### Why the narrowing is syntactic

`CBACT04C` has 52 of its own fields, 17 of them numeric. Selecting on Java type would take all 17,
including `FD-ACCT-ID`, `IO-STATUS-0401`, `APPL-RESULT`, `ABCODE`, `TIMING` and `PARM-LENGTH` — file
aliases and status codes, none of them business quantities. Selecting on `scale > 0` would have
produced the right two here **by luck**, and would have dropped the first integer quantity a program
ever computes.

Selecting on *arithmetic receiving position* gives 3 in `CBACT04C` and 1 in `CBTRN02C`, each mapped
to the paragraph that computes it — which is what lets a value be charged to the step that declares
that paragraph rather than to the job.

### The probes

Same rule this repository applies to caveats and to structure assertions: **a guard nobody has seen
fail is a guard nobody has tested.** Each was applied, the suite run, and the guard restored.

| # | Probe | Assertion that caught it |
|---|---|---|
| 1 | `_NO_GIVING` exclusion removed | `test_giving_takes_the_target_away_from_a_declared_to_operand` |
| 2 | `MOVE` added to the receiving-position table | **nothing failed** — see below |
| 3 | Processor-only restriction removed | `test_only_processors_answer_for_computed_values` |
| 4 | Escape analysis removed | `test_a_value_that_never_leaves_its_paragraph_is_not_refused` |
| 5 | Landing-field delivery removed | `test_a_value_landing_in_a_record_the_output_carries_is_delivered` |
| 6 | `BigDecimal` import omitted | `test_a_bigdecimal_computed_field_brings_its_import` |

Probes 3, 4 and 5 each failed **exactly one** test, and it was the test that names the narrowing —
so each of the three is doing work no other part of the check does.

**Probe 1 corrected a test rather than confirming one.** The first version of the `GIVING` test used
the real `ADD 8 TO ZERO GIVING APPL-RESULT` from `CBACT01C` and passed with the exclusion removed:
`ZERO` is a figurative constant, so the vocabulary gate already excluded it and the exclusion was
unreachable. The exclusion is kept for `ADD A TO B GIVING C` where `B` is a declared field — a form
this corpus does not contain — and the test was rewritten as a constructed case, labelled as such,
which does fail without it.

**Probe 2 found a false claim in a docstring.** The module said a `MOVE` is excluded so the
accumulator reset would not be misattributed to the reader's paragraph. Adding a `MOVE` pattern at
runtime changed nothing. The real reason `MOVE 0 TO WS-TOTAL-INT` is invisible is that it sits in
`CBACT04C`'s main loop, before the first paragraph header, where `extract_paragraphs` attributes
nothing — and so does `ADD 1 TO WS-RECORD-COUNT`, which *is* arithmetic and *is* genuinely not
reported. Both the docstring and the test now say this, and the limit is recorded rather than
implied.

**Probe 6 was not a probe.** The first render of `AccruedCategoryInterest` was read rather than
tested, and it was missing `import java.math.BigDecimal;` — a record that reads correctly and does
not compile. Composites had never needed an import because every entity component lives in the same
package. Caught the same way the defect this entry is about was caught: by reading generated output.

### The refusal, end to end

`design_solution` with a stubbed architect returning the step-49 design:

```
SolutionArchitectParseError: solution_architect step 'computeMonthlyInterest' computes
WS-MONTHLY-INT, WS-TOTAL-INT, which its output type 'RatedCategoryBalance' cannot carry. ...
Declare an output composite with a computed_fields entry naming each of WS-MONTHLY-INT,
WS-TOTAL-INT ... or make the output type carry the record the value is moved into.
```

Two model calls were made, not one: the failure reaches `parse_with_repair`, which re-asks with the
message appended, so the instruction lands where a model can act on it. A test asserts on the repair
prompt rather than only on the exception — the difference between a check that refuses and one that
teaches.

Both values are named. `WS-TOTAL-INT` is the accumulation the generated javadoc claimed and did not
perform, and a refusal naming only `WS-MONTHLY-INT` would invite a design that fixes half of it.

### What the fix changes at generate time

Once the output type differs from the input type, the rendered signature becomes
`ItemProcessor<RatedCategoryBalance, AccruedCategoryInterest>` and `return item;` **does not
compile**. The model has to construct the output record, whose constructor takes the computed value.
The design-time refusal makes the defect loud; the type change makes the same mistake impossible one
phase later.

The generator is also shown the accessor, which it was not before — gap G24's lesson applied to the
new field:

```
#### AccruedCategoryInterest
- TranCatBal categoryBalance()  // e.g. item.categoryBalance().someField()
- BigDecimal monthlyInterest()  // WS-MONTHLY-INT: precision 11, scale 2, signed -- this step computes it
```

### Commands and results

```
$ ./.venv/Scripts/python -m pytest tests/unit tests/contract -q
859 passed, 3 skipped
$ ./.venv/Scripts/python -m ruff check src/ tests/
All checks passed!
$ ./.venv/Scripts/python -m mypy
Success: no issues found in 54 source files
```

## What this does not establish

**No model has been asked to produce a design under `v1_2_0`.** Every run above uses a stubbed
architect. What is verified is that a correct design is accepted, the step-49 design is refused, and
the repair prompt carries the fix — not that a real `solution_architect` call now produces the
better design. That needs a live run and is not claimed here.

**No generated Java has been compiled from a design carrying a computed field.** The record renders
with its import and reads correctly; `render_composite`'s output has not been through `javac` in
this change.

**Two programs, not four.** Per `CLAUDE.md`'s rule on declaring a capability complete, this is closed
for `CBACT04C` and `CBTRN02C` — the two with real business logic. The other two exercise the
tasklet narrowing and nothing more.
