# ADR-0023: A step this pipeline does not render is reported, not dropped

## Status

Accepted (2026-08-10). Closes the reporting half of audit gap **G27**; the generation half is
explicitly left open and scoped below.

Corrects an assumption made in step 42's loop and inherited from
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md)'s
generation scoping. Additive to `GenerateCliResult` — no schema break.

## Context

`generate` renders `ItemProcessor`s and nothing else, and skipped every other step with a comment
that said, in effect, *nothing is wrong here*:

```python
if step.role != "processor":
    # Readers, writers and tasklets are Spring Batch wiring rather than translated
    # business logic ...
    continue
```

**The premise is false.** `CBACT04C`'s `1050-UPDATE-ACCOUNT` does
`ADD WS-TOTAL-INT TO ACCT-CURR-BAL` — a control-break accumulation that mutates a customer's
balance. It cannot be an `ItemProcessor`, because a stateless per-item processor holds nothing
across items, so any design that gives it an owning step must give it a non-processor role. Under
the old code that step then reached **no outcome, no count and no gate**.

The measured consequence: a design of one processor plus one writer reported

```json
{"status": "ok", "steps_total": 1, "steps_compiled": 1}
```

— byte-identical to a design that contained nothing else. A human approving that at control-plane's
gate saw a complete success over a job whose account update had never been generated.

**This was found by a model, not by a test.** Asked to translate the interest calculation, it noted
that the accumulator is cross-item state which does not belong in a stateless processor, and stated
the cost precisely: whichever step owns `1050-UPDATE-ACCOUNT` must accumulate the emitted amounts,
*"or the account balance update will be wrong (silently, and by the full interest amount)."*

Silence is what makes it severe. Every generated line is correct; only the total is wrong. No
compile fails, no equivalence row goes red, and the interest arithmetic — the part anyone would
think to check — is exactly right.

## Decision

**A non-processor step produces a `StepOutcome` with status `not_generated`**, carrying its role and
its `source_paragraphs` in the reason, and `GenerateCliResult` gains `steps_not_generated`.

Three sub-decisions worth stating, because each could reasonably have gone the other way:

1. **Reported, not failed.** Most non-processor steps really are Spring Batch wiring, and failing on
   them would make every realistic design report an error — training a reviewer to ignore the
   signal, which is worse than not having it.
2. **`succeeded` is measured over *generable* steps**, not all outcomes, so adding this category
   does not silently flip existing runs to failure. What "succeeded" means is narrowed honestly:
   every processor compiled — and here is what was never generated.
3. **The reason names the paragraphs.** A role alone cannot distinguish wiring from lost business
   logic; `1050-UPDATE-ACCOUNT` in the reason is what lets a reviewer make that call in seconds.
   This is the same instinct as `spec_extractor`'s provenance: a fact is only actionable if you can
   see where it came from.

## What this does not do, stated plainly

**It does not generate the accumulator, and G27 stays open for that.** Rendering a stateful
control-break writer is real work — Spring Batch's chunk boundaries do not align with COBOL's
account breaks, so it is a design question before it is a code question — and ADR-0019 already
scopes this pipeline to processors. Pretending otherwise would replace a silent gap with a
mislabelled one.

What changes is that the gap is now **visible at the gate** instead of being invisible everywhere.
That is the difference between a known limitation and a defect.

## Consequences

**Good.** The largest known correctness gap between a passing step 45 and a migrated `CBACT04C` is
now reported by the tool that creates it. The old expectation was itself encoded in a test asserting
`len(outcome.outcomes) == 1` — the defect written down as a requirement — and that test now asserts
the reader is *both* present and non-fatal.

**Cost.** One more field for control-plane's gate to render, and a category a reviewer must learn to
read. Cheap against a balance that is wrong by the full interest amount with nothing anywhere
saying so.

**The pattern, again.** ADR-0022 recorded that this platform keeps fixing instances of *the
generator was never shown something this repo knows*. This is the sibling class: **the pipeline knew
something and never said it.** Both are silent, and both were found by a model reporting what it
could not justify rather than by any check in the suite.
