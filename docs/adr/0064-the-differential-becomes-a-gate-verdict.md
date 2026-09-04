# ADR-0064: The differential becomes a gate verdict, not only a test assertion

## Status

**Accepted** (2026-09-04). Implemented in PR #122.

Written **after** the code that cites it, which is a defect in itself and is recorded here rather
than quietly corrected: fourteen citations across ten committed files named `ADR-0064` while no such
file existed. `test_adr_numbers_run_from_one_without_gaps` passed throughout, because there was no
*gap* — nothing checked that a cited ADR *resolves*. That is precisely the defect class the
structure test was built for (a committed file naming something nothing verifies), one level up from
paths, and the same PR that adds this record adds the assertion.

Builds on [ADR-0021](0021-a-hand-computed-oracle-for-the-interest-equivalence-test.md) (the oracle),
[ADR-0028](0028-what-the-round-trip-metric-requires-and-why-it-has-not-moved.md) and
[ADR-0029](0029-the-differential-compares-fields-and-an-excluded-field-is-reported.md) (the comparison), and
is bounded by [ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md)'s
processor-only `generate` scope — see Consequences, where that bound is the whole limitation.

## Context

Two live runs shipped wrong money past the release gate on 2026-09-04:

| Run | What reached `card-service` |
|---|---|
| step 49 | a processor computed the monthly interest and **discarded** it |
| step 51 | it returned the value and set a per-account running total to **one row's amount** |

In both, the gate rendered:

> `Generated and compiled 4 processor step(s).`

That sentence is true, contains no claim about correctness, and reads as success. **A gate that
reports quantities trains the approver to approve.** The only thing that caught either defect was a
person reading the generated Java, and the second was caught only because the first had taught
someone to look.

### The check that would have caught it already existed and could not be called

`compare`, both record layouts, `EXCLUSIONS` and the oracle loaders sat in
`tests/unit/test_cobol_oracle_comparison.py`. The account half compares `ACCT-CURR-BAL` for every
account with **no field excluded**, and `test_the_account_comparison_would_catch_a_wrong_balance`
demonstrates it detecting a **one-cent** divergence. Step 51's defect was far larger than a cent on
every multi-category account.

It ran only in the test suite, against Java this repository writes by hand. `generate` could not
reach it, so the differential could prove the *hand-written* implementation right and say nothing
whatsoever about what the pipeline actually produced.

### And the contract had nowhere to put an answer

```
GenerateCliResult: status, phase, run_id, output_path, detail,
                   steps_total, steps_compiled, steps_blocked,
                   steps_exhausted, steps_not_generated
```

Five counts and a string. **There was no field capable of carrying evidence**, which is why the gate
could only report arithmetic about steps.

## Decision

### 1. The harness moves into the package

`cobol_modernizer.equivalence` holds `compare`, `TRAN_LAYOUT`, `ACCOUNT_LAYOUT`, `EXCLUSIONS` and the
loaders. Bodies are byte-verbatim — 160 of 161 code lines identical, the single removal being
`import pytest`, which a library has no business carrying. **The tests are unchanged and still pass
against the same fixtures**, which is the proof the move carried no behaviour rather than a claim
that it did not.

**The loaders take the oracle directory as a parameter.** They resolved a module-level `ORACLE_DIR`
under the test tree; keeping that would have forced the packaging question into the extraction.
Nothing in the package knows where the oracle lives.

### 2. `GenerateCliResult` carries an `EquivalenceVerdict`, defaulting to `not_run`

**`not_run` is a verdict, not an absence, and that is the decision.** The field is not optional: a
result cannot be silent about correctness, because *silence is the failure mode*. A run that could
not execute the comparison says so **and says why**, which is a materially different thing for a
reviewer to weigh than a summary that omits the subject.

A match reports `excluded_fields`. ADR-0029's exclusions are decisions, and a differential that
hides what it did not compare overstates itself.

The verdict travels in `detail` as well as its own field, because `detail` is the sentence the
release gate actually renders.

### 3. The oracle ships in the wheel

Moved, not copied — two oracles that can drift is worse than either, and this is a byte-exact record
of a real COBOL run, the one artifact here where a silent divergence would be least visible and most
expensive. `ORACLE_ROOT` joins `PROMPTS_ROOT` and `CONFIG_ROOT`, so it resolves identically in a
checkout and an installed wheel — ADR-0055's lesson, where six modules used `parents[3]` and CI
never noticed because it installs editable.

Verified by inspecting the built artifact: six files at their exact byte sizes.

### 4. `compare_project_output` returns a verdict, and never guesses

A missing output file is **`not_run`, never `mismatched`**. One says the generated code is wrong and
the other says nothing ran; reporting the second as the first would put a false accusation in the
audit trail.

## Consequences

**The gate does not catch a defect yet, and this record says so rather than implying otherwise.**
`generate` renders processors (ADR-0019), not readers, writers or job configuration, so no runnable
project exists to compare. For `CBACT04C`'s real design `plan_steps` reports 6 of 9 steps
renderable, and the gate line reads:

```
Generated and compiled 4 processor step(s). Equivalence: NOT RUN -- the job is not fully
renderable, so no runnable project exists to compare: readTransactionCategoryBalance (...),
computeFees (...), closeInterestFiles (...)
```

What changed is that the gate stops implying correctness it never checked, and **names the three
steps standing between it and a real verdict**. It becomes a real verdict with no change to this
code the moment a run produces output.

**Closing it is a scope change to ADR-0019, not a patch.** The renderers already exist in the
package — `render_item_writer`, `render_staging`, `aggregation_source`, the reader renderer — and
the integration test calls them through thin wrappers today. Deciding that `generate` may render
wiring deserves its own record.

**Tenant-shaped data now ships in a domain-general wheel**, and this is the first such thing here.
The alternative — read the oracle from the tenant repository — keeps the wheel clean and makes the
gate depend on a tenant laying its oracle out the way this expects. Shipping it keeps the guarantee
inside the artifact that makes the claim, and the loaders still take a directory, so a caller with a
better source is not blocked by this default.

**This does not close the defect class it was motivated by.** `CLAUDE.md` asks that the *n*-th
defect of a kind get a mechanism making the *n+1*-th impossible or loud. ADR-0062 and ADR-0063 were
instance fixes six and seven; this is the mechanism, and it is **half-built** until the wiring
renders. Recording it as complete would be the same overclaim as a gate reporting a count.

## Alternatives considered

**Leave the comparison in the tests and run it in CI.** It already is, and both defects shipped
anyway — CI compares hand-written Java, and the pipeline's output never reaches it. This is the
whole gap.

**Make the verdict optional (`EquivalenceVerdict | None`).** Rejected: an absent verdict reads
exactly like a gate that has nothing to say, which is the failure being removed. A required field
defaulting to `not_run` makes silence unrepresentable.

**Have `generate` fail when the comparison mismatches.** Rejected for the specialist contract's rule
5: this repository emits facts for a gate and never decides. A mismatch is for a human to weigh, and
`build_validator` already owns the distinction between "no rewrite reaches this" and "try again".

**Read the oracle from the tenant repository.** Kept as the live alternative, recorded above rather
than dismissed — it is more faithful to the tenant/specialist split and costs plumbing. The
parameterised loader means adopting it later is a caller change, not a rewrite.
