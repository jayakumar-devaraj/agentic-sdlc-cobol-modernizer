# Equivalence, and the first real model-authored business logic

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### Step 45 — the equivalence test, and the first real model-authored business logic

**The harness.** `rendering/java_equivalence_test.py` renders a JUnit test *into the generated
project*, from the oracle plus a declared `java_binding`. It has to be rendered rather than
hand-written in the template: the processor class, the composite it consumes and the record it
returns all come from `design.json`. A test written against `CobolArithmetic` instead would pass no
matter what the model wrote, which is the one thing step 45 exists to check.

**Demonstrated to discriminate, against real Maven** — three scripted bodies through `author=`:

| Body | Result |
|---|---|
| Faithful (`divide`, truncating) | **passes** |
| `divideRounded` — one token different | **fails on exactly R1, R2, R5, R6, R7, R8** |
| Correct arithmetic, always emits a transaction | passes every numeric row, **caught only by R10** |

The failing set for the rounding body is asserted against the oracle's own `rejects` metadata, not
eyeballed — so the prediction `test_interest_oracle.py` checks against Python's `Decimal` is now
also checked against real Java in a real JVM. R6 failed it with *"expected 0.00 but was -0.01"*, so
the negative-zero row earns its place outside theory.

#### The first real model call to write business logic through this pipeline

Two runs, both `claude-opus-5` through `modernization_engineer`.

**Run 1 — blocked, and it found a real gap.** The model wrote **correct arithmetic twice** and
guessed the composite's accessors twice: `item.tranCatBal()` (flattening it), then
`item.getTranCatBal()` (JavaBean getters, which no Java record has). Its own notes:

> ACCESSOR NAMES ON TranCatBalWithRate ARE UNVERIFIED … If this fails again, the next attempt
> should be given the class declaration rather than another guess — I have no further evidence to
> narrow it.

It was right. `TranCatBalWithRate` is a record **this repo renders**, and its declaration appeared
nowhere in the prompt — PR #28's `CobolArithmetic` finding exactly, reappearing for the type
ADR-0020 introduced after that fix landed. `build_validator` then did its hard job correctly and
refused to call it repairable, because from the diagnostics a misspelling and a missing field are
the same message. **4 calls, $0.79 notional** — above the ~$0.30 a single call was estimated at,
because the heal loop ran.

**The fix**: composites now reach `render_domain_facts`, guarded by a test asserting through the
real `build_engineer_prompt` rather than the helper (the G21 lesson).

**Run 2 — compiled on attempt 1, capped at one attempt.** Accessors correct
(`item.balance().tranCatBal()`), arithmetic correct, and its notes state the truncation reasoning
unprompted: *"the division is truncated once, directly at the receiving field's scale 2, because
the COMPUTE has no ROUNDED."* It also used `requireFits` for the receiving field's precision.

**Equivalence result: 9 of 10 — all nine arithmetic rows pass, R10 fails.**

```
ComputeInterestEquivalenceTest.writesNoTransactionWhenTheRateIsZero
AssertionFailedError: R10: a zero rate must produce no transaction at all
  ==> expected: <null> but was: <Tran[... tranAmt=0.00 ...]>
```

**This is a design finding, not a model error, and the distinction is the point.**
`STEP.source_paragraphs` is `1300-COMPUTE-INTEREST`, and the guard is *not in it*:
`IF DIS-INT-RATE NOT = 0` is at `CBACT04C.cbl:214`, in the main `PROCEDURE DIVISION` loop that
calls the paragraph, which begins at line 462. The model was shown one paragraph and translated
exactly that paragraph, faithfully. What the equivalence test caught is that **the step design
hands the generator the callee while the oracle describes behaviour spanning the caller.**

Pinned by `test_the_zero_rate_guard_is_not_in_the_paragraph_the_step_names` rather than fixed by
widening `source_paragraphs`, because whether the step should name the loop or R10 belongs to a
different step is a `solution_architect` question, and settling it inside a test would make a design
decision by accident.

