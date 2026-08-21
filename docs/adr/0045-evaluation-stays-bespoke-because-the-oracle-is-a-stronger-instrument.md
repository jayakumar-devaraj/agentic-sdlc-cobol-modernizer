# ADR-0045: Evaluation stays bespoke, because the differential is a stronger instrument than a score

## Status

**Accepted** (2026-08-21). Records a decision that was never made — `tests/evaluations/` was built
without an adopt-or-build comparison, and ADR-0024 has no *Options considered* section. Written now
under ADR-0044's rule that a default with a cost is still a decision.

## Context

Pillar 22 (*Quality Evaluation Frameworks*) has been 🟡 for fourteen audit revisions. It crossed to
✅ at R2.23 and was **withdrawn at R2.27**: the same judge, over the same corpus, with the same
prompt, scored **6 of 6 with a 0.00 false-positive rate** on one run and **4 of 6 with 0.50** on the
next. The criterion should have said *reproducibly*, and did not.

What exists is `tests/evaluations/` — six committed cases, four criteria each traceable to a defect
this platform actually produced, a judge pinned rather than routed (ADR-0024), and scoring shown to
discriminate before anything was billed. It measures **one sample of a non-deterministic
instrument**.

The obvious question, asked and never answered on the record: **should this be Ragas** (or DeepEval,
or promptfoo) instead of a hand-rolled harness?

## Decision

**The harness stays bespoke. The reproducibility defect is fixed inside it, by sampling.**

### Why not adopt a RAG-evaluation framework

**1. It answers a question this repo does not ask.** Ragas's differentiating metrics — faithfulness,
context precision, context recall, answer relevancy — score a retrieval pipeline: *question →
retrieved context → answer*. This repo's evaluation target is *"does model-authored Java reproduce
this COBOL's semantics?"* There is no retrieval in production at all: `tools/knowledge_store.py` has
**zero production callers**, by decision (ADR-0016). Adopting a RAG framework for a non-RAG question
means using it purely as a harness for custom LLM-judged metrics — which is the small part of it.

**2. This repo already holds a stronger instrument.** ADR-0029's differential compares the generated
program's *own output* against what the unmodified COBOL wrote: 500 of 500 transaction fields, 598
of 600 account fields. A judge score, however well calibrated, is weaker evidence for the same claim
than byte-comparable output. Making a similarity score the headline instrument would be a downgrade,
and this platform's characteristic failure (R2.7) is claiming more than the evidence carries.

**3. Part of the metric suite is unusable in this environment for a reason already recorded.**
Embedding-based metrics need an embedding vendor. ADR-0016 established that Anthropic offers none
and that no second-vendor credential exists here — the same blocker that keeps retrieval unwired. A
framework whose embedding-backed half cannot run is a dependency carrying its own dead weight.

### What is adopted instead — the part that was actually missing

**Sampling and aggregation.** A judge run becomes *n* runs, and the harness reports the distribution
rather than the sample: detection rate, false-positive rate, and the **variance across runs**. The
crossing criterion for pillar 22 is restated to say what R2.27 said it should have: a run clears the
bars **reproducibly**, not once.

This is a change of tens of lines inside an existing harness, against 3–5 days to adopt a framework
that would still need the same sampling added on top.

## Consequences

- **Pillar 22's blocker becomes addressable without a dependency.** It does not become *closed* —
  closing it needs real judge calls, which cost real money (~$0.94 measured for six Opus calls), and
  under ADR-0044 it needs a second instance besides `CBACT04C`'s corpus.
- **A cost accepted deliberately**: no dataset browser, no hosted run comparison, no ready-made
  metric library. If a future need is genuinely RAG-shaped — retrieval wired, contexts retrieved,
  groundedness in question — this ADR should be revisited rather than worked around. That trigger is
  written here so it is recognisable when it arrives.
- **`tests/evaluations/` stays a test suite rather than a service.** It runs where every other check
  runs, needs no account, and sends the tenant's COBOL nowhere — which is the same property
  ADR-0046 turns on for tracing.
- **This ADR does not claim Ragas is bad.** It claims the fit is wrong here, for reasons specific to
  this repo: no retrieval, no embedding credential, and an existing differential that outranks any
  score. A different repo with a real RAG path should reach the opposite conclusion.
