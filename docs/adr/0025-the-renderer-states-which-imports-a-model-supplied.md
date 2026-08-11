# ADR-0025: The renderer states which imports a model supplied, rather than leaving ownership to geometry

## Status

Accepted (2026-08-11). Closes gap **G30**, found by step 43's injected-error harness on its first run
(PR #47) and pinned there as a failing expectation waiting for this decision.

Refines [ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md)-era
reasoning about which lines a model owns, and leaves the rendered/model-authored split itself exactly
where [ADR-0010](0010-unified-design-shape-and-the-deterministic-llm-split.md) put it.

## Context

`render_processor` brackets the model-authored body in `BEGIN`/`END` markers, and `build_validator`
decides whether a compile error is the model's to repair by asking whether the diagnostic's line
falls between them. Inside, a model wrote it and a rewrite might fix it. Outside, this repo wrote it,
and asking a model to repair it invites the thing the split exists to prevent — a model rewriting
deterministic code to make a symptom disappear.

That rule is right. The implementation had a hole.

**A model also supplies its own imports.** It has to: the renderer never reads the body, so it cannot
derive them (`java_processor`'s own docstring). Those imports are rendered into the import block —
structurally *outside* the markers, near the top of the file. So a model-supplied import that does
not resolve produces a diagnostic on line 3, which the line test attributes to rendered scaffolding.

Measured, not hypothesised. Step 43's harness injected `com.modernized.batch.nowhere.NoSuchHelper`
and the loop **blocked on attempt 1** where the other three error classes healed in two. The class is
real: PR #32 found the pre-Spring-Batch-6 `ItemProcessor` package shipping in *every* generated
processor, and `_validated_imports` checks an import's shape and never its existence.

**Two costs, and the second is worse.**

1. The step spends one attempt instead of two and stops, on a defect a rewrite would plainly fix.
2. The blocked reason tells a reviewer *"That is a defect in this repo's renderer; asking a model to
   repair it would let it rewrite deterministic code."* For this class that is **false**. A reviewer
   acting on it investigates the code generator, finds nothing wrong, and loses the time — and § 4b
   of the feasibility assessment puts human review three to four orders of magnitude above inference
   cost. A confident wrong explanation is more expensive than none.

### The options

**(a) Move the imports inside the markers.** Refused. It would put a model's text in the middle of a
file whose whole design is that everything outside one region is a pure function of `design.json`,
and Java requires imports at the top anyway.

**(b) Relax the rendered-region refusal.** Refused, and it is the tempting one because it is a
one-line change. It would let a model be handed errors in genuinely rendered scaffolding — exactly
the failure the refusal exists to prevent — to fix a case where the refusal was misapplied. The
standing decision is explicit that this is to be fixed deliberately rather than by loosening the rule.

**(c) Thread the model's import list alongside the source.** `build_validator` receives
`{path: rendered_text}` and nothing else. Passing a parallel structure would create a second copy of
*what the model wrote*, which can disagree with the file that actually compiled.

**(d) Have the renderer say so in the file.** Taken.

## Decision

**The rendered file states which imports the model supplied, and attribution is read from that
statement rather than inferred from line position.**

`MODEL_IMPORT_MARKER` (`// model-authored import`) is appended to each import the model supplied and
that this renderer would not have emitted anyway. `model_authored_line_numbers` returns the body's
lines *plus* those import lines, and `build_validator` attributes from that set.

**The root cause was not the validator's arithmetic — it was that the artifact under-reported what
the model wrote.** Model-authored text lives in two disjoint regions and only one of them was marked.
The `BEGIN`/`END` markers exist so a *reviewer* can see at a glance which lines a model produced; an
unmarked model-supplied import defeated that for the reviewer in exactly the way it defeated the
validator. This fixes both, and it is why the fix belongs in the renderer rather than in the checker.

Three properties that keep it honest:

1. **An import the renderer emits unconditionally is never marked**, even when the model also names
   it. That line would be in the file regardless, so a diagnostic on it is this repo's to fix.
   Marking it would be G30 inverted — handing a model a defect it could not have caused.
2. **The marker only means something on a line that is really an `import` statement**, so nothing
   written inside a body can forge attribution for a line outside it. The other route is closed
   already: `_validated_imports` refuses anything that is not a bare qualified name, so a marker
   cannot arrive through the import list.
3. **The file stays deterministic.** Imports are still deduplicated and sorted, so one design renders
   byte-identically however the model happened to order them.

A repair now also carries `RepairContext.previous_imports`, on the reasoning `render_repair_facts`
already gives for `previous_body`: a repair that cannot see what it is repairing is a rewrite from
scratch. It is recovered from the rendered file rather than carried as loop state, so there is only
one account of what the model wrote.

## Consequences

**Good.** All four of step 43's error classes now heal in two attempts, where three did before. The
misleading message is gone, asserted by a test of its own rather than folded into the heal assertion
— a future change could restore the heal while reintroducing the wrong explanation, and an
outcome-only test would pass. The generated file is now a complete statement of what a model wrote,
which is a reviewer-facing improvement independent of the loop.

**Accepted cost.** Generated files carry a trailing comment on some import lines. That is real noise
in the artifact, and it is the price of the artifact being self-describing — the same trade the
`BEGIN`/`END` markers already make, extended to the region that had been missed.

**The rule is unchanged, which is the point.** Everything unmarked is still deterministic, still
rendered from `design.json`, and still refused to a model. What changed is that the renderer *states*
provenance instead of leaving it to be inferred from geometry. A future region of model-authored text
outside the body must mark itself the same way; the failure mode this ADR exists for is a third such
region appearing unmarked, at which point the symptom will again look like a renderer defect.

**What this does not do.** The round-trip metric does not move. This repairs the loop's attribution,
not its translation.
