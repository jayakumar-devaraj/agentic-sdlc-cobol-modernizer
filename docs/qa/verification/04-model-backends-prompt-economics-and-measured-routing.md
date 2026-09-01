# Model backends, prompt economics, and measured routing

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### Prompt duplication between `spec_extractor` and `spec_critic` (ADR-0017)

**Verified**: a harness captured the real user-turn prompt of every call in a four-program run
(fakes in place of models, no spend), rather than reasoning about prompt sizes from the source.

**What it found.** `spec_critic` re-resolves each program from disk and rebuilds `spec_extractor`'s
entire prompt character for character:

| Program | Extractor | Critic | Byte-identical | Dup |
|---|---:|---:|---:|---:|
| `CBCUS01C` | 11,346 | 11,419 | 11,346 | 99.4% |
| `CBACT01C` | 78,647 | 78,720 | 78,647 | 99.9% |
| `CBTRN02C` | 81,902 | 81,975 | 81,902 | 99.9% |
| `CBACT04C` | 74,230 | 74,303 | 74,230 | 99.9% |
| **Total** | **246,125** | **246,417** | **246,125** | |

Calibrated against ADR-0015's measured `CBACT04C` extraction (74,230 chars → 36,320 input tokens =
**2.04 chars/token**), that is ≈120,400 duplicated input tokens per run — ≈$0.60, or **~26% of the
measured $2.31 four-program run**. Two framings the measurement ruled out, both checked rather than
assumed: **no multi-turn history exists anywhere** (every call is stateless and single-turn, the
graph passes typed objects not transcripts), and **nothing is near a context limit** (largest
prompt ≈40k tokens against 1M).

**The fix verified here is the ordering, not the caching.** Before ADR-0017 the shared span was a
*suffix* of the critic prompt, which no prefix-matched cache could act on. After the reorder,
confirmed directly for all four programs:

```
CBCUS01C   prefix=True  suffix=False  shared= 11,346/ 11,428 = 99.3%
CBACT01C   prefix=True  suffix=False  shared= 78,647/ 78,729 = 99.9%
CBTRN02C   prefix=True  suffix=False  shared= 81,902/ 81,984 = 99.9%
CBACT04C   prefix=True  suffix=False  shared= 74,230/ 74,312 = 99.9%
```

Pinned by two tests, because `in prompt` assertions pass under either order and the property would
otherwise regress silently: one asserts `Known Facts < source < narration` and that the narration
is the tail, the other asserts the critic prompt literally `startswith` the extractor's captured
real prompt and that the shared span exceeds half the total.

**Command**: `pytest tests/unit/test_spec_critic.py -v`
**Result**: 23/23 passed (21 pre-existing + 2 new ordering tests).

**Open, deliberately not claimed**: the reorder itself is unbenchmarked against a live model. The
registry prompt now states the new order explicitly, so the model is not surprised by it, but no
live run confirms identical critic behaviour. `test_critic_discrimination.py` (a real narration
with three planted errors, opt-in, ~$0.10–0.35 on Haiku) is the cheap check that would.

### `claude` CLI prompt-cache behaviour, and why ADR-0017 dropped its caching half

**Verified**: three live `claude` CLI calls (~$0.03 of real Haiku spend) run *before* implementing
`cache_control`, to test whether that backend already reuses a cached prefix across separate
subprocess invocations. `A` = the real `spec_critic` system prompt, `B` = a byte-identical repeat,
`C` = the larger `spec_extractor` prompt as a confound control — the CLI ships its own harness
system prompt, so a constant read on `B` would be the harness caching itself rather than ours.

| Call | input | cache_write | cache_read | output |
|---|---:|---:|---:|---:|
| A `spec_critic` sys, 1st | 10 | 10,704 | 0 | 44 |
| B identical repeat | 10 | 4,506 | **6,318** | 84 |
| C `spec_extractor` sys | 10 | 5,599 | 5,441 | 59 |

- **Caching works across separate `claude -p` invocations** — `cache_read` goes 0 → 6,318. Each
  call is a fresh subprocess; the cache is keyed on content, not process.
- **Reuse is partial**: a byte-identical repeat reused ~59% of the prefix and re-wrote 4,506
  tokens, so something volatile sits inside the CLI's own prefix. n=1 per condition — recorded as
  suggestive, not established.
- **The decisive figure**: total prefix ≈10,700–11,050 tokens against system prompts of only
  ~900–1,100 tokens, putting **CLI harness overhead at ≈9,800 tokens per call**. This
  independently corroborates ADR-0013's measured **9,819** cache-creation tokens with the default
  system prompt replaced — the same number reached from the opposite direction.