**What this does and does not establish.** The arithmetic half of the translation is now verified
against COBOL's own answers by a test that has been shown to fail. **The round-trip count stays
`0 of 4`**: R10 fails, so no program yet passes a full differential test — and the reason it fails
is worth more than a green run would have been.

18 tests across the two modules.

### G25 closed — 10 of 10, and the first paragraph-level round trip

**The fix could not be what the gap said it was.** G25 read *"add the guard paragraph to the step"*,
and there is no such paragraph: `CBACT04C`'s first **named** paragraph is `0000-TCATBALF-OPEN.` at
line 234, and the guard is at line 214, in the unnamed main body. `source_paragraphs` could only
have carried `PROCEDURE DIVISION`, scoping the interest step to the file opens, the read loop and
the account update too. A guard answers *when a step runs*; `source_paragraphs` answers *what code
it came from*. ADR-0022 adds `guard_condition` rather than overloading the latter.

**Verified against a real model, capped at one attempt.** With the guard declared, `claude-opus-5`
compiled on attempt 1 and wrote the branch:

```java
// Guard from the caller of 1300-COMPUTE-INTEREST: IF DIS-INT-RATE NOT = 0
BigDecimal disIntRate = item.disclosureGroup().disIntRate();
if (disIntRate == null || disIntRate.compareTo(BigDecimal.ZERO) == 0) {
    return null;
}
```

```
Tests run: 10, Failures: 0, Errors: 0, Skipped: 0
  -- in com.modernized.batch.processor.ComputeInterestEquivalenceTest
```

**All ten rows pass, including R10.** That is COBOL → compiling Java → **passing differential
test**, and it is the first time this platform has closed that loop on anything.

**It is a paragraph-level round trip, not a program-level one, and the metric stays `0 of 4`.**
`CBACT04C` is also a rate lookup, a `'DEFAULT'` group fallback, a per-account accumulation, an
account update and a transaction write. What is verified is `1300-COMPUTE-INTEREST`'s arithmetic and
the condition it runs under. Reporting this as a migrated program would be exactly the overclaim
ADR-0021 was written to prevent.

#### Four design gaps the model raised unprompted, none of them guessed at

The `notes` field earned its keep again. Recorded here because each is a real finding about **this
repo's design**, not about the model:

1. **The step's output type is unreachable from its input.** `Tran` stands in for `1300-B-WRITE-TX`,
   but `tranId` needs `PARM-DATE` and a per-run counter (neither reachable from a stateless
   processor — and, it noted, not reproducible under restart or partitioning), `tranCardNum` needs
   `CardXref`, and `tranOrigTs`/`tranProcTs` come from a paragraph whose target fields are
   `REDEFINES`, which the construct matrix routes to a human gate. It left them `null` deliberately
   and said so. Either the composite gains `Account` and `CardXref`, or `1300-B-WRITE-TX` becomes
   its own downstream step.
2. **`ADD WS-MONTHLY-INT TO WS-TOTAL-INT` is cross-item state** and does not belong in a stateless
   processor. Its warning is the sharp one: whichever step owns `1050-UPDATE-ACCOUNT` must
   accumulate these amounts, *"or the account balance update will be wrong (silently, and by the
   full interest amount)."*
3. **`MOVE SPACES` is not `""`.** An empty string and 50 blanks are not the same record on disk if
   the writer emits fixed width.
4. It confirmed the interest computation itself is complete and faithful, and said which parts it
   was confirming rather than asserting completeness in general.

Three of these describe work no step currently owns. They are the natural input to whatever revisits
`solution_architect`'s step decomposition.

### The judge comparison — and a pillar crossing withdrawn

**The headline: `claude-opus-5` is not deterministic on this corpus, and the previous entry's
"6 of 6 / 0.00" was one sample of it.**

```
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 pytest tests/evaluation/test_judge_benchmark.py -q -s
1 failed, 3 passed, 4 errors in 245.84s
```

| Run | Result | False positives |
|---|---|---|
| PR #49, run 2 | 6 of 6 | **0.00** |
| This run | **4 of 6** | **0.50** |

