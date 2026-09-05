# ADR-0065: `generate` renders the equivalence test, and resolves its result field from the design

## Status

**Accepted** (2026-09-04).

Written **before** the code it governs, deliberately. ADR-0062 and ADR-0063 were both written after
their implementations and both shipped wrong — ADR-0063 shipped inert, and a live run was what
exposed it. ADR-0064 records the same defect about itself. This record states what the gate will and
will not catch *first*, so that a green run cannot later be read as covering more than it does.

Completes the half of [ADR-0064](0064-the-differential-becomes-a-gate-verdict.md)
that its own Consequences section calls half-built. Applies
[ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md)'s declare-rather-infer rule to a fact
that turned out to live in the design rather than in the oracle. Bounded by, and does **not** amend,
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md)'s
processor-only `generate` scope — see Consequences.

## Context

Four defects have reached `card-service` unchallenged. The test suite caught none of them; three
were found by a person reading generated Java and one by a live run failing.

`rendering/java_equivalence_test.py` was built for exactly this and **is wired into nothing**. Its
own docstring states the constraint that justifies it — *the test must run against generated code,
or it is not an equivalence test* — and `generate` has never called it. One integration test does.

Wiring it looked like an afternoon's work. It is not, and the reason is worth recording, because it
is the same class of mistake the renderer itself was built to prevent.

### The oracle's binding describes COBOL, and was being used to describe a design

`interest-oracle.json` declares where the interest lands:

```json
"result_field": {"entity": "Tran", "field": "tranAmt", "cobol": "TRAN-AMT", "component": "tran"}
```

That is a true and durable fact about `CBACT04C`: `1300-B-WRITE-TX` does
`MOVE WS-MONTHLY-INT TO TRAN-AMT`. But `component: "tran"` is not a fact about COBOL at all. It is a
fact about **one decomposition** — the fixture's, where `1300-COMPUTE-INTEREST` and
`1300-B-WRITE-TX` are one step and the output composite carries a `Tran`.

A model decomposes the program afresh on every run, and neither live decomposition looks like that:

| Design | The `1300-COMPUTE-INTEREST` step | Where the interest actually lands |
|---|---|---|
| step 49 | `RatedCategoryBalance → RatedCategoryBalance` | **nowhere** — the output composite carries no field for it |
| step 51 | `RatedCategoryBalance → AccruedCategoryInterest` | `monthlyInterest`, a computed field |

In step 51's design `1300-B-WRITE-TX` is a **separate step** (`AccruedCategoryInterest → Tran`), so
the value never reaches `TRAN-AMT` inside the step under test. Rendering against that design was run
before this record was written, and refuses:

```
REFUSED: 'AccruedCategoryInterest' has no 'tran' component;
components are ['account', 'cardXref', 'categoryBalance', 'disclosureGroup']
```

**Wiring the renderer unchanged would therefore ship a gate that blocks step 51 — whose arithmetic
was correct.** A gate that refuses working code is not a strict gate; it is a broken one, and it
would be abandoned within two runs.

### The design already declares the answer

The fact the renderer needs is not missing. It is on the composite, declared rather than inferred,
put there by ADR-0062:

```
AccruedCategoryInterest -> computed_fields: [
  {"field_name": "monthlyInterest", "cobol_field_name": "WS-MONTHLY-INT"},
  {"field_name": "totalInterest",   "cobol_field_name": "WS-TOTAL-INT"}]
```

So both halves of the binding are declared by whoever is entitled to declare them: the **oracle**
says which COBOL field carries the result (`WS-MONTHLY-INT`, landing in `TRAN-AMT`), and the
**design** says which Java accessor carries that COBOL field in this particular decomposition.
Neither side guesses, and nothing matches on field names that look plausible.

## Decision

### 1. `generate` renders the equivalence test beside the processor it tests

For the step whose `source_paragraphs` contains the oracle's `source.paragraph`, and only that step.
Selection is by declared paragraph, never by step name: `computeMonthlyInterest` is a name a model
chose and is not a fact about anything.

The rendered file goes to `src/test/java/<package>/<Processor>EquivalenceTest.java`, beside the
processor it tests. That is what makes it an equivalence test rather than a test of
`CobolArithmetic`.

**And `generate` runs it, because otherwise it renders inert.** The renderer's own docstring says
the test is *"compiled and run by the same Maven the heal loop already drives"*. That sentence is
false, and it was repeated into the first draft of this record before the loop was checked: the heal
loop's goal is `compile`, which never compiles test sources. Rendering the file and stopping there
would have shipped a feature that does nothing while the suite stays green — ADR-0063's failure,
repeated in the record written to avoid repeating it.

So `run_generate` runs one narrowed build after the heal loop:

```
mvn -B -ntp -Dtest=<Processor>EquivalenceTest -Dsurefire.failIfNoSpecifiedTests=false test
```

**The narrowing is required, not an optimisation.** The baseline template ships `BaselineStackTest`,
which is `@SpringBootTest @Testcontainers`. An unfiltered `test` or `verify` inside `generate` would
demand a Docker daemon on the specialist host and fail for reasons that say nothing about the
interest arithmetic — and a gate whose correctness signal is really a Docker availability signal is
worse than no signal.