**Consequence**: across a nine-call design run the harness costs ≈88,200 tokens versus ≈9,350 for
every system prompt in the repo combined — **~9.4× larger than the entire target** — and the CLI
exposes no `cache_control` flag regardless. The planned caching work was **dropped on this
measurement** rather than implemented and then found worthless.

**Command**: three `call_model(..., backend="claude_cli")` calls on `claude-haiku-4-5`, usage read
from `ModelCallResult` (instrumentation ADR-0013 already provides).
**Result**: as tabulated above.

### `core/model_client.py` — the real `claude` CLI, and the retry policy (ADR-0013)

**Verified**: every claim ADR-0013 makes about the CLI backend was measured against the real
`claude` CLI (2.1.212) *before* the module was written, not asserted from documentation.

- **A subscription really is enough — no API credential.** `claude -p --output-format json`
  returned a result with `ANTHROPIC_API_KEY` unset. This is what closes the long-standing "no
  credential in this environment" blocker.
- **A 40 KB payload over stdin works.** Confirms the argv path would have been wrong: a real
  `CBACT04C` prompt is tens of kilobytes, past the ~32 KB Windows command-line limit.
- **`--system-prompt-file` is genuinely applied.** Passing
  `prompts/registry/spec_critic/v1_0_0.md` made the model answer *as* `spec_critic`, asking for
  the narration and Known Facts it expects — the prompt was in force, not ignored.
- **Per-call harness overhead, measured**: 19,549 cache-creation tokens with the default system
  prompt, 9,819 with it replaced ($0.0399 → $0.0207 notional). Recorded because it is a real cost
  of this backend and a genuine reason to prefer the SDK at production volume.
- **A live round-trip through `call_model` passes** — `test_live_claude_cli_round_trip`, run
  explicitly with `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`. Every other test in that module is a
  fake wearing the CLI's shape; only this one proves the shape is right.

**Retry policy verified by call count, not by hope**: retryable statuses (429/500/503), timeouts,
and SDK connection failures each retry and then succeed; a 4xx is attempted exactly once; the loop
stops at `MAX_TRANSPORT_ATTEMPTS` rather than running forever. Backoff is confirmed bounded by the cap and
actually jittered (30 samples, all distinct values, each equal to what was really slept).

**A real accident, recorded rather than quietly fixed.** Changing the default backend to
`claude_cli` did **not** fail `tests/unit/test_cli_design.py`. Those tests fake
`anthropic.Anthropic`, which is no longer the default path, so they silently began spawning real
`claude` subprocesses against a live subscription and the suite hung. A test that quietly costs
money and calls a live model is worse than one that fails. `tests/conftest.py` now pins the backend
for the whole suite, and any test wanting the real CLI must declare a `live_claude_cli` marker
*and* opt in by environment. The general lesson: **when a default changes, every fake positioned at
the old default becomes a silent pass-through**, and a green suite will not say so.

**Side effect worth noting**: `spec_extractor`, `spec_critic`, and `solution_architect` all reach
100% module coverage for the first time. Their `_default_*` bodies used to be the untested live-API
calls; they are now one-liners delegating to `call_model`, which the SDK-backend tests exercise.

**Command**: `pytest tests/integration/test_model_client.py -v`
**Result**: 25 passed, 1 skipped (the live test, unless opted in); with the opt-in, 26 passed.

### Bounded fan-out — the concurrency cap (plan pillar 25)

**Verified**: `MAX_CONCURRENT_PROGRAMS` (default 4) is actually enforced, not merely configured.
The test runs 8 branches against a cap of 2 and asserts **peak observed concurrency**, because a
cap that is defined but never passed to `invoke()` would still satisfy a constant check — that
being the exact bug worth catching. Confirmed falsifiable: removing the `config={"max_concurrency":
...}` argument makes all 8 branches run at once and the test fails naming that count.

**Command**: `pytest tests/unit/test_design_graph.py -v`
**Result**: 12/12 passed.

### Complexity-based routing — measured, not assumed (ADR-0014)

**Verified**: the bands were set from real measurements of all four Track C programs taken by
running the real pipeline, *before* the thresholds were chosen — not chosen first and justified
after.

| Program | Prompt chars | Paragraphs | Tier | Model (was) |
|---|---:|---:|---|---|
| `CBCUS01C` | 11,346 | 5 | `simple` | `claude-haiku-4-5` (was `claude-opus-5`) |
| `CBACT04C` | 74,230 | 22 | `complex` | `claude-opus-5` (unchanged) |
| `CBACT01C` | 78,647 | 16 | `complex` | `claude-opus-5` (unchanged) |
| `CBTRN02C` | 81,902 | 26 | `complex` | `claude-opus-5` (unchanged) |

