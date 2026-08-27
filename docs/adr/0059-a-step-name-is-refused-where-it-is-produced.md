# ADR-0059: A step name Java cannot take is refused where it is produced

## Status

**Accepted** (2026-08-27). Closes gap **G22**. Applies the principle
[ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md) decision 5 already stated for
`input_type`/`output_type` to the one field on the same model that was missed.

## Context

`BatchStepDesign.step_name` is LLM-authored, and three renderers derive a Java class name from it.
The simplest of them just capitalises the first character:

```python
base = step.step_name[:1].upper() + step.step_name[1:]   # java_job, java_aggregation
return f"{base}ItemReader"
```

So a COBOL-style `1300-COMPUTE-INTEREST` becomes `1300ComputeInterestItemReader`, which starts with
a digit. `require_java_identifier` catches it — **at `generate` time.**

That timing is the whole defect. Generate runs *after* a human approved the design at the gate, so
the failure spends a review and throws the result away. The platform's governance claim rests on
gates being decisions a human can usefully make; asking someone to approve a design that cannot be
generated from is the opposite.

**And the model was following the prompt it was given.** `v1_0_0` says `job_name` must be *"a
camelCase Spring Batch job bean name"* and says nothing at all about `step_name`'s shape. The
contract asked for a name, never said what kind, and three consumers assumed camelCase. That is the
root cause, and enforcing a rule nobody stated would have been the worse half of a fix.

## Decision

### 1. The rule is stated in the prompt, and enforced on the contract

Two halves, and neither works alone:

- **`solution_architect` prompt `v1_1_0`** requires a camelCase `step_name` that is a legal Java
  identifier, names `1300-COMPUTE-INTEREST` as the case that is refused, and says the COBOL
  paragraph belongs in `source_paragraphs`.
- **A `field_validator` on `BatchStepDesign.step_name`** refuses anything Java would not take.

Enforcement without the statement punishes a model for a rule it was never given. The statement
without enforcement is a suggestion — and this is a field whose violation is silent until three
layers later.

### 2. It is refused on the contract, not in the parse function

`_parse_unified_design_response` was the other candidate and would have worked for the architect's
own output. The contract is the better place because it also covers a design assembled by hand, one
loaded from a stored `design.json`, and any future producer. A design carrying a name Java cannot
take is invalid wherever it came from.

The validator is reached through `parse_with_repair`, so a model that emits a bad name gets **one
repair attempt carrying this message** rather than a run that dies an approval later. That is why
the message names the fix (`computeMonthlyInterest`) and says where the COBOL name does belong,
rather than only stating the fault: it is read by a model, not only by a person.

### 3. One definition of what Java accepts, in a new leaf module

`core/java_lexicon.py` holds `JAVA_IDENTIFIER`, `JAVA_RESERVED` and `why_java_rejects`.
`core/contracts.py` and `rendering/java_names.py` both import it.

**The first attempt duplicated the regex into `contracts.py`** with a comment claiming a circular
import forced it — following the precedent `java_job` and `java_aggregation` set for their shared
naming derivation. That was wrong: `java_names.py` imports nothing but `re`, so no cycle existed.
Duplicating fifty Java keywords on a premise that was never checked would have created exactly the
drift the two-copy precedent tolerates only because it has no alternative.

**The reserved-word half matters and was nearly missed.** A first version of the validator checked
only the pattern, which accepts `class` — a perfectly good identifier *shape* and an illegal
identifier. Had it shipped, the contract would have been strictly weaker than the renderer it
exists to protect, and a reserved-word step name would still have failed after the gate. Sharing
one definition removes the possibility rather than testing for it.

### 4. The schema documents the rule; it does not enforce it

`json_schema_extra` carries the pattern and a description, rather than `Field(pattern=...)`. A
pattern would be enforced by pydantic *before* the validator and would report itself as *"String
should match pattern '^[A-Za-z_$]…'"* — which reaches a model as repair instructions and tells it
nothing about what to write. The schema cannot express the reserved-word half in any case, so the
description states it in words.

## Consequences

**A design that cannot be generated from now fails before a human sees it**, joining
`input_type`/`output_type` under the same rule.

**This narrows what a valid design may contain**, and a stored `design.json` with a COBOL-style step
name will now fail to load. Such a design could never have been generated from, so nothing that
worked stops working — but it is a real behaviour change for a stored artifact, not only for new
model output.

**A hyphenated name like `compute-monthly-interest` is now refused too**, though
`processor_class_name` alone would have handled it by splitting on hyphens. The reader derivation
would not, and a design cannot know which steps will need a reader. The rule is set to the strictest
consumer deliberately, rather than to the most forgiving one.

**What is not verified here.** No model has been run against `v1_1_0`. The prompt change is
unbilled: the rule it states is enforced by the validator either way, so a model that ignores it
gets a repair attempt rather than a wrong result — but *whether stating it reduces how often the
repair is needed* is unmeasured, and would need a billed comparison against `v1_0_0` to claim.
