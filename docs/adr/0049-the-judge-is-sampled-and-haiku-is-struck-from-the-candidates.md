# ADR-0049: The judge is sampled across runs, and Haiku 4.5 is struck from the candidates

## Status

**Accepted** (2026-08-23). Implements the sampling [ADR-0045](0045-evaluation-stays-bespoke-because-the-oracle-is-a-stronger-instrument.md)
decided, and settles the open question [ADR-0024](0024-the-eval-judge-is-calibrated-against-the-oracle-and-pinned-not-routed.md)
left standing: whether the Opus pin was justified or merely convenient.

## Context

Pillar 22 has been 🟡 for fifteen audit revisions. It crossed to ✅ at R2.23 and was **withdrawn at
R2.27**: the same judge, over the same corpus, with the same prompt, scored **6 of 6 with a 0.00
false-positive rate** on one run and **4 of 6 with 0.50** on the next. Neither run was wrong. The
*summary* was, because one sample of a non-deterministic instrument cannot say how much of what it
reports is the instrument moving.

ADR-0045 decided the fix — *n*-run sampling with reported variance inside the existing harness — and
explicitly did not claim it closed anything, because closing needs real judge calls.

This record is what those calls found. **Three runs per candidate, seven cases each, both
candidates: ~$4.15 of real spend.**

*(The corpus is seven cases, not the six the README and several docstrings claimed. The count went
stale when a case was added and nothing recomputed it; corrected in the same change. It is also why
each candidate shows 21 calls rather than 18 — no retries, just a miscounted corpus.)*

## Decision

### 1. A judge run is *n* runs, and the bars apply to every one of them

`SampledBenchmark` reports each metric as a `Spread` — mean, standard deviation, min and max — and
the benchmark asserts its bars **per run rather than on the mean**. A judge that catches every defect
in two runs of three has a mean of 0.67 and an eligibility of none: the run that matters is the one
nobody is watching.

`Spread.is_constant` requires more than one sample, so `n=1` can never report as reproducible. That
is the original defect, closed at the type level rather than by convention.

### 2. Reproducible and eligible are kept separate

A judge that passes everything is perfectly reproducible and completely useless. `is_reproducible`
answers *"can these numbers be trusted?"*; the detection and false-positive bars answer *"are they
good enough?"*. Folding them together gives a metric that cannot tell a noisy judge from a bad one —
and that is precisely the pair R2.27 could not separate.

### 3. A malformed response is recorded, not raised

`judge_case` still raises on a response that breaks the contract, and a test pins that it does:
outside a candidate comparison there is one judge, and a broken answer from it is a broken run.

The *benchmark* uses `attempt_case`, which records it. The first billed run showed why — Haiku broke
the contract on its **first call**, the exception aborted the module-scoped fixture, and seventeen
already-paid-for calls were discarded along with the finding. "This candidate cannot answer" is the
most decisive thing a candidate comparison can discover, and a harness that turns it into a stack
trace is the wrong instrument.

Malformed responses are barred **before any rate is read**, because every rate is computed over the
cases the judge answered: a candidate that fails its hardest cases and answers the rest correctly
would otherwise report a *better* detection rate than one that answered them all and got a single
verdict wrong.

### 4. `claude-haiku-4-5-20251001` is struck from `CANDIDATE_JUDGES`

Ineligible on three independent grounds, measured over three runs:

| | `claude-opus-5` | `claude-haiku-4-5` |
|---|---|---|
| responses holding the contract | **21 of 21** | **16 of 21** |
| oracle-grounded detection | 1.00 ± 0.00 | 1.00 ± 0.00 † |
| source-grounded detection | 0.67 ± 0.00 | 1.00 ± 0.00 † |
| false-positive rate | **0.00 ± 0.00** | **0.33 ± 0.29** (max 0.50) |
| reproducible | **yes** | **no** |
| output tokens | 15,981 | **259,591** |
| notional cost | $2.18 | $1.93 |

† over the subset it answered, and therefore not a measurement of this candidate.

**It was not even cheaper.** Sixteen times the output tokens for the same work put it within 12% of
Opus's price. The cost case that justified Haiku for `spec_critic` — matched detection at 2.3× lower
cost — is simply not present here, which is why the two decisions differ rather than one of them
being inconsistent.

Struck rather than reported, following the precedent the list's own comment cites: both Sonnets were
removed from `spec_extractor` the same way, with the evidence written down. A benchmark that merely
printed scores would let an ineligible model drift into use.

## Consequences

**The Opus pin is now measured rather than assumed.** ADR-0024 called it scaffolding pending a
comparison; the comparison ran and the pin survived it. That question is closed.

**Sampling revealed stability; it did not create it, and the distinction matters.** `README.md`
recorded Opus's false-positive rate as *"not stable — 0.00 on one run and 0.50 on another"*, and this
run measured 0.00 ± 0.00. That is not sampling disagreeing with the earlier finding: those runs
predate the `impure_criteria` correction, which was made *because* the judge's disagreements twice
turned out to be right about the code. Before it, a criterion a case genuinely violated counted
against the judge as a false positive. So the earlier instability was substantially the **corpus
mislabelling**, since fixed.

What this run establishes is therefore narrower than *"sampling fixed the judge"*: with the corrected
corpus, the pinned judge's reported rates repeat across three runs. Sampling is what makes that a
statement anyone can check rather than a hope, which is all ADR-0045 claimed for it.

**Pillar 22's reproducibility defect is fixed and measured — and the pillar does not close.**
ADR-0044's rule requires a second instance, and every case in this corpus is derived from
`CBACT04C`. What is established is narrower and worth stating exactly: *the pinned judge scores this
corpus identically across three runs.* A second corpus, grounded in `CBTRN02C`, is what the pillar is
now waiting on — and that program is fully understood as of ADR-0048, so building one is tractable
in a way it was not before.

**Verdict churn is recorded and deliberately not barred.** Reading the run rather than its summary
showed Opus returning `1.00 / 0.67 / 0.00` on all three runs while its raw verdicts moved:
`fixed_width_text` flagged on `interest_rounds` in run 1, not in run 2, and on `interest_faithful`
in run 3. The scoring absorbed all of it, because a case is correct as long as its defect was caught
and a criterion a case genuinely violates is excluded from its false positives.

So *"the numbers repeat"* and *"the judge says the same thing"* are different claims. Barring on the
second would fail a judge whose reported numbers are perfectly stable, which is the wrong trade — but
churn is what crosses a threshold later and moves a rate, and it is the most plausible mechanism
behind R2.23 becoming R2.27. Leaving it unmeasured is how that stayed a surprise.

**A cost overrun, stated rather than absorbed.** This was estimated at ~$3 and cost **~$4.15**. The
overrun is entirely Haiku's verbosity — 259,591 output tokens where the estimate assumed per-call
costs similar to Opus's. An evaluation harness whose own price is mis-estimated by 38% is a small
irony in a repo whose § 4b argument is about cost per unit of review, and the number is recorded here
so the next estimate starts from a measurement.

**One candidate left means the benchmark no longer compares.** That is honest rather than ideal: it
now measures whether the pinned judge is reproducible, which is what pillar 22 needs, and a future
candidate is added to the list and faces the same bars.