- **The tiering works end to end through the real CLI, not just in unit isolation.**
  `test_complexity_routes_the_two_programs_to_different_models` runs `cobol-modernizer design`
  over `CBCUS01C` + `CBACT01C` and asserts the two extractor calls really reached the API with
  *different* models and efforts (`claude-haiku-4-5`/`low` and `claude-opus-5`/`high`). If tiering
  silently stopped working, both would resolve to the same entry and that test is what notices.
- **Bands are not borderline.** `CBCUS01C` measures 11,346 against a 25,000 ceiling; the next
  smallest program measures 74,230 against a 60,000 floor. `test_complexity.py` asserts that
  headroom directly, so a future threshold tweak that puts a real program on a knife edge fails
  here rather than in production.
- **Classification provably makes no model call.** The test that measures `CBTRN02C` replaces
  `model_client.call_model` with a function that fails the test if invoked — so a regression to
  "probe a model to pick a model" is caught rather than showing up as a quiet bill increase.
- **The architect takes the highest tier present**, asserted directly: a run containing one
  complex program routes the architect to `complex` even though half its programs are simple.
- **Config validation covers the whole file, not just the entry requested** — asserted by putting
  an invalid `effort` in a tier the test never asks for and confirming the lookup still fails. A
  typo in a band nothing hits today would otherwise lie in wait for whichever program first lands
  there.

**A latent truncation bug this surfaced, now fixed and pinned by a test**: `max_output_tokens` was
hardcoded at 4096, while every real response measured runs 5,485–23,366 output tokens. The SDK
backend would have truncated all of them (silently mid-narration, or as a hard parse error
mid-JSON); the CLI backend never sent the value, which is the only reason nothing had failed yet.
Per-tier ceilings now sit above observed output, and
`test_real_config_ceilings_clear_every_observed_output_length` asserts that against the measured
figures.

**Command**: `pytest tests/unit/test_complexity.py tests/unit/test_model_routing.py tests/unit/test_cli_design.py -v`
**Result**: 71/71 passed.

### `spec_critic` — does it actually catch a wrong narration, and is the cheap model enough?

**Verified** — the question ADR-0004 flagged at Milestone C2 and deferred, now answered with real
model runs rather than reasoning.

A genuine `CBCUS01C` narration (from the live run, checked in at
`tests/fixtures/narrations/CBCUS01C/spec.md`) was corrupted with three factual errors, each
checkable against the source by line number:

| Corruption | Source says |
|---|---|
| `APPL-EOF` = 16 → **99** | line 63: `88 APPL-EOF VALUE 16.` |
| abend code 999 → **16** | line 157: `MOVE 999 TO ABCODE` |
| status `'10'` is clean EOF → **fatal** | lines 98/107–108 |

Every paragraph name, field name, and Known-Facts row was left intact, so the deterministic checks
cannot see these — asserted, not assumed: `compute_fidelity_issues` returns `[]` on the corrupted
narration, which makes the model the only line of defence.

| Model | Rules | Min score | Below 0.7 | Notional cost |
|---|---:|---:|---:|---:|
| `claude-haiku-4-5` | 12 | **0.00** | **3** | $0.1058 |
| `claude-opus-5` | 28 | 0.15 | **3** | $0.2388 |

**Both caught all three.** Haiku was more decisive on the `APPL-EOF` constant (0.00 vs 0.30) at
2.3× lower cost, so the cheap tier is not a compromise for this node — ADR-0004's deferred question
is closed in favour of keeping Haiku. It also establishes the critic is **load-bearing rather than
decorative**: the deterministic layer caught none of the three.

**This calibrates `LOW_CONFIDENCE_THRESHOLD` too** (ADR-0008 shipped `0.7` as an admitted guess).
Genuine narrations score 0.70–1.00 at minimum; a corrupted one scores 0.00–0.40. The threshold
separates them with margin. **An earlier reading was wrong** and is corrected in ADR-0008: a 0.70
minimum failing to flag looked like the threshold being too permissive, but that was a borderline
claim about a timestamp format, not a missed defect.

**A real limitation in the verification technique, found by this work.** The `faithful_narrate`
approach used across the suite — feeding the Known Facts block back as the narration — produces a
prompt a live critic rejects outright: *"I don't see the `spec.md` narration file in your
message."* It is right to; that prompt contains the same block twice under two labels. The
technique validates the deterministic machinery correctly, but every test using it also injects a
*fake* critic, so nothing exercised the combination. Hence the checked-in real narration, which is
explicitly **not** a golden fixture (`tests/fixtures/narrations/README.md`) — it is unreviewed
model output, valuable because it is what the pipeline really produces.

