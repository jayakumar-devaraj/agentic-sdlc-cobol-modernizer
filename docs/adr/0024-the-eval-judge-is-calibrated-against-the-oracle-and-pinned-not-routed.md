# ADR-0024: The eval judge is calibrated against the oracle, and pinned rather than routed

## Status

Accepted (2026-08-11). Delivers step 44 — `tests/evaluations/` had been a single 0-byte
`__init__.py` for 47 PRs.

Depends on [ADR-0021](0021-a-hand-computed-oracle-for-the-interest-equivalence-test.md), whose
stated ceiling is the entire reason this harness exists, and shares its posture with
[ADR-0015](0015-compute-model-selection-from-a-priced-evidence-gated-catalog.md): a model is
eligible for a job because it was measured doing it, never because it is plausible.

## Context

Everything this repo knows about the quality of model-authored Java was **spot-measured**. A real
call was made, a human read the output, and a number went into an ADR or the verification report:
the interest body scored 10 of 10, `spec_critic`'s tier was settled at 3 of 3, the write-transaction
body compiled on attempt 1. All of it is real evidence, and none of it re-runs. The session before
this one changed generator prompts five times and re-scored nothing.

That is a gap with a specific shape. `pillar 22` rests on it, and the pillar's own note says so:
step 45 evaluates **one `COMPUTE` of one program**.

### The ceiling that makes a judge necessary

ADR-0021 chose a hand-computed oracle and wrote down what it costs: *"it can only ever test
arithmetic someone already understood"*, over one paragraph. Nine literals, derived by hand, for
`1300-COMPUTE-INTEREST`. There is no version of that artifact for the other forty-three programs in
the corpus — hand-deriving expected values does not scale, and option (a), a real COBOL runtime, is
still gated behind the four triggers ADR-0021 names.

So the question this ADR answers is not *"should quality be evaluated"* but **"what can evaluate a
translation where no oracle can be written?"**

### Why an LLM-as-judge is the obvious answer and the dangerous one

A judge model can read a paragraph of COBOL and the Java claiming to implement it, and say whether
they agree. It costs cents, it needs no toolchain, and it generalises to any program.

It is also the canonical way to build a check that cannot fail. Give a judge a vague rubric and
output from its own model family and everything scores well, permanently and cheerfully. This
platform has produced four such checks in a week (audit R2.8) — a helper written, tested and never
called; a pipeline that never rendered the records it depended on; a composite no test ever
constructed; a dataset whose every expected answer was zero. A judge harness would be the fifth and
the best disguised, because its output is a percentage and percentages read as measurement.

## Decision

**Calibrate the judge against the oracle, on the one paragraph where both exist. Pin the judge model
in the harness rather than routing it. Report false positives beside detections, and never blend the
two grounds.**

### 1. The corpus is graded by things that are not opinions

Six committed cases in `tests/evaluations/corpus.py`, each carrying *how its expected verdict is
known*:

- **`ORACLE` (3 cases).** The exact bodies `tests/system/test_interest_equivalence.py` compiles and
  runs through real Maven against ADR-0021's literals. `interest_rounds` fails rows R1, R2, R5–R8;
  `interest_unguarded` fails R10; `interest_faithful` passes all ten. **A JVM decided these, not a
  reading.** They are *imported* from that module rather than copied, so the two cannot drift, and a
  test asserts the cited test functions still exist.
- **`SOURCE` (3 cases).** Real defects with no oracle — G28's `PIC X(50)` written as `""`, G26's
  unreachable `TRAN-ID` filled with a fabricated value — checkable against the copybook by line.

Every criterion in the rubric is a defect this platform actually produced. None was invented to be
easy, which is step 43's discipline applied to semantics instead of syntax.

### 2. The judge is measured where the answer is already known

The benchmark's one hard bar: **every `ORACLE`-grounded defect must be caught.** The reasoning is
the whole argument for the harness, run backwards — a judge is proposed *because* the oracle cannot
be extended to other programs, so a judge that misses a defect **where an oracle exists to check it**
has no claim on the programs where none does. That threshold is derived from what the judge is for,
not chosen to be passable.

`SOURCE`-grounded cases are **reported and not asserted on**. Making them a bar would promote this
repo's reading of the COBOL to ground truth, which is exactly ADR-0021's refused option (c) wearing
a different hat.

### 3. The judge model is pinned here, not routed

Every other model call in this repo resolves through `core/model_routing.py`. That is correct for a
pipeline node and wrong for a measuring instrument. A judge that follows the routing config is a
yardstick that changes length whenever someone retunes an unrelated node — and the resulting score
movement is indistinguishable from a real change in generator quality, which is the one thing the
harness exists to detect. `JUDGE_MODEL` is a module constant, and changing it is an edit a reviewer
sees in a diff.

### 4. Detection rate is not the metric on its own

A judge that fails every criterion of every case detects all four defects: a detection rate of 1.0,
and worthless. So `false_positive_rate` is reported beside it, and § 4b of the feasibility assessment
is why it matters more than it looks — human review runs three to four orders of magnitude above
inference cost, so every spurious flag routes a body to the dominant cost centre. *The efficiency
metric to design against is the fraction of generated output that requires human review*, and a
judge's false-positive rate is that number directly.

## Consequences

**Good.** Pillar 22 gains a standing evaluator rather than a series of dated spot-measurements, and
G9 closes. The harness runs at zero cost in CI — the model call is behind an injectable seam, so 65
tests exercise the prompt, the response contract and the scoring without spending anything. It was
shown to discriminate before anything was billed, the way step 45's `divideRounded` body was: a
scripted perfect judge scores 1.0/0.0, one that passes everything scores 0.0 detection, one that
fails everything scores 1.0 detection **and** 1.0 false positives. Three mutations of the harness
itself — leaking the case name into the prompt, making the rubric case-dependent, never reporting a
false positive — each fail the test that exists for them.

**The judge's own fitness is unmeasured, and `verified_for` does not cover it.** ADR-0015 makes
`verified_for` a hard gate: a model is eligible for a node because a benchmark says so. No model is
verified for judging, and pinning rather than routing means this harness **bypasses that gate
mechanically**. Stated plainly rather than left implicit, because the gate is load-bearing elsewhere.
The justification is that the gate cannot apply to the instrument that produces the evidence it
requires — that is circular — and the first billed run of this benchmark is precisely the measurement
that would earn a listing. Until it happens, the judge is an unvalidated instrument and the harness
says so in its own module docstring.

**Not yet run.** As of this commit the benchmark has never made a real call. What is verified is the
harness; what is not is any claim about how well a real judge scores. `docs/qa/verification-report.md`
records that as an absence rather than implying otherwise, and the first run either clears the bars
above or is a finding.

**A cheaper judge is an open question, not a decision.** `spec_critic` earned its Haiku pin by being
benchmarked against Opus on the same corrupted narration and matching it at 2.3× lower cost. The same
comparison is exactly what this harness makes possible for judging, and it has not been spent. The
Opus 5 pin is scaffolding that says it is scaffolding.

**Six cases is a thin corpus, and two faithful ones is thinner.** The false-positive floor rules out
a judge that flags everything; it does not measure a rate. Widening the corpus needs more bodies
whose verdict is known for a reason — which realistically means more real generate runs, since every
case here came from one.

**What this does not do.** The round-trip metric does not move. `0 of 4` needs COBOL → compiling
Java → passing differential test, and an evaluation harness scores translations rather than producing
them. This is an instrument, not a capability.
