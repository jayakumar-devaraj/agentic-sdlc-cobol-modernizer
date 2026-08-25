# Spec extraction, critique, and Milestone C2's numeric-field gate

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### `nodes/spec_extractor.py` — real CBACT04C and its five real copybooks

**Verified**: every deterministic step this node performs before calling a model, run against the
real `CBACT04C.cbl` fixture and its five real copybooks (`CVTRA01Y`, `CVACT03Y`, `CVTRA02Y`,
`CVACT01Y`, `CVTRA05Y`) — the same fixture `tools/tenant_repo.py` and `parsing/cobol_parser.py`
are already verified against, not a synthetic stand-in:

- **Field mapping**: 75 of the program's real fields map correctly, including the plan's own
  verified-real targets — `ACCT-CURR-BAL` (precision 12, scale 2), `DIS-INT-RATE` (precision 6,
  scale 2), `TRAN-CAT-BAL`/`TRAN-AMT`/`WS-MONTHLY-INT`/`WS-TOTAL-INT` (precision 11, scale 2 each).
- **Construct isolation**: the program's own two real `REDEFINES` groups
  (`TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY`, `FILLER REDEFINES DB2-FORMAT-TS`) produce exactly
  9 correctly-isolated `unsupported_fields` entries, every one flagged with a `REDEFINES` reason —
  confirmed the other 75 fields, including the `REDEFINES`-target fields themselves
  (`TWO-BYTES-BINARY`, `DB2-FORMAT-TS`), are unaffected. See ADR-0006 for the design decision this
  verifies and a real parser interaction it surfaced.
- **Paragraph flow**: all 22 real paragraphs extracted in real source order, including the plan's
  own verified-real interest-calculation flow
  (`1200-GET-INTEREST-RATE` → `1300-COMPUTE-INTEREST` → `1300-B-WRITE-TX` → `1400-COMPUTE-FEES`).
- **Guardrail wrapping**: every one of the six real source units (the program plus five
  copybooks) is wrapped in its own labeled `<untrusted-cobol-source>` block, and the real
  `CBACT04C` source (license header and functional comments included) produces zero
  false-positive injection flags, consistent with `test_guardrails.py`'s own finding.
- **The interest formula is preserved verbatim** in the prompt content handed to the model:
  `COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200`, exactly as it appears in the
  real source, never paraphrased.
- **End-to-end wiring**: `extract_spec` resolves a real, non-empty model identifier from
  `config/model_routing.yaml` and loads the real (non-stub) system prompt content, confirmed via a
  fake `narrate` callable that captures what it was called with.

**Command**: `pytest tests/system/test_spec_extractor.py -v`
**Result**: 9/9 passed.

### `nodes/spec_critic.py` — real spec_extractor output for CBACT04C, faithful and corrupted

**Verified**: every deterministic fidelity check, and the confidence-composition rules ADR-0007
documents, run against a real `SpecExtractionResult` for `CBACT04C` (produced by `extract_spec`
itself, not a hand-built stand-in) — plus a `faithful_narrate` fake that reproduces the real Known
Facts block verbatim, and targeted `_corrupt_*` mutations of a copy of that real text so each
negative test proves exactly one failure mode:

- **Field-reference fidelity**: correctly passes the faithful narration, correctly detects a real
  precision drift (`ACCT-CURR-BAL` narrated as precision 99 instead of the real 12) and a dropped
  field row (`DIS-INT-RATE`). A real bug was caught while verifying this, not after: `pic_mapper`
  always names `FILLER` fields literally `"FILLER"`, never `None` — the check's original
  `field_name is None` guard was dead code, and `CBACT04C`'s five real `FILLER` fields (one per
  copybook) were silently colliding under one dict key. Fixed to skip by name; a regression test
  confirms all 5 real `FILLER` mappings exist and produce no false-positive mismatch.