Same model, same corpus, same prompt, same effort. Detection stayed **1.00 on both grounds** — the
claim ADR-0024 actually rests on held — but the false-positive figure did not, and
`completion_faithful` and `interest_rounds` both moved.

**Pillar 22 is reverted ✅ → 🟡 on this** (audit R2.27). R2.22's criterion, *"a real run clears the two
derived bars"*, cannot distinguish an instrument that clears them from one that clears them
sometimes. One sample of a non-deterministic instrument is not a measurement, and the criterion
should have said **reproducibly**.

#### The judge was right again, which is the more useful half

Rationales are retained now (that fix paid for itself immediately). On `completion_faithful`:

> *"TRAN-SOURCE (PIC X(10)) is written as the bare 6-character literal `"System"` rather than
> blank-padded to 10, and `"01"` is only correct by coincidence of width."*

`CVTRA05Y:8` declares `05 TRAN-SOURCE PIC X(10)`, and the real PR #44 body writes `"System"` — six
characters. **The judge is correct and the corpus label is wrong.** This also corrects a claim made
about that PR: it was recorded as having *"padded every alphanumeric field, going further than the
scripted body"*. It padded `TRAN-DESC` and the three merchant fields, and left `TRAN-SOURCE` short.

Second time the judge has found a real defect in a body this corpus certifies — after the carrier
record in run 1. **The consequence is methodological**: the false-positive bar scores the judge
against the corpus, so a fallible corpus reports its own errors as the judge's. Cases must declare
which criteria they are clean specimens *for*, which is the fix applied below.

#### Haiku: ineligible on this run, for a contract reason rather than a judging one

It emitted prose before the JSON array — *"Looking at this translation... I'll evaluate each
criterion"* — so `parse_judge_response` raised and all four of its tests errored at fixture setup. It
was never scored, and no cost figure exists for it because the fixture raised before reporting.

**The parser was deliberately not loosened.** Extracting the first JSON array from surrounding prose
would have made Haiku pass, and that is teaching to the test: a strict parse exists because a
malformed response means the measurement did not happen. What this measures is contract-following,
and it is recorded as that rather than as judging quality.

#### The first real cost figure this harness has produced

**$0.9406 for 6 Opus calls** — `in=12, out=4502, cache_read=40665`. Almost every input token came from
cache, which is the stable-prefix ordering working as ADR-0017 predicted. The estimate offered before
the run was $0.40–1.00; the actual sits at the top of it, and is recorded rather than the estimate.

### G27's generation half — the account break as a second pass (ADR-0027)

**What the gap was.** `ADD WS-MONTHLY-INT TO WS-TOTAL-INT` accumulates per account and
`1050-UPDATE-ACCOUNT` posts the total on each account break. A stateless `ItemProcessor` cannot hold
that total and Spring Batch's chunk boundaries do not align with COBOL's breaks, so ADR-0023 reported
the step `not_generated` rather than rendering something that looked right. This is the other half.

**Reading the COBOL surfaced three things the gap's summary did not say**, and each shaped the answer:
the break **assumes its input is grouped by account and never checks**; the flush is **offset by one
plus a second flush at EOF** (getting that wrong loses the last account's interest silently, by the
full amount); and `1050-UPDATE-ACCOUNT` is three concerns, only two of which are translated logic —
the `REWRITE` is persistence.

**The decision is to stop holding the state rather than to manage it.** If the item arriving at the
processor is already one account with its interest already summed, there is no cross-item state and
the generated body is exactly COBOL's two statements. **The sum is provably the same number**:
`WS-TOTAL-INT` accumulates `WS-MONTHLY-INT`, every `WS-MONTHLY-INT` is written to `TRAN-AMT`, and both
happen inside `1300-COMPUTE-INTEREST` under the same `IF DIS-INT-RATE NOT = 0` guard — so they cannot
diverge, and `SUM(tran_amt)` per account *is* `WS-TOTAL-INT`. That equality is what makes this a
re-ordering rather than a re-implementation, and it is why (d) beat a stateful writer on correctness
rather than on convenience.

```
JAVA_HOME=... pytest tests/integration/test_account_break_posting.py -q
4 passed in 46.99s
```

