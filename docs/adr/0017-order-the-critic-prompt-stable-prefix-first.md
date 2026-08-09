# ADR-0017: Order the critic prompt stable-prefix-first, and drop the caching half after measuring it

## Status

Accepted (2026-08-08). Refines `nodes/spec_critic.build_critique_prompt`'s output order and
`prompts/registry/spec_critic/v1_0_0.md` to match. Records a measurement that **stopped** planned
work, in the same spirit as the effort-level experiment and the 1,200-tokens-per-rule hypothesis.

## Context

A request to "implement context window engineering to prevent context bloat, reduce token costs,
and avoid forgotten information" prompted a review of the `design` phase. Two of the three stated
problems turned out not to exist here, and the third was real but not where it was expected.

**There is no accumulated history to prune.** Every node makes exactly one stateless, single-turn
call; `graph/design_graph.py` passes typed Pydantic objects between nodes, never transcripts.
Sliding windows, rolling summarization, and vector-based state hydration all target multi-turn
accumulation. Building any of them here would add machinery for a problem this architecture does
not have.

**Nothing is near a context limit.** The largest prompt in a real four-program run is 81,975
characters, roughly 40,000 tokens against a 1M window. "Forgotten information" is not a live risk
at Track C scale, and pretending otherwise would justify a fix by its category rather than by
evidence.

**What is real is duplication.** A harness captured every prompt in a real four-program run (fakes
in place of models, no spend):

| Program | Extractor chars | Critic chars | Byte-identical | Dup |
|---|---:|---:|---:|---:|
| `CBCUS01C` | 11,346 | 11,419 | 11,346 | 99.4% |
| `CBACT01C` | 78,647 | 78,720 | 78,647 | 99.9% |
| `CBTRN02C` | 81,902 | 81,975 | 81,902 | 99.9% |
| `CBACT04C` | 74,230 | 74,303 | 74,230 | 99.9% |
| **Total** | **246,125** | **246,417** | **246,125** | |

`spec_critic` re-resolves each program from disk and rebuilds `spec_extractor`'s entire prompt
character for character. Calibrated against ADR-0015's measured `CBACT04C` extraction (74,230
chars → 36,320 input tokens, so **2.04 chars/token** — COBOL is dense), that is roughly **120,400
duplicated input tokens per run, about $0.60, or ~26% of the measured $2.31 four-program run**.

**None of that duplication is wasted *work*.** Two calls are architecturally required: ADR-0007
makes the critic an *independent* check, and a single call that narrates and then grades its own
narration is not independent. The critic genuinely needs the source, too — PR #20 established that
it caught three planted factual errors *only* by reading it, while the deterministic layer caught
none. What is wasteful is paying full input price twice for identical bytes within seconds.

**And the ordering made that impossible to fix.** Prompt caching is a prefix match from position 0.
The shapes were:

```
extractor:  [system_E][known_facts + source]
critic:     [system_C][spec.md][known_facts + source]
```

The shared span sat at the *end* of the critic prompt — a suffix, not a prefix. No cache
configuration could have acted on it.

### The measurement that removed half of this ADR's original scope

The plan was to pair the reorder with `cache_control` on the system block. Before implementing,
three live `claude` CLI calls (~$0.03 of real Haiku spend) checked whether that backend already
gets cache reads across separate subprocess invocations. `A` = the real `spec_critic` system
prompt, `B` = a byte-identical repeat, `C` = the larger `spec_extractor` prompt as a confound
control, since the CLI ships its own harness system prompt:

| Call | input | cache_write | cache_read | output |
|---|---:|---:|---:|---:|
| A `spec_critic` sys, 1st | 10 | 10,704 | 0 | 44 |
| B identical repeat | 10 | 4,506 | **6,318** | 84 |
| C `spec_extractor` sys | 10 | 5,599 | 5,441 | 59 |

Server-side caching **does** work across separate `claude -p` invocations — the cache is keyed on
content, not process. But the total prefix is ~10,700–11,050 tokens while our system prompts are
only ~900–1,100 tokens of it, putting **CLI harness overhead at ≈9,800 tokens per call**. That
independently corroborates ADR-0013's measured 9,819 cache-creation tokens with the default system
prompt replaced — the same figure reached from the opposite direction.

Across a nine-call design run the harness costs ≈88,200 tokens against ≈9,350 for *every system
prompt in this repo combined*: **the unavoidable overhead is ~9.4× larger than the entire target**,
and the CLI exposes no `cache_control` flag to act on regardless.

## Decision

**1. `build_critique_prompt` emits Known Facts, then wrapped source, then the narration under
review.** The stable span leads; the only part unique to this call trails. Verified as a genuine
prefix for all four Track C programs, not merely "present somewhere".

**2. `prompts/registry/spec_critic/v1_0_0.md` states the same order, and the two must stay in
step.** The prompt previously said *"You are given, in order: the narration (`spec.md`) to judge,
…"*. Reordering the payload without it would have left the prompt contradicting its own input —
worse than either order chosen consistently. The revised wording also makes the ordering
*meaningful* to the model rather than merely accurate: evidence first, claim last.

**3. No `cache_control` is set, and no caching is claimed.** The measurement above says it would
buy nothing on the default backend and cannot even be expressed there. This ADR only removes the
ordering as a blocker; whoever revisits caching starts from a prompt shaped to allow it.

**4. Cross-node caching (Tier 2) is deferred, with two alternatives rejected on the record.**
Sharing a prefix across `spec_extractor` and `spec_critic` requires a shared *system* block, which
means moving each node's registry instruction into the user turn's tail. That is worth ≈$0.52/run
and changes where instructions carry weight, so it needs the `CBACT04C` six-fact benchmark re-run
to prove no regression — not an assumption. **Rejected: putting the shared COBOL block in
`system`.** It would give clean caching, and the system role carries operator authority while
tenant COBOL is untrusted input — that is precisely the injection surface `core/guardrails.py`
exists for. **Rejected: trimming what the critic sees.** PR #20 makes that a correctness
regression, not an optimization.

## Consequences

**Ordering is now a contract, and is tested as one.** `in prompt` assertions pass under either
order, so the property would have regressed silently on the next edit to that f-string. Two tests
pin it: one asserts `known_facts < source < narration` and that the narration is the tail, the
other asserts the critic prompt literally `startswith` the extractor's real captured prompt and
that the shared span exceeds half the total.

**The measured saving from this change alone is zero, and that is the honest framing.** It changes
no token count today. Its value is that a ~$0.52/run optimization went from structurally impossible
to merely unproven, and the cost was one reordered string plus a prompt sentence.

**A prompt's content changed, and prompt versioning still does not exist.**
`prompts_registry_client/loader.py` hardcodes `v1_0_0`, so `v1_0_0.md` was edited in place rather
than superseded. This is the first genuine prompt-content change in the repo and it makes pillar 26
concrete: there is no mechanism to run an old and a new prompt side by side, so a future prompt
regression has no baseline to be caught against. Recorded as a deliberate deferral, not solved
here.

**The reorder itself is unbenchmarked.** Reordering user-turn content is lower risk than moving
instructions between roles, and the registry prompt now describes the new order explicitly, so the
model is not surprised by it. But no live run has confirmed the critic behaves identically.
`tests/system/test_critic_discrimination.py` is the cheap check that would — a real narration with
three planted errors, opt-in behind `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, roughly $0.10–0.35 on
Haiku. Stated as an open verification rather than quietly assumed.
