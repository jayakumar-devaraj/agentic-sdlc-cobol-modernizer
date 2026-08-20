# Evaluation harnesses: the judge, the injected-error run, and the handoff

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### Step 44 — the LLM-as-judge harness, verified as an instrument *(then run; see the entry below)*

**Stated first, because it is the thing most likely to be over-read.** This entry reports a
**harness**, not a measurement of judge quality. No real judge call has been made. What follows is
verified; *"a real model scores generated Java at N%"* is not claimed anywhere, and
`tests/evaluations/test_judge_benchmark.py`'s own docstring says so.

**What the gap was.** Every claim this repo makes about model-authored Java is spot-measured — the
interest body at 10 of 10, `spec_critic` at 3 of 3, the write body compiling on attempt 1. Real
evidence, none of it re-running, while the previous session changed generator prompts five times.
`tests/evaluations/` had been a 0-byte `__init__.py` for 47 PRs (G9).

**Why a judge rather than more oracles.** ADR-0021 wrote down its own ceiling: a hand-computed table
tests *"arithmetic someone already understood"*, over one paragraph. There is no such table for the
other 43 programs. So the judge is the proposed generalisation — and ADR-0024's decision is to
calibrate it exactly where the oracle already answers, because that is the one place the claim is
falsifiable.

```
./.venv/Scripts/python -m pytest tests/evaluations -q
65 passed, 4 skipped in 0.14s
```

The 4 skips are this module's own benchmark. CI's run on this change reports **767 passed, 8 skipped,
98.84%** — exactly +65 tests and +4 skips against the previous 702/4, and **coverage unchanged**,
which is the expected reading rather than a lucky one: step 44 adds no `src/` code at all. The only
non-test file it touches is `tests/conftest.py`.

**The corpus is graded by a JVM where it can be.** Three of six cases are the exact body strings
`tests/system/test_interest_equivalence.py` compiles and runs through real Maven against ADR-0021's
literals — `interest_rounds` fails rows R1/R2/R5–R8, `interest_unguarded` fails R10,
`interest_faithful` passes all ten. They are **imported** from that module, not copied, and a test
asserts the cited test functions still exist so the citation cannot go stale. The other three are
labelled `SOURCE` and checkable against the copybook by line, and the two grounds are reported
separately rather than averaged — four agreements with this repo's reading must not outvote a
disagreement with a real JVM.

**Shown to discriminate before anything was billed**, the same discipline as step 45's
`divideRounded` body. Three scripted judges through the real scoring code:

| Scripted judge | detection | false positives | cases correct |
|---|---|---|---|
| perfect | 1.00 | 0.00 | 6 of 6 |
| passes everything | **0.00** | 0.00 | 2 of 6 |
| fails everything | 1.00 | **1.00** | **0 of 6** |

The third row is the reason detection rate is not the metric on its own: failing every criterion
catches all four defects and is worthless. § 4b of the feasibility assessment is why — human review
runs three to four orders of magnitude above inference cost, so a spurious flag is expensive in the
term that decides funding.

**Mutation-tested, three of three caught.** Each mutation was applied to `judge.py`, the suite run,
and the source restored:

| Mutation | Test that caught it |
|---|---|
| leak the case name into the prompt | `test_the_prompt_never_leaks_what_the_case_expects` |
| make the rubric depend on the case | `test_the_rubric_is_identical_for_every_case` |
| never report a false positive | `test_a_judge_that_fails_everything_...is_still_wrong` |

#### The finding: an ordinary `pytest` spent 67 seconds calling a real model

The benchmark put its six judge calls in a **module-scoped** fixture. `tests/conftest.py` guards
live tests in a **function-scoped autouse** fixture — and pytest sets higher-scoped fixtures up
first, so the calls happened before the guard could skip anything. Measured, not theorised:
`pytest tests/evaluations` took **67.56s** and ended in `ModelCallError`, with
`COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS` unset.

That is precisely the accident `conftest.py`'s own docstring exists for — *"a test that quietly
costs money and calls a live model is worse than one that fails"* — arriving by the one route a
function-scoped guard structurally cannot cover.

Fixed at the level of the defect class rather than the module: `pytest_collection_modifyitems` adds
the skip at **collection** time, before any fixture of any scope runs, so the next live test needing
a module-scoped fixture is protected without knowing any of this. After: **0.20s, 4 skipped, no
calls.** Both directions are pinned by
`test_the_live_opt_in_guard_skips_and_unskips_correctly` — the un-skip direction included, because
verifying *that* by running the suite would cost exactly what the guard prevents, and a guard that
skips unconditionally would look identical in CI.

#### Two smaller findings, both from checking rather than assuming

1. **`TRAN-ID` is `PIC X(16)`** (`CVTRA05Y:5`). The invented-identifier case as first written was
   short *and* fabricated — two defects in one body, which cannot distinguish a judge that found the
   fabrication from one that flagged the width and stopped. Padded to 16, so every case isolates
   exactly one criterion.
