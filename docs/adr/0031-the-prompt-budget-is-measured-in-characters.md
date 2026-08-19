# ADR-0031: The prompt budget is measured in characters, and exceeding it fails the call

## Status

**Accepted** (2026-08-19). Closes step 39a, the last step of milestone C4, and audit gap **G11**
(pillar 3, Context Window Engineering).

## Context

Pillar 3 was deferred to *"once `spec_extractor` calls `knowledge_store`"* — a precondition
[ADR-0016](0016-no-retrieval-for-a-fixed-corpus-that-fits-in-context.md) removed when it decided
that call is not being made. The deferral outlived its condition and the pillar was left with no
owner, which is what gap G11 records.

It binds at `generate` rather than at extraction. A `generate` prompt carries the design's domain
facts, the program's own COBOL, the target-API facts and the step's paragraphs together, and that
is the first place in this repo where several large things are concatenated into one call.

**Measured, before deciding anything:**

| | characters | note |
|---|---|---|
| largest `generate` prompt (`CBACT04C`, 3 steps) | **85,215** | measured 2026-08-19, no model called |
| largest `design` prompt | 81,975 | step 37g |
| ceiling adopted here | **600,000** | ~7× the largest measured |

The three `generate` prompts differ from each other by under 0.4%, which is consistent with the
99.8% shared prefix PR #28 recorded and is what makes cross-invocation caching worth anything.

**So there is no context pressure today** — at the conservative 2 characters/token this repo has
used since step 37g, the largest prompt is ~43k tokens against a 1M window. The question this ADR
answers is not *"does it fit"* but *"what happens when something grows"*, which is a policy that
has to exist before the growth, not after.

## Decision

**1. The budget is measured in characters, not tokens.**

This repo has no tokenizer, and acquiring one is worse than the problem. The SDK's token counter is
a network call, so a guard built on it would cost a round trip per call and would be the first thing
disabled when it got in the way; a bundled tokenizer would be a second dependency that has to track
the model's real one, and a guard that is subtly wrong about the quantity it guards is worse than an
honest approximation. Characters are exact, free, deterministic, identical on both backends, and
monotone in tokens — which is all a ceiling needs.

The token figure is reported, never enforced, and always with its assumed ratio stated.

**2. The ceiling is 600,000 characters, and its derivation is checked rather than described.**

`tests/system/test_context_budget.py` rebuilds the real `generate` prompts on every run through the
`author` seam — no model call, nothing spent — and fails if the largest has drifted more than 15%
from the 85,215 recorded here. A ceiling whose justification lives only in prose goes stale the
first time a prompt template changes; this one cannot.

Seven times the largest measured prompt is deliberate headroom. Track B's CICS/BMS programs are
substantially larger than Track C's, and a ceiling that only just fits today would fail on the first
real one — while still being loose enough that unbounded growth is caught long before a 1M window.

**3. Exceeding it raises `PromptBudgetExceededError`, before the call.**

It joins the `UnsupportedPicConstructError` family, and it is enforced in `call_model` — the one
place this repo talks to a model — so every node and both backends are covered by one check rather
than by a convention each caller has to remember.

The diagnostic names the total, the ceiling, and **the split between the system prompt and the user
content**, because those are different defects: an oversized system prompt is a template problem, an
oversized user content is a data problem.

**4. Truncating to fit is refused, permanently.**

This is the option the decision exists to close. A truncated prompt produces a call that succeeds,
costs money, and answers a question missing its tail — the copybook whose fields were cut, the
paragraph whose second half is gone — and nothing downstream can distinguish that from a model that
simply did worse. It is the exact failure mode `pic_mapper`'s no-model rule exists for: a wrong
answer that looks right.

**5. The ceiling is a parameter, not an environment variable.**

`call_model(..., max_prompt_chars=...)` lets a caller override it deliberately. An environment
variable would let any run raise its own ceiling silently, which is how a measured budget becomes a
number nobody has re-checked in a year. Raising `MAX_PROMPT_CHARS` is meant to cost a code change,
a fresh measurement, and an update to this ADR.

## Consequences

**Good.** Pillar 3 has a policy rather than a deferral, the number behind it is measured and
re-measured, and the failure mode is loud. The guard costs one integer comparison and no network
call, so there is no incentive to route around it.

**Accepted cost.** A legitimately huge program will now fail rather than run — deliberately, since
the alternative is a silently truncated prompt — and unblocking it takes a code change. That is the
right trade at this size and is worth re-examining at Track B, which is exactly when the plan says
to re-check.

**The token figure stays an estimate.** No calibration point exists yet, because the live runs
recorded aggregate usage rather than per-call input tokens. The round-trip test now prints
`input_tokens`, so the next live run yields a real characters-per-token ratio for these prompts at
no extra cost. Until then, every token number derived from this budget carries its assumed ratio.

**Not decided here.** Anything about output size (`DEFAULT_MAX_OUTPUT_TOKENS` already bounds that on
the SDK backend and cannot on the CLI), and anything about splitting a prompt that is too large —
the answer today is that it fails, and a splitting strategy would need its own measurement.
