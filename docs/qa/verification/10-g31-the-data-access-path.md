# G31 — the data access path, stage by stage

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### Step 39a — the context budget, measured rather than assumed

**What was open.** Pillar 3 (Context Window Engineering) had been deferred to a precondition
ADR-0016 removed, leaving it with no owner (gap G11). The deliverable the plan asked for was *a
measured budget and a documented behaviour when it is exceeded*.

**The measurement, taken without calling a model.** The real `generate` prompts are rebuilt through
the same `author` seam every other test uses, so this costs nothing and can run in CI:

```
JAVA_HOME=... pytest tests/integration/test_context_budget.py -q -s
generate prompts (chars): [85215, 84928, 84938]; ceiling 600,000
6 passed
```

| | characters |
|---|---|
| largest `generate` prompt (`CBACT04C`) | **85,215** |
| largest `design` prompt (step 37g) | 81,975 |
| ceiling adopted (ADR-0031) | **600,000** |

The three prompts differ by **under 0.4%**, independently consistent with the 99.8% shared prefix
PR #28 recorded from cache behaviour — derived here from the prompts themselves rather than from a
bill, and asserted, so an edit that breaks the shared prefix fails a test instead of showing up as a
cost increase nobody attributes to it.

**The policy** (ADR-0031): characters rather than tokens, because this repo has no tokenizer and the
SDK's counter is a network call — a guard that costs a round trip is a guard that gets disabled.
Enforced in `call_model`, the single place this repo talks to a model, so every node and both
backends are covered by one check. `PromptBudgetExceededError` is raised **before** the call, so an
oversized prompt costs nothing.

**Truncation is refused permanently**, and that is the substance of the decision rather than a
footnote: a truncated prompt produces a call that succeeds, bills, and answers a question missing
its tail, which nothing downstream can distinguish from a model that did worse.

**Shown to guard rather than to report.** Both backends are replaced with functions that raise if
they are ever entered, so a check placed after backend selection — or absent — fails with a
different exception than the one asserted. The boundary is tested from the other side too: a prompt
of exactly `MAX_PROMPT_CHARS` must reach the (stubbed) backend, since an off-by-one in a ceiling is
the difference between a guard and an outage.

**Found by running it**: patching only the CLI backend let the test pass through to the SDK and fail
with an authentication error — an exception, but not the one under test, and a looser assertion
would have called that green. Both backends are patched now, with the reason recorded in the test.

**What stays an estimate.** No characters-per-token calibration point exists yet, because the live
runs recorded aggregate usage. The round-trip test now prints `input_tokens`, so the next live run
produces one at no extra cost. Until then the token figures carry their assumed ratio explicitly.

### G31 stage 2 — the design carries how each program reaches its data

