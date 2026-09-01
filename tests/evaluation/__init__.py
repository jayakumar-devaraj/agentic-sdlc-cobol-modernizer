"""Step 44 -- the standing evaluator for model-authored method bodies.

Everything this repo has measured about model output so far was **spot-measured**: a real call was
made, a human read the answer, and a number went into an ADR or the verification report. That is how
the interest body came to be scored 10 of 10 and how `spec_critic`'s tier was settled. It is real
evidence and it does not re-run. Prompts changed five times in the session before this package
existed, and nothing re-scored anything.

**What this package is.** A committed corpus of translated bodies, a rubric of named criteria, and a
judge model that scores one against the other -- so that "the generator still writes faithful Java"
becomes a number that can be recomputed after a prompt edit or a model swap, rather than a claim
whose evidence has a date on it.

**The one property that keeps it from being decoration.** An LLM-as-judge harness is the canonical
way to build a check that cannot fail: give a judge a vague rubric and its own family's output, and
everything scores well forever. This platform has produced four such checks already (audit R2.8), so
the corpus is built the other way round -- **every unfaithful case is a body already known to be
wrong, and known for a reason recorded in the case itself.** Two of them are wrong by the standard of
a real JVM: `interest_rounds` and `interest_unguarded` are the exact bodies `test_interest_equivalence`
runs through Maven against ADR-0021's hand-derived oracle, where they fail six rows and one row
respectively. A judge that passes those is a judge that would have missed a defect the oracle caught.

That is what makes the benchmark falsifiable, and it is also the only reason this harness is worth
building: **the oracle exists for one `COMPUTE` of one program and cannot be written for the other
forty-three.** ADR-0021 recorded that ceiling as a known cost. The judge is the instrument proposed
to cover the rest, so the question that decides whether it is worth anything is whether it agrees
with the oracle where both exist. Here they both exist, so the question is answerable.

**Layout.**

- `corpus.py` -- the criteria and the cases, each carrying how its expected verdict is known.
- `judge.py` -- prompt, response contract, scoring.
- `test_corpus.py` -- the preconditions. Chiefly: the deterministic guards must **not** catch the
  unfaithful cases, or this package is measuring something `render_processor` already refuses.
- `test_judge.py` -- the harness itself, against injected judges, at no cost.
- `test_judge_benchmark.py` -- the billed run, opt-in behind `live_claude_cli`.
"""
