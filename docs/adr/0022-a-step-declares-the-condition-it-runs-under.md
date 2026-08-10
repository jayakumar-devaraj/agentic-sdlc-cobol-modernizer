# ADR-0022: A batch step declares the condition it runs under

## Status

Accepted (2026-08-10). Closes audit gap **G25**, found by step 45's first real run against
model-authored business logic.

Extends [ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md),
which is the same decision applied to a different missing fact, and keeps
[ADR-0010](0010-unified-design-shape-and-the-deterministic-llm-split.md)'s deterministic/LLM line
where it is. Bumps `schema_version` **2.0.0 → 3.0.0**.

## Context

A real `claude-opus-5` body for `CBACT04C`'s `1300-COMPUTE-INTEREST` passed **every arithmetic row**
of the interest oracle (ADR-0021) — truncation, both signs, sub-cent results, the negative zero, and
both of `dailytran.txt`'s real extremes — and failed one case: it returned a `Tran` with
`tranAmt=0.00` where the COBOL writes no transaction record at all.

**The model did nothing wrong.** It was told to translate `1300-COMPUTE-INTEREST` and it translated
that paragraph, faithfully. The guard is not in it:

```cobol
*  CBACT04C.cbl:213-216, in the unnamed main body
             PERFORM 1200-GET-INTEREST-RATE
             IF DIS-INT-RATE NOT = 0
               PERFORM 1300-COMPUTE-INTEREST     *> the paragraph begins at line 462
               PERFORM 1400-COMPUTE-FEES
```

So a design can name every paragraph a step comes from, hand a generator the whole program source,
and still describe the step incorrectly — because **the condition under which a paragraph runs is
usually not inside that paragraph.** The result is the failure mode this repo exists to prevent: a
program that compiles, passes every numeric check, and writes records the original never wrote.

### Why the obvious fix does not exist

The instruction this ADR started from was *"add the guard paragraph to the step."* There is no such
paragraph. `CBACT04C`'s first **named** paragraph is `0000-TCATBALF-OPEN.` at line 234; the guard is
at line 214, in the unnamed main body directly under `PROCEDURE DIVISION`.

`source_paragraphs` could therefore carry only `PROCEDURE DIVISION`, which names the file opens, the
read loop, the account update and the close sequence as well — scoping the interest step to most of
the program. That trades a step that does too little for one that would do far too much.

The deeper reason it does not fit: `source_paragraphs` answers *what code this step came from*. A
guard answers *when the step runs*. Those are different questions, and overloading the first to
carry the second is what made the fix look impossible.

## Decision

**`BatchStepDesign` gains `guard_condition: str | None` — a required key with a nullable value.**

It carries the condition verbatim from the COBOL (`"IF DIS-INT-RATE NOT = 0"`), or `null` when the
step runs for every record. `render_step_facts` renders both branches, and the guarded branch states
the target-side consequence explicitly: **when the condition does not hold, return `null`** — an
`ItemProcessor` returning null filters the item, which is Spring Batch's way of saying "no output
record", and is the faithful translation of a paragraph that is never performed.

### Required, not optional, and this is the load-bearing part

The field could have been `= None` and backward-compatible. It is not, because that would make
**"this step is unconditional"** and **"nobody considered whether it was"** identical in the
document — which is precisely how the defect got through in the first place.

This is the `notes` precedent from `modernization_engineer`, where a model with nothing to flag must
say so explicitly rather than omit the key. That choice paid for itself three times in PR #28 by
turning silent wrong answers into findings. A design is a document read by a human at a gate, and a
missing guard should look different from a considered absence of one.

The cost is a breaking schema change: `schema_version` goes to **3.0.0** and every existing
`design.json` is invalid until regenerated. That is acceptable while the only producer is this
repo's own `solution_architect` and no design has been through control-plane's gate (G7). It would
not be acceptable later, which is an argument for making it now rather than after.

## Consequences

**Good.** The one failing row of step 45's equivalence test has a mechanism behind it rather than a
note. `solution_architect`'s prompt now asks for the guard and explains why, so the fact is produced
where the judgment lives rather than inferred where it does not. Both prompt branches are asserted
through the real `build_engineer_prompt`, not the helper — the G21 lesson.

**Cost, stated.** A breaking schema bump, and one more required field for `solution_architect` to
get right. The guard is LLM-authored like the rest of `batch_jobs` (ADR-0010 decision 3), so it is
a judgment that can be wrong — but a *stated* judgment a reviewer can check against the COBOL at the
gate, which is strictly better than an omission nobody can see.

**Verified** (added after the first paragraph above was written, which said this had not been run).
With the guard declared, `claude-opus-5` compiled on attempt 1 and wrote the branch — commenting it
*"Guard from the caller of 1300-COMPUTE-INTEREST"* — and the equivalence test passes **10 of 10**,
R10 included. Stating a guard in the prompt is sufficient; the model did not need the caller's
source, which it already had, only the declaration that the condition was its responsibility.

**Still not claimed:** that a program round-trips. This verifies one `COMPUTE` and the condition it
runs under. `CBACT04C` is also a rate lookup, a `'DEFAULT'` fallback, a per-account accumulation, an
account update and a transaction write, and the same run flagged three of those as work no step
owns. The round-trip count stays `0 of 4`.

**The pattern worth carrying.** This is the third instance of one defect class — *the generator was
never shown something this repo knows*. PR #28 was `CobolArithmetic`'s API; PR #33 was
`WS-MONTHLY-INT`'s scale; PR #37 was a composite's accessors; this is the condition a step runs
under. Each was fixed as an instance. The standing question at every contract change should be:
**what else does the generator have to know that nobody has told it?**
