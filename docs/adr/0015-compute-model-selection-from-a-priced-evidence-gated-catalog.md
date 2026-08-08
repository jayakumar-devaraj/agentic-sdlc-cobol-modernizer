# ADR-0015: Compute model selection from a priced, evidence-gated catalog

## Status

Accepted (2026-08-08). Amends **ADR-0004** and **ADR-0014**, which stand in principle: this is
still deterministic lookup, not a runtime scoring engine.

## Context

ADR-0014 moved routing from `(node)` to `(node, tier)` but still named a model against each tier.
That left three things invisible, and a question that could not be answered from the repo at all:

1. **Price was nowhere in the codebase.** Choosing Opus over Sonnet is a decision about money, made
   without the numbers written anywhere a reviewer could check.
2. **"Good enough" was an assertion.** Nothing distinguished a model somebody had benchmarked for a
   node from one somebody had typed in.
3. **A price change required a human to notice.** Claude Sonnet 5's introductory rate expires
   2026-08-31. With model names hardcoded per tier, nothing re-evaluates anything when it does.

The prompting question was direct: *why Opus, can't we use a cheaper Sonnet — 4.6?* Answering it
properly required measurement, and the measurement produced a result strong enough to change the
design rather than just fill in a table.

### The benchmark that settled it

Four models, real `spec_extractor` run over `CBACT04C` (the hardest Track C program), scored on six
facts each independently verified against the source:

| Model | Facts | `fidelity_issues` | In tok | Out tok | Cost @ API rates |
|---|---:|---|---:|---:|---:|
| **Opus 5** | **6/6** | 0 | 36,320 | 13,000 | $0.507 |
| Sonnet 5 | 5/6 | 0 | 40,099 | 12,619 | $0.206 |
| Sonnet 4.6 | 5/6 | 0 | 30,246 | 11,652 | $0.266 |
| Haiku 4.5 | 4/6 | 0 | 23,867 | 7,996 | $0.064 |

**The missing sixth fact is not a gap — it is a confident false statement.** `CBACT04C`'s
`ELSE PERFORM 1050-UPDATE-ACCOUNT` is unreachable (`END-OF-FILE` only ever holds `'N'` inside the
loop), so the final account's accrued interest is never posted. Opus 5 identified it. Both Sonnets
narrated the dead branch as live:

> Sonnet 5: *"the main loop still performs one final `1050-UPDATE-ACCOUNT` call after EOF to flush
> the last account's accumulated interest"*
>
> Sonnet 4.6: *"the loop's outer `ELSE` branch fires on the next iteration"*

Sonnet 4.6 even names the mechanism it believes works, and has it backwards — the loop exits rather
than re-entering. This is a plausible-looking wrong answer about financial posting: exactly the
failure class `pic_mapper` exists to prevent for fields, now appearing in narration. Generated Java
would faithfully implement a wrong description.

Three further findings from the same run:

- **All four scored `fidelity_issues = 0`.** The deterministic layer cannot separate them at all —
  including the one that narrated dead code as live. Necessary, nowhere near sufficient.
- **Haiku also missed COMPUTE-without-`ROUNDED` truncation**, a second financial-precision fact.
- **The tokenizer difference is confirmed for COBOL**: Sonnet 4.6 consumed 30,246 input tokens
  where Sonnet 5 consumed 40,099 on the identical prompt — **+32.6%**, matching the documented ~30%.

## Decision

**1. `config/model_catalog.yaml` holds what each model costs, its capability rank, and — the part
that matters — `verified_for`: the nodes with real benchmark evidence behind them.**

**2. `config/model_routing.yaml` becomes policy and names no models.** A tier declares
`min_capability_rank`, `effort`, `max_output_tokens`, and a **measured** token profile.

**3. Selection is computed: the cheapest catalogued model that clears the tier's rank and is
`verified_for` the node.** Ranking uses the token profile, not list price — output is ~70% of a
real `spec_extractor` call and ~80% of a `spec_critic` call, so a model with cheap input and dear
output would otherwise look better than it is.

**4. `verified_for` is a hard gate with no fallback.** No eligible model raises. Adding a model to
the catalog does not make it eligible; running the benchmark does.

**5. Pinning is allowed for unbenchmarked nodes and must state a reason.** An unexplained pin is
indistinguishable from hardcoding a model to bypass the gate, so a missing `pin_reason` is a config
error. Pinned decisions are labelled `selection: "pinned"` in the result, so "nobody benchmarked
this" reaches the review gate rather than looking like a considered choice.

**This is not the runtime scoring engine ADR-0004 rejected, and the distinction is load-bearing.**
Selection is a pure function of checked-in data: same catalog plus same policy always yields the
same model, so `design.json` still answers "which model produced this?" and tests stay
reproducible. What is dynamic is that the *inputs* are data — edit a price or land a benchmark and
every qualifying node re-selects on the next invocation, with no code change and no hand-editing of
fifteen entries.

## Consequences

**Sonnet 4.6 is answered as data, not prose.** It is `verified_for: []` because it failed the
extraction benchmark, and it is dominated on price by Sonnet 5 today. A test asserts both, so the
conclusion survives someone later reading only the price column.

**The gate immediately withdrew a saving this repo had already claimed.** ADR-0014 routed
`CBCUS01C` to Haiku on the reasoning that a small program is an easy task. Haiku had never been
benchmarked on extraction; when it was, it scored 4/6. No model except Opus 5 is verified for
`spec_extractor`, so **every tier now resolves to Opus** and `CBCUS01C` costs more than it did
yesterday. That is the mechanism working: the previous saving was an assumption wearing a
config entry, and the honest response to discovering that is to pay for it until it is measured.

**Restoring it is a benchmark, not a code change.** Running the extractor benchmark on a *simple*
program (`CBCUS01C`) would establish whether a cheaper model suffices there; if it does, adding
`spec_extractor` to that model's `verified_for` re-selects it with no code touched. That is the
concrete next experiment, and it is deliberately not assumed here.

**`spec_critic` demonstrates the payoff.** Haiku is selected over Opus with the rationale naming
the beaten alternative and its price (`$0.0940` vs `$0.4700` at the complex tier) — a 5× saving on
that node, backed by a benchmark rather than an assumption.

**Cost estimates ignore prompt caching.** Real cache reads run at ~0.1× input price, but cache
state depends on invocation order and what ran recently. A selection function whose answer changed
with cache state would not be reproducible, and overstating every candidate by the same factor does
not change their ranking.

**`capability_rank` is coarse and not a quality score.** Adjacent ranks claim no measurable
difference; it exists so a tier can say "at least Sonnet-class". The evidence is `verified_for`.

**Prices go stale, and the catalog can only make that visible, not prevent it.** Every entry
requires a `price_note` naming when it was checked, and Sonnet 5's records the 2026-08-31 expiry.
Nothing enforces a re-check — a date-triggered failing test would be a time bomb — so this remains
a real operational gap, honestly labelled rather than automated away.

**`solution_architect` is pinned and unbenchmarked.** It is the only producer of `unified_design`
and reasons across every program at once, so it is pinned to the strongest verified model rather
than cost-ranked. Its benchmark is genuinely harder to design than the extractor's — there is no
golden unified design to score against — which is why it is named as an open gap here instead of
being quietly cost-optimized.
