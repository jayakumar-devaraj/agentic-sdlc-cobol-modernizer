# The write path, the systemic gate fixes, and the round trips

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### Generating the write step — by splitting logic from wiring, not by rendering writers

**`generate` renders `ItemProcessor`s only** (ADR-0019, reaffirmed by ADR-0023), so there were two
ways to produce Java for `1300-B-WRITE-TX`: teach the renderer about `ItemWriter`s, or find the line
inside the paragraph where business logic stops and infrastructure begins. The second is the one
this repo's architecture already points at — render the mechanical, model the logic.

**The paragraph is mostly per-item field population**: fourteen `MOVE`s and two `STRING`s. Three
statements are not, and they are the ones that cannot be an `ItemProcessor`:

| Not translatable here | Why |
|---|---|
| `ADD 1 TO WS-TRANID-SUFFIX` + `STRING PARM-DATE …` | a per-run counter and a job parameter |
| `PERFORM Z-GET-DB2-FORMAT-TIMESTAMP` | a clock, into `REDEFINES` fields the matrix gates |
| `WRITE FD-TRANFILE-REC` | the physical I/O |

So the step is a `processor` named `completeTransaction`, and those three stay wiring. **Typing the
whole paragraph as a `writer` would have left its field population ungenerated for the sake of three
statements** — the same mistake as putting the guard in `source_paragraphs`: taking a boundary the
COBOL draws and assuming the design must draw it in the same place.

**Verified through `run_generate`**: both steps compile, `not_generated` is now empty, and the
generated completion body contains `item.account().acctId()`, `item.cardXref().xrefCardNum()` and
`CobolText.spaces(50)` — G26's two newly-reachable fields and G28's padding, exercised in generated
Java rather than asserted about it.

**The residual is unchanged and still stated**: `TRAN-ID` and the timestamps are `null` in the body,
because a stateless processor has neither a run counter nor a clock. That is the same boundary G26
recorded, now visible in the generated file rather than only in a gap register.

#### The real run — and the G28 fix landing on a model

Two Opus 5 calls, capped at one attempt each, **$0.6062 notional**. Both processors compiled on
attempt 1, and `computeInterest` still passes the oracle **10 of 10** under the changed output type.

**G28's fix worked.** The model wrote `CobolText.spaces(50)` for the three `MOVE SPACES` fields —
not `""`. Its previous run, before the width and the helper existed, wrote `""` and flagged that it
was wrong to.

**It went further than the scripted body.** It padded *every* alphanumeric field to its declared
width — `pad("01", 2)`, `pad("System", 10)`, the card number to 16, the timestamps to 26 — where the
hand-written fixture only padded the three the COBOL spells `SPACES`.

**G26's reachability worked**: `item.account().acctId()` and `item.cardXref().xrefCardNum()`, the
two fields it left `null` last time.

**One finding is a correction to this repo's own fixture, not to the model.** For
`STRING 'Int. for a/c ', ACCT-ID DELIMITED BY SIZE`, it wrote:

```java
String acctIdDigits = String.format("%011d", item.account().acctId().toBigInteger());
```

with the reasoning that `ACCT-ID` is an unsigned 11-digit **display** field, so `DELIMITED BY SIZE`
contributes all eleven zero-padded positions rather than a trimmed number. That is right, and the
scripted `_COMPLETE_BODY` in this repo concatenates the bare value — which would write
`Int. for a/c 194` where the COBOL writes `Int. for a/c 00000000194`. **The hand-written fixture is
the less faithful of the two.**

**What it decided rather than refused, and therefore needed a fix.** It implemented the DB2
timestamp, reconstructing the format from `Z-GET-DB2-FORMAT-TIMESTAMP`'s sub-fields — including that
the trailing group is hundredths of a second — and used `LocalDateTime.now()`. Those sub-fields are
`REDEFINES`, which the construct matrix routes to a human, and `now()` is non-deterministic in a
batch record. It said so in its notes rather than passing it off as settled, **which is the only
reason it was caught.** See the next section.

#### Ambient state is now refused, not reviewed

**A batch record has to come out the same on every run over the same input**, including a restart
that reprocesses a chunk. `LocalDateTime.now()` in a generated body means it does not, and nothing
downstream notices: it compiles, it looks right, and the only symptom is two runs disagreeing.

That is the failure class this repo's deterministic core exists to prevent, arriving through the one
part a model writes — so it is a **refusal** (`NonDeterministicBodyError`), the same posture as the
forgery check, rather than a note a reviewer might skim. `render_processor` rejects
`LocalDate/LocalDateTime/LocalTime/Instant/OffsetDateTime/ZonedDateTime/Year/Clock.now(...)`,
`System.currentTimeMillis/nanoTime/getenv/getProperty`, `new Random(...)`, `new Date(...)`,
`Math.random(...)` and `UUID.randomUUID(...)`.

**It matches call sites, not words.** A field named `nowField`, a `CobolText` call, and a comment
explaining why the clock was *not* used all pass — a guard that fired on the word would push a model
into writing worse notes. Pinned both ways, and the positive case is the **verbatim body the real
model wrote**, so this cannot regress into a check that no longer catches the thing it was built for.

**The right answer is not a different clock call.** A run timestamp and `TRAN-ID`'s
`PARM-DATE`-plus-counter are the same kind of thing — **job-level facts**, belonging to the
invocation rather than the item — and a stateless processor has access to neither. Supplying them is
one design decision, not two (audit **G29**). Until it is made, the fields stay unset and flagged,
which the prompt now asks for explicitly rather than leaving a model to infer.

**Still refused, and flagged for a human**: `TRAN-ID`'s counter and job parameter, `TRAN-AMT`'s
provenance across the step boundary, the `WRITE` and its status handling, and whether
`MOVE '05' TO TRAN-CAT-CD (PIC 9(04))` is faithfully `BigDecimal("5")`.

### `1300-B-WRITE-TX` becomes its own step

**The design decision G26 and G27 both left open, now made.** The interest paragraph performs the
write paragraph, and a single step owning both was what made `Tran`'s id, description, card number
and timestamps unreachable in the first place.

**The split needed a change to the check before it meant anything.** `unreachable_entities` follows
`PERFORM`, so `computeInterest` would still have been charged with the write paragraph's data
however the design divided them. A `PERFORM` is a call, and a design may legitimately split one
into two steps — so paragraphs another step of the same job owns are now boundaries: not followed
into, not included, and a step is never excluded from its own.

**The assertion that matters is that the finding relocates rather than vanishing:**