2. **The first leak test asserted the wrong property.** It checked the failing criterion's id was
   absent from the prompt; that id appears in the rubric for *every* case, correctly. The property
   that matters is that the rubric is **identical across cases** — a rubric narrowed to the criterion
   a case violates would look like a token saving and would hand over the answer.

**And the prompt ordering was wrong on the first pass.** Step facts sat ahead of the ~25k-token COBOL
source, putting the large identical span behind a varying prefix — G13's shape and ADR-0017's
correction, reintroduced in a new module. Reordered so all six cases share the rubric and the source
as a genuine prefix, asserted by `test_all_six_cases_share_the_rubric_and_the_source_as_a_common_prefix`.

### Step 44's benchmark, run for real — and the first run failed

*(Supersedes the "what is not verified" paragraph that closed the entry above, which read: **"No real
judge call, so: no detection rate, no false-positive rate…"** That was true when written and is no
longer. Kept rather than deleted, per this file's convention.)*

```
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 pytest tests/evaluations/test_judge_benchmark.py -q -s
```

**Run 2 (final): 4 passed in 89.24s.** `claude-opus-5`, 6 calls.

| case | ground | expected | judge said | correct |
|---|---|---|---|---|
| `interest_faithful` | oracle | (faithful) | (none) | yes |
| `interest_rounds` | oracle | `arithmetic_mode` | `arithmetic_mode` | yes |
| `interest_unguarded` | oracle | `guard_applied` | `guard_applied` | yes |
| `completion_faithful` | source | (faithful) | (none) | yes |
| `completion_empty_string` | source | `fixed_width_text` | `fixed_width_text` | yes |
| `completion_invented_tran_id` | source | `no_invented_values` | `no_invented_values` | yes |

**oracle-grounded detection 1.00 · source-grounded detection 1.00 · false-positive rate 0.00.**

**Run 1 failed, and it is the more useful of the two.** `1 failed, 3 passed in 92.58s`. Detection was
already 1.00 on both grounds — the bar that matters passed on the first attempt — but the
false-positive rate was **0.50**: the judge failed `fixed_width_text` on all three `computeInterest`
bodies, the faithful one included, and additionally `no_invented_values` on that one.

**The judge was right on the facts, and the corpus was wrong.** Checked against the copybook before
concluding anything: all three interest bodies build a carrier

```java
new Tran("", "01", new BigDecimal("5"), "System", "Int.", {amount}, BigDecimal.ZERO, "", "", "", "", "", "")
```

and `CVTRA05Y` declares `TRAN-ID PIC X(16)` (gets `""`), `TRAN-DESC PIC X(100)` (gets `"Int."`),
`TRAN-SOURCE PIC X(10)` (gets `"System"`), plus six more fixed-width fields getting `""`. Every
`fixed_width_text` flag was factually correct.

What makes those placeholders legitimate is that `completeTransaction` reads only `tranAmt` off that
record and rebuilds every other field — **a fact `design.json` holds in its step chain and the judge
was never given.** Fifth instance of one defect class, after G21, G24, G28 and G26: *a computed fact
this repo holds and never hands over.*

**Read the other way, this is the strongest result the run could have produced.** The judge found a
real property of a body ADR-0021's oracle certifies as correct, *because* the oracle asserts on
`tranAmt` and the judge reads the whole record. That is precisely the capability the judge was
proposed for — arriving disguised as a benchmark failure.

**Two fixes, and neither was the bar.** `DOWNSTREAM_BY_STEP` supplies what becomes of a step's
output, keyed by step so it cannot vary with the case and cannot leak which body is defective; and
`interest_faithful`'s claim was narrowed to arithmetic and control-flow fidelity, since "passes 10 of
10" was never evidence about the other 13 columns. Relaxing the false-positive bar was available and
**refused** — the bar did its job.

**Verified that the fix did not blunt the criterion**, which is what separates a real prompt gap from
teaching to the test: `completion_empty_string` still fails `fixed_width_text` in run 2, because
`completeTransaction` is terminal and a short string there is still a defect.

#### Two defects in the harness itself, both exposed by running it

1. **Rationales were discarded.** `parse_judge_response` kept verdicts and dropped the reasoning, so
   when run 1 disagreed with the corpus there was no way to tell a judge error from a corpus error
   without paying for another run. Now retained and required — a missing or blank rationale raises,
   the `modernization_engineer` `notes` precedent — and `render_disagreements()` prints the judge's
   own reasoning for anything it got wrong.
2. **The run could not report its own cost.** Nothing bound a `UsageAccumulator`, so **no cost figure
   exists for either run** — 12 calls total, ~90s each, and that is all that can honestly be said.
   The fixture now runs inside `collect_usage`, so the next run reports calls, tokens, cache reads
   and notional dollars. No estimate is recorded here in place of the measurement.

**Still not measured.** Whether a cheaper judge does as well (both runs are Opus only — the
`spec_critic` comparison this corpus makes possible has not been spent), and what the false-positive
rate actually is: 0.00 over **two** faithful cases is a floor that rules out a judge flagging
everything, not a rate over 44 programs.

### Step 43 — the injected-error harness, and what it found on its first run

**Why one demonstrated heal was not enough.** Step 42 showed the loop repairing a compile error.
That says the machinery works for the error that was tried and nothing about the next one — which is
why pillar 20 stayed 🟡. This parameterises the loop over four error classes that differ in *how the
compiler reports them*, since acting on a diagnostic is what the loop's whole design rests on.

**Every class is one this platform really produced.** None was invented to be easy:

| Class | Where it came from |
|---|---|
| `unknown_method` | PR #28 — a model assumed `CobolArithmetic`'s API rather than being told it |
| `missing_import` | Hit **twice in one session** — a body constructing `Tran` by simple name, and the rendered equivalence test constructing composite components it had not imported |
| `unresolved_import` | PR #32 — the pre-Spring-Batch-6 `ItemProcessor` package shipped in *every* processor. `_validated_imports` checks an import's shape, never its existence |
| `wrong_return` | The output type churned three times this session; returning the previous shape is the natural mistake |

**Two properties are asserted per class, not once overall.** That each produces a **located
diagnostic attributed to the generated file** — a class the parser could not locate would exhaust no
attempts, report `blocked`, and look exactly like a design defect — and that the loop heals it in
two attempts. Plus a test that the four collapse to more than one distinct compiler message, since
four cases producing one message would be one test wearing four names.

#### The finding: the loop refuses to repair a defect the model caused (G30)

`unresolved_import` **does not heal.** It blocks, and the reason is attribution.

A model supplies the imports its body needs — the renderer never reads the body, so it cannot derive
them. But those imports are **rendered into the import block**, outside the
`BEGIN/END model-authored` markers, and `build_validator` attributes a diagnostic by **line**. A bad
import lands on line 3, is attributed to rendered scaffolding, and the loop refuses to hand it back.
Correct by its own rule; wrong in substance, because the model wrote it and a rewrite would fix it.

**Two costs, and the second is worse.** The step spends one attempt instead of two. And the blocked
reason tells a reviewer *"That is a defect in this repo's renderer"* — which for this class is
**false**, misattributing a model's mistake to the code generator.

Pinned as the behaviour that exists rather than the behaviour that should, with the misattribution
asserted so the fix has a failing test waiting for it. Moving where the attribution line falls
changes which lines a model is considered to own, and that belongs in its own decision rather than
smuggled into a test harness.

### G7 — the handoff, finally exercised from the receiving side

**The gap in one sentence.** ADR-0003 specifies the whole exchange; it had been implemented and
tested **on the producing side only**, and control-plane had never received a `design.json`. Each
side looked complete from inside itself, which is why this survived as the platform's largest
integration unknown for eleven revisions of the audit.

**Now tested from both sides, each in its own repo**, because it cannot be done in one.
control-plane's ADR-0001 forbids tenant vocabulary and its own ADR-0001 says the platform ships no
tenant fixtures, so a CardDemo `design.json` cannot live there. The split is forced by the
architecture, not by convenience:

| Side | Where | What it settles |
|---|---|---|
| Receiving | control-plane [PR #9](https://github.com/jayakumar-devaraj/agentic-sdlc-control-plane/pull/9) | An opaque artifact survives the allowlisted serde, a real durable pause and resume, and reaches a gate as *facts* |
| Producing | `tests/system/test_handoff_contract.py` | A **real** `design.json` meets every requirement that spike established |

**What the receiving-side spike found by running.** `build_serde` restricts msgpack to
control-plane's own state models, so an artifact needing a registered type would have required a
package change over there before a gate could even pause on it. It does not — plain JSON
round-trips byte-identically under a canonical dump. And two **independent** `PostgresSaver`
contexts against the published container prove the container-restart case rather than simulating
it: the first pauses and closes, the second resumes on the same thread id and **reads the artifact
back from the store before anyone resumes**, which is what an approval interface would render.

**What this side now asserts**, each requirement traceable to something the spike ran:

- the document is plain JSON containing no type control-plane must declare;
- it is self-contained and **leaks no local path** — the specific way "self-contained" fails
  quietly, since the document still parses and the reviewer following the reference finds nothing.
  Verified falsifiable against a poisoned document rather than assumed;
- `gate_items` travel with it and carry **no verdict** — no severity, no score, no `blocks` flag;
- `schema_version` and `generated_at` are stated, and the narration a reviewer reads is reachable
  without re-running extraction.

**Scope, stated.** This retires the unknown, not the integration. control-plane still has no node
that invokes this CLI, and the spike deliberately adds none: it introduces no node, no gate type and
no state field, so that whatever Phase 2.1 needs is now a measured requirement instead of a guess.
G7 moves to partial, not closed.
