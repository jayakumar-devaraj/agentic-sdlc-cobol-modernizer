# ADR-0013: Two model backends behind one resilient client

## Status

Accepted (2026-08-08).

## Context

Every node reached a model the same way and with the same omissions:

```python
client = anthropic.Anthropic()
response = client.messages.create(model=model, max_tokens=4096, ...)
```

Three copies, in `spec_extractor`, `spec_critic`, and `solution_architect`. **No timeout, no retry,
no backoff, and no record of what any call cost.** A compliance audit against the platform plan's
30-pillar matrix put numbers on it: pillar 20 (fault tolerance), pillar 24 (token/cost), and the
rate-limiting half of pillar 25 were all absent from `src/` entirely — zero matches for `retry`,
`backoff`, `timeout`, `usage`, or `cost`. The first real run with a credential would also have been
the first test of what happens on a 429.

Two further facts shaped the decision.

**This repo had no credential and could not get one from an API key.** The standing "the live model
calls have never run" gap in `docs/qa/verification-report.md` had been carried since PR #6, and the
assumed fix was an `ANTHROPIC_API_KEY`. That assumption was wrong, and it was the user who caught
it: a Claude Pro/Max subscription already authenticates the `claude` CLI, and this platform already
uses that mechanism — ADR-0001 describes this repo as shipping "the same shape as [control-plane]'s
existing `coder.py` → `claude` CLI call". The platform's established way to reach a model was the
CLI all along; the SDK was the novel choice, adopted without the question being asked.

**A subscription and an API credential are not interchangeable at the far end.** A subscription is
right for a developer verifying work on their own machine. A service billing other enterprises
needs per-tenant quotas, isolation, and real cost attribution. Choosing only one backend would have
meant choosing which of those two situations to be wrong about.

## Decision

**1. One module owns every model call**: `core/model_client.py`, exposing `call_model()`. Nodes call
it and nothing else. The retry policy is written once instead of being three chances to get backoff
subtly wrong.

**2. Two backends behind that one interface.** `claude_cli` is the default: `claude -p` in print
mode, authenticated from an existing subscription, no API credential. `anthropic_sdk` remains for
deployments that need per-tenant billing and quotas. Selected by `COBOL_MODERNIZER_MODEL_BACKEND`,
or explicitly per call. An unrecognized value raises rather than falling back to a default — a typo
in deployment config silently sending traffic to a different provider is exactly the quiet wrong
answer this repo rejects everywhere else.

Backend choice is an environment variable, not a mounted file, and that does not contradict ADR-0005:
that ADR governs *credentials*. Which backend to use is a non-secret operational toggle.

**3. Retry policy: bounded attempts with full-jitter exponential backoff, capped.** Retryable is
429, 5xx, timeout, and connection failure. A 4xx is not retried — that is our request being wrong,
and four more identical attempts burn quota to receive the same answer. Full jitter rather than
fixed backoff specifically because branches run concurrently: a fixed schedule would have them all
retry in step and re-collide on the same limit.

**4. The SDK's own retries are disabled** (`max_retries=0`). Five attempts here against its default
two is ten real requests, and neither layer's logs would show the true count.

**5. Usage is captured on every call and named honestly.** The CLI's JSON envelope returns
`total_cost_usd`, per-model token counts, and cache metrics — richer than the SDK response. On a
subscription that figure is an equivalent-price estimate rather than a charge, so the field is
`notional_cost_usd`. The SDK backend leaves it `None` instead of multiplying tokens by a hardcoded
rate card that would silently go stale.

**6. Prompt delivery was measured, not assumed.** The user turn goes over **stdin**: a real prompt
is Known Facts plus every wrapped COBOL source unit, tens of kilobytes for `CBACT04C` alone, well
past the ~32 KB Windows argv limit. Verified with a 40 KB payload before the module was written.
The system prompt goes to a temp file via `--system-prompt-file` for the same reason — argv would
work at today's prompt sizes and break quietly as they grow.

**7. Tools are disabled on the CLI backend.** This module wants a text completion, not an agent
loose in a real repository. `--system-prompt-file` replaces the CLI's default instructions and
`--disallowed-tools` removes the capability, so neither layer is relied on alone.

**Considered and rejected: `--fallback-model`.** The CLI can silently switch models when the primary
is overloaded. That is real resilience, but it would quietly violate ADR-0004's deterministic
per-node routing and make `design.json` unable to answer "which model produced this?" — a provenance
question `CLAUDE.md` treats as first-class. Failing loudly and retrying the *same* model is the
better trade.

## Consequences

**The standing "live calls have never run" gap is closable.** A live round-trip against the real
CLI now passes, confirming the flags are accepted, the envelope keys exist, and usage really comes
back. That test is marked `live_claude_cli` and skipped unless opted in.

**Test fakes had to move, and finding that out was ugly.** Changing the default backend did not
fail `test_cli_design.py` — it kept faking `anthropic.Anthropic`, which is no longer the default
path, so the tests began spawning real `claude` subprocesses against a live subscription and the
suite hung. A test that quietly costs money and calls a live model is worse than one that fails.
`tests/conftest.py` now pins the backend suite-wide and any test wanting the real CLI must declare
it via a marker plus an environment opt-in. The lesson generalizes: **when a default changes,
every fake positioned at the old default becomes a silent pass-through**, and nothing in a green
suite says so.

**Cost is logged but not yet in the contract.** `ModelCallResult` carries the usage fields, and
every call logs them, but `DesignDocument` has no cost field yet. Adding one is a contract change
with schema regeneration and belongs in its own PR rather than smuggled into a resilience fix.
Pillar 24 is therefore *observable* but not yet *reportable to control-plane*.

**The per-call overhead of the CLI backend is real and worth knowing**: roughly 9.8k cache-creation
tokens per call for the CLI's own harness, measured, even with the system prompt replaced. For a
four-program `design` run (9 calls) that is ~88k tokens of overhead. Acceptable for development and
verification; a reason to prefer the SDK backend at production volume, alongside the billing reason.

**A retry now costs wall-clock time inside a bounded invocation.** Five attempts with backoff capped
at 30s is a worst case of roughly two minutes per call before failing. That is bounded and logged,
but control-plane's own invocation timeout (whatever it is) must exceed it — an integration detail
for Milestone C5, noted here so it is not discovered as a mystery hang.

**`spec_critic`'s model tier becomes testable.** ADR-0004 deferred validating its cheaper tier
empirically "once real critiques exist"; real critiques can now be produced cheaply on a
subscription, so that deferred question is answerable rather than blocked.