**Two measurements that came out negative, recorded rather than dropped:**

- **`--effort` does not measurably control the critic's cost on the CLI backend.** Six runs
  (low/medium/high × 2) on identical input: `medium` spanned 11,530→25,721 output tokens (2.2×),
  and `low` averaged *more* than `high`. Within-level variance exceeds between-level difference; at
  n=2 there is no signal. No cost saving is claimed from effort tuning.
- **The critic is not reproducible run-to-run.** The same input produced **11 to 20** scored rules
  across runs. For what ADR-0001 calls "the only independent check on extraction quality", that is
  a real property to know; it does not affect the pass/fail behaviour measured above (all runs
  flagged all three defects) but it means rule counts are not a stable metric.
- **An earlier hypothesis was disproved.** The critic's output was assumed to be verbose prose
  (~1,200 tokens per scored rule). Measuring the real output: the JSON is 1,535–2,028 tokens with
  rationales averaging 196 characters — already tight. **~90% of reported output tokens are
  thinking, not answer.** The planned "tighten the critic prompt" work was cancelled as addressing
  the wrong 10%.

**Command**: `pytest tests/unit/test_critic_discrimination.py -v` (add
`COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1` for the billed half)
**Result**: 4 passed, 3 skipped without the opt-in; 7 passed with it.

### `spec_extractor` — can a cheaper model do this job? (ADR-0015)

**Verified**: four models run through the real extractor over `CBACT04C`, the hardest Track C
program, each narration scored against six facts independently verified against the source.

| Model | Facts | `fidelity_issues` | In tok | Out tok | Cost @ API rates |
|---|---:|---|---:|---:|---:|
| **`claude-opus-5`** | **6/6** | 0 | 36,320 | 13,000 | $0.507 |
| `claude-sonnet-5` | 5/6 | 0 | 40,099 | 12,619 | $0.206 |
| `claude-sonnet-4-6` | 5/6 | 0 | 30,246 | 11,652 | $0.266 |
| `claude-haiku-4-5` | 4/6 | 0 | 23,867 | 7,996 | $0.064 |

**The missing sixth fact is a confident false statement, not an omission.** `CBACT04C`'s
`ELSE PERFORM 1050-UPDATE-ACCOUNT` is unreachable, so the last account's accrued interest is never
posted. Opus 5 identified it; both Sonnets asserted the opposite — Sonnet 5: *"the main loop still
performs one final `1050-UPDATE-ACCOUNT` call after EOF to flush the last account's accumulated
interest"*; Sonnet 4.6: *"the loop's outer `ELSE` branch fires on the next iteration"*, naming a
mechanism that is backwards. Haiku additionally missed COMPUTE-without-`ROUNDED` truncation.

Three findings beyond the ranking:

- **All four scored `fidelity_issues = 0`.** The deterministic layer cannot distinguish them at
  all, including the one narrating dead code as live. That is the sharpest available statement
  that those checks are necessary and nowhere near sufficient.
- **The Sonnet-5 tokenizer difference is confirmed for COBOL**: 30,246 input tokens (Sonnet 4.6)
  vs 40,099 (Sonnet 5) on the byte-identical prompt — **+32.6%**, matching the documented ~30%.
  So Sonnet 4.6's lower token count does not make it cheaper per unit of work while Sonnet 5's
  introductory rate holds; it becomes cheaper only after 2026-08-31.
- **Detection was by substring and therefore under-reports.** Every narration was written to disk
  and the contested claims read directly — the Sonnet quotes above were confirmed by reading, not
  inferred from a missing keyword.

**This withdrew a saving the repo had already claimed.** ADR-0014 routed `CBCUS01C` to Haiku on the
reasoning that a small program is an easy task; Haiku had never been benchmarked on extraction, and
scored 4/6 when it was. With `verified_for` enforced, **every `spec_extractor` tier now resolves to
Opus 5** and `CBCUS01C` costs more than before. Restoring the saving is a benchmark on a *simple*
program, not a code change.

**Command**: `pytest tests/unit/test_model_catalog.py tests/unit/test_model_routing.py -v`
**Result**: 73/73 passed.

### CI itself — verified on GitHub, not just locally

Every module above was also verified green on a **real GitHub Actions run**, not assumed from a
local pass, for every PR merged so far (PRs #1–#4): lint, coverage-floor-gated test suite, and the
mermaid-diagram-parse check reused from `agentic-sdlc-control-plane`.

**Command**: `gh run list --repo jayakumar-devaraj/agentic-sdlc-cobol-modernizer --limit 1`
**Result**: `completed success` on every merge to date.
