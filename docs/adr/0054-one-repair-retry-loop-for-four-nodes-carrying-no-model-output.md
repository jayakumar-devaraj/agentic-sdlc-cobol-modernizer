# ADR-0054: One repair-retry loop for four nodes, carrying no model output back

## Status

**Accepted** (2026-08-25). Closes plan **step 35**, the last numbered step in Milestone C3.

Supersedes the "not yet" in
[ADR-0007](0007-confidence-score-composition-is-deterministic-first.md) § 3 and
[ADR-0010](0010-unified-design-shape-and-the-deterministic-llm-split.md) § 5, which deferred this
work rather than declining it. Both stay accepted; only their "yet" expires.

## Context

Four nodes parse structured JSON out of a model's raw text — `spec_critic`, `solution_architect`,
`modernization_engineer`, `build_validator` — and all four had the same shape: `strip_code_fence`,
`json.loads`, validate, raise a node-specific `*ParseError` that propagates and fails the run.

**Three separate records deferred the fix to this step, and two of them said why.** ADR-0007:
building a smaller `spec_critic`-specific version "would be exactly the kind of premature
abstraction this repo's own conventions warn against — two similar-but-not-identical retry
mechanisms are harder to reason about than one shared one built once, deliberately, when its actual
contract is known." ADR-0010 repeated it at the third site. ADR-0012 named the real blocker: with no
live call anywhere in the repo, "the failure modes such a loop would handle are hypothesized rather
than observed."

**That blocker is gone, and it left evidence rather than an assumption.** ADR-0049 sampled 21 judge
calls per candidate over the same corpus:

| | responses holding the contract |
|---|---|
| `claude-opus-5` | **21 of 21** |
| `claude-haiku-4-5-20251001` | **16 of 21** |

All five failures were **one shape**: a prose preamble ahead of otherwise-valid JSON, against a
prompt reading *"Respond with a JSON array and nothing else."* `tests/evaluations/judge.py` keeps
the excerpts. That is a model **ignoring** an instruction, not one unable to follow it.

## Decision

### 1. One loop in `core/structured_output.py`, parameterised by the caller's own parser

`parse_with_repair(node, raw_response, parse, reask, *, on, max_attempts)` parses first — so the
overwhelmingly common well-formed answer costs nothing — and on a failure listed in `on`, re-asks
through a callable the node supplies, appending a repair instruction to the **unchanged** request.

Every node keeps its own parser, its own error type and its own validation. The loop changes how
many times the model is asked; it never widens what counts as a valid answer. `solution_architect`
still refuses a design naming a program, entity, step role or REST method it did not offer.

**`on` is required and narrow, never a bare `except Exception`.** A `TypeError` raised inside a
parser is a defect in this repo, and re-asking a model to fix it would spend real money hiding a bug
behind a retry that cannot work. A test pins that such an error reaches the caller without a call.

**On exhaustion the caller's own error propagates**, from the final attempt. The loop never
substitutes an error type of its own, so a caller already handling `SolutionArchitectParseError`
keeps handling exactly that, and the traceback names why the *last* response was unusable rather
than "repair exhausted".

### 2. `MAX_CONTENT_ATTEMPTS = 2`, measured rather than picked

The observed failure is an ignored instruction, so one more attempt carrying a stronger instruction
is the whole of the remedy. A third would spend a full prompt to distinguish "ignored it twice" from
"cannot do it", and this repo's answer to "cannot do it" is to fail loudly rather than keep paying.

**This is the third attempt cap in the repo and it is unrelated to the other two.**
`model_client.MAX_TRANSPORT_ATTEMPTS` (5) bounds one HTTP call against a 429;
`generate_pipeline.MAX_HEAL_ATTEMPTS` (3) bounds a model rewriting code that did not compile — new
content and a new question each time. This one re-asks an *identical* question. `model_client`
already warns that confusing two of these multiplies them invisibly and quadratically; each is
defined once and named for what it bounds, and `build_validator`'s docstring — the one place all
three are in scope — states the distinction.

### 3. The repair prompt carries this repo's parse error and none of the model's words

The obvious alternative is to quote the malformed response back and ask the model to fix *that*. It
is not taken, for two reasons pointing the same way:

1. **It would put model-authored text inside a prompt, unwrapped** — precisely the question
   [ADR-0053](0053-the-narration-the-critic-judges-is-wrapped-and-the-prompt-says-so.md) left open
   for `build_validator` rather than answering in passing. A loop spanning four nodes is the worst
   possible place to pre-empt that decision.
2. **A malformed response is untrusted content.** `spec_critic` and `spec_extractor` read tenant
   COBOL. A response echoing an injected instruction back into the next prompt is a laundering path
   straight through the boundary `core/guardrails` exists to hold — the same argument ADR-0053 made
   about a narration, one step further along.

**The cost, stated rather than hidden**: re-asking without the prior text sends a full prompt again
rather than a diff. At an observed failure rate of 5 in 21 for a struck candidate and 0 in 21 for the
pinned one, that is a rare second call, and it buys a boundary that needs no exception.

### 4. Redaction, because decision 3 was not sufficient on its own

**Writing the instruction to carry "the error and nothing else" did not achieve it.** Three of the
four parse errors interpolate the response into their message — `spec_critic`'s reads
`f"... is not valid JSON: {exc}. Raw response: {raw_response!r}"` — which is an excellent message
for a human reading a log and exactly the wrong thing to paste into the next prompt.

`redact_response` excises the model's own words from the message by **literal** match on both the
plain and `repr()` forms, since `!r` is what actually put the text there. Nothing is guessed at, and
a message that never quoted the response (`build_validator`'s) is returned unchanged.

## Consequences

**The defect in decision 4 was found by a test, not by review, and that is the point worth
recording.** The boundary property was written as an assertion before the first node was wired; it
failed on that node; the docstring claiming the property was already written and was wrong. Had the
property lived only in prose — the shape ["an unverified caveat needs a probe or an
owner"](../../CLAUDE.md) names — it would have read as true for as long as anyone believed it.

**A second finding, same mechanism.** After wiring, a damage probe setting `max_attempts=1` left
`solution_architect`'s 35 tests, `modernization_engineer`'s and `build_validator`'s 68 all green:
the loop was wired into three nodes and no test could distinguish that from its being unwired. One
test per node now drives the measured failure and asserts the node re-asked exactly once. This is
trap 6 in [`docs/development-environment.md`](../development-environment.md) — *a test that passes
on the artifact that produced it is not evidence* — applied to a change of my own.

**What this does not do.** It does not make a run robust to a model that will not answer; that still
fails loudly, by design. It does not deterministically salvage JSON out of surrounding prose, which
would be free and would handle the observed mode without a second call: extracting a balanced value
from prose is a heuristic that can select the wrong span, and `strip_code_fence`'s licence in
ADR-0007 was granted to a strictly syntactic wrapper. Declining it is a deliberate scope choice, not
an oversight — if the second call ever shows up as a real cost, that is the first thing to revisit.

**Milestone C3 has no numbered steps left.** Its gate item *"a real control-plane gate exercised"*
remains open and belongs to Milestone C5, per ADR-0012.