**The gap.** ADR-0030: a reader cannot be rendered from a design that declares *which* entities a
composite carries and nothing about which is a stream, which are keyed lookups, or what the keys
are. Stage 1 (PR #63) parsed `FILE-CONTROL`; this puts the facts in the contract and gives them a
consumer in the same change.

**What `design.json` now carries** (`UnifiedDesign.file_access_paths`, schema **3.2.0**), for
`CBACT04C`:

| file | entity | lookup | effective key | declared key |
|---|---|---|---|---|
| `TCATBAL-FILE` | `TranCatBal` | no (the stream) | — | `FD-TRAN-CAT-KEY` |
| `XREF-FILE` | `CardXref` | yes | **`FD-XREF-ACCT-ID`** | `FD-XREF-CARD-NUM` |
| `ACCOUNT-FILE` | `Account` | yes | `FD-ACCT-ID` | `FD-ACCT-ID` |
| `DISCGRP-FILE` | `DisGroup` | yes | `FD-DISCGRP-KEY` | `FD-DISCGRP-KEY` |
| `TRANSACT-FILE` | — (written) | no | — | — |

**`effective_key` is the row that earns the parse.** `XREF-FILE` is declared on the card number and
read `KEY IS FD-XREF-ACCT-ID` -- the alternate. A renderer taking the declared key would compile and
find nothing, because the account id is what the program has in hand. That was finding **F2** from
the hand-written wiring, discovered by writing it; it is now read from the source.

**Found by running it**: the `KEY IS` phrase sits on the line *after* the `READ`, so the first,
line-scoped version of this parse reported the file and the record correctly and the key as `None` --
green, plausible, and missing the one fact the parse exists for. Fixed by joining a `READ` across its
continuation lines, which is also what surfaced `INVALID KEY DISPLAY ...`: it contains the word KEY
and would otherwise yield a key of `DISPLAY`.

**The type has a consumer in the same PR, deliberately.** This repo has produced a computed fact
that never reached its target four times (G21, G24, G28, G26), and once shipped a helper called by
nothing. `unobtainable_inputs` reads these paths and reports, as a gate item, any entity a step
consumes that no earlier step produces and no declared file yields -- the third link in a chain:
ADR-0020 checked a step's types *resolve*, PR #42 checked its data is *reachable*, this checks it can
be *obtained*.

**Both halves of that check are tested**, because either alone makes it useless: it stays silent on
the real three-step design (a check that fires on working input trains reviewers to ignore it) and
reports `Customer` when the composite is widened to an entity `CBACT04C` never reads. Step order
decides it -- reversing the chain makes `Tran` unobtainable for `completeTransaction`, the same
design failing only on order.

**One deliberate silence, which the review should look at.** `file_access_paths` defaults to empty so
a pre-3.2.0 design still validates, and a design carrying none reports nothing rather than
everything: no information is not the same as no access, and a check is least trustworthy exactly
where it knows least. Pinned by a test, alongside a case proving it is not silent in general.

**Verification**: `pytest tests/unit/test_file_control.py tests/unit/test_file_access_paths.py -q`
→ **44 passed**, `parsing/file_control.py` at **100%** including every refusal path.

**Limits, stated rather than implied.** Only `READ ... INTO` is parsed, so a file the program writes
appears as a declaration with no entity -- `WRITE ... FROM` is the writer side's fact and is not
parsed yet. The renderer itself is stage 3; nothing here renders a reader.

### G31 stage 3a — the record layout, checked against three independent derivations

**Finding F1, answered.** `DomainField` carried a width and no position, so a reader built from the
design had to assume fields are contiguous from byte zero. That held for the four copybooks the
hand-written wiring used **by luck** -- every `FILLER` in them is trailing -- and is false the moment
one sits in the middle. `DomainField.byte_offset` and `DomainEntity.record_length` now carry it
(schema **3.3.0**), computed by `parsing/record_layout.py`.

**Offsets are computed over every declaration, `FILLER` included**, which is what makes an interior
`FILLER` a non-event rather than a mis-slice of everything after it. Group items contribute nothing:
they own their children's bytes, and counting them would double every byte underneath.

**The verification is the part worth reading.** These numbers are not asserted against numbers this
module invented -- three independent derivations already existed:

1. `TRAN_LAYOUT` and `ACCOUNT_LAYOUT` in the differential, hand-written, and **validated by COBOL's
   own output**: the comparison built on them matches `transact.dat` 500 of 500 and
   `acctdata-posted.dat` 598 of 600. Wrong offsets could not have produced that.
2. The copybooks' own `RECLN` comments, read out of the file rather than restated in the test.
3. The hand-written wiring's Java constants, which the round trip runs through real Maven.

All three agree with the computed layout, field for field.

**Refusals**: `USAGE COMP`/`COMP-3` (two digits per byte, so sizing it as digits would misplace
every following field -- no record in this corpus has one, which is why the refusal needed a test),
and any record containing a field `pic_mapper` rejects. **Refused whole, never partial**: a layout
missing one width is not incomplete, it is wrong for every field that follows.

**Verification**: `pytest tests/unit/test_record_layout.py -q` -> **16 passed**,
`parsing/record_layout.py` at **100%**.

**One test corrected in passing.** Stage 2's `test_the_schema_version_records_the_addition` pinned
`SCHEMA_VERSION == "3.2.0"` and broke on the very next additive change -- a test that fails for
being right teaches people to edit tests. It now asserts the published schema carries
`file_access_paths` and that the version has moved past the release that introduced it.

### G31 stage 3c — the reader is rendered, and the round trip runs on it

**The measurement, unchanged in value and different in meaning.** The same round trip that reported
`500 of 500` transaction fields and `598 of 600` account fields now runs with the interest step's
`ItemReader` **rendered from design.json** instead of hand-written. Same oracle, same differential,
same numbers -- and the hand-written reader has been deleted rather than kept beside it, because two
readers would mean the result no longer says which one was measured.

```
JAVA_HOME=... pytest tests/integration/test_hand_written_round_trip.py -q -s
round trip: 500 of 500 fields matched; 3 excluded by decision; reader rendered from design.json,
  job and step wiring hand-written (ADR-0030), bodies scripted rather than model-authored
account half: 598 of 600 fields matched; 0 excluded by decision
8 passed, 1 skipped
```

**Nothing in the renderer is inferred.** Every offset, key and join it emits came from a fact the
three previous PRs parsed out of the COBOL and put in the contract:

| what the reader needs | where it comes from |
|---|---|
| which file yields which entity, stream or lookup | `FileAccessPath` — `FILE-CONTROL` + `READ ... INTO` |
| the key it is read by | `effective_key` — the read's key, not the declared one |
| what fills that key | `LookupKeyPart` — `MOVE ... TO` the key field |
| where each field sits, and how long a record is | `DomainField.byte_offset`, `DomainEntity.record_length` |

**Three findings became generated code rather than notes:**

- **F2** — the `XREF` lookup is indexed at offset 25, the *alternate* key. A reader built on the
  declared record key would compile and find nothing.
- **F4** — the `'DEFAULT'` retry is emitted as a second probe with the literal padded to the key's
  declared width, because a ten-byte key field holds `DEFAULT` plus three spaces.
- **The lookup order** — `DISCGRP`'s key is filled from `ACCT-GROUP-ID`, a field of the account
  record, so the account read is emitted first. Nothing declares that ordering; it is derived from
  which entity owns each key source, and a design where it does not resolve is refused.

**The refusals are the bulk of the test module, deliberately.** A reader that guesses a key, an
offset or an order compiles, runs, and differs from COBOL in ways only a differential catches. Eight
are covered: no access path for a component, a lookup whose key nothing fills, a key with no byte
position, a width mismatch between a key and its source (a `MOVE` there pads or truncates, and this
renders a straight copy), a key source from a record the step never reads, zero or two driving
streams, an entity with no record length, and a field with neither length nor precision.

**A runtime helper joined the template**: `CobolRecord`, beside `CobolArithmetic` and `CobolText`.
It holds the two rules a literal translation loses -- a record is a fixed number of bytes rather than
a line, and a signed zoned field carries its sign in its last digit. 10 tests, run by the template's
own build.

**One decision that could have gone the other way.** The renderer emits `fixedRecords` for every
file, because that is what a COBOL `WRITE` produces. The shipped corpus stores two lookup files as
line-terminated text, so the harness converts them -- exactly as the oracle pipeline's own `LOADIDX`
does before either program sees them. Teaching the renderer to guess framing per file would have
baked a property of one distribution into every generated project.

**Verification**: `pytest tests/unit/test_java_reader.py -q` -> **19 passed**, renderer at 98%
(the two uncovered lines are refusals for states the earlier contract checks already make
unreachable). The end-to-end proof is the round trip above.

**What G31 still leaves open**: the writer, the step beans and the job bean. `WRITE ... FROM` is not
parsed, so a written file appears in the design as a declaration with no entity.

### G31 stage 3d — the writers are rendered, and the candidate becomes COBOL's own format

**What changed.** `rendering/java_writer.py` renders both of `CBACT04C`'s writers from
`design.json`: an appending writer for the interest transactions and an **in-place `REWRITE`**
writer for the account master. The hand-written JSON writers are deleted, and with them the last
piece of *harness* serialisation -- the candidate files are now the program's own output, parsed
with the same layout the oracle is read with.

**`WRITE` and `REWRITE` are not rendered alike, and that distinction is the point.** A writer that
appended in both cases would turn an update of fifty accounts into fifty new records. Every record
would still be individually correct; only the file's *length* would say otherwise, which is exactly
what a field-level differential cannot see. The update writer loads the file it is updating, replaces
records by key, and writes the result back -- so an account the job never posts survives untouched.

**The result is unchanged, which is the claim.** 500 of 500 transaction fields and 598 of 600 account
fields, with reader and writers both rendered.

**What the parse added** (schema 3.5.0): `WRITE ... FROM` and `REWRITE ... FROM`, attributed to a
file through the `FD` record areas -- a write names the record *area*, not the file, so without that
association it cannot be attributed at all. Read positionally (the `01` after an `FD` is that file's
record) rather than by matching names, which only rhyme by convention. `CBTRN02C` both creates and
updates `TCATBAL` rows, and both bindings are kept: collapsing them would erase the fact that the
program can *create* a balance row, which is the difference between 50 rows and 94.

**A runtime helper gained an encoder**: `CobolRecord.zoned`. Positive values are written as plain
digits, which is what the reference run's COBOL produced; negatives take the standard overpunch,
because a `-` has nowhere to live in a field whose width is its digit count. The positive
representation is compiler-dependent and on the oracle's own known-unverified list -- which is
exactly why the differential compares field *values* rather than bytes. A value too large for its
field throws rather than being truncated into a smaller number that looks valid.

**On rendering a fixed-width serialiser at all.** ADR-0029 declined to build one, on the grounds that
a serialiser whose only consumer is the assertion about it is a check written to match whatever it
needs to match. That reasoning does not apply here and the difference is worth stating: this writer
is the *program's output*, not the test's. A batch program that cannot write its file is not
finished.

**Verification**: `pytest tests/unit/test_java_writer.py -q` -> **13 passed**, renderer at 100%;
`parsing/file_control.py` back to 100% with the write side covered; the template's `CobolRecordTest`
at 15 tests. The end-to-end proof is the round trip.

**What G31 still leaves open**: the job bean, the three step beans, the staging between steps, and
the aggregating reader for the account-posting step.

### G31 stage 3e — the job, its steps and the handoff between them

**What is rendered now.** `rendering/java_job.py` produces the `JobRepository`, the transaction
manager, the registry and operator, the staging that carries a value across a step boundary, one
`Step` bean per renderable step, and the `Job` that chains them. For `CBACT04C` that is two of three
steps and everything around them.

**What it refuses, and why that is the useful part.** `postAccountInterest` consumes an *aggregate*
of earlier output. The design carries no grouping key, no summed field and no ordering -- those are a
control break in the COBOL -- so the renderer declines and says so in the generated file's own
Javadoc. It still **names the step in the job**, and the job looks every declared step up by name, so
a missing bean is a startup failure naming itself rather than a job that quietly runs two steps
instead of three.

**The chain handoff is rendered as in-memory staging, and the generated class says it is not
restartable** (ADR-0032). Two alternatives were available: fusing the two steps into one chunk step,
which removes the question along with the step boundary a human approved at the gate; and a staging
table, which is the restartable answer and needs a schema for a type that corresponds to no copybook.
The middle option keeps the design's shape and carries its cost in writing, at the place the cost
applies.

**Readers and writers are injected rather than constructed.** A rendered step declares
`ItemReader<TranCatBalWithRate>` and takes it as a bean, because binding a reader to a *path* is
deployment: the COBOL says `ASSIGN TO TCATBALF`, an environment name, and nothing anywhere says what
that resolves to.

**Chunk size is rendered as a named constant that says it is not a COBOL fact.** Nothing in the
source implies a batch size.

**Result unchanged**: 500 of 500 transaction fields and 598 of 600 account fields, now with the job
itself rendered.

**Found by running it**: the hand-written remainder reads the rendered `CHUNK_SIZE` so its one step
is chunked like the others, and package-private was not enough -- the two live in different packages.
Made public with the reason in its Javadoc rather than copied.

**A drift caught while narrowing the qualifier.** The scripted and live paths each carried their own
copy of the "what is rendered" sentence, and only one was updated when the reader started being
rendered -- so the live run had been reporting a qualifier two stages out of date. There is now one
`WIRING_QUALIFIER` and both read it.

**Verification**: `pytest tests/unit/test_java_job.py -q` -> **13 passed**, renderer at 100%.

**What remains of G31**: the account-posting step and the paths. The first needs the design to be
able to express a control break, which is a `solution_architect` contract question rather than a
rendering one.

### The control break, recognised — G31's last missing fact

**What was missing.** Every other fact a rendered job needs is *declared*: a `PIC` clause, a
`SELECT`, a `READ ... INTO`, a `WRITE ... FROM`. A control break is an **idiom** -- four statements
spread across a loop -- and it is why `postAccountInterest` could not be rendered and why ADR-0027's
"already-summed item" stayed a note.

**What `CBACT04C` says, now parsed:**

| | |
|---|---|
| break key | `TRANCAT-ACCT-ID` (line 194) |
| saved key | `WS-LAST-ACCT-NUM` |
| accumulator | `WS-TOTAL-INT`, reset at the break |
| accumulated from | `WS-MONTHLY-INT` (line 467) |
| lands in | `TRAN-AMT` |
| performed at the break | `1050-UPDATE-ACCOUNT` |

**Recognition is a conjunction, and that is the safety.** All five elements must be present: the
`NOT =` test, the saved key *advancing* from the tested field, the accumulator reset beside it, an
`ADD` into that accumulator elsewhere, and a `PERFORM`. An inequality test alone is not a break --
`CBACT04C` has `IF DIS-INT-RATE NOT = 0` twelve lines after the real one, and without the
saved-key-advances requirement this would report two breaks for that program, one of them on a rate.
A wrong grouping key produces plausible totals against the wrong accounts, which is `pic_mapper`'s
objection in a new place.

**Three of the four Track C programs report none**, which is a fact about them rather than a gap.

**The tracing hop that makes it usable.** `WS-TOTAL-INT` is a program variable no generated record
has. Following `MOVE WS-MONTHLY-INT TO TRAN-AMT` gives the *column* an aggregation can sum -- without
it, a rendered aggregation would carry a field name it could not find in any type it was given.

**Attached to the step that declares the paragraph**, which is the whole matching rule:
`1050-UPDATE-ACCOUNT` belongs to `postAccountInterest`. A break whose paragraph no step declares is
dropped rather than attached to a guess.

**What it unblocks, stated precisely.** The refusal has changed from *"the design carries no
grouping key"* to:

```
it aggregates: 1050-UPDATE-ACCOUNT runs at a control break on TRANCAT-ACCT-ID (line 194),
summing WS-MONTHLY-INT which lands in TRAN-AMT. Rendering that needs both readable from Tran,
and TRANCAT-ACCT-ID is not -- widen that type to carry it, or give this step an input the
design can supply
```

**That surfaced a real discrepancy.** The declared chain puts `postAccountInterest` after
`completeTransaction`, whose output is a `Tran` -- and a `Tran` carries the account id only inside
its description text. The hand-written aggregating reader actually consumes the *first* step's
output, which carries an `Account`. So the design's declared order and the working implementation
disagree about what this step reads, and nothing had said so before.

**Verification**: `pytest tests/unit/test_control_break.py -q` -> **18 passed**, parser at 100%
including the period-terminated form (`END-IF` is optional in COBOL, and the older style is more
common in real estates).

**Still refused, deliberately**: the aggregation is not rendered. ADR-0032's amendment records that
the decision is unchanged and only its reason has narrowed.

### The control-break aggregation, rendered — G31 closes except for file paths

**What changed.** The control break parsed in the previous PR is now *used*: the aggregating reader
that turns a stream of transactions into one already-summed item per account is generated, the
`postAccountInterest` step renders with it, and the hand-written reader is deleted. **Result
unchanged**: 500 of 500 transaction fields and 598 of 600 account fields.

**The design change that unblocked it.** `TranWithContext` was widened to carry `TranCatBal`, so the
stream carries what the break groups by. Before that, the account id reached the posting step only
inside `TRAN-DESC`'s text -- not something anything can group on. That is the same move PR #40 made
for G26, and the refusal that asked for it named the exact field.

**Which stream an aggregate reads is derived, not declared.** A chain says each step consumes its
predecessor's output; an aggregate does not. `aggregation_source` walks backwards to the nearest
earlier step whose output carries **both** the break key and the summed column -- `computeInterest`,
not the `completeTransaction` that immediately precedes it. That resolves the discrepancy the
previous PR surfaced between the declared order and what the implementation actually reads.

**The summed record copies rather than fabricates.** The hand-written version filled every
non-total field with PIC-width spaces and zeros; the rendered one takes the group's first record and
replaces the accumulated column. Both are choices, and copying carries values that exist -- so a
body reading more than the total sees real data rather than padding.

**Found by javac**: the first version reached the copied fields as `first.tranId()` where `first` is
the *source item*, not a `Tran`. It needed the component that holds the entity --
`first.tran().tranId()`. A compile error rather than a silent one, which is the only reason it was
cheap.

**A duplication removed while covering it.** Two functions walked the same components -- one to
build an accessor string, one to find the owning entity -- and the second had a branch nothing could
reach, because callers always asked the first. Merged into one `_locate`, which made the branch both
reachable and tested.

**Verification**: `pytest tests/unit/test_java_aggregation.py -q` -> **14 passed**, renderer at
100%. The Maven-backed suites (`test_hand_written_round_trip`, `test_interest_equivalence`,
`test_account_break_posting`) -> **30 passed, 1 skipped**, which matters here because widening the
composite changed what every `computeInterest` body has to construct.

**What is left of G31**: file paths. The COBOL says `ASSIGN TO TCATBALF` -- an environment name --
and nothing anywhere says what it resolves to, so binding it is deployment rather than design.
