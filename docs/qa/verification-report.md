# QA Verification Report

Running record of this repo's unit-test coverage and functional verification. Updated in the same
change as whatever it reports on — per `CLAUDE.md`, "a doc claim not backed by a command actually
run... is a bug, not documentation." Every entry below states the exact command run and its real
output, not a paraphrase. Per `.claude/agents/qa.md`: unit-test coverage and functional
verification are reported side by side, never one standing in for the other — a green test suite
was never treated as proof on its own.

## Unit test coverage

```
pytest --cov=cobol_modernizer --cov-report=term-missing --cov-fail-under=90
```

As of this report: **369 tests passed (4 skipped — the opt-in live-CLI tests), 99.01% overall
coverage.**

| Module | Coverage |
|---|---|
| `cli.py` | 98% |
| `core/complexity.py` | 100% |
| `core/contracts.py` | 100% |
| `core/design_outputs.py` | 100% |
| `core/guardrails.py` | 100% |
| `core/model_client.py` | 98% |
| `core/model_routing.py` | 98% |
| `core/model_routing.py` | 100% |
| `core/schema_export.py` | 100% |
| `core/source_units.py` | 100% |
| `core/structured_output.py` | 100% |
| `graph/design_graph.py` | 100% |
| `nodes/solution_architect.py` | 100% |
| `nodes/spec_critic.py` | 100% |
| `nodes/spec_extractor.py` | 100% |
| `parsing/cobol_parser.py` | 98% |
| `prompts_registry_client/loader.py` | 100% |
| `tools/knowledge_store.py` | 100% |
| `tools/pic_mapper.py` | 99% |
| `tools/tenant_repo.py` | 100% |
| `telemetry/logging_config.py` | 100% |

`cli.py`'s one uncovered line is the `sys.exit(main())` under `if __name__ == "__main__"`, which
only runs when the module is executed directly rather than through the installed console script —
that script's real behavior is verified as a real process instead (see the `design` end-to-end
entry below).
The three node modules reached 100% with ADR-0013: their `_default_*` bodies used to be untested
live-API calls and are now one-liners delegating to `core/model_client.call_model`, which the
SDK-backend tests exercise. `core/model_client.py`'s single uncovered line is an
`AssertionError("unreachable")` guarding the end of the retry loop.

## Functional verification

Unit tests prove a module does what it says in isolation. The entries below are the separate
question: does the real thing behave as documented against real data, real infrastructure, real
external systems — not a mock standing in for one.

### `pic_mapper.py` — deterministic PIC-to-BigDecimal mapping against real CardDemo fields

**Verified**: the module correctly maps every numeric field in the real `CVACT01Y.cpy`, `CVTRA05Y.cpy`,
and `CVTRA02Y.cpy` copybooks (fetched live from `carddemo-tenant-service`, not transcribed from
memory) to the expected `BigDecimal` precision/scale — including `ACCT-CURR-BAL`'s `PIC S9(10)V99`
→ precision 12, scale 2.

**Command**: `pytest tests/system/test_pic_mapper.py -v`
**Result**: 19/19 passed.

### `parsing/cobol_parser.py` — structural parsing against real `CBACT04C.cbl` / `CVACT01Y.cpy`