`run_generate` renders the step and reports **`compiled`, with `not_generated` empty** — the direct
inverse of ADR-0023's finding, where this step reported `not_generated` with its paragraph named and
produced no Java. The faithful body then passes **4 of 4** JUnit cases under `mvn verify`: the total
is *added* to the balance, both cycle totals are cleared, a negative total posts as a decrease
(`dailytran` really carries negative amounts), and every untouched field survives — the last because a
body that rebuilt the record from defaults would satisfy the arithmetic and silently blank the account.

#### The finding: the discrimination check was vacuous on its first run

The two wrong bodies — one that forgets the cycle reset, one that uses `MOVE` where COBOL says `ADD`
— were asserted with `assert not result.succeeded`. **That is worthless, and it passed while
rejecting nothing.** The build was failing for every body, correct and incorrect alike, because the
template's Testcontainers test could not pull `postgres:16-alpine`; a build that breaks for any
reason satisfies "did not succeed".

Worth recording plainly because of where it happened: this module's own docstring claimed it was
*shown to discriminate before it is trusted*, and the check backing that claim could not fail. The
pattern is the one this repo has now hit six times, and this is the first instance authored **inside
the safeguard against it**.

Closed by **attributing the failure** rather than observing one: surefire's per-class summary is
parsed, and the assertion is that `PostAccountInterestTest` itself reported failures. The positive
test asserts the class **ran 4 cases** for the same reason — a run where it silently did not execute
would otherwise read as green. And the template's stack test is removed from the throwaway copy,
since it is the `template-build` CI job's business and leaving it in makes every result here depend on
a Docker daemon.

#### Two options refused, on the record

A **stateful `ItemWriter`** is the literal translation and fails on the two things COBOL never had to
survive — a chunk boundary landing mid-account, and a restart replaying one — besides breaking
ADR-0019's processor-only scope. **Making the item an account group** is architecturally the better
answer and is **blocked on the contract**: a group's output carries many transactions, and
`CompositeType.components` names one entity each with no cardinality, which is the array support
ADR-0019 scopes out. ADR-0027 does not foreclose it.

**Recorded divergence.** COBOL interleaves posting with transaction writing in one pass; this posts
in a second pass after all transactions exist. Final state is identical; intermediate state is not,
and pass 2 is meaningless unless pass 1 completed. **Neither is idempotent across runs** — `ADD ... TO
ACCT-CURR-BAL` posts again if re-run, exactly as COBOL's `REWRITE` does — stated so "restart-safe" is
not read as "re-runnable".

**Not claimed.** No real model has written this body; it is scripted, and what is verified is that the
pipeline generates the step and that the check has teeth. The aggregating reader is infrastructure and
is **not generated** — the same line PR #44 drew inside `1300-B-WRITE-TX` and ADR-0026 drew for job
parameters. **The round-trip metric does not move**: `TRAN-ID` is still unpopulated by ADR-0026's
decision, so `CBACT04C`'s transaction record remains incomplete.

### G29's open half — job parameters reach a rendered processor (ADR-0026)

**The dead end this closes.** PR #45 made `LocalDateTime.now()` in a generated body a refusal, and
supplied nothing to read instead — so the model was told *don't* with no alternative, and
`1300-B-WRITE-TX`'s timestamps stayed `null`. Its logic was otherwise generated and correct (PR #44).

**Read from the source rather than assumed**, the three missing fields turned out not to be one
problem: `PARM-DATE` is `PIC X(10)` in the **LINKAGE SECTION**, arriving via
`PROCEDURE DIVISION USING EXTERNAL-PARMS` — a job parameter in the COBOL too; `DB2-FORMAT-TS` is
`FUNCTION CURRENT-DATE` read **per record**; `WS-TRANID-SUFFIX` is `PIC 9(06) VALUE 0` incremented
per written transaction. `TRAN-ID` is `STRING PARM-DATE, WS-TRANID-SUFFIX` — 10 + 6, exactly filling
`PIC X(16)` — so it needs the first and the third.

#### The `@StepScope` package was verified by javac, not by memory