| | `computeInterest` | `writeTransaction` |
|---|---|---|
| Undivided | `['Account', 'CardXref']` | — |
| Split | `[]` | `['Account', 'CardXref']` |

A boundary that made a real gap disappear would be worse than no boundary. It lands on the step that
now owns the work, which is why `writeTransaction`'s input is the composite that reaches them.

**The shape.** `computeInterest` takes `TranCatBalWithRate` and returns `TranWithContext` — the
transaction plus the context its successor needs; a composite carries existing entities only
(ADR-0020), which is why the amount travels inside a `Tran` rather than as a bare value.
`writeTransaction` takes that and produces the `Tran`. That is the chain ADR-0020 recorded a real
architect run producing.

**End to end, not merely declared.** `writeTransaction` is a `writer`, so `generate` reports it
`not_generated` naming `1300-B-WRITE-TX` (ADR-0023) rather than rendering it — the honest end state:
the step exists, is owned, and its logic is declared as not yet produced. Asserted through
`run_generate`, because a step nothing exercises is a step that has not really been added.

The oracle's result now sits one accessor deeper (`result.tran().tranAmt()`), declared as a
`component` in `java_binding` rather than inferred.

**CI's coverage caught a quieter cost of the split.** It fell 98.62% → 98.50%, and the uncovered
lines were the renderer's refusals — including the **plain-entity output path**, which every call in
the harness stopped exercising the moment the output became a composite. Most steps return an
entity, so that is the common case, not a legacy one; it was silently untested by a change that
looked like it only added something. Closed, along with the two new refusals and two pre-existing
ones the same pass exposed: the module is at **100%**, and every `UnrenderableOracleError` branch
now has a test. Third time this session a coverage delta was the thing that noticed.

### G26's systemic half — resolving is not populating

**The gap in one sentence.** ADR-0020 checks a step's `input_type`/`output_type` **resolve** — that
each names a declared entity or composite. Nothing checked they were **populatable**: whether the
data the step's COBOL actually reads is reachable from the type it was handed. Those two failed
apart once already, and the only signal was a model refusing to invent values.

**What the check does.** `unreachable_entities` matches declared COBOL field names against the
step's paragraph text and reports entities that are mentioned but not reachable from the declared
types. Deterministic, and deliberately shallow — no expression parsing, no dataflow inference.

**It follows `PERFORM`, and that is the load-bearing part.** G26's fields are not in the paragraph
the step names: `1300-COMPUTE-INTEREST` performs `1300-B-WRITE-TX`, and the moves live there. A
check reading only the named paragraph finds nothing wrong with the design that produced the defect.
Asserted directly rather than assumed:

```
XREF-CARD-NUM in 1300-B-WRITE-TX        → True
XREF-CARD-NUM in 1300-COMPUTE-INTEREST  → False
```

**Demonstrated on the real before and after**, which is what makes it a check rather than a claim:

