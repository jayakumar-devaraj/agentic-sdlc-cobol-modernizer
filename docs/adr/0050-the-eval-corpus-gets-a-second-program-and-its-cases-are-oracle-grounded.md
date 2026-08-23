# ADR-0050: The eval corpus gets a second program, and its cases are graded by a JVM

## Status

**Accepted** (2026-08-23). Supplies what [ADR-0049](0049-the-judge-is-sampled-and-haiku-is-struck-from-the-candidates.md)
named as the one thing pillar 22 was left waiting on, under
[ADR-0044](0044-a-capability-closes-on-a-second-instance-and-a-caveat-carries-a-status.md)'s rule that
a capability closes against a second instance.

## Context

ADR-0049 measured the pinned judge reproducible — 1.00 ± 0.00 oracle-grounded detection and
0.00 ± 0.00 false positives across three runs — and deliberately did **not** close pillar 22, because
every case in `tests/evaluations/corpus.py` derived from `CBACT04C`. A corpus drawn from one program
measures a judge against one program's idioms and reports the result as a property of the judge.

That is not a hypothetical worry in this repository. It is the same defect
[ADR-0044](0044-a-capability-closes-on-a-second-instance-and-a-caveat-carries-a-status.md) was written
about: the renderer was called complete on `CBACT04C`, and `CBTRN02C` then needed four new contract
facts across three schema versions before it would build at all.

**`CBTRN02C` is a different shape, not more of the same**, which is what makes it worth adding rather
than merely another data point:

| | `CBACT04C` | `CBTRN02C` |
|---|---|---|
| what the guard decides | whether a *field* is computed | whether the record **exists at all** |
| unreachable field | `TRAN-ID`, from a per-run counter | `TRAN-PROC-TS`, from a per-record clock |
| state | a control-break accumulator | reads what its own writes produced |
| outputs per item | one record | **three**, routed by component |

A judge scoring well on interest arithmetic has said nothing about any of that.

## Decision

**Two cases, both `ORACLE`-grounded**, added to the existing `CASES` rather than to a parallel
structure — one corpus with a `program` per case, so every existing guard covers the new entries by
construction.

### The grounding is the expensive part, and it is the point

These cases could have been `SOURCE`-grounded: read the COBOL, write a plausible defect, label it.
That is cheap and it is what a corpus of *"plausible-looking mistakes nobody has made"* is made of —
which the module's own docstring names as the failure it was built to avoid.

Both are graded by a run instead:

- **`posting_faithful`** is the exact body `test_cbtrn02c_round_trip` builds and runs under real
  Maven, matched against `CBTRN02C`'s own output on **4,144 fields** — 3144 of 3144 transaction
  fields, 600 of 600 account, 400 of 400 balance, one exclusion (ADR-0048). Measured identically with
  a model writing it and with it scripted. It is the strongest faithful case in the corpus.
- **`posting_unguarded`** is that body **minus one `if`** — `1500-B-LOOKUP-ACCT`'s credit-limit check —
  so a disagreement between the two is attributable to that guard and nothing else. Run under real
  Maven it writes **300 records against the oracle's 262**.

**The bodies are imported, never retyped.** The moment a corpus body diverges from the string a JVM
compiled, its `ORACLE` ground becomes a claim about a body no JVM ever saw.

### What the defective specimen turned out to prove

With no credit-limit guard, **every one of the 300 daily transactions posts** — the expiry guard
rejects none on this corpus. That trips the hand-written wiring's own run assertion
(`written > 0 && written < 300`) and **fails the Maven build before the differential is consulted**.

So the defect is caught twice over, at two independent levels, for free, on every run. A judge that
misses it has missed something cheaper instruments already catch — which is exactly the bar
`test_the_judge_catches_every_defect_a_real_jvm_already_catches` exists to apply.

The fixture therefore expects the build to fail. `generate_wire_build_and_run` gains an
`expect_build` parameter used **only** by the damaged path; the faithful path still asserts, so a
real regression there cannot hide behind it.

## Consequences

**Two existing guards needed widening rather than satisfying**, and both were the interesting kind —
checks that would have kept passing while covering less:

1. `test_every_oracle_grounded_case_cites_a_test_that_still_exists` resolved citations against
   `test_interest_equivalence` alone. A second program's citation would have failed it for the wrong
   reason, and "fixing" that by dropping the assertion would have left the corpus's strongest claim
   unchecked. It now searches the modules that own the runs.
2. `DOWNSTREAM_BY_STEP` had no entry for the posting step. That fact is what tells a judge whether a
   placeholder in a field is *carried data* or *written output* — and for this step the answer is
   different from either existing one, because its output is routed by component to three files.

**The second-instance property is itself asserted**, four ways: the corpus covers more than one
program, each program contributes both a faithful and a defective case, the second program's cases
are `ORACLE`-grounded, and its bodies are the identical objects the run compiled. A rule about second
instances enforced by nobody is how the first one went unnoticed.

**Counted as a count, not by name.** `test_the_corpus_covers_more_than_one_program` asserts
`len(programs) >= 2` rather than naming `CBTRN02C`, so removing the second program fails rather than
leaving a green test about a corpus that no longer has one.

**What this does not do.** Nine cases across two programs is a second instance, not coverage.
`CBCUS01C` and `CBACT01C` contribute a sequential read and a print (ADR-0035) and have no bodies to
grade; the corpus can only grow with programs that carry business logic, and there are two.

**A cost that is now estimated from a measurement.** The sampled run over nine cases is 27 Opus calls
at `n=3`. ADR-0049 recorded a 38% under-estimate on the previous run because it assumed per-call
costs it had never measured; this one is scaled from the $2.18 that run actually cost.