### 2. The result accessor is resolved from the design, in two declared cases

The oracle gains one field naming the **COBOL** working-storage field the result lives in
(`WS-MONTHLY-INT`); `component` stays, as the fixture case it always described. Resolution is then:

| Case | Condition | Accessor |
|---|---|---|
| **carried** | the step's output composite declares a `computed_fields` entry for that COBOL field | `monthlyInterest()` |
| **written** | the output composite has the oracle's declared `component` | `tran().tranAmt()` |

Carried is tried first: when a design splits the write off, the computed field is the nearer and
more direct observation of the value under test. Written is the fixture's case and stays supported
because a design may legitimately keep the two paragraphs together.

### 3. Neither case matching is a refusal, and the refusal is the finding

If the output composite carries the value in no declared form, the step computing `WS-MONTHLY-INT`
has nowhere to put it, and **that is step 49's defect stated exactly**. The renderer already raises
`UnrenderableOracleError`; this decision is that `generate` reports it rather than swallowing it —
consistent with the specialist contract's rule 5, as a fact for the gate, not a decision.

This is a design-time catch. It fires before any Java is written, which is earlier and cheaper than
the failing assertion it replaces.

### 4. The verdict is its own field, never folded into `equivalence`

`GenerateCliResult` gains `equivalence_test: EquivalenceTestVerdict`, defaulting to `not_rendered`
for the reason ADR-0064 gave for `not_run`: a run that checked nothing must say so.

Kept separate from `equivalence` because they are different claims at different granularities. That
one compares a built-and-run job's **output records** against COBOL's; this one checks **one
`COMPUTE`, per row**. Folding them would let a green unit test read as a passing differential —
orders of magnitude more than it has evidence for, which is ADR-0064's overclaim one level down.

### 5. The oracle ships where the test that needs it can find it

`interest-oracle.json` moves into the package beside the `oracle/` directory ADR-0064 already moved.
The same decision, already settled once, applied to the file left behind.

## Consequences

**This catches the discard class and not the accumulator class, and the honest table is:**

| Defect | Caught | How |
|---|---|---|
| step 49 — interest computed and discarded | **yes** | refusal: the output composite carries `WS-MONTHLY-INT` nowhere |
| step 51 — `totalInterest` set to one row's amount | **no** | `monthlyInterest` was correct; the wrong value is a different field |
| wrong rounding mode | **yes** | the oracle's rows are chosen to separate truncation from `HALF_UP` and `FLOOR` |
| a zero-rate row emitting a transaction | **yes** | the `assertNull` case |

**Why step 51 stays uncaught, stated rather than left to be discovered.** `WS-TOTAL-INT` is an
accumulator across the rows of an account. The oracle has no expected values for it, and a per-row
`@CsvSource` cannot express one: the expected total depends on which rows preceded this one. Catching
it needs a sequence-of-rows test with a control-break fixture, which is a different test with a
different shape. **Half a known class, honestly labelled, is what this ships.** A green run means the
per-row interest arithmetic matches and the value reaches the output — and no more than that.

**ADR-0019's scope is untouched.** A rendered *test* is not job wiring. `generate` still renders no
readers, writers or job configuration, so it still cannot produce a project that runs, and
`compare_project_output` still reports `not_run`. This closes the unit-granularity half of ADR-0064's
gap and leaves the end-to-end half open — that one needs the wiring decision, and gets its own
record.

**The gate can now say something false-negative rather than only something empty.** That is a real
cost: a reviewer reading "equivalence test passed" may take more from it than the table above
allows. The rendered file's Javadoc carries the same limit as this record's first row, which is the
only place a reviewer is certain to look.

## Alternatives considered

**Wire the renderer unchanged.** The literal cheapest path, and rejected on evidence: it was executed
against the real step-51 design and refuses code that is correct. Two runs of that and the gate gets
switched off.

**Put the accessor in the oracle.** Rejected because it is not knowable there. The oracle is written
by hand from COBOL, once; the accessor differs per run because the decomposition differs per run. An
oracle field that must be edited after seeing the design is not a hand-derived fact — it is a
transcription of the answer, which is what ADR-0021 refused.

**Match the computed field by name.** Rejected — `monthlyInterest` is a name a model chose. Matching
on it is exactly the "`tranAmt` is probably the amount" guess this repository removes everywhere
else. `cobol_field_name` is the declared key and is the one used.

**Widen the oracle to cover `WS-TOTAL-INT` and catch step 51 too.** Not rejected — deferred, and
recorded here so it is not mistaken for an oversight. It needs a sequence-of-rows test and a
control-break fixture, and bundling it here would delay a gate that already catches a defect that
shipped.

**Render the test for every step rather than the bound one.** Rejected: the oracle covers one
`COMPUTE`. A test rendered against a step it has no expected values for could only assert something
vacuous, and a vacuous green is the failure ADR-0064 exists to remove.