```
pytest tests/integration/test_build_validator.py -k job_parameters_actually_compiles
1 passed in 10.55s
```

`org.springframework.batch.core.configuration.annotation.StepScope` **does** resolve in Spring
Batch 6.0.4. Checked before trusting it, because this is precisely where PR #32 was bitten: the
renderer carried the pre-6 `ItemProcessor` package, every generated processor had an unresolvable
import, and only compiling one showed it. A rendered package name is a claim about a jar.

#### The claim G29 exists for, run rather than argued

```
pytest tests/integration/test_job_parameter_determinism.py -q
2 passed in 47.12s          # `mvn verify` — compiles the processor and runs 3 JUnit cases
```

A real processor is rendered with an injected `runTimestamp`, a JUnit test is rendered around it, and
both go through real Maven. Green means: the `@StepScope`/`@Value` shape is legal, the injected value
is readable **from inside the model-authored region**, and the output is stable across instances and
across calls.

**Shown to discriminate before being trusted**, the discipline step 45 established with its
`divideRounded` body. Two instances with the *same* parameter must agree; two with *different*
parameters must **disagree**. Without that third case the first two are vacuous — a body that ignored
the parameter, or a renderer that silently dropped it, would pass a same-input equality check
perfectly.

#### Two decisions, both recorded rather than slipped in

**The run timestamp is one per run, and that is a divergence.** COBOL reads the clock per record with
millisecond precision, so a run spanning a millisecond boundary stamps records differently; one
supplied instant collapses that. Taken because PR #45's standing rule is that a batch record must be
reproducible across runs and restarts — but recorded in ADR-0026 as a **known divergence with a
stated cost**, in ADR-0021's manner, not as an equivalence. It binds at the first byte-for-byte
record comparison.

**The per-run counter is scoped out, not faked.** An `AtomicLong` is one line away, compiles, reads
correctly, and is wrong: Spring Batch reprocesses a chunk on restart, so it advances where COBOL's —
reinitialised to `VALUE 0` — repeats its sequence, and partitioning interleaves it. A real model
flagged exactly this, unprompted, in the run that produced G29. So `TRAN-ID` stays unpopulated with a
declared reason rather than holding a plausible wrong value.

#### Also pinned

A step naming a job parameter its job does not declare raises `UnresolvedJobParameterError` rather
than rendering a constructor argument Spring would bind to `null` — a processor that compiles,
starts, and writes a record with a hole in it. The resolving case is asserted alongside it, so the
refusal is not simply *"this never works"*.

A step consuming **no** job parameters renders exactly as before — `@Component`, no `@StepScope`, no
constructor. Asserted, because a renderer that always emitted the annotation would be invisible until
someone wondered why every processor was step-scoped.

#### The coverage delta, chased again — and it was the prompt

The local run fell to 98.63%, and the uncovered lines were `render_job_parameter_facts` in full:
**written, wired, and executed by no test.** G21's shape a sixth time, and the worst place for it —
a model never told the parameters exist reaches for a clock, `NonDeterministicBodyError` refuses it,
the field stays null, and the loop is stuck exactly where it started. Closed by asserting through
the real `build_engineer_prompt` rather than the helper, which is the lesson G21 cost two attempts
to learn.

CI's run on the change: **792 passed, 8 skipped, 98.87%** — above the 98.85% it started from.

**Not claimed.** No real model has written a body against an injected parameter — the body here is
scripted, and what is verified is the mechanism. `TRAN-ID` remains unpopulated, so `CBACT04C`'s
transaction record is still incomplete and **the round-trip metric does not move**. G27's accumulator
is untouched: `1050-UPDATE-ACCOUNT` needs cross-item state, the same stateless-processor limit reached
from the other side.

### G30 closed — the loop repairs a model's own import, and stops blaming the renderer