| Composite | Result |
|---|---|
| `balance` + `disclosureGroup` (what the design had when the model failed) | `['Account', 'CardXref']` |
| plus `account` + `cardXref` (PR #40's fix) | `[]` |
| a plain `TranCatBal` step, no composite | `['DisGroup', …]` — cannot reach the rate it multiplies by |

The first row is G26 reproduced from the real COBOL: every type name resolved, and two entities were
still out of reach.

**A fact, not a refusal.** It emits a `GateItem` rather than raising, because a referenced entity
may be legitimately absent — mentioned in a `DISPLAY`, or read by a paragraph whose logic belongs to
another step. Surfacing it and letting a reviewer weigh it is the specialist contract's rule 5, and
the same posture ADR-0008 fixed for every other gate item. `build_design_document` gained
`design_gate_items` for facts that are properties of the *design* rather than of a program's
extraction; they are passed in rather than derived there because deriving them needs the tenant's
COBOL, which `core/contracts.py` deliberately does not read.

**Ambiguity resolves toward silence, deliberately.** A field name owned by two entities counts as
reachable if either is. A gate nobody trusts is worse than one that occasionally under-reports.

**CI's coverage caught the wiring untested — the G21 pattern, one more time.** The first CI run on
this branch reported **98.37% against 98.59%**, and every uncovered line was
`unpopulatable_gate_items`: the function that turns the check's answer into something a human at the
gate actually reads. `unreachable_entities` had four tests of its own the whole time. That is
exactly the shape G21 was closed twice over — a helper tested directly while the path to production
was never exercised — and the only reason it surfaced is that a number moved.

Closed by testing through the wiring: the gate item is produced, it names both entities and the
paragraph, and it **reaches `DesignDocument.gate_items`** (a gate item nobody assembles into
`design.json` is a gate item nobody sees). Both modules are now at **100%**, and the set includes
the case where the check must go quiet — without it, a check that always fires would look exactly
like a check that works.

### G28 — the width was computed all along and thrown away one line early

**"Pad in the writer" could not be done as stated: there is no writer.** `generate` renders
`ItemProcessor`s only, and ADR-0023 records that non-processor steps are reported rather than
rendered. But the defect is not a writer's — **the `""` the model wrote appeared in a *processor*'s
output record**, so it is fixable where it occurs.

**The root, found by reading the chain rather than the symptom.** `pic_mapper` computes
`string_length` for every `PIC X(n)`. `_to_domain_field` copied `precision`, `scale` and `signed`
and **dropped the width one line before it reached the design** — so a `PIC X(50)` became a bare
`String`, and the width existed nowhere the generator, the record, or a reviewer could see it. Same
defect class as G21 (`WS-MONTHLY-INT`'s scale) and G24 (a composite's accessors): a computed fact
that never reached the target.

| Where the width now appears | Form |
|---|---|
| `DomainField.length` | carried from `pic_mapper`, optional with a default |
| Generated record Javadoc | `@param tranMerchantName from COBOL TRAN-MERCHANT-NAME; PIC X(50), space-padded to that width` |
| Generator prompt | `String tranMerchantName()  // PIC X(50) -- pad to this width, do not emit a short value` |

**Why optional here and required for `guard_condition`** (ADR-0022): a guard is LLM judgment, where
a missing key and a considered `null` must look different. A width is deterministic — the producer
always fills it — so there is no silence to distinguish, and a required key would break every
existing design for no signal. The asymmetry is deliberate, not an oversight.

**`CobolText`, beside `CobolArithmetic`.** The semantic lives in one place, per that class's own
stated rationale. `pad` truncates on the **right** — the opposite end from a numeric `MOVE`, which
discards high-order digits — and `null` maps to spaces, because a COBOL `PIC X` field has no null
state and mapping it to `""` would reintroduce the defect. 7 Java tests; the template now runs **20**
(`mvn -B test`, `BUILD SUCCESS`).

**The helper would have been invisible.** `render_target_api_facts` read one hardcoded class, so
adding a second would have produced a helper written, tested and unreachable — G21 exactly. It now
takes a list, and a test asserts both classes and both new signatures appear in the prompt.

**The suite caught a violation in the fix itself.** `CobolText`'s first Javadoc explained the
defect using the program and field names it was found in, and
`test_the_template_carries_no_tenant_vocabulary` failed on it. That guard exists because the
template is the scaffold *every* tenant's generated code is seeded into — ADR-0001's boundary, and
the inverse of `guardrails.py`'s concern. The explanation is unchanged in substance and now names no
tenant. Worth recording because the violation was in documentation, which is where this rule is
easiest to break without noticing.

**Not claimed:** that any generated body pads today. No model has been asked to translate
`MOVE SPACES` since the width and the helper existed. What is verified is that the fact and the
primitive now reach the generator, and that the template builds and tests them.

### G26 — the composite gains `Account` and `CardXref`, and two fields stay out of reach

**What was verified.** `1300-B-WRITE-TX` builds the `Tran` this step returns, and two of its moves
read records the composite did not carry: `ACCT-ID` → `TRAN-DESC`, and `XREF-CARD-NUM` →
`TRAN-CARD-NUM`. Both are now reachable as `item.account().acctId()` and
`item.cardXref().xrefCardNum()`, asserted against the real COBOL text and the real `pic_mapper`
entities rather than against the composite alone — so the test fails if either the copybooks or the
paragraph move underneath it.

**Two of the four flagged fields cannot be fixed this way, and a test says so by name.** A composite
carries *existing domain entities* (ADR-0020), and neither of the remaining two is one:

| Field | Source | Why no composite reaches it |
|---|---|---|
| `TRAN-ID` | `STRING PARM-DATE, WS-TRANID-SUFFIX` | A job parameter and a per-run counter. The model separately noted a stateless processor cannot produce a monotonic suffix correctly under restart or partitioning — a stronger objection than reachability |
| `TRAN-ORIG-TS` / `TRAN-PROC-TS` | `Z-GET-DB2-FORMAT-TIMESTAMP` | Its target fields are `REDEFINES`, which the construct matrix routes to a human gate rather than translating |

The real remainder is a design decision left unmade: either `1300-B-WRITE-TX` becomes its own step
with access to job parameters and a clock, or those fields are populated outside translated logic.

**Widening the composite broke the renderer twice, and both were found by compiling.**

1. It **refused** unbound components. Correct while the composite was exactly balance-plus-rate;
   wrong once it carried types the interest arithmetic does not read. Those are now constructed from
   placeholders — refusing them would force the test's composite to differ from the design's, which
   is the one thing a test rendered from the design must never do.
2. The import set was derived from the **bound** entities only, so the rendered file constructed two
   types it had not imported — `cannot find symbol`, twice, from javac. Now guarded by asserting
   every `new X(` in the file has a matching import, which is the form that survives the next
   component someone adds rather than a fix for these two names.

Neither was visible by reading the renderer. Both are the same shape as PR #30's finding that local
green is structurally weaker than a real build for anything touching Java.

### G27 — the accumulator's owning step, and what giving it one exposed

**The gap said the accumulator needs an owning step. Giving it one showed the owning step would have
been invisible.**

`1050-UPDATE-ACCOUNT` does `ADD WS-TOTAL-INT TO ACCT-CURR-BAL`. It cannot be an `ItemProcessor` — a
stateless per-item processor holds nothing across items — so any step owning it must carry a
non-processor role. And `generate` skipped every non-processor with a bare `continue`, appending no
outcome at all.

**Measured, not inferred.** A design of one processor plus one writer reported:

```json
{"status": "ok", "steps_total": 1, "steps_compiled": 1}
```

byte-identical to a design containing nothing else. A human approving that at control-plane's gate
saw a complete success over a job whose account update had never been generated.

**The test was written to fail first**, and it did, for the right reason — `Right contains one more
item: 'updateAccount'` — before any fix existed.

**What changed** (ADR-0023): a non-processor step now produces a `StepOutcome` with status
`not_generated`, carrying its role and its `source_paragraphs`, and `GenerateCliResult` gains
`steps_not_generated`. `succeeded` is measured over *generable* steps, so a declared writer does not
flip a run to failure — most non-processors really are wiring, and failing on them would train a
reviewer to ignore the signal.

**An old test had encoded the defect as a requirement.** `test_non_processor_steps_are_skipped_rather_than_failed`
asserted `len(outcome.outcomes) == 1` — i.e. that the reader vanished. It now asserts the reader is
**both present and non-fatal**, which is what it was actually protecting.

**G27 is closed for reporting and open for generation, and that split is deliberate.** Rendering a
stateful control-break writer is a design question before a code one — Spring Batch's chunk
boundaries do not align with COBOL's account breaks — and ADR-0019 scopes this pipeline to
processors. The gap is now **visible at the gate** rather than invisible everywhere, which is the
difference between a known limitation and a defect. The balance arithmetic itself is still not
generated, and nothing here claims otherwise.

### The oracle's first record was not reproducible, and nothing could have seen it

**What was wrong.** `tests/fixtures/golden/CBACT04C/oracle/transact.dat`, committed by PR #56,
carried `900014.55` in record 0's `TRAN-AMT`. The balance that produces it is `1164.70` at
`15.00`, which truncates to `14.55`.

**Measured, not argued.** The pinned image was re-run against the same tenant fixture (input
hashes identical to the fixture's own `PROVENANCE.md`):

```
docker run --rm -v <repo>/tests/fixtures/tenant_repo_sample/app:/src:ro \
                -v <repo>/tools/cobol-oracle:/co:ro -v <out>:/out \
                cobol-oracle:gnucobol3 sh /co/run-oracle.sh
```

Four fresh runs plus the previous session's own leftover output — **five samples** — all write
`00000001455`. They agree with each other on **every non-timestamp byte of all fifty records**, and
their `tcatbal-posted.dat` and `acctdata-posted.dat` are byte-identical to the committed ones. The
committed `transact.dat` differs from all five in **one byte**.

**The fixture disagreed with itself.** `acctdata-posted.dat` is identical across every run, and the
account it belongs to gained `14.55`, not `900014.55`. Cause not established; a single stray digit
in one digit position of the first record written, varying between processes, is consistent with
uninitialised storage on the first store — but that is a hypothesis and is recorded as one.

**Why no existing check saw it.** Every assertion the fixture had asked whether it looked plausible
*alone*: fifty records, fifty distinct amounts, at most one zero, `TRAN-SOURCE` padded. All four
pass on the wrong file. This is the check-that-cannot-fail pattern once more — this time in the
artifact rather than in the code — and the instrument that finally caught it was, again, a number
compared against another number rather than a review.

**The check now in the suite.** `run-oracle.sh` unloads the account file *between* the stages
(`acctdata-stage1.dat`, committed), so the interest CBACT04C posted per account is measurable.
`1050-UPDATE-ACCOUNT` does `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` and nothing else in stage 2 touches
a balance, so each account's balance gain **is** the interest it was written. Both sides are values
COBOL produced; Python only sums exact two-decimal values. Recomputing
`(TRAN-CAT-BAL * DIS-INT-RATE) / 1200` here is what ADR-0021 forbids, and this deliberately does
not do it.

**Shown to fail first.** Restoring the old fixture and running
`pytest tests/system/test_cobol_oracle_comparison.py -k credited_exactly`:

```
AssertionError: 00000000001: balance moved 14.55, transactions total 900014.55
```

— the account named, both numbers printed, and the direction of the disagreement visible.

**It found a defect in the tenant program on its first run.** `CBACT04C`'s main loop is
`PERFORM UNTIL END-OF-FILE = 'Y'` with `PERFORM 1050-UPDATE-ACCOUNT` in the `ELSE` of
`IF END-OF-FILE = 'N'`. `1000-TCATBALF-GET-NEXT` sets the flag at EOF, the loop condition is then
true, and the loop exits — so that `ELSE` is unreachable and the **last account is written interest
it is never credited** (account `00000000050`, `18.76`). Pinned by
`test_the_last_account_is_written_interest_it_is_never_credited`, because ADR-0027's
`postAccountInterest` step posts every account including the last: a future balance comparison will
differ here for a reason that is COBOL's defect, not the translation's.

**Also fixed, found by trying to reproduce the fixture.** `run-oracle.sh` was CRLF in a Windows
working tree (`core.autocrlf=true`), so the container failed at `set -eu` with
`set: Illegal option -`. `.gitattributes` now pins it LF, as it already did for `mvnw`. And the
stage-2 account unload had no count assertion; it has one now, for the reason the 94-row finding
already established.

**Suite after the change**: `pytest tests/system/test_cobol_oracle_comparison.py -q` → **21 passed**.

### The round trip, run — generated logic inside hand-written wiring

**What ran.** `tests/system/test_hand_written_round_trip.py` generates `CBACT04C`'s two processors
through `run_generate`, copies the hand-written wiring from `tests/fixtures/handwritten/CBACT04C/`
into the generated project, and builds and runs it with real Maven over the oracle's own inputs
(`tcatbal-posted.dat`, `acctdata-stage1.dat`, plus the corpus's untouched `discgrp.txt` and
`cardxref.txt`). The job writes each generated `Tran` as JSON; the Python differential
(`docs/adr/0029`) compares it field-for-field against `transact.dat`.

```
JAVA_HOME=... pytest tests/system/test_hand_written_round_trip.py -q
3 passed in 69.58s
```

**The result, with the qualifiers that belong to it.** **500 of 500 comparable fields matched across
50 records; 3 fields excluded by ADR-0026** (`TRAN-ID` and both timestamps). `describe_result`
renders it as *"500 of 500 fields matched; 3 excluded by decision; wiring hand-written (ADR-0030),
bodies scripted rather than model-authored"*, and that string exists so the number cannot travel
without them. **This is not "the platform generated a working program."** The wiring — reader,
writer, two step beans, job bean — is hand-written, because nothing renders it (G31); and the method
bodies are step 45's scripted fixtures, so what a *model* would write remains an open question
needing a live call.

**It found a defect on its first run, and no other check here could have.** `TRAN-SOURCE` is
`PIC X(10)` (`CVTRA05Y:8`) and the completion body wrote a bare `"System"`, so **fifty records
disagreed on that field while every amount matched**:

```
record 0 TRAN-SOURCE: got 'System' want 'System    '
... 450 of 500 fields matched
```

The eval judge flagged that same body defect in PR #44 (audit R2.27) and the copybook had said so
all along — but the equivalence test asserts on `tranAmt` alone, so nothing in the suite could fail
on it until a whole record was compared against COBOL's own. Third independent agreement on one
defect, and the first one that a machine could act on.

**The guard is exercised by the run, not asserted about it.** 94 balance rows go in and 50 records
come out, because `IF DIS-INT-RATE NOT = 0` is running in generated Java. The record-count check
happens before the field comparison, so "the job wrote nothing" cannot present as fifty mismatches.

**Three defects found only by running it**, each costing one Maven cycle and each recorded in the
wiring's `README.md` as a finding rather than a fix:

1. `BatchApplication` component-scans `com.modernized.batch`, so the hand-written `@Configuration`
   joined the context of every Spring Boot test in the generated project and `BaselineStackTest`
   failed to load. ADR-0030's first bound arriving through a side door — the wiring is now gated
   behind a `handwritten-wiring` profile.
2. `TaskExecutorJobOperator` refuses to initialise without a `JobRegistry`.
3. `MapJobRegistry` registers every `Job` bean itself, so registering the job explicitly throws
   `DuplicateJobException` — "register the thing you built" is the obvious shape and it is wrong.

**What the stopgap is for.** ADR-0030's third bound requires every fact the wiring needed that
`design.json` lacks to be recorded, so the eventual renderer starts from practice rather than a
design session. Five are listed in `tests/fixtures/handwritten/CBACT04C/README.md`: no byte offsets
or record lengths (only widths, and contiguity happens to hold for these four copybooks); no
statement of where data comes from or how the four composite components join; no store for the step
chain's intermediate type; the `'DEFAULT'` rate fallback and abend-on-missing-lookup are business
rules left to wiring; and the Spring Batch 6 facts above.

**Still true after this run**: `0 of 4 programs round-trip`. The candidate exists and matches, and
the two things standing between this and the metric are a rendered wiring layer (G31) and a
model-authored body.

*(superseded 2026-08-19 — both of that sentence's two blockers were removed the same day: the live
run supplied model-authored bodies, and the account half closed the "only half the output is
compared" objection. On the maintainer's decision the count is now reported as **`1 of 4`, wiring
hand-written**, which is exactly what ADR-0030 bound 2 permits. The entry is left standing rather
than edited, because what it recorded was true when it was written.)*

### The same round trip, with a model writing the bodies

**The run.**

```
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 JAVA_HOME=... \
  pytest tests/system/test_hand_written_round_trip.py -q -s -k live
1 passed in 164.37s
```

```
live round trip: 500 of 500 fields matched; 3 excluded by decision;
  wiring hand-written (ADR-0030), bodies model-authored
  steps and attempts: {'computeInterest': 1, 'completeTransaction': 1}
  2 model call(s), 7563 tokens, notional cost 0.5766754
```

**What is now established.** Two `claude-opus-5`-authored method bodies, placed in hand-written
wiring, produce **every comparable field of all fifty transaction records exactly as the unmodified
`CBACT04C` wrote them under GnuCOBOL**. Both compiled on the first attempt — the heal loop did not
run — and the differential is the one ADR-0029 built and demonstrated to fail on a one-cent change,
a short alphanumeric and a record-count mismatch.

**Identical to the scripted run in everything but the bodies.** Same design, same wiring, same
inputs, same comparison, one shared `wire_build_and_run`. That is deliberate: if any of those
differed, a disagreement between the two runs would not be attributable to the model.

**The notes are the other half of the result.** The model flagged three gaps unprompted, and every
one of them is a decision this repo had already recorded:

1. **`ADD WS-MONTHLY-INT TO WS-TOTAL-INT` is not implemented**, because a stateless `ItemProcessor`
   has nowhere to hold an accumulator that spans an account's records, and *"reproducing it here with
   a field would be order- and restart-dependent"*. That is ADR-0027's reasoning, arrived at
   independently.
2. **`TRAN-ID`, `TRAN-ORIG-TS`, `TRAN-PROC-TS` are job-level facts, not item-level ones** — a run
   parameter, a monotonic counter and a clock read — and *"reading a clock or a counter inside
   process() would make the same input produce different records across runs and across a chunk
   restart"*. That is ADR-0026 and `NonDeterministicBodyError`, restated by the model that would have
   tripped them.
3. **It emitted PIC-width spaces rather than empty strings** for fields it could not fill, and said
   so. G28's lesson, applied without being asked for that field.

It also stated the arithmetic reasoning explicitly: the product is exact at scale 4, the division is
the only lossy step, and it truncates *"since the `COMPUTE` has no `ROUNDED`"* — ADR-0021's semantic,
volunteered.

**What this does not establish**, and the list is short and specific:

- **The wiring is still hand-written** (G31). Nothing renders readers, writers, steps or jobs, so
  what ran is generated logic inside a human's scaffolding.
- **Only half of `CBACT04C`'s output is compared.** The program writes transactions *and* rewrites
  the account file; this compares `transact.dat` and not `acctdata-posted.dat`. The account posting
  step (ADR-0027) is not in this job, which the model's first note independently identifies.
- **One program, one run.** ADR-0024's lesson about the judge applies here too: one sample of an
  instrument is not a measurement of its reliability, though unlike the judge this one is checked
  against a fixed oracle rather than against itself.

**Cost, recorded because this repo records it**: `$0.5766754` notional for 2 calls / 7,563 tokens,
inside a `RunBudget(max_model_calls=8)` ceiling that was never approached.

### The account half, and a divergence pinned rather than smoothed

**Half of `CBACT04C`'s output was still unmeasured.** It writes interest transactions *and* rewrites
the account master; the first round trip compared `transact.dat` only. ADR-0027's
`postAccountInterest` step now runs as a third step in the same hand-written job, over items whose
interest is already summed by an aggregating reader, and its output is compared against
`acctdata-posted.dat`.

**The result: `598 of 600` fields, and nothing excluded.** The transaction record gives up `TRAN-ID`
and both timestamps by ADR-0026; the account record gives up nothing, so every one of its twelve
fields has to match by being right rather than by being skipped.

**The two that differ are one defect, and it is COBOL's.**

| field | candidate | COBOL |
|---|---|---|
| `ACCT-CURR-BAL` (record 49) | 2060.06 | 2041.30 |
| `ACCT-CURR-CYC-CREDIT` (record 49) | 0 | 1549.30 |

`CBACT04C`'s loop is `PERFORM UNTIL END-OF-FILE = 'Y'` with `PERFORM 1050-UPDATE-ACCOUNT` in the
`ELSE` of `IF END-OF-FILE = 'N'`, so that branch never runs and the last account is never posted --
the defect PR #59's cross-artifact identity found. The paragraph does exactly three things (add the
interest, zero the cycle credit, zero the cycle debit) and **the divergence set is exactly its write
set**, minus the field that was already zero. The balance difference is `18.76`, which is precisely
that account's interest read off the transaction oracle.

**The cheap option was available and refused.** Making the reader skip the last account would have
turned this green by encoding a bug, and the number would then describe the wiring rather than the
generated logic. `assert_account_half_matches_except_the_last` pins the shape instead: one record,
fields confined to that paragraph's writes, balance difference equal to the uncredited interest. A
second diverging account, a different field, or a different amount all fail.

**Both halves, with model-authored bodies:**

```
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 JAVA_HOME=...   pytest tests/system/test_hand_written_round_trip.py -q -s -k live

live round trip: 500 of 500 fields matched; 3 excluded by decision;
  wiring hand-written (ADR-0030), bodies model-authored
  steps and attempts: {'computeInterest': 1, 'completeTransaction': 1, 'postAccountInterest': 1}
  account half: 598 of 600 fields matched; 0 excluded by decision
  3 model call(s), 9802 tokens, notional cost 0.7626982
```

All three bodies compiled on the first attempt. Session total for both live runs: **$1.339**.

**The third body's notes flagged something the first run's did not**: that `1300-COMPUTE-INTEREST`
unconditionally performs `1300-B-WRITE-TX`, so it included that paragraph's field moves and said
*"if the design intended `1300-B-WRITE-TX` to be a separate step, this body needs to be split"* --
which is exactly the split PR #43 made. The comparison is unaffected because `completeTransaction`
rebuilds every field, but the model located a design ambiguity from the COBOL alone.

### `CBTRN02C`'s own output captured, and the oracle re-run to prove the addition changed nothing

**What was missing.** Stage 1 has always run `CBTRN02C`, and two of its outputs were already
committed because `CBACT04C` needs them as *inputs* — the posted `tcatbal` and the account file
between the stages. Its **primary** output, the transaction master it `OPEN OUTPUT`s and writes
every accepted daily transaction to, went to `/work/idx/tranmaster` and vanished with the container.
A comparison for that program had two of its three in-scope targets and no way to check the third.
(`DALYREJS` is the fourth and is out of scope for generation by ADR-0038.)

**What was added.** `tools/cobol-oracle/UNLOADTR.cbl`, beside the existing `UNLOADTC` and `UNLOADAC`
and built the same way — `ORGANIZATION IS SEQUENTIAL` on output, because `LINE SEQUENTIAL` trims
trailing spaces and a 350-byte record would come out short with every field past the last non-blank
lost. `run-oracle.sh` runs it immediately after stage 1's account snapshot and **asserts the count**:

```
expect_count_in stage1-tran-unload.log "TRANFILE unloaded" 257
```

257 rather than 300 because `CBTRN02C` rejects 43 — the program's own reported figures, and the
assertion is what makes a partially-posted run fail loudly instead of producing a plausible file of
individually-correct records.

**The run, and what it proves.** The pinned image, same command and same read-only mounts as the
regeneration above:

```
docker run --rm -v <repo>/tests/fixtures/tenant_repo_sample/app:/src:ro \
                -v <repo>/tools/cobol-oracle:/co:ro -v <out>:/out \
                cobol-oracle:gnucobol3 sh /co/run-oracle.sh
```

Reached `--- done ---`, every count assertion passing, and produced `transact-stage1.dat` at
**89,950 bytes = 257 × 350**.

**Three of the four committed artifacts came back byte-identical** — `acctdata-stage1.dat`,
`acctdata-posted.dat` and `tcatbal-posted.dat` all `cmp`-clean against the files already in the
fixture. `transact.dat` differs, and it differs in exactly the place the fixture's own
*Known-unverified* section names: the two DB2 timestamps.

```
new:  ...96802941546036972026-08-21-11.59.05.590000 2026-08-21-11.59.05.590000
old:  ...96802941546036972026-08-19-00.20.25.590000 2026-08-19-00.20.25.590000
```

Both are `FUNCTION CURRENT-DATE`, both are already `EXCLUSIONS` entries citing ADR-0026, and no
other byte of any of the 50 records moved. That is the evidence the pipeline change is inert: adding
an unload after stage 1 did not perturb what either program wrote.

**The whole set was re-committed rather than the one new file**, because `PROVENANCE.md` is written
by the run and must describe the artifacts beside it. Committing a new file with an old provenance —
or a new provenance with an old `transact.dat` — would leave a directory whose contents came from
two different runs and whose own record said otherwise. The regenerated provenance carries the three
unchanged hashes verbatim, which is itself the byte-identity claim in the artifact.

**Verification**: `pytest tests/system/test_cbtrn02c_oracle.py -q` → **4 passed**; the existing
oracle suites (`test_cobol_oracle_comparison`, `test_cobol_oracle_check`, `test_interest_oracle`) →
**36 passed** against the regenerated fixture.

The new module checks the fixture against the run's own report rather than against a number typed
into it — records written must equal *processed minus rejected*, both parsed out of `PROVENANCE.md`
— and **both of its checks are shown to fail first**, on in-memory damaged copies: one record short
breaks the identity, and two records swapped break the key-order assertion. Damaged in memory
deliberately: the fixture is the artifact, and a test that rewrites it to prove a point is a test
that can leave it rewritten.

**A property pinned for later, not decided here.** `UNLOADTR` reads an INDEXED file `ACCESS MODE IS
SEQUENTIAL`, so the oracle is in **key order**. A rendered writer that appends records as it
produces them emits the same records in a different order. That is ADR-0037's stated open question,
and this makes the premise it rests on a checked fact rather than an assumption.

### `CBTRN02C` decides what to accept from state it is writing, and the corpus says by how much

**Why this was measured before anything was generated.** With the write modes fixed (ADR-0037) and
the transaction master captured, the next step would have been to declare this program's steps and
render them. This measurement is what says whether such a job could reproduce the program — taken
first, because the answer changes what is worth building.

**The mechanism, read off the source.** `1500-B-LOOKUP-ACCT` computes
`ACCT-CURR-CYC-CREDIT - ACCT-CURR-CYC-DEBIT + DALYTRAN-AMT` against `ACCT-CREDIT-LIMIT`, and those
cycle fields are what `2800-UPDATE-ACCOUNT-REC` `ADD`s to and `REWRITE`s for every accepted
transaction. The decision for transaction *n* reads what *1..n-1* wrote. `2700-UPDATE-TCATBAL` is
the same shape against `TCATBAL`, which is also where ADR-0037's `upsert` came from.

**The proof uses committed artifacts, not a replay of the program.** `transact-stage1.dat` holds
exactly the transactions `CBTRN02C` wrote, each carrying its `DALYTRAN-ID`, so the rejected set is
*known* rather than modelled. Each rejected transaction was then judged the way a stateless
processor would have to judge it — on its own, against the account state the job started from:

```
daily transactions in the corpus                                  300
written by CBTRN02C (transact-stage1.dat)                         257
rejected                                                           43   all reason 0102 OVERLIMIT
rejected, yet passing the limit check against the INITIAL state    30
accepted, yet failing that check                                    0
```

**A stateless implementation therefore writes 287 records where the program writes 257** — and every
one of the 287 is individually correct. A field-level differential sees nothing; only the count
does, which is ADR-0037's blindness one level up.

The error runs one way only, and that is asserted rather than assumed: every accumulation here moves
the projected balance toward the limit, so ordering can turn an acceptance into a rejection and
never the reverse. A stateless run is a strict superset. If that assertion ever fails, the corpus has
gained a case where the two disagree in both directions and this entry understates the problem.

**What it rules out** is not a particular decomposition but every order-independent one: aggregation
computes sums over a transaction set whose *membership* is what the ordering decides.

**Verification**: `pytest tests/system/test_cbtrn02c_order_dependence.py -q` → **4 passed**,
including a discrimination case — an acceptance check that accepted everything would satisfy the two
central assertions without meaning anything, so the standalone check is shown to reject an
impossible amount and accept an impossibly negative one.

**Decision**: ADR-0039. The posting path is declared and refused by name via ADR-0023's
`not_generated` reporting; the round trip stays `1 of 4` with a specific cause rather than a vague
one. A mechanical check is deliberately not built — "reads a file it writes back" is true of
`CBACT04C`'s account writer too, which is correct and shipped, and the distinguishing fact (can two
input items reach the same written key) is not established by any parse here.

