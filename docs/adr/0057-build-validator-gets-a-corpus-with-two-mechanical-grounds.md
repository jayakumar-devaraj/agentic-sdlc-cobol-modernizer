# ADR-0057: `build_validator` gets a corpus, and both its verdicts are mechanically grounded

## Status

**Accepted** (2026-08-25). Builds the instrument
[ADR-0056](0056-the-generate-loop-wraps-what-it-quotes-back.md) named as its own open gap:

> `build_validator` has none — its tests are scripted on both sides… So this change is verified
> structurally and not behaviourally… and cannot be until someone builds the discrimination corpus
> this node has never had.

**Amended 2026-08-26: it has been run.** n=2, 16 calls, **$0.3676** against a $0.37 quote, zero
malformed responses. Full numbers and rationales in
[`docs/qa/verification/08-…`](../qa/verification/08-evaluation-harnesses-judge-injected-error-and-the-handoff.md).
Three findings, in order of what they cost to learn:

1. **The `SYMBOL_ABSENT` bar passed 6 of 6** — not one blocked case was called repairable, which is
   the direction that spends the whole heal budget on an unfixable build.
2. **The `COMPILER_PROVEN` bar failed, and the model was right.** `unresolved_import` came back
   `blocked` in both samples because **v1_1_0 of this node's prompt was stale**: it said the imports
   block is "not yours to change", which ADR-0025 had already made false. Fixed in **v1_2_0**. The
   benchmark earned its cost on its first run by finding a prompt defect no test could see.
3. **The cost quote was right for the wrong reason.** Predicted from a declared 8,000 input tokens;
   real input was **144 tokens across all 16 calls**, with output at 20,932. The profile is inverted
   from its placeholder — negligible input, dominant output — which is the measurement the stale
   `pin_reason` admitted nobody had.

**v1_2_0 is unverified against a model.** Re-running is a further $0.37 and a separate decision.

*(Original:)* **The corpus and its harness are built and unrun.** No model has been called. Running
it is a decision with a price, quoted below.

## Context

**This node decides how the heal budget is spent, and nothing has ever measured its judgment.**
`tests/system/test_build_validator.py` scripts the model on both sides (`_advise(True)` returns a
fixed verdict), and step 43's four-error-class harness says so in its own docstring: *"what is under
test is the loop — that it compiles, judges, re-prompts and recompiles for each class — not whether
a model can repair them. Those are different claims, and conflating them is how a passing suite
would come to stand for something nobody measured."*

That was correct and it left the second claim unmade. ADR-0056 then changed this node's prompt and
could not say whether the change moved anything.

### Two things found while building it

**1. The routing pin's reason is stale, and says so.** `resolve_routing("build_validator", …)`
returns:

```
model:     claude-haiku-4-5-20251001
selection: pinned
rationale: pinned: Node not built (Milestone C4); profile is a placeholder, not a measurement.
```

Milestone C4 is complete — the node is built. So the pin rests on a reason that expired, and the
declared token profile (8,000 in / 3,000 out) is an admitted placeholder that nothing has checked.

**2. It is pinned to the model ADR-0049 struck from the judge candidates.** That record removed
`claude-haiku-4-5-20251001` on three independent grounds, the first being that **5 of 21 responses
did not hold the response contract**. The node that gates the heal budget runs on it. This is not an
accusation — `spec_critic` runs on Haiku on real evidence that it matches Opus at 2.3× lower cost,
and the judge's task is not this one — but it is a fact worth having in front of whoever reads the
first results.

## Decision

### 1. Eight cases, and both verdicts are checkable by machine

`tests/evaluations/corpus.py` had to split its cases into oracle-grounded (asserted) and
source-grounded (**reported, never asserted**), because grading a reading of COBOL would promote an
interpretation to ground truth. **This corpus mostly escapes that problem**, and the reason is the
substance of this record:

| Ground | Meaning | Cases |
|---|---|---|
| `COMPILER_PROVEN` | the heal loop repairs this exact body under real Maven, so a rewrite is demonstrably sufficient | 4 (step 43's injected classes, **imported not copied**) |
| `SYMBOL_ABSENT` | the symbol the body needs is absent from the whole rendered project, so no rewrite and no import reaches it | 3 |
| `REPO_HISTORY` | a defect this repo produced, classified by this repo's reading | 1 — **reported, not asserted** |

`test_build_validator_corpus.py` **enforces the second ground** against a real copy of the baseline
rather than trusting the corpus's own word, and separately asserts each case still references the
symbol it calls absent — a body that stopped mentioning it would pass the first check by saying
nothing.

The one `REPO_HISTORY` case is the job-level timestamp: `withProcessedAt` is genuinely absent, but a
reasonable validator might call the *clock* the repairable part, and grading that reading is exactly
what `corpus.py` refuses to do.

### 2. The diagnostics are compiled, not written

Each case is rendered into a real processor and built by real Maven once; the model is shown javac's
own output. Inventing plausible compiler messages would measure this node against my idea of what a
compiler says — the failure `corpus.py` names in its first paragraph. One build per case, no money,
and the samples share the result.

### 3. Two bars, per run, both derived from what the node is for

Inheriting [ADR-0049](0049-the-judge-is-sampled-and-haiku-is-struck-from-the-candidates.md)'s
correction rather than re-deriving it: a bar applied to the mean cannot tell a noisy instrument from a bad one.

1. *Every `COMPILER_PROVEN` case judged `repairable`* — the loop repairs these bodies, so calling
   one `blocked` stops a build that demonstrably heals.
2. *Every `SYMBOL_ABSENT` case judged `blocked`* — **the expensive direction.** The system prompt
   states the cost: a false `repairable` spends every attempt rewriting statements that were never
   the problem and hands a human three worse versions of the same code.

### 4. What running it costs, quoted rather than estimated by feel

**$0.55** for the default `n=3` — 8 cases × 3 samples = 24 calls at the routing layer's own
`estimated_cost_usd` of $0.0230 per call.

**That number is an over-estimate and knowing why is part of the point.** It is computed from the
placeholder profile above: 8,000 input tokens per call, where a real prompt here is a method body,
a few imports and a handful of diagnostics. **So the run also produces the first measurement of this
node's token profile**, which is the second thing the stale `pin_reason` admits nobody has.

## Consequences

**A typo in the summary block was caught by a free test, and only because one was written.** The
first draft printed `usage.cost_usd` and `usage.calls_without_cost`; the real fields are
`notional_cost_usd` and `calls_without_reported_cost`. **The entire suite passed** — the only module
touching them is skipped unless someone is spending money, so the first person to find it would have
been the one who had already paid for eight calls. `test_build_validator_corpus.py` now asserts
those attribute names, that all eight cases reach the report, and that the benchmark carries the
`live_claude_cli` marker at all.

This is the standing rule in `docs/development-environment.md` — *before paying for any live test,
check it prints on success* — with the correction that **checking is not enough if the check is a
reading.** A free test that exercises the billed module's plumbing is the version that works.

**The run will also be the first real exercise of ADR-0054's repair loop.** This node runs on the
model that broke the response contract 5 of 21 times in the only measurement this repo has of it,
and `parse_with_repair` now sits between that and a raised error. Whether it fires, and how often,
is data the run produces for free.

**What this record does not claim.** No model has been called, so nothing here says whether
`build_validator` discriminates. The bars are written and unmet-or-met by nobody. That is the honest
state: ADR-0056 identified a missing instrument, and this builds it — pointing it at the node is a
separate, priced decision.