**The gap, restated as what it actually was.** Step 43's harness found that `unresolved_import` would
not heal: a model-supplied import that does not resolve produced a diagnostic on line 3, outside the
`BEGIN`/`END` markers, so `build_validator` called it rendered scaffolding and refused to hand it
back. The instinct is to read that as a bug in the validator's line test. It is not. **The artifact
under-reported what the model wrote** — model-authored text lives in two disjoint regions of the file
and only one of them was marked — so the validator got an incomplete answer from the only thing it
has, and so did any reviewer skimming for "which lines did a model produce here".

Fixed in the renderer, not the checker (ADR-0025). `MODEL_IMPORT_MARKER` marks the imports the model
supplied; `model_authored_line_numbers` returns the body's lines plus those; `build_validator`
attributes from that set.

**The rule was not relaxed, which was the constraint.** Everything unmarked is still deterministic
and still refused to a model. The one-line alternative — letting the rendered-region refusal go — was
available and refused on the record: it would hand a model errors in genuinely rendered scaffolding
in order to fix a case where that refusal was merely misapplied.

```
JAVA_HOME=... pytest tests/integration/test_generate_pipeline.py -q
20 passed in 329.25s (0:05:29)
```

**All four injected error classes now heal in two attempts**, where three did before:
`test_the_loop_heals_every_injected_error_class` is parametrised over the full `_INJECTED_ERRORS`
list, and `unresolved_import`'s exclusion — which existed on evidence, not convenience — is gone.

**The message is pinned separately from the outcome**, deliberately. A misattributed *verdict* costs
one retry; a misattributed *reason* costs a reviewer an investigation of the wrong component, and
§ 4b puts human review three to four orders of magnitude above inference. A future change could
restore the heal while reintroducing the wrong explanation, and a test that only checked the outcome
would pass it — so `test_the_blocked_message_no_longer_blames_the_renderer_for_a_models_import`
asserts the reason on its own.

```
pytest tests/unit/test_java_processor.py tests/integration/test_build_validator.py -q
61 passed in 16.62s
```

**Three properties checked rather than argued**, each with its own test:

| Property | Why it matters |
|---|---|
| An import the renderer emits anyway is **never** marked | Marking it would be G30 inverted — a model made answerable for a line it could not have caused |
| The marker counts only on a real `import` line | Nothing written inside a body can forge attribution for a line outside it; `_validated_imports` closes the other route by refusing an import that is not a bare qualified name |
| Imports stay deduplicated and sorted | One design still renders byte-identically whatever order the model emitted them in |

**Also verified directly rather than assumed**: `_extract_model_imports` reads the marked imports back
out of the rendered file and strips the marker — given a model that supplied both
`java.math.BigDecimal` and `org.springframework.stereotype.Component`, it returns exactly
`('java.math.BigDecimal',)`, excluding the framework import the renderer would have emitted regardless.

#### The coverage delta, and what it caught

CI fell **98.84% → 98.73%** on this change. Chased rather than explained away, per the standing rule
that a moving coverage number has been a real gap every time here. It was two, and the first is the
one worth reading.

**`validate_build`'s deterministic short-circuit lost its only test.** `classify` has a direct test
for every deterministic branch, and nothing tested that `validate_build` actually *short-circuits* on
one — until this change, a single integration test happened to cover it: **G30's own case**, which
drove a rendered-region error through the real entrypoint. Closing G30 made that case heal instead,
and the early return silently went uncovered. **G21's shape a fifth time**: a helper with its own
tests, and no test of the path to production, where the only cover was incidental.

Closed by asserting it through the real entrypoint with an `advise` that raises if called — which
pins the property as economic as well as behavioural: *a verdict this node can reach on its own must
never pay for a model call.*

**Second, smaller:** `_extract_model_imports` returning `()` for a file with no marked region — the
"attribution unavailable" posture the G30 fix introduced, which nothing exercised.

Neither was found by review. Both were found by a number moving 0.11%. Coverage ended at **98.85%**,
above the 98.84% it started from — which is the same shape as PRs #42 and #43: chasing the delta left
the suite better than the change found it.

**Not claimed.** No real model has repaired an import through this path; the four classes are scripted
on both sides, which is what step 43 established is being measured — the **loop**, not a model's
ability to fix things. And the round-trip metric does not move: this repairs attribution, not
translation.