**Verified**: correct `COPY` statement extraction and order, correct 22-paragraph `PROCEDURE
DIVISION` order matching the real program, correct field extraction from a header-less copybook
(including the real `ACCT-EXPIRAION-DATE` typo, preserved not corrected), and a real integration
round-trip of every `CVACT01Y.cpy` field through `pic_mapper.map_pic_clause()`. Also confirmed
`TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY` (a real construct in `CBACT04C.cbl`'s own
`WORKING-STORAGE`, on a group header rather than either child field's own line) is still caught via
sibling-text detection.

**Command**: `pytest tests/system/test_cobol_parser.py -v`
**Result**: 28/28 passed.

### `tools/tenant_repo.py` — real filesystem reads, real casing inconsistency

**Verified**: reads the real `CBACT04C.cbl` and its five real copybooks from a fixture mirroring
the tenant repo's actual `app/{cbl,cpy}/` layout, resolving the real extension-casing
inconsistency confirmed live against `carddemo-tenant-service` (`CBSTM03A.CBL`/`CBSTM03B.CBL` and
`COSTM01.CPY` are uppercase; everything else lowercase). A real defect was caught during this
verification, not after: probing `Path.is_file()` directly is case-insensitive for the whole
filename on Windows NTFS, silently defeating the case-sensitive program-name-matching requirement
on this exact development machine — fixed, with a regression test that failed before the fix.

**Command**: `pytest tests/system/test_tenant_repo.py -v`
**Result**: 15/15 passed.

### `core/guardrails.py` — real COBOL source, adversarial synthetic cases

**Verified**: the real `CBACT04C.cbl` (license header, functional comments, all included) wraps
cleanly with zero false-positive injection flags. Delimiter forgery (a comment containing the
literal prompt-wrapping tag) raises unconditionally, including a forged tag with a different
label. Each injection-phrase heuristic class is individually verified to flag. A real gap was
caught during this verification: `"IGNORE ALL PREVIOUS INSTRUCTIONS"` — the canonical real-world
phrasing of this attack — didn't match a regex that only allowed one qualifier word between
"ignore" and "instructions"; fixed to treat each qualifier as independently optional.

**Command**: `pytest tests/system/test_guardrails.py -v`
**Result**: 18/18 passed.

### `tools/knowledge_store.py` — real Postgres+pgvector, both locally and in CI

**Verified**: `ensure_schema`/`store_entry`/`search_similar` against a real, running
Postgres+pgvector container (this repo's own `docker-compose.yml`, isolated from
`agentic-sdlc-control-plane`'s shared instance). Includes an analytically-known nearest-neighbor
test — three orthogonal basis vectors and a query deliberately closer to one — asserting the exact
expected ordering and strictly increasing distances, not just "it returned something." Verified
that connection failures never echo the credentials file's contents or the connection string.

Also verified the tests **actually execute against a service container in CI**, not silently skip
there forever (the tests skip gracefully without a reachable database, so this had to be checked,
not assumed):

**Command**: `gh run view <run-id> --log | grep -iE "test_knowledge_store|skipped|passed"`
**Result**: `tests/system/test_knowledge_store.py ...........` (11 dots — 11 tests ran, zero
skipped), `98 passed`, confirmed against the CI run for PR #4
(https://github.com/jayakumar-devaraj/agentic-sdlc-cobol-modernizer/pull/4).

### `core/model_routing.py` — real config/model_routing.yaml, ADR-0004's actual mechanism

**Verified**: `load_model_routing`/`resolve_model` against the real, checked-in
`config/model_routing.yaml` every node will actually read (not a fixture standing in for it) —
confirms it maps all five known node types to non-empty model identifiers, and that
`resolve_model("spec_extractor", ...)` resolves correctly against that real file. Failure paths
(missing file, invalid YAML, unknown node key, incomplete config, non-string value) are exercised
against fixture configs only, never the real one, so a bad fixture can never be mistaken for a
real-config regression.

**Command**: `pytest tests/system/test_model_routing.py -v`
**Result**: 10/10 passed.

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

### `parsing/cobol_parser.py` — every `DATA DIVISION` section, and the fixed-`OCCURS` decision (ADR-0011)

**Verified**: the fix for the defect above, against real source for all four programs.

- **Nothing is silently absent any more.** An independent re-scan of every real source file for
  level-numbered numeric `PIC` declarations, diffed against what the pipeline produces, reports
  **zero unaccounted fields for all four programs** — every declaration lands in either
  `field_mappings` or `unsupported_fields`. Before the fix, 20 were in neither (1 `CBCUS01C`,
  10 `CBACT01C`, 3 `CBTRN02C`, 6 `CBACT04C`).
- **`COMP-3` is genuinely reached and correctly typed.** `CBACT01C`'s `OUT-ACCT-CURR-CYC-DEBIT`
  maps as `BigDecimal`, precision 12, scale 2, signed, `usage=COMP_3` — with its `USAGE` clause on
  a continuation line, which also confirms the parser joins a wrapped declaration into one sentence
  before mapping it. Confirmed it is the only mapped `COMP-3` field in Track C.
- **`CBACT04C`'s `LINKAGE SECTION` is reached**: `PARM-LENGTH` (`PIC S9(04) COMP`, precision 4,
  signed) — the program's real `PROCEDURE DIVISION USING EXTERNAL-PARMS` input parameter.
- **The fixed-`OCCURS` group is isolated, not flattened.** `CBACT01C`'s `ARR-ACCT-BAL OCCURS 5
  TIMES` group produces four `unsupported_fields` entries carrying construct name
  `"OCCURS (fixed)"`, and zero mappings. Asserted explicitly, including that all four fields are
  flagged rather than only the two genuinely inside the array — the over-flagging is a real cost
  ADR-0011 accepts, so it is pinned by a test rather than left implicit.
- **Real counts moved and were re-verified, not assumed**: `CBACT04C` 75 → 93 mapped fields,
  `CBTRN02C` 88 → 102, `CBACT01C` unsupported 28 → 32 (28 still `REDEFINES`, 4 new
  `OCCURS (fixed)` — asserted separately so the two reasons can't be conflated).
- **The golden fixture was regenerated, not hand-edited.** `render_known_facts` was re-run against
  the real fixture to produce the Field reference table. Confirmed by diffing that this added
  exactly 18 rows, removed nothing, and left the hand-verified Overview / Paragraph flow / Business
  rules prose byte-identical — the property that section was generated for in the first place.
- **Two prior decisions were reversed, both with tests that had asserted the old behavior**: a
  fixed `OCCURS` mapping cleanly (`test_pic_mapper.py`), and `LINKAGE SECTION` fields being treated
  as a leak if they appeared (`test_cobol_parser.py`). Both tests were rewritten to assert the new
  behavior and state what changed, rather than deleted.
- **The `WORKING-STORAGE`-only fallback is now tested.** No real Track C fixture reaches it
  (programs have a `DATA DIVISION`, copybooks have no headers), so extending the region left a
  documented branch uncovered — caught by reading the coverage report, and closed with a fragment
  test rather than left to rot.

**Command**: `pytest tests/system/test_cobol_parser.py tests/system/test_pic_mapper.py tests/system/test_numeric_field_coverage.py -v`
**Result**: 68/68 passed.

### `nodes/solution_architect.py` — cross-program domain-entity unification, against real data for all four programs

**Verified**: `build_domain_entities` against real `extract_spec`/`critique_spec` output for all
four Track C programs at once (`CBACT04C`, `CBCUS01C`, `CBACT01C`, `CBTRN02C`) — the first node in
this repo to look across every program together, not one at a time:

- **Real cross-program merge**: `Account` (from `CVACT01Y`) correctly merges into one entity used
  by all three programs that `COPY` it (`CBACT04C`, `CBACT01C`, `CBTRN02C`), with 12 non-`FILLER`
  fields and byte-exact `pic_mapper` data reused verbatim (`ACCT-CURR-BAL`: `BigDecimal`,
  precision 12, scale 2 — the same real value `test_spec_extractor.py` already verifies).
- **`CODATECN` correctly produces no domain entity at all** — confirmed it contributes zero
  successfully-mapped fields (all 28 are inside its four real `REDEFINES` groups), so there is
  nothing to represent, rather than an empty or guessed-at entity.
- **Structurally similar copybooks stay separate, confirmed directly**: `CVTRA06Y`'s
  `Dalytran` and `CVTRA05Y`'s `Tran` are both real 350-byte transaction-shaped records but remain
  two distinct entities — proving the merge-by-exact-copybook-name-only rule (ADR-0010 decision 1)
  actually holds against real, easily-confusable data, not just a description of intent.
- **A real bug in ADR-0010 itself was caught by running this against real data, not assumed
  correct from the design alone**: the ADR's own Consequences section originally claimed 6 domain
  entities; running `build_domain_entities` for real produced 7 (`Dalytran` was missed in the
  original count). Corrected in the same commit as the code that surfaced it.
- **Structured-output validation** (an architect response referencing an unknown program, domain
  entity, batch-step role, or REST method; a response missing required fields; a response covering
  only some of the four real programs) is exercised directly against real Known Facts data, not
  synthetic placeholders.

**Command**: `pytest tests/system/test_solution_architect.py -v`
**Result**: 22/22 passed.

### The `design` subcommand end-to-end — a real LangGraph run, as a real process (ADR-0012)

**Verified**: `cobol-modernizer design` wired through the real graph over real fixture source, and
run as an actual OS process rather than only in-process, because the stdout/stderr split is the
contract with control-plane and `capsys` is an in-process approximation of it.

- **A real four-program run produces real artifacts.** `CBACT04C CBCUS01C CBACT01C CBTRN02C` →
  exit 0, a **413,532-byte `design.json`** plus one `spec.md` per program, 52
  `unsupported_construct` gate items (9 + 2 + 32 + 9, matching each program's own verified count),
  7 unified domain entities, programs in requested order. The written file re-validates through
  `DesignDocument.model_validate_json`.
- **The `--json` stdout contract holds under a successful run**, which is the harder case — 49 log
  lines went to stderr while stdout carried **exactly one line**, parsed as one JSON object. Also
  verified on the failure path through the installed console script
  (`.venv/Scripts/cobol-modernizer.exe design --tenant-repo /nonexistent`): exit 1, stdout still
  exactly one parseable object with `status="error"` and `TenantRepoFileNotFoundError` in `detail`,
  full traceback on stderr only.
- **Branches genuinely overlap.** Asserted by observed overlap (latest branch start < earliest
  branch end) plus more than one thread, rather than a wall-clock threshold that a loaded CI
  machine could make flaky. Independently confirmed against LangGraph directly: three 1.0s
  branches complete in 1.01s wall on a real `ThreadPoolExecutor`.
- **A measured assumption turned out to be wrong, and is recorded rather than quietly fixed.**
  `run_design` re-orders program entries because fan-in was assumed to follow completion order. It
  does not: LangGraph applies a reducer's writes in `Send` order, so the raw state is already
  deterministic — confirmed by deleting the re-ordering and watching the ordering test still pass,
  then measured directly with randomized per-branch delays over repeated runs. The normalization
  was kept (ADR-0012 decision 4) but the test now states that it passes either way, and a second
  test pins LangGraph's behavior so a future change is loud instead of silently becoming the only
  thing keeping `design.json` deterministic.
- **Reproducibility asserted directly**: two runs into different directories produce
  byte-equal `design.json` apart from `generated_at`.
- **Every node is confirmed to use its own registry prompt and its own routed model.** The test
  fake dispatches on *system-prompt identity* against the real registry files rather than sniffing
  substrings, so a prompt mix-up raises instead of silently producing a plausible design. Confirmed
  the real `config/model_routing.yaml` values resolve through the real lookup: `spec_extractor` and
  `solution_architect` → `claude-opus-5`, `spec_critic` → `claude-haiku-4-5-20251001`, with exactly
  one extraction and one critique per program and one architect call per run.
- **A real LangGraph constraint was found by running the graph**, not by reading docs: a sub-graph
  state key that collides with its parent's makes every concurrent branch write the same
  non-reducer channel in one superstep, and LangGraph rejects it
  (`InvalidUpdateError: At key 'worktree_root': Can receive only one value per step`) even though
  all branches write an identical value. Hence `branch_worktree_root`.
- **Failure policy**: one bad program name fails the whole invocation and **writes no partial
  `design.json`** — asserted directly, since a document silently covering three of four requested
  programs is indistinguishable at a review gate from a complete one.

**Command**: `pytest tests/system/test_design_graph.py tests/system/test_cli_design.py tests/system/test_cli_contract.py -v`
**Result**: 30/30 passed.

### `core/model_client.py` — the real `claude` CLI, and the retry policy (ADR-0013)

**Verified**: every claim ADR-0013 makes about the CLI backend was measured against the real
`claude` CLI (2.1.212) *before* the module was written, not asserted from documentation.

- **A subscription really is enough — no API credential.** `claude -p --output-format json`
  returned a result with `ANTHROPIC_API_KEY` unset. This is what closes the long-standing "no
  credential in this environment" blocker.
- **A 40 KB payload over stdin works.** Confirms the argv path would have been wrong: a real
  `CBACT04C` prompt is tens of kilobytes, past the ~32 KB Windows command-line limit.
- **`--system-prompt-file` is genuinely applied.** Passing
  `prompts/registry/spec_critic/v1_0_0.md` made the model answer *as* `spec_critic`, asking for
  the narration and Known Facts it expects — the prompt was in force, not ignored.
- **Per-call harness overhead, measured**: 19,549 cache-creation tokens with the default system
  prompt, 9,819 with it replaced ($0.0399 → $0.0207 notional). Recorded because it is a real cost
  of this backend and a genuine reason to prefer the SDK at production volume.
- **A live round-trip through `call_model` passes** — `test_live_claude_cli_round_trip`, run
  explicitly with `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`. Every other test in that module is a
  fake wearing the CLI's shape; only this one proves the shape is right.

**Retry policy verified by call count, not by hope**: retryable statuses (429/500/503), timeouts,
and SDK connection failures each retry and then succeed; a 4xx is attempted exactly once; the loop
stops at `MAX_ATTEMPTS` rather than running forever. Backoff is confirmed bounded by the cap and
actually jittered (30 samples, all distinct values, each equal to what was really slept).

**A real accident, recorded rather than quietly fixed.** Changing the default backend to
`claude_cli` did **not** fail `tests/system/test_cli_design.py`. Those tests fake
`anthropic.Anthropic`, which is no longer the default path, so they silently began spawning real
`claude` subprocesses against a live subscription and the suite hung. A test that quietly costs
money and calls a live model is worse than one that fails. `tests/conftest.py` now pins the backend
for the whole suite, and any test wanting the real CLI must declare a `live_claude_cli` marker
*and* opt in by environment. The general lesson: **when a default changes, every fake positioned at
the old default becomes a silent pass-through**, and a green suite will not say so.

**Side effect worth noting**: `spec_extractor`, `spec_critic`, and `solution_architect` all reach
100% module coverage for the first time. Their `_default_*` bodies used to be the untested live-API
calls; they are now one-liners delegating to `call_model`, which the SDK-backend tests exercise.

**Command**: `pytest tests/system/test_model_client.py -v`
**Result**: 25 passed, 1 skipped (the live test, unless opted in); with the opt-in, 26 passed.

### Bounded fan-out — the concurrency cap (plan pillar 25)

**Verified**: `MAX_CONCURRENT_PROGRAMS` (default 4) is actually enforced, not merely configured.
The test runs 8 branches against a cap of 2 and asserts **peak observed concurrency**, because a
cap that is defined but never passed to `invoke()` would still satisfy a constant check — that
being the exact bug worth catching. Confirmed falsifiable: removing the `config={"max_concurrency":
...}` argument makes all 8 branches run at once and the test fails naming that count.

**Command**: `pytest tests/system/test_design_graph.py -v`
**Result**: 12/12 passed.

### Complexity-based routing — measured, not assumed (ADR-0014)

**Verified**: the bands were set from real measurements of all four Track C programs taken by
running the real pipeline, *before* the thresholds were chosen — not chosen first and justified
after.

| Program | Prompt chars | Paragraphs | Tier | Model (was) |
|---|---:|---:|---|---|
| `CBCUS01C` | 11,346 | 5 | `simple` | `claude-haiku-4-5` (was `claude-opus-5`) |
| `CBACT04C` | 74,230 | 22 | `complex` | `claude-opus-5` (unchanged) |
| `CBACT01C` | 78,647 | 16 | `complex` | `claude-opus-5` (unchanged) |
| `CBTRN02C` | 81,902 | 26 | `complex` | `claude-opus-5` (unchanged) |

- **The tiering works end to end through the real CLI, not just in unit isolation.**
  `test_complexity_routes_the_two_programs_to_different_models` runs `cobol-modernizer design`
  over `CBCUS01C` + `CBACT01C` and asserts the two extractor calls really reached the API with
  *different* models and efforts (`claude-haiku-4-5`/`low` and `claude-opus-5`/`high`). If tiering
  silently stopped working, both would resolve to the same entry and that test is what notices.
- **Bands are not borderline.** `CBCUS01C` measures 11,346 against a 25,000 ceiling; the next
  smallest program measures 74,230 against a 60,000 floor. `test_complexity.py` asserts that
  headroom directly, so a future threshold tweak that puts a real program on a knife edge fails
  here rather than in production.
- **Classification provably makes no model call.** The test that measures `CBTRN02C` replaces
  `model_client.call_model` with a function that fails the test if invoked — so a regression to
  "probe a model to pick a model" is caught rather than showing up as a quiet bill increase.
- **The architect takes the highest tier present**, asserted directly: a run containing one
  complex program routes the architect to `complex` even though half its programs are simple.
- **Config validation covers the whole file, not just the entry requested** — asserted by putting
  an invalid `effort` in a tier the test never asks for and confirming the lookup still fails. A
  typo in a band nothing hits today would otherwise lie in wait for whichever program first lands
  there.

**A latent truncation bug this surfaced, now fixed and pinned by a test**: `max_output_tokens` was
hardcoded at 4096, while every real response measured runs 5,485–23,366 output tokens. The SDK
backend would have truncated all of them (silently mid-narration, or as a hard parse error
mid-JSON); the CLI backend never sent the value, which is the only reason nothing had failed yet.
Per-tier ceilings now sit above observed output, and
`test_real_config_ceilings_clear_every_observed_output_length` asserts that against the measured
figures.

**Command**: `pytest tests/system/test_complexity.py tests/system/test_model_routing.py tests/system/test_cli_design.py -v`
**Result**: 71/71 passed.

### `spec_critic` — does it actually catch a wrong narration, and is the cheap model enough?

**Verified** — the question ADR-0004 flagged at Milestone C2 and deferred, now answered with real
model runs rather than reasoning.

A genuine `CBCUS01C` narration (from the live run, checked in at
`tests/fixtures/narrations/CBCUS01C/spec.md`) was corrupted with three factual errors, each
checkable against the source by line number:

| Corruption | Source says |
|---|---|
| `APPL-EOF` = 16 → **99** | line 63: `88 APPL-EOF VALUE 16.` |
| abend code 999 → **16** | line 157: `MOVE 999 TO ABCODE` |
| status `'10'` is clean EOF → **fatal** | lines 98/107–108 |

Every paragraph name, field name, and Known-Facts row was left intact, so the deterministic checks
cannot see these — asserted, not assumed: `compute_fidelity_issues` returns `[]` on the corrupted
narration, which makes the model the only line of defence.

| Model | Rules | Min score | Below 0.7 | Notional cost |
|---|---:|---:|---:|---:|
| `claude-haiku-4-5` | 12 | **0.00** | **3** | $0.1058 |
| `claude-opus-5` | 28 | 0.15 | **3** | $0.2388 |

**Both caught all three.** Haiku was more decisive on the `APPL-EOF` constant (0.00 vs 0.30) at
2.3× lower cost, so the cheap tier is not a compromise for this node — ADR-0004's deferred question
is closed in favour of keeping Haiku. It also establishes the critic is **load-bearing rather than
decorative**: the deterministic layer caught none of the three.

**This calibrates `LOW_CONFIDENCE_THRESHOLD` too** (ADR-0008 shipped `0.7` as an admitted guess).
Genuine narrations score 0.70–1.00 at minimum; a corrupted one scores 0.00–0.40. The threshold
separates them with margin. **An earlier reading was wrong** and is corrected in ADR-0008: a 0.70
minimum failing to flag looked like the threshold being too permissive, but that was a borderline
claim about a timestamp format, not a missed defect.

**A real limitation in the verification technique, found by this work.** The `faithful_narrate`
approach used across the suite — feeding the Known Facts block back as the narration — produces a
prompt a live critic rejects outright: *"I don't see the `spec.md` narration file in your
message."* It is right to; that prompt contains the same block twice under two labels. The
technique validates the deterministic machinery correctly, but every test using it also injects a
*fake* critic, so nothing exercised the combination. Hence the checked-in real narration, which is
explicitly **not** a golden fixture (`tests/fixtures/narrations/README.md`) — it is unreviewed
model output, valuable because it is what the pipeline really produces.

**Two measurements that came out negative, recorded rather than dropped:**

- **`--effort` does not measurably control the critic's cost on the CLI backend.** Six runs
  (low/medium/high × 2) on identical input: `medium` spanned 11,530→25,721 output tokens (2.2×),
  and `low` averaged *more* than `high`. Within-level variance exceeds between-level difference; at
  n=2 there is no signal. No cost saving is claimed from effort tuning.
- **The critic is not reproducible run-to-run.** The same input produced **11 to 20** scored rules
  across runs. For what ADR-0001 calls "the only independent check on extraction quality", that is
  a real property to know; it does not affect the pass/fail behaviour measured above (all runs
  flagged all three defects) but it means rule counts are not a stable metric.
- **An earlier hypothesis was disproved.** The critic's output was assumed to be verbose prose
  (~1,200 tokens per scored rule). Measuring the real output: the JSON is 1,535–2,028 tokens with
  rationales averaging 196 characters — already tight. **~90% of reported output tokens are
  thinking, not answer.** The planned "tighten the critic prompt" work was cancelled as addressing
  the wrong 10%.

**Command**: `pytest tests/system/test_critic_discrimination.py -v` (add
`COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1` for the billed half)
**Result**: 4 passed, 3 skipped without the opt-in; 7 passed with it.

### `spec_extractor` — can a cheaper model do this job? (ADR-0015)

**Verified**: four models run through the real extractor over `CBACT04C`, the hardest Track C
program, each narration scored against six facts independently verified against the source.

| Model | Facts | `fidelity_issues` | In tok | Out tok | Cost @ API rates |
|---|---:|---|---:|---:|---:|
| **`claude-opus-5`** | **6/6** | 0 | 36,320 | 13,000 | $0.507 |
| `claude-sonnet-5` | 5/6 | 0 | 40,099 | 12,619 | $0.206 |
| `claude-sonnet-4-6` | 5/6 | 0 | 30,246 | 11,652 | $0.266 |
| `claude-haiku-4-5` | 4/6 | 0 | 23,867 | 7,996 | $0.064 |

**The missing sixth fact is a confident false statement, not an omission.** `CBACT04C`'s
`ELSE PERFORM 1050-UPDATE-ACCOUNT` is unreachable, so the last account's accrued interest is never
posted. Opus 5 identified it; both Sonnets asserted the opposite — Sonnet 5: *"the main loop still
performs one final `1050-UPDATE-ACCOUNT` call after EOF to flush the last account's accumulated
interest"*; Sonnet 4.6: *"the loop's outer `ELSE` branch fires on the next iteration"*, naming a
mechanism that is backwards. Haiku additionally missed COMPUTE-without-`ROUNDED` truncation.

Three findings beyond the ranking:

- **All four scored `fidelity_issues = 0`.** The deterministic layer cannot distinguish them at
  all, including the one narrating dead code as live. That is the sharpest available statement
  that those checks are necessary and nowhere near sufficient.
- **The Sonnet-5 tokenizer difference is confirmed for COBOL**: 30,246 input tokens (Sonnet 4.6)
  vs 40,099 (Sonnet 5) on the byte-identical prompt — **+32.6%**, matching the documented ~30%.
  So Sonnet 4.6's lower token count does not make it cheaper per unit of work while Sonnet 5's
  introductory rate holds; it becomes cheaper only after 2026-08-31.
- **Detection was by substring and therefore under-reports.** Every narration was written to disk
  and the contested claims read directly — the Sonnet quotes above were confirmed by reading, not
  inferred from a missing keyword.

**This withdrew a saving the repo had already claimed.** ADR-0014 routed `CBCUS01C` to Haiku on the
reasoning that a small program is an easy task; Haiku had never been benchmarked on extraction, and
scored 4/6 when it was. With `verified_for` enforced, **every `spec_extractor` tier now resolves to
Opus 5** and `CBCUS01C` costs more than before. Restoring the saving is a benchmark on a *simple*
program, not a code change.

**Command**: `pytest tests/system/test_model_catalog.py tests/system/test_model_routing.py -v`
**Result**: 73/73 passed.

### CI itself — verified on GitHub, not just locally

Every module above was also verified green on a **real GitHub Actions run**, not assumed from a
local pass, for every PR merged so far (PRs #1–#4): lint, coverage-floor-gated test suite, and the
mermaid-diagram-parse check reused from `agentic-sdlc-control-plane`.

**Command**: `gh run list --repo jayakumar-devaraj/agentic-sdlc-cobol-modernizer --limit 1`
**Result**: `completed success` on every merge to date.

## Not yet covered (honest gaps, not silently skipped)

- **No full `design` run has been executed against real models yet.** This gap changed shape with
  ADR-0013 rather than closing. What is now proven: a real `claude` CLI round-trip through
  `call_model` works on a subscription, with no API credential
  (`test_live_claude_cli_round_trip`). What is still unproven: the three nodes' *actual* prompts
  against real models — no `spec.md` has been narrated, critiqued, or architected by a live model,
  so nobody has read real output and judged it. Until that run happens, the prose quality of
  `spec.md`, the usefulness of `spec_critic`'s per-rule scores, and the soundness of
  `solution_architect`'s batch/REST design are all unevaluated. That run is now possible and
  cheap; it is the obvious next verification step.
- *(historical, superseded above)* The original form of this gap was that
  `_default_narrate`/`_default_critique`/`_default_architect` had never been invoked at all — only every deterministic step upstream of each (field
  mapping, paragraph extraction, domain-entity merging, prompt construction, guardrail wrapping,
  fidelity checks, JSON parsing) is verified against real data; every test injects a fake
  `narrate`/`critique`/`architect` in its place. This is a real gap, not a mock standing in for a
  "done" claim — revisit once a real credential is available (Milestone C5 integration, or
  whenever this repo is actually invoked with one), by running `extract_spec`, `critique_spec`,
  then `design_solution` against all four real Track C programs with the default callables and
  manually reviewing the resulting `spec.md` prose, the critic's per-rule scores, and the
  architect's batch job/REST endpoint design against the source, the way `.claude/agents/qa.md`
  requires for anything a unit test can't meaningfully reach. This also means ADR-0004's own
  deferred question — whether `spec_critic`'s cheaper model tier holds up empirically once real
  critiques exist — is still unanswered.
- **A fixed `OCCURS` cannot be represented, only flagged.** `PicMapping` has no cardinality field
  and `DomainField` cannot express a collection, so `CBACT01C`'s `ARR-ACCT-BAL OCCURS 5 TIMES`
  group is routed to the human gate rather than mapped (ADR-0011). That is the correct behavior
  given the current types — flattening an array to a scalar would be a wrong answer that looks
  right — but it is a capability gap, not a solved problem. Track C has exactly one occurrence and
  no node consumes that file yet; revisit when Milestone C4 generates a reader for it, or with
  Track B's B3 alias-analysis module.
- **`FD` record layouts do not become domain entities.** They are now parsed and visible in
  `spec.md`, but `build_domain_entities` only promotes copybook-sourced fields (ADR-0010), and
  these are program-local. Whether a file record layout should be an entity in its own right is a
  real open question for Milestone C4, when a Spring Batch reader needs a type to read into —
  deliberately not answered by ADR-0011.
- **Only `CBACT04C` has a hand-verified golden fixture**, and that is now a deliberate choice
  rather than an open question. `CBCUS01C`, `CBACT01C`, and `CBTRN02C` have real, byte-verified
  source, confirmed-working `spec_extractor`/`spec_critic` extraction (via the
  faithful-Known-Facts-narration technique), and — as of the entry above — exhaustive
  hand-cross-checked numeric-field verification. What they do not have is `CBACT04C`-level golden
  narrative *prose*. Writing that today would mean hand-authoring expected prose that nothing in
  the pipeline currently produces to compare against: `spec.md`'s narration comes from a live model
  call this environment has no credential for (see the first gap above). The decision is to defer
  those three golden fixtures until a real credential exists, at which point they become a genuine
  regression baseline for real model output rather than a hand-written artifact checked only
  against itself. Milestone C2's gate is being closed on its literal numeric-field wording, which
  is checkable without a model, not on prose.
- **Auditing/provenance** (`CLAUDE.md`'s stated concern: every generated artifact traces back to
  the exact COBOL source line it came from) is now source-label-precise (every fact
  `spec_extractor` emits names the exact program or copybook it came from) but not yet
  line-precise — see ADR-0006 for why, and what closing this gap would require
  (`parsing/cobol_parser.py` carrying line numbers through field/paragraph extraction).
- **No `low_confidence_rule` gate item has ever been produced by a real model.**
  `LOW_CONFIDENCE_THRESHOLD = 0.7` (ADR-0008) and `spec_critic`'s cheaper model tier (ADR-0004)
  are both still uncalibrated for the same reason as the gap above: every critique in every test
  is a fake returning a chosen score. The threshold is exercised at its boundary by construction,
  but whether `0.7` separates anything meaningful in practice is unknown.
- **Control-plane's real gate has not been exercised against a real `design.json`.** The artifact
  now exists and is schema-valid, which was the missing half; wiring control-plane's side is
  Track P1 / Milestone C5. Until then, "control-plane's durable gate reviews this" is a design
  claim backed by control-plane's own verified gate machinery, not by an observed end-to-end run
  through it.