### The second program built and run, and where it disagrees with COBOL

**What ran.** `run_generate` produced `CBTRN02C`'s domain records and its one `ItemProcessor`; the
reader, the writer, the working set, the step and the job were rendered from `design.json`;
`tests/fixtures/handwritten/CBTRN02C/` supplied file paths and nothing else. Real Maven built it and
a plain `AnnotationConfigApplicationContext` ran the job to `COMPLETED`.

```
pytest tests/system/test_cbtrn02c_round_trip.py -q   ->  4 passed (mvn verify inside)
```

**This is what lifted ADR-0040's refusal.** That refusal existed because nothing could run such a
step correctly; ADR-0041 built what runs it, and the refusal came off when a build showed it working
rather than when an argument said it should.

**What matched.** 256 of the oracle's 257 transactions, and the account file exactly — 50 in, 50
out, no account created, dropped or duplicated. That last one is worth separating: it says the
`replace` write mode and the shared store handle an update correctly, independent of whether the
decision feeding them was right.

**What did not, and why it is the interesting half.** Seven decisions differ — six transactions
accepted here that `CBTRN02C` rejected, one the reverse — and the balance file holds 100 rows where
COBOL leaves 94.

**Every one of the six carries an amount whose last byte is a negative zoned-decimal overpunch**
(`}JKLMNOPQR`); the corpus has 50 such records. This pipeline reads that byte as a negative sign
(G16 finding 2, and `decode_signed`'s own docstring), so the projected balance *falls* and the
credit-limit check passes. GnuCOBOL rejected them as `0102 OVERLIMIT`, which is only possible if it
**added** the amount rather than subtracting it. The seventh divergence carries no overpunch and
follows from the other six: once a transaction is accepted that should not have been, every later
decision reads a different running balance — this program's order dependence (ADR-0039) amplifying a
single-record disagreement into a seventh.

**The oracle's bytes cannot settle whose reading is right.** An input `0000009190}` comes back as
`00000091900` — the same magnitude, with no overpunch at all — and the fixture's own `PROVENANCE.md`
already lists *"the zoned-decimal sign representation on REWRITE"* among the things **not**
corroborated by ADR-0021's hand-derived table. So the disagreement is about the tenant's compiler,
not about this code, and it is recorded rather than resolved. Resolving it needs either IBM
Enterprise COBOL or a hand-derivation of the kind ADR-0021 built for the interest arithmetic.

**The counts are asserted at what the run produces, not at what it should produce.** A test
asserting 94 against a pipeline that writes 100 is a failing test with no diagnosis in it, and the
diagnosis is the finding. The characterisation is asserted too — *every* extra record's amount ends
in a negative overpunch — so a change that alters the cause fails rather than shifting a number.

**The round-trip metric does not move.** `CBTRN02C` is not `2 of 4`: two of its three comparable
outputs differ, for a reason now named precisely instead of generally.

### The overpunch, hand-derived and then put to the compiler

**The question.** `CBTRN02C`'s run and its oracle disagree on seven decisions, all of them on
amounts whose final byte is a sign overpunch. Which side is right is a question about a number, so
it got ADR-0021's treatment: derive it by hand, write the literals down, make the runtime match
them.

**Four independent lines, none of which needs a mainframe.**

1. **The standard.** `PIC S9(09)V99` carries the sign on the trailing digit: `{` is `+0`, `A`-`I`
   are `+1`..`+9`, `}` is `-0`, `J`-`R` are `-1`..`-9`. So `0000005047G` is **504.77**, not 504.70.

2. **The corpus corroborates the table from its own construction.** All twenty characters appear in
   `dailytran.txt`, and in every record the digit *before* the overpunch equals the digit the
   overpunch carries -- `...4161A` reads 416.**11**, `...0709R` reads -70.**99**. A wrong table
   breaks that for eighteen of the twenty. It breaks for none.

3. **GnuCOBOL's own arithmetic carries the loss.** Account `00000000030` has exactly one posted
   transaction, `0000000294D` (29.44), and a starting cycle-credit of zero. The oracle's run ends it
   at `000000002940` -- 29.40 -- and moves its balance 2.00 -> 31.40. The missing digit is inside a
   *computed total*, so the value was already wrong when it entered the `ADD`. Not an output
   formatting artifact.

4. **The compiler, asked directly** (`tools/cobol-oracle/OPTEST.cbl`, same image and dialect):

```
0000005047G (G = +7) ->        504.70
0000000567P (P = -7) ->         56.70
0000000294D (D = +4) ->         29.40
0000009190} (} = -0) ->        919.00
0000003250{ ({ = +0) ->        325.00
```

GnuCOBOL 3.1.2 reads the overpunch byte as digit `0` and drops the sign, `-std=ibm` included. Where
the carried digit is itself zero the magnitude survives, which is exactly why `CBACT04C` -- whose
signed inputs all end in `{` or carry no overpunch -- is untouched.

**Verification**: `pytest tests/system/test_overpunch_derivation.py -q` -> **23 passed** (twenty
hand-derived literals plus three structural checks). `./mvnw test -Dtest=CobolRecordTest` ->
**16 passed**, the same twenty literals against the decoder that ships *inside every generated
project*, which is what a migrated program reads its own input with.

**The literals are hand-written, not computed**, and the module says so: deriving them with the
decoder under test would compare two renderings of one interpretation -- ADR-0021's refused option
(c), the check that cannot fail.

**What it changes**: nothing in this repo's decoders, which agree with the standard. ADR-0043 puts
the fix in the oracle pipeline beside `LOADIDX` and `DALYCONV`, where the corpus's other
representation quirks are already normalised. **The round-trip metric stays `1 of 4`** -- "our
number is right and the oracle is wrong" is the claim that needs the strongest evidence, not the
loudest assertion, and even four lines of it do not license moving a number that means *measured
against COBOL*.
### The overpunch converted, and both programs re-measured against a faithful oracle

**The decision, not the change.** ADR-0043 located this fix and deliberately did not make it: the
oracle's runtime cannot read the corpus's IBM sign overpunches, and correcting that would re-measure
`CBACT04C`'s green `500 of 500` against a regenerated fixture. The trade was a known-green
measurement for a faithful instrument, and it was taken because ADR-0028's whole case for trusting
this oracle is that it is COBOL's own output — which, for these fields, it was not.

**Reconnaissance first, per ADR-0044.** Every signed field in every corpus file was scanned before
anything was written, rather than the one field the defect surfaced in:

```
=== acctdata.txt  15050 bytes  50 records  all 300
  ACCT-CURR-BAL            plain-digit=0    overpunched=50   chars=[{]
  ACCT-CREDIT-LIMIT        plain-digit=0    overpunched=50   chars=[{]
  ACCT-CASH-CREDIT-LIMIT   plain-digit=0    overpunched=50   chars=[{]
  ACCT-CURR-CYC-CREDIT     plain-digit=0    overpunched=50   chars=[{]
  ACCT-CURR-CYC-DEBIT      plain-digit=0    overpunched=50   chars=[{]
=== tcatbal.txt   2599 bytes  50 records  all 50
  TRAN-CAT-BAL             plain-digit=0    overpunched=50   chars=[{]
=== discgrp.txt   2601 bytes  51 records  all 50
  DIS-INT-RATE             plain-digit=0    overpunched=51   chars=[{]
=== dailytran.txt 105300 bytes 300 records all 350
  DALYTRAN-AMT             plain-digit=0    overpunched=300  chars=[ABCDEFGHIJKLMNOPQR{}]
```

`{` is `+0`, the one overpunch where the lossy reading and the correct one agree. **Three of the four
files needed nothing, and that is measured rather than assumed** — the previous claim that
`CBACT04C` was unaffected was true, but the test asserting it read the wrong byte (below).

**The target table, asked of the compiler** (`tools/cobol-oracle/SIGNTEST.cbl`, same image and
dialect). Both halves, because they are not symmetric:

```
(a) what this runtime WRITES -- negative
    01 raw=[0000000000q] last=[q] ord=113 val=        -0.01
    ...
    09 raw=[0000000000y] last=[y] ord=121 val=        -0.09
    00 raw=[00000000000] last=[0] ord=048 val=         0.00     <- never writes a negative zero
(b) what this runtime READS, on 0000009190 + byte
    last=[p] ->       -919.00
    last=[0] ->        919.00
```

It writes plain digits for positives and `q`–`y` for −1..−9, and never emits `p` because `COMPUTE`
collapses −0 to +0 before the store. It **reads** `p` as −0 regardless. That asymmetry decided one
mapping: the corpus's `}` is −0, and half (a) alone would have argued for mapping it to `0`, which
reads back as **+919.00** where the corpus means −919.00.

**The conversion is bidirectional.** `SIGNCONV` converts `DALYTRAN-AMT` on the way in, after
`DALYCONV` frames the records; `SIGNBACK` converts every output back, so the oracle directory is in
the corpus's representation and GnuCOBOL's own sign bytes never leave the container. The reason for
the second half is that `tcatbal-posted.dat` and `acctdata-stage1.dat` are **inputs to the generated
Java** — leaving them in the runtime's encoding would have taught `CobolRecord.number` a test
harness's representation, inside every migrated program this platform ships.

**Command**:

```
docker run --rm -v <repo>/tests/fixtures/tenant_repo_sample/app:/src:ro \
                -v <repo>/tools/cobol-oracle:/co:ro -v <out>:/out \
                cobol-oracle:gnucobol3 sh /co/run-oracle.sh
```

**Real output**, every count asserted by the script rather than displayed:

```
--- sign-position fingerprint of the corpus ---
sign fingerprint OK
DALYTRAN signs converted:    300
DALYTRAN signs negative:      50
TRANSACTIONS PROCESSED :000000300
TRANSACTIONS REJECTED  :000000038
SIGNBACK TRN records:    262   negatives:  50
SIGNBACK TCB records:    100   negatives:  50
SIGNBACK ACC records:     50   negatives:  53   (stage 1)
SIGNBACK ACC records:     50   negatives:   4   (stage 2)
TCATBALF unloaded:       100
```

**What moved, and every one of them moved toward what the generated pipeline already produced:**

| | before | after |
|---|---|---|
| daily transactions rejected | 43 | **38** |
| transaction master records | 257 | **262** |
| `TCATBAL` rows after posting | 94 | **100** |
| rows `CBTRN02C` creates | 44 | **50** |
| `CBTRN02C` exit code | 4 | **0** |

The exit code is worth its own line: `run-oracle.sh` allowed 4 explicitly because the program
returned it while completing normally and the meaning was not established. With faithful amounts it
returns 0. **The warning code was itself a symptom.**

**The three refusals were shown to fire, on deliberately damaged input** — a corpus with `Z` in one
sign position:

```
ABORT: /src/data/ASCII/dailytran.txt byte 143 holds [ABCDEFGHIJKLMNOPQRZ{}],
       expected [ABCDEFGHIJKLMNOPQR{}]                      <- guard, before anything is produced
ABORT: SIGNCONV record 000001 carries [Z] in the sign position, which is not an IBM overpunch
signconv exit=16, output file 0 bytes                       <- reached by bypassing the guard
ABORT: SB_SHAPE is [XXX], expected ACC, TCB or TRN
```

**`CBACT04C` was re-verified first**, which was the condition on taking this at all:

```
$ pytest tests/system/test_hand_written_round_trip.py -q -s
round trip: 500 of 500 fields matched; 3 excluded by decision
account half: 597 of 600 fields matched; 0 excluded by decision
8 passed, 1 skipped in 57.42s
```

**`500 of 500` stands. The account half moved 598 → 597, and the cause is the same single one.**
`1050-UPDATE-ACCOUNT` writes three fields and its account-break post sits in an unreachable `ELSE`,
so the last account keeps them all. Two of the three used to differ; the third matched because the
oracle's cycle total for that account happened to be zero. With the corpus's real amounts it is not,
so the divergence now shows in every field it can reach. The guard that pins this is unchanged and
still passes: every mismatch on that one record, every field in that paragraph's write set, and the
balance differing by exactly the uncredited interest.

**`CBTRN02C` now agrees exactly:**

```
$ pytest tests/system/test_cbtrn02c_round_trip.py -q
6 passed in 41.09s
```

262 of the oracle's 262 transactions, **every amount equal by value**, 100 balance rows against the
oracle's 100, and the account file exactly. Before this change the same run produced 262 and 100
against an oracle holding 257 and 94, with seven decisions differing. **The pipeline did not move.**

**A near-miss found while re-checking the claim.** `test_cbact04c_is_not_affected_by_it` asserted
that `tcatbal.txt`'s balances *"end in a plain 0 and are not overpunched at all"* — and passed,
because it sliced `line[17:29]`: twelve bytes for an eleven-byte `PIC S9(09)V99`. It was reading the
`FILLER` after the field. Every one of those balances ends in `{`. The conclusion was right and the
evidence for it was not, which matters because that check was cited for `500 of 500`. Corrected, and
widened from three signed account fields to the five `CVACT01Y` actually declares.

**Why the metric stays `1 of 4`.** `CBTRN02C`'s transactions are compared on record identity and
`TRAN-AMT`, not field-for-field at full declared width the way `CBACT04C`'s are. Two of
`CBACT04C`'s three ADR-0026 exclusions do not transfer — `CBTRN02C` copies `TRAN-ID` and
`TRAN-ORIG-TS` straight from its input, so inheriting them would excuse fields this program
reproduces exactly. The count moves when that comparison exists, and not for a number that looks
good without it.