- **Paragraph coverage** and **unsupported-construct carry-forward**: correctly pass the faithful
  narration and correctly detect a dropped paragraph mention (`1400-COMPUTE-FEES`). The
  carry-forward check's own real limitation is confirmed directly, not assumed: every unsupported
  field in `CBACT04C`'s two real `REDEFINES` groups cross-references its siblings' names inside
  its own `reason` text (`cobol_parser`'s `sibling_text`), so no real field name there is ever
  fully absent from a faithful narration to begin with — a fabricated field name isolates the
  check's actual detection behavior instead.
- **Confidence composition**: a mechanically-proven fidelity issue forces `overall_confidence` to
  `0.0` even when a fake critique call returns a `0.99` score for everything — confirms the
  deterministic check cannot be talked past. With no fidelity issues, `overall_confidence` is
  confirmed to be the minimum (not average) of per-rule scores, and `1.0` when the critique
  returns no rules at all.
- **Structured-output parsing**: valid JSON, a JSON payload wrapped in a `` ```json `` fence
  despite the prompt forbidding it, non-JSON text, a non-array response, a missing required field,
  and an out-of-range confidence value are each confirmed to behave exactly as ADR-0007 specifies
  — the fence is stripped, every other malformed case raises `SpecCritiqueParseError` rather than
  being guessed past.
- **End-to-end wiring**: `critique_spec` resolves a real, non-empty model identifier and loads the
  real (non-stub) system prompt content, confirmed via a fake `critique` callable that captures
  what it was called with.

**Command**: `pytest tests/system/test_spec_critic.py -v`
**Result**: 21/21 passed.

### `core/source_units.py` — real CBACT04C source-unit ordering

**Verified**: `iter_source_units` against the real `CBACT04C` fixture returns the program itself
first, then its five real copybooks in real `COPY` order, each paired with its own real source
text — extracted from `nodes/spec_extractor.py`'s own private helper once `nodes/spec_critic.py`
needed the identical behavior (no behavior change; `spec_extractor`'s own tests still pass
unmodified after the extraction).

**Command**: `pytest tests/system/test_source_units.py -v`
**Result**: 2/2 passed.

### `tests/fixtures/golden/CBACT04C/spec.md` — hand-verified golden fixture (plan step 32)

**Verified**: per `.claude/agents/qa.md`'s standard for golden fixtures ("only as trustworthy as
the manual cross-check that produced it"), this fixture's Overview, Paragraph flow (all 22
paragraphs), and Business rules sections were written and checked by hand against the real
`CBACT04C.cbl` source and its five real copybooks — not generated by a model and trusted on
sight. Specific details confirmed against the source, not assumed: account/cross-reference data is
only re-fetched when the account id changes (not per transaction-category-balance record); a
`DISCGRP-STATUS` of `'23'` triggers a hard-coded `'DEFAULT'` group fallback rather than skipping
the account; `1400-COMPUTE-FEES` is called unconditionally alongside interest computation but its
body is an unimplemented stub (`* To be implemented`) — a detail easy to narrate incorrectly as
"fees are computed" if not read carefully. The Field reference (75 fields) and Flagged for human
review (9 fields) sections are not hand-transcribed at all — they are the literal output of
`render_known_facts` run against the real fixture, byte-identical to what the real pipeline
computes today.

This is made falsifiable, not just asserted once: `test_golden_fixture.py` re-derives the real
deterministic facts from the live fixture on every run and asserts
`compute_fidelity_issues(golden_extraction) == []` — the concrete, checkable form of Milestone
C2's gate, "golden fixture matches exactly," for `CBACT04C`. The gate as stated in the plan covers
all four Track C programs; all four now have byte-verified tenant-repo fixtures, but only
`CBACT04C` has a golden `spec.md` — deliberately, since the other three's narrative prose has no
live model output to regress against yet (see "Not yet covered" below). The gate's numeric-field
clause is met for all four programs by the exhaustive cross-check entry below; this entry closes
its "golden fixture matches exactly" clause for `CBACT04C` specifically.

**Command**: `pytest tests/system/test_golden_fixture.py -v`
**Result**: 7/7 passed.

### `core/contracts.py` — `gate_items` against real CBACT04C spec + critique output

**Verified**: `build_gate_items` against a real `ProgramDesignEntry` built from the golden
`CBACT04C` extraction and a real `critique_spec` run (fake `critique` callable, per this repo's
established pattern):

- All 9 of `CBACT04C`'s real unsupported fields surface as `unsupported_construct` gate items
  regardless of how confident the critique is — confirmed a `REDEFINES` field always needs human
  review, never suppressed by a high confidence score elsewhere in the same program.
- A deliberately corrupted narration (a dropped paragraph heading, `0200-DISCGRP-OPEN` — chosen
  because `grep -c` confirmed it appears exactly once in the golden fixture, unlike paragraph
  names also mentioned in prose) produces a real `spec_critic` fidelity issue, which surfaces as
  its own `fidelity_issue` gate item — confirmed a reviewer sees *what* is wrong, not just a
  zeroed `overall_confidence` they could miss.
- The `0.7` low-confidence threshold (`LOW_CONFIDENCE_THRESHOLD`) correctly separates rule scores
  at `0.95`/exactly `0.7` (not flagged) from `0.5`/`0.69`/`0.1` (flagged) — confirmed the boundary
  is "strictly below," not "at or below."
- The real 9 unsupported-construct items and zero real injection flags from `CBACT04C`'s actual
  source are both confirmed directly, plus a fabricated `InjectionFlag` exercises the
  `injection_flag` gate-item path itself (real source triggers none, so this path needed a
  synthetic case to reach at all — same style as `test_guardrails.py`'s own adversarial cases).
- `build_design_document` is confirmed to derive `gate_items` from `programs` (never passed
  separately, so the two can't go stale relative to each other), and `DesignDocument` round-trips
  losslessly through `model_dump_json()`/`model_validate_json()`.

**Command**: `pytest tests/system/test_contracts.py -v`
**Result**: 12/12 passed.

### `schemas/*.schema.json` — generated from the live Pydantic models, drift-checked

**Verified**: every committed schema file is confirmed to match `core/contracts.py`'s models
exactly, generated fresh at test time via the same `core/schema_export.py` mapping
`scripts/generate_schemas.py` uses (so the generator and the test can't independently drift from
each other about which models get a schema). The drift check was confirmed to actually catch a
real mismatch, not just tautologically pass: `schemas/design_cli_result.schema.json` was hand-corrupted
to `{"tampered": true}`, the test was re-run and failed with a clear message naming the stale file,
then the real file was regenerated via `scripts/generate_schemas.py` and the test passed again.

**Command**: `pytest tests/system/test_schemas.py -v`
**Result**: 3/3 passed.

### Real tenant-repo fixtures for the other three Track C programs, and a real corruption caught

**Verified**: `CBCUS01C`, `CBACT01C`, `CBTRN02C` and their real copybooks (`CVCUS01Y`,
`CODATECN`, `CVTRA06Y`) were fetched from `carddemo-tenant-service` and every file's git blob SHA
confirmed byte-for-byte against the GitHub Contents API's own `sha` — completing real fixtures for
all four Track C programs (`CBACT04C` already had one).

**A real corruption was caught during that verification, not after**: `CODATECN.cpy` genuinely has
`CRLF` line endings in the upstream repo (confirmed via hex dump — every other fetched file is
`LF`-only). `git add` on this development machine (`core.autocrlf=true`) was silently converting
its `CRLF` to `LF` while staging the file — the raw downloaded bytes matched the remote SHA
exactly, but the *staged* blob did not, which would have committed a silently line-ending-corrupted
copy of real source despite the download itself being verified correct. Fixed with a repo-root
`.gitattributes` (`tests/fixtures/tenant_repo_sample/** -text`), which disables line-ending
conversion for this directory on any machine's `autocrlf` setting; re-verified afterward that every
fixture file's staged blob SHA — old and newly added — still matches the real repo exactly. Two
regression tests (`test_codatecn_retains_its_real_crlf_line_endings`,
`test_other_fixture_files_are_lf_only`) act as a cheap, network-free canary if this class of
corruption ever recurs.

**`spec_extractor`'s deterministic pipeline was confirmed to generalize** to real, structurally
different programs it had never run against: `CBACT01C`'s `CODATECN` copybook has four real
`REDEFINES` groups (date-format conversion aliases — a construct shape `CBACT04C` never exercises)
plus a standalone elementary `REDEFINES` on its own declaration line
(`WS-REISSUE-DATE REDEFINES WS-ACCT-REISSUE-DATE`), all correctly isolated (28 unsupported fields,
none silently mapped). `CBTRN02C` (26 paragraphs, 5 copybooks — the largest Track C program) and
`CBCUS01C` (5 paragraphs — the smallest) both parse and map cleanly, with real field-level spot
checks (`CUST-ID`, `DALYTRAN-AMT`, etc.) matching their real `PIC` clauses.

**Command**: `pytest tests/system/test_tenant_repo.py tests/system/test_spec_extractor_track_c_programs.py -v`
**Result**: 28/28 passed.

### `spec_critic` against the other three real Track C programs

**Verified**: `critique_spec`'s deterministic fidelity-check machinery against `CBCUS01C`,
`CBACT01C`, and `CBTRN02C`, using the same faithful-narrate technique (a narration reproducing the
real Known Facts block verbatim) already used to verify `spec_extractor` generalizes to these
programs. This is **not** a substitute for hand-verified narrative prose (`CBACT04C`'s golden
fixture remains the only one of the four with that level of verification — now a deliberate
deferral rather than an open question, see "Not yet covered" below) — it confirms the deterministic
checking machinery itself behaves correctly against real, structurally different data.

`CBACT01C` is the real stress case: its `CODATECN` copybook contributes 28 real unsupported
fields — the largest set of any Track C program (`CBACT04C`'s is 9) — confirmed correctly carried
forward as a whole (a faithful narration produces zero fidelity issues against all 28), with
individual-field detection confirmed via a fabricated entry (the same real cross-referencing
limitation ADR-0006 documents for `CBACT04C` applies here too: `CODATECN`'s `REDEFINES` groups
embed sibling field names inside each other's `reason` text, so no single real field name is ever
fully absent from a faithful narration to test against directly). A corrupted field precision
(`CUST-ID` in `CBCUS01C`, `ACCT-CURR-BAL` in `CBACT01C`, `DALYTRAN-AMT` in `CBTRN02C`) is correctly
detected for each program.

**Command**: `pytest tests/system/test_spec_critic_track_c_programs.py -v`
**Result**: 12/12 passed.

### Milestone C2's numeric-field gate, exhaustively, for all four Track C programs

**Verified**: Milestone C2's gate as literally worded — *"`spec.md` for all four Track C programs
correctly identifies 100% of numeric/`COMP-3` fields, manually cross-checked"* — for the scope the
pipeline actually parses. Every numeric field's precision, scale, signedness, and `USAGE` clause
was hand-derived by reading the real `PIC` clause in the real fixture source and applying COBOL's
own rules (precision is total `9` positions, scale is the count after `V`, `S` means signed)
**before** running the pipeline, then reconciled against what the pipeline actually produces. The
assertions are exact-set equality per program, not spot checks, so a numeric field that stopped
being mapped fails just as loudly as one mapped wrong. Coverage is 9 numeric fields for `CBCUS01C`,
17 for `CBACT01C`, 27 for `CBTRN02C`, 26 for `CBACT04C`.

Confirmed falsifiable rather than tautological, the same way the schema drift check was:
`ACCT-CURR-BAL`'s expected scale was hand-corrupted from `2` to `0`, the suite re-run, and it failed
exactly the three programs that `COPY` `CVACT01Y` (`CBACT01C`, `CBTRN02C`, `CBACT04C`) while
`CBCUS01C`, which does not, kept passing — then restored and re-run green.

Also confirmed directly, since ADR-0010's merge-by-exact-copybook-name rule depends on it: the same
copybook maps to byte-identical field data in every program that `COPY`s it (`CVACT01Y`'s full
`PicMapping` list is equal across all three of its callers). And every real monetary field across
all four programs — balances, credit limits, transaction amounts, the interest accumulators,
`DIS-INT-RATE` — is confirmed scale 2 and signed, the platform's headline rounding/precision risk
checked as a property rather than field by field.

**A real defect was found by this cross-check, not after it** — and has since been fixed, see the
entry below. `parsing/cobol_parser.extract_working_storage_fields` sliced out only the
`WORKING-STORAGE SECTION` body and stopped at the next section/division header, so a program's
`FILE SECTION` (`FD`) record layouts and its `LINKAGE SECTION` parameters never reached `pic_mapper`
at all — neither mapped nor flagged as unsupported, simply absent. That is the one outcome this
repo's error handling exists to prevent; `UnsupportedPicConstructError`'s whole purpose is failing
loudly instead of going quiet. Three real consequences, verified against the fixture source, not
hypothesized:

- `CBACT01C`'s `OUT-ACCT-CURR-CYC-DEBIT` and `ARR-ACCT-CURR-CYC-DEBIT` are declared
  `PIC S9(10)V99 USAGE IS COMP-3` — the **only** `COMP-3` fields anywhere in Track C, and the
  construct the gate names explicitly. Neither is seen. This also **corrected a real error in
  `docs/cobol-construct-support-matrix.md`**, which recorded `COMP-3` as "not present in Track C's
  scope": that verdict was reached by reading every copybook the four programs `COPY` (where it is
  still true that none declares `COMP-3`) and wrongly generalized to the programs' own source. The
  matrix now separates "in scope" from "currently reached by the parser", which is the distinction
  it had been collapsing.
- `CBACT04C`'s `PARM-LENGTH` belongs to `EXTERNAL-PARMS`, the record its own
  `PROCEDURE DIVISION USING EXTERNAL-PARMS` clause names — the program's actual input parameter.
- All four programs' `FD` record layouts describe the files each batch job reads and writes: 1
  unreached numeric field in `CBCUS01C`, 10 in `CBACT01C`, 3 in `CBTRN02C`, 6 in `CBACT04C`.

The gap was recorded as its own falsifiable test rather than as prose, written to fail the moment
the parser was extended — which is what happened; ADR-0011 closed it and the tests are inverted.
**Milestone C2's numeric-field gate is now met for all four programs across every section they
declare fields in.**

**Command**: `pytest tests/system/test_numeric_field_coverage.py -v`
**Result**: 16/16 passed.

---

## The untrusted boundary, asserted against the prompts the nodes actually send (2026-08-24, ADR-0052)

Twenty tests covered `core/guardrails.py` as a unit — wrap, raise on delimiter forgery, flag
injection phrasings without flagging ordinary comments — and none covered the property the
repository depends on: that no tenant text reaches a model **outside** the block the model is told
is inert data. What stood in for it was a CI step asserting `tests/system/test_guardrails.py`
existed as a file.

Each of the four prompt-building nodes is now run through its real entrypoint with only the model
call replaced, and the prompt it sends is captured from that callback. Every
`<untrusted-cobol-source …>` block is cut out of that prompt, and no comment line of any source unit
the program resolves to may appear in what remains.

**Measured through that capture path, not through the builder in isolation.** `spec_extractor`'s
prompt for `CBACT04C` is **74,230 characters** — the same figure ADR-0017 measured from the other
direction, arrived at here independently — of which **16,254 sit outside the untrusted blocks**, all
of it the deterministic Known Facts block this repo computes itself. Six source units resolve:
`CBACT04C` contributing 53 comment lines and its five copybooks 30 more. **Zero of the 83 are
outside.**

**Shown failing first, on deliberately damaged input.** One line appended to `build_prompt` echoing
`resolved.source_text` after the wrapped sections:

```
AssertionError: 73 tenant comment line(s) reached the prompt outside the untrusted block,
first: ('CBACT04C', '      *****...')
1 failed, 2 passed
```

The two undamaged nodes stayed green in the same run, so the failure is attributable to the damage
rather than to a check that fires indiscriminately. The damage was reverted with `git checkout --`
and never reached a commit.

**It found something already true.** `spec_critic` wraps every COBOL source unit and then appends
`extraction.spec_markdown` raw, where `solution_architect` and `modernization_engineer` both wrap
that same artifact. Pinned by a test recording the current state rather than designed around: the
fix edits a live prompt, and a prompt edit wants a billed critic run to say the node still
discriminates. ADR-0052 states what would be wrong if it is never fixed.

**Command**: `pytest tests/system/test_guardrails.py -q`
**Result**: 27 passed in 0.34s. CI runs that exact command as a named step, replacing the
file-existence check.

---

## The critic still discriminates with the narration inside the boundary (2026-08-24, ADR-0053)

The defect the boundary check found (entry above) is closed: `spec_critic` wraps the narration it
judges under `<PROGRAM>-spec`, and `prompts/registry/spec_critic/v1_1_0.md` — **the first `v1_1_0`
in this registry** — says so to the model. The question that gated the change was whether the node
still catches a wrong narration once the text it judges arrives delimited.

**Command**: `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 pytest tests/system/test_critic_discrimination.py -q -s`
**Result**: **7 passed in 556.93s (9m16s)** — four free, three billed, four model calls, ~$0.56 at
the per-call costs measured on 2026-08-08.

| assertion | what held |
|---|---|
| `test_both_tiers_catch_every_planted_error[claude-haiku-4-5-20251001]` | at least one rule below `0.7` per planted error — **three of three** |
| `test_both_tiers_catch_every_planted_error[claude-opus-5]` | the same, on the strongest model |
| `test_the_low_confidence_threshold_separates_good_from_bad` | `bad_min < 0.7 < clean_min` |

The three corruptions are chosen so the deterministic fidelity checks **cannot** catch them, which
`test_the_deterministic_checks_do_not_catch_these_corruptions` asserts rather than assumes — so what
passed here is the model's own contribution on the prompt version this node now sends.

**A pre-flight check, free, before the money was spent**: the version resolved through
`node_prompt_version` is `v1_1_0`, the narration is wrapped under `CBCUS01C-spec`, and the user
prompt is 21,960 characters. Without `node_prompt_version` this benchmark would have loaded
`v1_0_0`'s text and sent it beside a `v1_1_0` payload — a silent mismatch whose output would have
looked like a real result.

**What this run did not leave, and the harness defect that explains it.** The confidence scores
themselves were not recorded. This module printed them only inside an assertion message, which does
not render when the assertion holds, so a passing run left nothing behind.
`tests/evaluations/test_judge_benchmark.py` has carried the rule in its own comment since it was
written — *printed so a real run leaves the artifact the verification report needs, whether or not
the assertions below pass* — and this module never adopted it. It does now: `_parsed_and_printed`
prints the score distribution, token counts and notional cost on every call, plus the rationale of
every flagged rule (ADR-0024, and trap 10 on what skipping rationales cost). **That fix cannot
recover this run**; recovering the numbers would mean paying for it again, which was not worth
$0.56 for a result whose assertions already passed.

So what is claimed here is what the assertions checked: on `v1_1_0`, both tiers still catch every
planted error and the threshold still separates. Whether wrapping moved the scores relative to
2026-08-08's 0.00/0.20/0.40 and 0.30/0.15/0.35 is **not** claimed, and the next run of this module
will be able to say.

---

## The structured-output repair loop, and the two defects the probes found (step 35, ADR-0054)

`core/structured_output.parse_with_repair` is plan step 35's shared repair-retry loop, wired into
all four structured-output nodes. It is fully unit-testable, so what is worth recording here is not
*"the tests pass"* — it is what the **damage probes** established, since both findings below were
invisible to a green suite.

**Command**, run on the branch before the ADR was written:

```
.venv/Scripts/python.exe -m pytest tests/system/test_structured_output.py \
    tests/system/test_spec_critic.py tests/system/test_solution_architect.py \
    tests/system/test_modernization_engineer.py tests/system/test_build_validator.py -q
```

### Probe 1 — the boundary property, which failed on the first real node

The loop is specified to send the parse error and **no model-authored text** (ADR-0054 decision 3).
That property was written as an assertion before any node was wired, and it **failed on
`spec_critic`**: three of the four parse errors interpolate the response into their own message
(`f"... is not valid JSON: {exc}. Raw response: {raw_response!r}"`), so an instruction built to
carry "the error and nothing else" carried the response too — injected text included.

| probe | result |
|---|---|
| `test_critique_spec_repair_never_quotes_the_malformed_response`, before `redact_response` | **failed** — the injected string reached the second prompt |
| the same test, after | passes |
| implementation damaged to append the prior response deliberately | **that test alone fails**; the other 9 in its module pass |

The last row is the isolation check: the property is pinned by a test that fails for this reason and
no other. Redaction matches the `repr()` form as well as the plain text, because `!r` is what
actually put the text there — `test_redaction_removes_the_repr_form_an_f_string_produces` fails if
only the plain form is excised.

### Probe 2 — three of four nodes were wired and wholly uncovered

After wiring, `max_attempts=1` was set in each node in turn — which disables repair entirely, making
the node behave exactly as it did before this change.

| node | tests in file at probe time | result with repair disabled |
|---|---|---|
| `spec_critic` | 25 | **2 failed**, 23 passed — covered |
| `solution_architect` | 35 | **35 passed** — uncovered |
| `modernization_engineer` | 46 | **46 passed** — uncovered |
| `build_validator` | 22 | **22 passed** — uncovered |

`spec_critic` differs from the other three only because its two repair tests were written first, in
the same commit as the loop; nothing about that node made it easier to cover.

Nothing distinguished a wired loop from an unwired one in three of the four. One test per node now
drives the measured failure — a prose preamble ahead of valid JSON, the shape ADR-0049 observed on
5 of 21 sampled calls — and asserts the node re-asked exactly once with the request unchanged.
**Re-running the probe after that fails exactly those three and nothing else** (3 failed, 103
passed), which is the evidence the coverage is real rather than incidental.

This is trap 6 in `docs/development-environment.md` applied to a change of this repo's own: a test
that passes on the artifact that produced it is not evidence.

### What is not claimed

No live model call was made for any of this. The repair path has never run against a real model that
actually broke the contract — the failure it repairs is reproduced from ADR-0049's recorded
excerpts, not re-observed. What is verified is that the loop re-asks, that it re-asks once, that it
carries the diagnosis, that it carries none of the model's words, and that a parser defect
(`TypeError`) reaches the caller without spending a call. Whether a real model complies on the
second ask is the open half, and the honest place to settle it is the next billed run that happens
to hit one.
