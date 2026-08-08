# ADR-0014: Route by measured complexity, not one model per node

## Status

Accepted (2026-08-08). Amends **ADR-0004** (which stands in principle: this is still a lookup
table, not a routing engine).

## Context

ADR-0004 chose one model per node and said so plainly about its own values: *"tentative default
tiers, not benchmarked final numbers"*, with `spec_critic`'s tier explicitly flagged to revisit
empirically. Nothing revisited them. A live four-program run then made the cost of that concrete —
$2.31 notional for nine calls, with `spec_extractor` on Opus 5 accounting for 49% of it.

The measurement that actually settles the design is the spread *within* one node's work:

| Program | Prompt chars | Paragraphs | Fields | Copybooks | Observed output tokens |
|---|---:|---:|---:|---:|---:|
| `CBCUS01C` | **11,346** | 5 | 32 | 1 | 5,485 |
| `CBACT04C` | 74,230 | 22 | 93 | 5 | 13,775 |
| `CBACT01C` | 78,647 | 16 | 56 | 2 | 14,350 |
| `CBTRN02C` | 81,902 | 26 | 102 | 5 | 12,872 |

`CBCUS01C`'s prompt is **7× smaller** than `CBTRN02C`'s, and the other three cluster tightly. One
model per node cannot serve both ends: it either overpays on the small program or under-serves the
large one. Narrating a 5-paragraph program with one copybook is not the same task as narrating a
26-paragraph program with five, and pricing them identically is a decision nobody made on purpose.

Two other facts came out of the same run and belong here:

- **Nothing ever set `effort` or `thinking`.** On Claude Opus 5 thinking is *on by default* and
  effort defaults to `high`, so every call had been paying the most expensive setting — not a
  neutral one.
- **`max_output_tokens` was hardcoded at 4096** while real responses run 5,485–23,366 tokens. The
  SDK backend would have truncated every single call; the CLI backend never sent the value at all,
  which is why nothing had failed yet.

## Decision

**1. The routing key is `(node, complexity_tier)`, not `(node)`.**

Still a lookup in `config/model_routing.yaml`. ADR-0004 rejected "a dynamic routing engine that
scores anything at runtime" and that rejection holds — what changed is the key, not the mechanism.
There is no runtime scoring of arbitrary signals, no probe call, and **no model call to decide
which model to call**, which would be self-defeating.

**2. Complexity is measured from facts the pipeline already has.** `core/complexity.py` reads
paragraph count (`cobol_parser`), field counts (`pic_mapper`), and — the dominant signal — the
length of the *actual* prompt `build_prompt` is about to send. That last one is not a proxy:
`build_prompt` is deterministic and runs before the model call, so the exact figure is in hand.
Estimating it from line counts would be guessing at something already known.

**3. Thresholds on two signals, not a weighted score.** A weighted sum over
paragraphs/fields/copybooks needs coefficients nobody can defend, and re-tuning one coefficient
silently moves every program's tier at once. Bands are explainable — *"complex because the prompt
is 74,230 characters"* — and move one program at a time. `prompt_chars` is the primary signal;
`paragraph_count` is the second so a short but branchy program isn't routed cheap on size alone.
The remaining counts are recorded for explainability and deliberately do not move the tier.

**4. The initial calibration changes exactly one program's routing.** `CBCUS01C` drops to Haiku at
`low` effort. `CBACT01C`, `CBACT04C`, and `CBTRN02C` stay on Opus 5.

This restraint is the point, not timidity. Opus found a real defect in `CBACT04C` — the
`ELSE PERFORM 1050-UPDATE-ACCOUNT` branch is unreachable, so the last account's accrued interest is
never posted — that a hand-verified golden fixture had missed. Downgrading those three on cost
grounds *before* benchmarking would trade a demonstrated capability for money, which is the same
class of mistake as leaving ADR-0004's tiers unexamined. The `moderate` band is empty for Track C
today and exists for a real estate's mid-size programs.

**5. `spec_critic`'s cheap path is a consequence of ADR-0007, not a heuristic.** When
`compute_fidelity_issues` has already proven a defect, `overall_confidence` is forced to `0.0`
*regardless of what the critic scores*. No model, at any price, can change the number the gate keys
on. Paying the strongest tier to produce scores that cannot affect the outcome is waste with no
capability argument behind it, so that case routes to `simple`. Otherwise the critic matches the
program's own tier.

**6. `solution_architect` takes the highest tier present, not an average.** It reasons across every
program at once (ADR-0010); a run containing one hard program is a hard run. Averaging would let
three simple programs pull the architect down to a tier the fourth needs.

**7. `effort` and `max_output_tokens` are part of the routing decision.** The injected callables
(`NarrateFn`, `CritiqueFn`, `ArchitectFn`) now take the whole `RoutingDecision` rather than a model
string — effort and the ceiling are as much "which call to make" as the model is, and a test fake
that only sees a model name cannot assert that a simple program was actually routed cheaply.

**8. `COMPLEX` is the default for any caller that cannot measure.** Being wrong toward more
capability costs money; being wrong toward less costs correctness. This repo's standing posture is
that a plausible-but-wrong answer is the worst outcome available.

## Consequences

**The routing decision is visible at the review gate.** `ProgramComplexity` — signals, tier, and a
plain-English rationale — rides on `SpecExtractionResult` into `design.json`. "This spec was
produced by the cheap model" is a fact a human reviewing that spec deserves, not an internal
optimization detail. The schema is regenerated and drift-checked accordingly.

**A latent truncation bug is fixed.** Per-tier `max_output_tokens` replace the hardcoded 4096 that
would have truncated every real response on the SDK backend. Ceilings are safety limits, not cost
levers — set above observed output, because truncating a critic response is a hard
`SpecCritiqueParseError`. The cost levers are `model` and `effort`.

**The backends are not at parity on the ceiling.** The `claude` CLI exposes no max-tokens flag, so
`max_output_tokens` applies on the SDK backend only. Stated on `call_model` rather than hidden: a
workload that needs a hard output cap must use the SDK backend.

**`spec_critic` is still uncalibrated.** ADR-0004 flagged its tier for empirical validation; the
live run produced 1.00/1.00/0.75/0.70 with zero fidelity issues, which is encouraging but is *not*
a benchmark — nobody has yet fed it a known-bad narration and checked that Haiku catches what Opus
would. Its config keeps Haiku across all three tiers pending that test.

**The critic's output volume is untouched here.** 1,200 output tokens per scored rule (16,675–23,366
per program) is the single largest remaining waste in a run, and it is a prompt problem, not a
routing one. Tracked separately rather than bundled into a routing change.

**Estimated saving is modest today and structural later.** One program of four moves off Opus —
roughly $0.06 of a $2.31 run. The value is not this run: it is that a real mainframe estate is
mostly small programs, and the mechanism now exists to route them cheaply without a human deciding
program by program.

**The thresholds will need re-tuning as the estate grows.** They are calibrated against four
programs, which is enough to separate a clear outlier and not enough to be confident about the
`moderate` band. `test_complexity.py` asserts the real programs sit clear of their boundaries, so a
future tweak that puts one on a knife edge fails loudly.
