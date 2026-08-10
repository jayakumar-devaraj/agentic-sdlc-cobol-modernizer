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

As of this report: **581 tests passed (4 skipped — the opt-in live-CLI tests), 99.03% overall
coverage** (17 of 1,748 statements uncovered). These are **CI's numbers**, from the run on the
change that added this line — not a local figure. Locally the Postgres-backed
`tools/knowledge_store.py` suite skips without a running Docker daemon, which is why the
authoritative count is taken from CI, where a real service container makes it skip nothing.

`templates/target-spring-boot-baseline/` is Java and is not in that figure. It has its own suite —
13 tests, 0 skipped — run by CI on the JDK it pins; see the entry below.

| Module | Coverage |
|---|---|
| `cli.py` | 98% |
| `core/complexity.py` | 100% |
| `core/contracts.py` | 100% |
| `core/design_outputs.py` | 100% |
| `core/guardrails.py` | 100% |
| `core/model_catalog.py` | 93% |
| `core/model_client.py` | 98% |
| `core/model_routing.py` | 98% |
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

This table previously listed `core/model_routing.py` twice (at 98% and 100%) and omitted
`core/model_catalog.py` entirely — a transcription error, corrected against a real
`--cov-report=term` run rather than by picking one of the two rows.

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

### `tools/knowledge_store.py` — the ADR-0016 dimension change, and the stale-schema defect it found

**Verified**: `EMBEDDING_DIMENSIONS` moving from 1536 (an OpenAI-shaped placeholder) to 1024
(Voyage AI's default output dimension, ADR-0016), re-run against the same real Postgres+pgvector
container. All eleven pre-existing tests pass unchanged at the new dimension — including the
analytically-known nearest-neighbour test, which re-derives its three orthogonal basis vectors from
the constant and still asserts the exact expected ordering and strictly increasing distances.

**A real defect was found by making the change, not reasoned about afterwards.** The first run
after editing the constant failed with `psycopg.errors.DataException: expected 1536 dimensions, not
1024` — because `ensure_schema` uses `CREATE TABLE IF NOT EXISTS`, which against an existing table
is a no-op that silently keeps the old column type. The local dev container had genuinely been
holding `vector(1536)` since PR #4. Two things were wrong with that failure beyond the mismatch
itself: it surfaced from `store_entry`, several calls downstream of the actual cause, and its
message blames the caller's embedding when the real culprit is a schema older than the code.
Confirmed against the live catalog that the dimension is readable up front —
`SELECT atttypmod, format_type(atttypid, atttypmod) FROM pg_attribute ...` returned
`1536 | vector(1536)`, establishing that pgvector stores the declared dimension in `atttypmod`
directly (no `+4` header, unlike `varchar`) rather than assuming it.

`ensure_schema` now reads that value and raises `KnowledgeStoreSchemaError` naming **both**
dimensions and the manual recovery, per this repo's fail-loudly-on-an-unambiguous-case rule
(the `UnsupportedPicConstructError` family). Recovery is deliberately not automatic: dropping or
altering the table would discard stored vectors, and this module cannot know whether they matter.

**Confirmed falsifiable rather than trusted on sight**, the same way the schema drift check and the
numeric-field gate were: the guard was first observed firing against the genuinely stale container
table, and is now pinned by `test_ensure_schema_rejects_a_table_whose_embedding_dimension_differs`,
which rebuilds that exact situation (drops the table, recreates it at
`EMBEDDING_DIMENSIONS + 512`, asserts the raise and both dimensions in the message, then restores
the table in a `finally`). The stale table was then dropped and the suite re-run green.

**Command**: `pytest tests/system/test_knowledge_store.py -v`
**Result**: 12/12 passed (11 pre-existing + 1 new regression test).

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

### `run_id` correlation and `RunCost`, verified under real concurrency (ADR-0018)

**Verified**: both halves of ADR-0018 against a real four-program `run_design` on a real thread
pool — not sequentially, because sequential execution cannot distinguish a working implementation
from a broken one here.

- **`run_id` reaches every branch.** A fake `narrate` records `current_run_id()` per program;
  all four branches report the id bound before `invoke`, and the test separately asserts **more
  than one thread id was observed** — without that, the propagation assertion would pass trivially
  on a single thread and prove nothing.
- **`RunCost` sums across concurrent branches.** With `_call_anthropic_sdk` faked to a known
  result, a four-program run totals exactly `2 × 4 + 1 = 9` calls (4 extractor + 4 critic
  concurrent, 1 architect after fan-in) with token counts multiplying out exactly. **This is the
  assertion that catches the subtle failure**: had the accumulator been a `ContextVar` of running
  integers rather than a mutable object, each branch would have incremented a private copy and the
  parent would have read only the architect's call.
- **Partial cost is distinguishable from zero cost.** On a backend reporting no cost,
  `notional_cost_usd` stays `None` while `calls_without_reported_cost` equals the call count and
  token counts stay exact — so a consumer can tell "nothing cost anything" from "nobody said".
- **The `--json` stdout contract still holds.** `test_cli_design.py` passes unchanged: the new
  cost log line goes to stderr with everything else, and stdout carries exactly one object.

**A real defect was found by the tests, not after them.** `bind_run_id` mutates the ambient
context and never restores it — correct for a CLI process, wrong under pytest where all tests share
one context. A test asserting the unbound placeholder **passed in isolation and failed in suite
order**. Fixed with an autouse reset in `tests/conftest.py`, alongside the existing backend pin,
rather than by weakening the assertion.

**A coverage blind spot worth recording.** `telemetry/logging_config.py` reported **100% coverage
with zero tests** — every line executed because `cli.main()` calls `configure_logging` during the
CLI tests, so coverage confirmed the module ran while nothing asserted what the logging did.
`tests/system/test_logging_config.py` now exists. Coverage measures execution, not verification.

**A code-review pass found the cost summary was unreachable on the failure path**, and it is fixed
here. Both the log line and the `RunCost` construction sat *after* `app.invoke` returned, so a run
that raised partway discarded the spend of every branch that had already completed — the exact
situation in which the question gets asked, and one where no `design.json` and no cost-bearing
`DesignCliResult` exist to fall back on. The summary now happens in a `finally`, driven by a test
that causes a real failure (one valid program, one missing) and asserts the line still reports
`model_calls=1`. **Confirmed falsifiable**: restoring the original shape makes exactly that test
fail, and nothing else.

**Command**: `pytest tests/system/test_design_graph.py tests/system/test_logging_config.py -v`
**Result**: 20/20 passed (12 pre-existing + 8 new).

### Prompt duplication between `spec_extractor` and `spec_critic` (ADR-0017)

**Verified**: a harness captured the real user-turn prompt of every call in a four-program run
(fakes in place of models, no spend), rather than reasoning about prompt sizes from the source.

**What it found.** `spec_critic` re-resolves each program from disk and rebuilds `spec_extractor`'s
entire prompt character for character:

| Program | Extractor | Critic | Byte-identical | Dup |
|---|---:|---:|---:|---:|
| `CBCUS01C` | 11,346 | 11,419 | 11,346 | 99.4% |
| `CBACT01C` | 78,647 | 78,720 | 78,647 | 99.9% |
| `CBTRN02C` | 81,902 | 81,975 | 81,902 | 99.9% |
| `CBACT04C` | 74,230 | 74,303 | 74,230 | 99.9% |
| **Total** | **246,125** | **246,417** | **246,125** | |

Calibrated against ADR-0015's measured `CBACT04C` extraction (74,230 chars → 36,320 input tokens =
**2.04 chars/token**), that is ≈120,400 duplicated input tokens per run — ≈$0.60, or **~26% of the
measured $2.31 four-program run**. Two framings the measurement ruled out, both checked rather than
assumed: **no multi-turn history exists anywhere** (every call is stateless and single-turn, the
graph passes typed objects not transcripts), and **nothing is near a context limit** (largest
prompt ≈40k tokens against 1M).

**The fix verified here is the ordering, not the caching.** Before ADR-0017 the shared span was a
*suffix* of the critic prompt, which no prefix-matched cache could act on. After the reorder,
confirmed directly for all four programs:

```
CBCUS01C   prefix=True  suffix=False  shared= 11,346/ 11,428 = 99.3%
CBACT01C   prefix=True  suffix=False  shared= 78,647/ 78,729 = 99.9%
CBTRN02C   prefix=True  suffix=False  shared= 81,902/ 81,984 = 99.9%
CBACT04C   prefix=True  suffix=False  shared= 74,230/ 74,312 = 99.9%
```

Pinned by two tests, because `in prompt` assertions pass under either order and the property would
otherwise regress silently: one asserts `Known Facts < source < narration` and that the narration
is the tail, the other asserts the critic prompt literally `startswith` the extractor's captured
real prompt and that the shared span exceeds half the total.

**Command**: `pytest tests/system/test_spec_critic.py -v`
**Result**: 23/23 passed (21 pre-existing + 2 new ordering tests).

**Open, deliberately not claimed**: the reorder itself is unbenchmarked against a live model. The
registry prompt now states the new order explicitly, so the model is not surprised by it, but no
live run confirms identical critic behaviour. `test_critic_discrimination.py` (a real narration
with three planted errors, opt-in, ~$0.10–0.35 on Haiku) is the cheap check that would.

### `claude` CLI prompt-cache behaviour, and why ADR-0017 dropped its caching half

**Verified**: three live `claude` CLI calls (~$0.03 of real Haiku spend) run *before* implementing
`cache_control`, to test whether that backend already reuses a cached prefix across separate
subprocess invocations. `A` = the real `spec_critic` system prompt, `B` = a byte-identical repeat,
`C` = the larger `spec_extractor` prompt as a confound control — the CLI ships its own harness
system prompt, so a constant read on `B` would be the harness caching itself rather than ours.

| Call | input | cache_write | cache_read | output |
|---|---:|---:|---:|---:|
| A `spec_critic` sys, 1st | 10 | 10,704 | 0 | 44 |
| B identical repeat | 10 | 4,506 | **6,318** | 84 |
| C `spec_extractor` sys | 10 | 5,599 | 5,441 | 59 |

- **Caching works across separate `claude -p` invocations** — `cache_read` goes 0 → 6,318. Each
  call is a fresh subprocess; the cache is keyed on content, not process.
- **Reuse is partial**: a byte-identical repeat reused ~59% of the prefix and re-wrote 4,506
  tokens, so something volatile sits inside the CLI's own prefix. n=1 per condition — recorded as
  suggestive, not established.
- **The decisive figure**: total prefix ≈10,700–11,050 tokens against system prompts of only
  ~900–1,100 tokens, putting **CLI harness overhead at ≈9,800 tokens per call**. This
  independently corroborates ADR-0013's measured **9,819** cache-creation tokens with the default
  system prompt replaced — the same number reached from the opposite direction.

**Consequence**: across a nine-call design run the harness costs ≈88,200 tokens versus ≈9,350 for
every system prompt in the repo combined — **~9.4× larger than the entire target** — and the CLI
exposes no `cache_control` flag regardless. The planned caching work was **dropped on this
measurement** rather than implemented and then found worthless.

**Command**: three `call_model(..., backend="claude_cli")` calls on `claude-haiku-4-5`, usage read
from `ModelCallResult` (instrumentation ADR-0013 already provides).
**Result**: as tabulated above.

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
stops at `MAX_TRANSPORT_ATTEMPTS` rather than running forever. Backoff is confirmed bounded by the cap and
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

### Track C's real file I/O, and CardDemo's real data files (ADR-0019)

**Verified**: the two factual premises ADR-0019 rests on, both of which had been asserted from
memory in a draft before being checked — and checking each one found the draft wrong.

**1. Every `SELECT` in the four programs, read from the real fixture source.** 16 files: 10
`ORGANIZATION IS INDEXED` (VSAM KSDS) and **6 `SEQUENTIAL`**, 7 `ACCESS MODE IS RANDOM`, 3 opened
`I-O` and `REWRITE`n. The full table is in ADR-0019. **This falsified the draft's claim that "these
are VSAM KSDS files, not flat files"**: `CBTRN02C`'s *driving* dataset (`DALYTRAN-FILE`) is a plain
sequential file, and five of six outputs are sequential. It also falsified a long-standing line in
`docs/cobol-construct-support-matrix.md` — *"Track C only reads existing files"* — contradicted by
`CBACT04C.cbl:356`, `CBTRN02C.cbl:510,528,554`, and five `OPEN OUTPUT` statements. Both are
corrected in this change rather than carried forward.

**Command**: `rg "^\s+(SELECT|ORGANIZATION|ACCESS MODE|RECORD KEY|OPEN|READ|WRITE|REWRITE)\s" tests/fixtures/tenant_repo_sample/app/cbl/`
**Result**: as tabulated in ADR-0019.

**2. CardDemo's real data files, against `carddemo-tenant-service` itself.** The plan carried this
as an explicitly *unverified* precondition ("this repo's fixture is source only"). It is now checked
and it changed what step 40a has to build:

- `app/data/ASCII/` holds nine fixed-width `.txt` files; `app/data/EBCDIC/` holds the mainframe
  `.PS` datasets. Five of the six files a Track C program reads match their copybook's own `RECLN`
  exactly: `acctdata.txt` 50 records × 300 bytes (`CVACT01Y`), `tcatbal.txt` 50 × 50 (`CVTRA01Y`),
  `dailytran.txt` 300 × 350 (`CVTRA06Y`), `custdata.txt` 50 × 500 (`CVCUS01Y`), `discgrp.txt`
  51 × 50 (`CVTRA02Y`).
- **The sixth does not, and that was the point of counting rather than assuming.** `cardxref.txt`
  is **36 bytes per record against `CVACT03Y`'s declared `RECLN 50`** — exactly the 16 + 9 + 11 of
  its three real fields, with the trailing `FILLER PIC X(14)` simply absent from the file. A reader
  built to the copybook's record length would misalign every record after the first. `XREF-FILE` is
  a random keyed lookup in **both** business programs (`CBACT04C`, `CBTRN02C`), so this is on the
  critical path, not in a demo output.
- **Signed numerics carry a zoned-decimal sign overpunch.** The first `acctdata.txt` record's
  `ACCT-CURR-BAL` is the twelve characters `00000001940{`, not `000000019400`; the trailing byte
  encodes the last digit *and* the sign. `dailytran.txt`'s `DALYTRAN-AMT` shows `0000005047G`
  (= 504.77) and contains real negatives. `new BigDecimal("00000001940{")` throws; dropping the
  last character instead loses a factor of ten and the sign. Decoding this is a required, testable
  part of step 40a, driven off `pic_mapper`'s existing signedness and scale.
- **`tcatbal.txt` has 49 `CR` bytes against 50 `LF`** — 49 of 50 records `CRLF`-terminated, one not;
  every other ASCII data file is pure `LF` (`acctdata` 0/50, `dailytran` 0/300, `discgrp` 0/51,
  `custdata` 0/50, `cardxref` 0/50). It is `CBACT04C`'s driving dataset, so this lands on the
  interest calculator specifically. Same defect class as `CODATECN.cpy`'s real `CRLF` (PR #10),
  same upstream repo, found the same way — by counting bytes rather than trusting a text file to be
  uniform.

No test pins these yet, deliberately: the data files are not in this repo (the fixture is source
only), and a test that reaches GitHub at run time would be a network dependency in a suite that has
none. They become falsifiable when step 40a lands a byte-verified data fixture, the same way
`tests/fixtures/tenant_repo_sample/` did for source.

**Command**: `gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/data/ASCII --jq '.[].name'`,
then each file's base64 `content` decoded locally and its bytes counted (record length, `CR`/`LF`
totals, and the literal characters at the signed-field offsets).
**Result**: as stated above.

### `templates/target-spring-boot-baseline/` — the Java 25 stack, compiled and run on Java 25 (step 38)

**Verified**: the four mitigations ADR-0019 wrote as *gates* on step 38, executed rather than
intended. This repo has no JDK, so all of it runs in CI (`.github/workflows/ci.yml`, job
`template-build`).

**The toolchain is the pinned one, confirmed from the build's own output** — not from the workflow
file that asked for it:

```
openjdk version "25.0.3" 2026-04-21 LTS
Apache Maven 3.9.16
Compiling 3 source files with javac [debug parameters release 25] to target/classes
Tests run: 13, Failures: 0, Errors: 0, Skipped: 0
BUILD SUCCESS
```

`BaselineStackTest` asserts `Runtime.version().feature() == 25` from inside the JVM as well, so a
workflow that silently resolved a different JDK fails rather than going green —
`maven.compiler.release` constrains the bytecode target and says nothing about what ran.

**Each named ecosystem risk is exercised against a real PostgreSQL container**, because all three
fail at *runtime*, where a compile-error-driven self-healing loop cannot help:

- **Hibernate/ByteBuddy**: the `EntityManagerFactory` is built and opened. Building it is what
  drives ByteBuddy, the library that historically breaks first on a new class-file version.
- **Mockito's instrumentation agent**: a `final` class is mocked, deliberately — an interface would
  be proxied by plain JDK reflection and would pass with the agent completely broken. Surefire runs
  with `-XX:+EnableDynamicAgentLoading` so the dependency on self-attach is explicit in the build
  rather than one JDK release from an unpredicted failure.
- **Testcontainers 2.0.5** starts the container at all.

**Zero tests skip.** A container test that skips without Docker would turn this gate into
decoration — the failure this repo already corrected once, when `test_knowledge_store.py` could
have skipped in CI forever.

**ADR-0019's fourth reason for choosing PostgreSQL is checked, not restated**: a `NUMERIC(12,2)`
column accepts `9999999999.99` and **rejects** `10000000000.00` with `numeric field overflow`,
where a COBOL `MOVE` would silently discard the high-order digit. That is the zero-drift property
becoming a database constraint rather than an application convention.

**A real defect was found by the first CI run, and it is a trap for the code generator.**
`BaselineStackTest` initially set `spring.batch.jdbc.initialize-schema=always` and then counted
**zero** batch metadata tables. **Spring Boot 4 removed `spring.batch.jdbc.*` from
`BatchProperties` entirely**; unknown configuration keys are silently ignored, so the key looked
decided and did nothing. Every pre-Boot-4 Spring Batch example sets it — the same shape as
`@EnableBatchProcessing`, which in Boot 3+ switches auto-configuration *off*. The test now applies
`org/springframework/batch/core/schema-postgresql.sql` itself, `application.yml` states the absence
and the reason instead of carrying a dead key, and ADR-0019 gains an amendment moving schema
ownership to step 40a. **Twelve of thirteen tests passed on that first run**, so the JDK 25 gate
itself was clear before this was fixed.

**Two coordinate facts verified against Maven Central before writing the pom**, rather than
discovered by a failed build: Testcontainers 2.x renamed `org.testcontainers:postgresql` to
`org.testcontainers:testcontainers-postgresql` (the 1.x coordinates resolve to nothing), and the
Spring Boot 4.1.0 BOM manages Hibernate 7.4.1, ByteBuddy 1.18.10, Mockito 5.23.0 and Testcontainers
2.0.5 — the four libraries ADR-0019 names as the actual risk.

**`CobolArithmetic`'s 8 tests pin the rules a literal translation gets wrong**, each with the wrong
answer asserted alongside the right one so the difference is visible rather than claimed:
truncation vs rounding (`194.995` → `194.99`, not `195.00`); truncation toward zero rather than
toward negative infinity (`-194.999` → `-194.99`, where `FLOOR` gives `-195.00` — the two agree on
every positive number, so this is the case that can tell them apart); `BigDecimal.divide` throwing
on `100/3` where the helper returns `33.33`; single rounding (`20099/20000` = `1.00495` → `1.00`,
where an intermediate-precision implementation gives `1.01`); and `requireFits` raising on
`10000000000.00` against `PIC S9(10)V99` instead of losing the high-order digit in silence.

**The template's textual invariants are pinned from this repo's own suite**
(`tests/system/test_target_template.py`, 6 tests, 0.02s, no JDK) because compiling proves the
ecosystem supports the pin but not that the pin still says 25, that no preview flag appeared, or
that the scaffold stayed free of this tenant's vocabulary. **Writing them found a real defect
immediately**: XML forbids a double hyphen inside a comment, so the pom comment explaining that the
preview flag stays off was making the file not well-formed — with no JDK on this machine *at the
time*, nothing else would have caught it before CI. (A JDK and Maven were installed locally on
2026-08-09; the reasoning stands as the reason these Python invariants exist, since they run in
0.02s against no toolchain at all and CI is still the only place the Java build is authoritative.) **Confirmed falsifiable**: flipping
`maven.compiler.release` to 21 fails exactly that one test and nothing else, with the substitution
asserted to have applied before the result was believed.

**Command**: `mvn -B -ntp verify` (CI job `template-build`, `temurin` 25) and
`pytest tests/system/test_target_template.py -v`
**Result**: `Tests run: 13, Failures: 0, Errors: 0, Skipped: 0` / `BUILD SUCCESS`; 6/6 passed.

### `nodes/modernization_engineer.py` + `rendering/` — the generate split, against real Track C data (step 39)

**What was verified, and what deliberately was not.** The node's whole design is that a model
writes one method body and everything around it is rendered. Both halves are exercised against the
real four-program corpus with the model call injected. **The live call has not run** — no real model
has yet written a line of this Java, and nothing below claims otherwise.

```
.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/system/test_knowledge_store.py \
  --cov=cobol_modernizer --cov-report=term
```

Real result locally: **466 passed, 4 skipped in 21.33s**, with the container suite excluded because
Docker's daemon was not running on this machine *when that run was made*. (It was started later the
same day; the run is left as recorded rather than re-run, because the point of the entry is CI's
number being the authoritative one, which does not change.)

**CI ran the same suite with a real Postgres service container and reported 478 passed, 4 skipped,
99.11% coverage** (run `31346976317`, both `test` and `template-build` green). The twelve-test
difference is exactly `tools/knowledge_store.py`'s suite, which skips nothing there — worth stating
because a local run of this repo is *structurally* weaker than CI, not merely slower, and a number
quoted from the wrong one would understate coverage while sounding more careful.

Per-module, on the modules this work added:

| Module | Coverage | The uncovered part |
|---|---|---|
| `rendering/java_names.py` | 100% | — |
| `rendering/java_records.py` | 100% | — |
| `rendering/java_processor.py` | 100% | — |
| `nodes/modernization_engineer.py` | 99% | `_default_author`'s body — the live model call, the same honest gap every other node carries and the reason it is injectable |

**Rendered against real copybooks, not hand-built entities.** `build_domain_entities` over the real
`CBACT04C`/`CBCUS01C`/`CBACT01C`/`CBTRN02C` fixtures produces seven entities; all seven render.
`Account` (from the real `CVACT01Y`, used by three real programs) renders twelve components with
`pic_mapper`'s computed shapes carried as documented fact — `acctCurrBal` as
`BigDecimal ... precision 12, scale 2, signed`. **`ACCT-EXPIRAION-DATE` renders as
`acctExpiraionDate`**: the typo is upstream in the real copybook, and a mechanical transform that
silently corrected it would break the trace back to source.

**The review boundary is real and is checked.** Everything outside the `BEGIN/END model-authored
logic` markers is a pure function of `design.json`; a test asserts no `@Override`, `@Component`,
`public class`, `package` or `import` ever appears inside the region. A body containing either
marker is refused (`GeneratedBodyForgeryError`) rather than escaped — the mirror of
`DelimiterForgeryError`, which refuses forged delimiters on the way *in*.

**Prompt ordering, measured rather than asserted.** Two steps of `CBACT04C` produce prompts sharing
**70,547 of 70,688 characters (99.8%) as a genuine common prefix**, leaving a 141-character
per-step tail. This was wrong when first written — the step facts led, making the ~68k shared span
a suffix behind a variable prefix, which is exactly the shape ADR-0017 corrected in `spec_critic`
after G13 measured it at ~26% of a run. The test asserts the shared span *is* a prefix, so
reordering the sections fails it.

**Falsifiability confirmed by breaking things on purpose**, not by reading the assertions:

- Renaming `generate`'s `--run-id` to `--corr-id` fails the parser-parity test with exactly
  `generate is missing ['--run-id']`.
- A step named `1300-COMPUTE-INTEREST` yields the class `1300ComputeInterestProcessor` and raises
  `UnrenderableJavaNameError: ... is not a legal Java identifier`, naming the COBOL source.
- Per-line `strip()` on the body flattens a wrapped expression's continuations onto their
  statement's column. It still compiles, so an explicit indentation assertion catches it where a
  build would not.

**The run budget, under real concurrency.** Eight threads racing a ceiling of four: every trial
gives exactly eight recorded calls and exactly four `RunBudgetExceededError`s, with no run of 500
producing any other outcome. Enforcement lives inside `UsageAccumulator`'s existing lock — outside
it, several branches would read the same pre-increment total and all pass, which is the
lost-update race the lock exists for, reintroduced in the check rather than the counter.

**What this does not establish**, stated plainly so a green suite does not imply it:

1. **No Java produced here has ever been compiled.** ~~There is no JDK on this machine and Docker's
   daemon is not running, so `javac` has not seen any of it.~~ **The stated cause is superseded
   (2026-08-09): a JDK and Maven are now installed and the template builds green locally.** The
   claim itself still holds and is the one that matters — **generated** output does not reach a
   build at all yet, because nothing writes it to a project `javac` is pointed at. That is step 40,
   and the missing toolchain was never the reason.
2. ~~**No real model has written a body.**~~ **Superseded the same day — one real call has now run.
   See the entry below.** What remains true is narrower: no real model has yet produced a *working*
   implementation, and quality across programs is unmeasured.
3. **Nothing is written to `card-service`**, which still holds 0 Java files. The node has no
   caller: `cli.py`'s `generate` subcommand still returns `"Not implemented"`.

### The first real `modernization_engineer` call — and the design defect it found (step 39)

**One live model call**, `claude-opus-5` via the `claude_cli` backend, 2026-08-09. Real COBOL
source, real copybooks, real `pic_mapper` output, real routing, real prompt. The narration was the
hand-verified golden `spec.md` (step 32) rather than a fresh extraction, so the call measured the
new node instead of re-proving nodes that already have measurements. The `BatchStepDesign` was
**constructed by hand** from real `CBACT04C` paragraph names — `solution_architect`'s batch design
is LLM-authored and running it would have been a second call. That hand-construction is what this
run turned out to be a test of.

**The model refused to implement the step.** Asked for
`BigDecimal process(TranCatBal item)` from `1300-COMPUTE-INTEREST`, it threw
`IllegalStateException` with a diagnostic instead of returning a number, on the grounds that the
paragraph needs `DIS-INT-RATE` and nothing reachable from a `TranCatBal` supplies it.

**Every factual claim in its response was checked against the real source. All of them hold:**

| Claim | Verified against |
|---|---|
| `COMPUTE WS-MONTHLY-INT = ( TRAN-CAT-BAL * DIS-INT-RATE) / 1200`, no `ROUNDED` | `CBACT04C.cbl:464-465` — verbatim |
| `WS-MONTHLY-INT` is `PIC S9(09)V99` | `CBACT04C.cbl:168` — exact |
| The paragraph also does `ADD WS-MONTHLY-INT TO WS-TOTAL-INT` and `PERFORM 1300-B-WRITE-TX` | `CBACT04C.cbl:467-468` |
| `DISCGRP-STATUS` `'23'` triggers a `'DEFAULT'` group re-read | Golden fixture's hand-verified business rules |
| `DIS-INT-RATE` is not reachable from `TranCatBal` | `TranCatBal` really has exactly four components |

**Zero invented identifiers.** The four accessors it emitted — `trancatAcctId()`,
`trancatTypeCd()`, `trancatCd()`, `tranCatBal()` — are all four real components of the real
`TranCatBal` entity, spelled correctly. This is the specific failure mode the Known Facts section
exists to prevent, and on this call it did.

**It also flagged two behaviours that would otherwise be silently lost**: the caller only performs
the paragraph when `DIS-INT-RATE` is non-zero, and the paragraph accumulates into `WS-TOTAL-INT`
and writes a transaction — neither expressible in a step whose signature returns one `BigDecimal`.

**The defect is in the design, not the model.** A step design of
`TranCatBal -> BigDecimal` for this paragraph is genuinely insufficient, and the hand-constructed
input was wrong in exactly the way the feasibility assessment's § 3.3 predicted in the abstract:
*"`design.json` is too thin to generate from... the generator will infer most of the architecture
from prose."* **That prediction is now measured rather than argued** — and the failure mode was the
good one: refusal with a diagnostic, not a confident invention.

**Measured cost and token profile — the first real numbers for this node:**

| | Placeholder in `model_routing.yaml` | Measured |
|---|---:|---:|
| Input tokens | 50,000 | **39,862** (39,860 cache-creation + 2) |
| Output tokens | 30,000 | **2,894** |
| Notional cost | — | **$0.302311** |
| Duration | — | 40.9s |

**Output came in at roughly a tenth of the placeholder**, which is the render-don't-generate split
showing up in the measurement: the model writes a method body, not a file. The profile is
deliberately **not** updated from this single call — it is one sample and an atypical one, since a
refusal is not a representative implementation. It is recorded so the next call has something to
be compared against rather than replacing evidence with a single point.

**Note for the caching work**: `cache_creation_input_tokens` was 39,860 against `cache_read` of 0,
so the CLI backend did write a cache entry for this prefix. Whether a second call against the same
99.8% prefix reads it is the obvious next measurement and has not been made.

### The second real call — the same step with the design fixed, and correct arithmetic out (step 39)

Run 1's refusal named two ways to make the step implementable. Option (a) — an input record
carrying both the category balance and the resolved rate — is the one the processor renderer can
currently express, so that is what this run supplied. **`TranCatBalWithRate` was hand-constructed**,
standing in for a corrected `solution_architect` design; its component names and numeric shapes are
copied verbatim from the real entities, but the joining is a human's, not a model's. The step
description explicitly scoped out the accumulate-and-write that run 1 flagged, so this call tests
one thing: **given an adequate design, is the COBOL arithmetic translated correctly?**

**It is.** The generated body:

```java
BigDecimal quotient = categoryBalance.multiply(annualRatePercent)
        .divide(new BigDecimal("1200"), MathContext.DECIMAL128);
return CobolArithmetic.truncate(quotient, 2);
```

Checked against the three rules a literal translation gets wrong: truncation not rounding
(`CobolArithmetic.truncate`, not `setScale`), toward zero not `FLOOR`, and `BigDecimal` constructed
from a **string literal**, never a `double`. All three correct, on a `COMPUTE` with no `ROUNDED`
whose receiving field is `S9(09)V99`.

**The model flagged an assumption, and the assumption was right.** Note 1 said the Known Facts did
not give it `CobolArithmetic`'s signatures, so it had called `truncate(BigDecimal, int)` and a
reviewer should substitute the real name if different. That method exists with exactly that
signature (`CobolArithmetic.java:45`). Guessing correctly is not the point — **saying it was
guessing is**.

**Two real gaps that same honesty exposed, both in this repo's prompt rather than in the model:**

1. **`CobolArithmetic.divide(dividend, divisor, scale)` already exists** (`:68`) and does the
   direct truncating divide in one step. The model described exactly that as the formulation it
   would prefer *"for a reviewer who wants zero intermediate rounding at all"* — and could not use
   it, because nothing told it the method was there. The prompt made the model write a
   second-choice implementation it had itself identified as second-choice.
2. **`CobolArithmetic.requireFits(value, precision, scale)` exists** (`:101`) and is precisely the
   size-guard note 3 asked for: *"If CobolArithmetic has a checked MOVE/store helper for a declared
   precision/scale, the return should go through it so an oversized interest amount throws rather
   than being written 10x too small."* It does. The model could not know.

**Fix implied and not yet made**: the Known Facts must carry the target's own helper API. This is
a cheap, well-evidenced prompt change, and it is exactly the kind of finding a single real call
buys that no amount of injected-fake testing can.

**Measured, run 2:**

| | Run 1 | Run 2 |
|---|---:|---:|
| Input tokens (fresh) | 39,860 cache-creation | 31,833 cache-creation |
| **Cache read** | **0** | **49,290** |
| Output tokens | 2,894 | 4,682 |
| Notional cost | $0.302311 | $0.295966 |

**Cross-invocation prompt caching is confirmed working on the `claude_cli` backend** — 49,290
tokens served from cache on the second call. This is the first direct evidence that the
stable-prefix-first ordering pays off in `generate`, and it corroborates the R1.5 probe's finding
from the other direction. Cost barely moved only because run 2's output was 62% larger (six
substantive notes rather than one).

**What is still not established**: none of this Java has been compiled. `TranCatBalWithRate` does
not exist as a type, `card-service` still holds zero files, and no test has executed the arithmetic
against real CardDemo data. Correct-looking arithmetic reviewed by a human is not a passing
differential test, and the distinction is the whole reason Phase 1 exists.

### The third real call — the prompt fix, and what it changed (step 39)

Run 2's two findings were that `CobolArithmetic.divide(dividend, divisor, scale)` and
`requireFits(value, precision, scale)` existed and the model had never been told. `rendering/
target_api.py` now extracts the class's public API **from the real source file** and puts it at the
head of the prompt. Run 3 is the identical step and design, re-run against that prompt.

**The generated body changed to exactly what run 2 had asked for and could not write:**

```java
// Run 2 -- named by the model itself as second-choice
BigDecimal quotient = categoryBalance.multiply(annualRatePercent)
        .divide(new BigDecimal("1200"), MathContext.DECIMAL128);
return CobolArithmetic.truncate(quotient, 2);

// Run 3
BigDecimal product = item.tranCatBal().multiply(item.disIntRate());
BigDecimal monthlyInterest = CobolArithmetic.divide(product, new BigDecimal("1200"), 2);
return CobolArithmetic.requireFits(monthlyInterest, 11, 2);
```

The intermediate `MathContext.DECIMAL128` quotient is gone in favour of the single truncating
divide at the target scale, and the overflow guard is present. `java.math.MathContext` dropped out
of the import list on its own.

**Cost fell as well: $0.295966 -> $0.243181, an 18% reduction for better code.** The prompt grew by
~2,800 characters of stable, cached prefix and the model stopped spending output tokens reasoning
its way around an API it could not see. Run 2 emitted six notes; run 3 emitted four, and none of
them is about a guessed method signature.

**A new finding, from the same honesty that produced the last two.** Run 3 flagged that
`WS-MONTHLY-INT` — the *receiving* field of the `COMPUTE`, and therefore the thing that determines
the target precision and scale — **is not in the Known Facts at all.** It appears only in the
untrusted narration and in the step description. The model inferred `precision 11, scale 2` and got
it right (`PIC S9(09)V99` is 9 integer digits plus 2 decimals), but it said plainly that it was
inferring: *"If a reviewer wants this beyond dispute, WS-MONTHLY-INT should be added to the
pic_mapper fact list rather than left to be read off the PIC clause here."*

It is correct, and this is the same defect class as the last one. `build_domain_entities` merges
**copybook-sourced** fields only (ADR-0010), so `WORKING-STORAGE` fields — which `cobol_parser`
already parses, per ADR-0011 — never reach the prompt as deterministic facts. **A `COMPUTE`'s
target scale is precisely the kind of number that must be computed and handed over, never
narrated**, for the same reason `pic_mapper` may not call a model: a wrong scale on a currency
field looks exactly like a right one. Recorded as an open gap rather than fixed in this PR.

**Three calls, $0.84 notional, $0 billed.** Each one found a defect the injected-fake test suite
could not: an under-specified design, a missing target API, and a missing class of deterministic
fact. None of that Java has been compiled yet.

### The Java toolchain, and pinning the last unpinned dependency

**A local JDK and Maven now exist**, so the Java half of this repo is no longer CI-only. Installed
2026-08-09: **Eclipse Temurin 25.0.4** (`winget`) and **Apache Maven 3.9.16**, the latter downloaded
from Apache's CDN with its **SHA-512 verified against `downloads.apache.org`'s published checksum**
before extraction — a binary that compiles model-authored code is not one to take on trust.

```
mvn -B -ntp verify   # in templates/target-spring-boot-baseline
```

Real result: **`Tests run: 13, Failures: 0, Errors: 0, Skipped: 0` — `BUILD SUCCESS`** on JDK 25.0.4
against a **real PostgreSQL Testcontainer**, matching CI exactly. CI pins Temurin 25.0.3; the
difference is a patch within the same feature release, and `BaselineStackTest`'s own
`Runtime.version().feature() == 25` assertion is what actually guards this.

**Maven 3.9.16 rather than 4.0.0, deliberately.** ADR-0019 chose Maven over Gradle *because* its
compile diagnostics are straightforward to parse and auto-repair, and step 42's self-healing loop is
built on parsing exactly that output. Anchoring a diagnostic parser to a brand-new major whose
output format may have moved is avoidable risk that buys nothing.

**Host install rather than a Maven container, with Docker available.** In production the specialist
is a container (step 46 / gap G5). Giving *that* container a Docker socket so it could launch Maven
containers would grant root-equivalent host access to the component that compiles LLM-authored code
derived from untrusted COBOL — the wrong trust boundary for a repo whose standing rule is that COBOL
is data, never instructions. The right shape is JDK and Maven **inside** the specialist image with
`local_compiler` invoking `mvn` on `PATH`, and the development environment now mirrors it. It is
also far faster: the self-healing loop compiles up to 12 times per run, and a cold container with a
cold `~/.m2` on each one would dominate the loop's wall time.

**The gap this exposed.** CI ran `mvn` from whatever the runner image ships — **an unpinned
dependency in a template that pins its JDK, its Spring Boot version, its Testcontainers version, and
asserts the JVM's own feature version at runtime.** The build tool was the one thing left free to
change under an image update, and it is the tool whose output step 42 will parse.

Closed by adding the **Maven Wrapper**, pinned to 3.9.16, with CI switched to `./mvnw`:

- `distributionType=only-script`, so the repo carries **three text files and no committed jar** —
  an opaque binary in source control that nobody reviews and every scanner flags.
- Verified: `./mvnw -B -ntp -version` resolves `Apache Maven 3.9.16` from
  `~/.m2/wrapper/dists`, downloading it on first use.
- Three new invariants in `tests/system/test_target_template.py` — the version is an exact pin, no
  jar is committed, and **CI actually calls the wrapper rather than a bare `mvn`**, because pinning
  a version is pointless if the pipeline still runs whatever is on `PATH`.

### `tools/local_compiler.py` — a real Maven build, and diagnostics a loop can act on (step 40)

**The first module in this repo whose correctness could not be established without running it.**
Four defects were found by compiling the real template, and every one of them would have survived a
mocked `subprocess.run` untouched.

```
mvnw -B -ntp compile   # driven by compile_project, against a copy of the real template
```

Real result on the clean template: **`succeeded=True`, exit 0, 0 diagnostics, ~10s.** With
`setScale` typo'd to `setScaleTypo`, one located diagnostic comes back:

```
error: src/main/java/com/modernized/batch/cobol/CobolArithmetic.java:46:21: cannot find symbol
    symbol:   method setScaleTypo(int,java.math.RoundingMode)
    location: variable value of type java.math.BigDecimal
```

**The four defects, in the order they were found:**

1. **The wrapper path was relative to the caller's working directory** while the child ran with
   `cwd=project_dir`, so Maven never started. Symptom: `succeeded=False`, **zero diagnostics, 186ms**
   — byte-for-byte the shape of code that does not compile.
2. **Maven prints every compile error twice**, and only one copy carries javac's
   `symbol:`/`location:` lines. Two copies invite a repair loop to believe there are two problems;
   dropping the wrong duplicate discards the only part a repair can act on.
3. **Paths came back absolute**, on Windows in a `/C:/...` form nothing else in this pipeline
   recognises. Diagnostics are now project-relative and POSIX-separated — a model pays for those
   tokens and cannot act on a path whose base it does not know.
4. **A missing JDK was indistinguishable from broken code.** Found by running this module's own
   tests in a shell without one: the wrapper exits non-zero with no located diagnostic. `JdkNotFound
   Error` and `CompilerNotFoundError` (both `ToolchainNotFoundError`) make the two distinguishable,
   and `CompileTimeoutError` is kept out of `CompileResult` for the same reason — **a timeout says
   nothing about whether the source compiles.**

**A fifth was found by CI, and could not have been found here.** `resolve_build_command` chose the
wrapper by existence order, so on the Linux runner it picked `mvnw.cmd` — a Windows batch file with
no execute bit — and failed with `PermissionError: [Errno 13]`. Selection is now by platform, with a
test asserting the choice matches the platform and another asserting the POSIX script still carries
its execute bit. **This is the second time in one session that the Java toolchain behaved
differently on the runner than on the development machine** (the first being `mvnw`'s mode bit and
line endings), and it is the concrete argument for CI being the arbiter for anything touching it.

**The Python CI job now installs a JDK** so these tests run there rather than skipping forever —
the same standard `template-build` already applies to Docker. Had they skipped, defect 5 would have
shipped and surfaced inside step 42's heal loop, where a `PermissionError` reads as an unparsed
build failure: exactly the confusion `ToolchainNotFoundError` exists to prevent, arriving by a route
that was not yet guarded.

**CI result on this change: 513 passed, 4 skipped, 99% overall**, `local_compiler.py` at 98%. The
uncovered lines are `PATH`-based JDK discovery (shadowed by `JAVA_HOME` everywhere, including CI)
and the offline flag.

**What this does not establish.** `compile_project` has only ever compiled **hand-written** Java --
the template, and the template with a deliberate typo. **No model-generated file has been compiled**,
because nothing yet writes one to a project: `generate` still has no caller and `card-service` still
holds zero files. Wiring that is steps 41 and 42, and until then the round-trip count stays 0 of 4.

### The self-healing loop, the `generate` subcommand, and the round trip (step 42, ADR-0020)

**A `design.json` now produces a target project that compiles.** One design document yields five
domain records and a processor in `card-service`'s package layout, and `mvn compile` is green:

```
src/main/java/com/modernized/batch/domain/{Account,CardXref,DisGroup,Tran,TranCatBal}.java
src/main/java/com/modernized/batch/processor/ComputeMonthlyInterestProcessor.java
```

**The loop heals a real compile error, against real Maven.** Attempt 1 calls a method that does not
exist, javac says so, attempt 2 compiles — no human, two attempts, verified in
`test_generate_pipeline.py` rather than against a mocked compiler. A mock would let the loop
"recover" from failures a compiler would never report, and would have hidden the Spring Batch 6
package rename that step 41 caught.

**Refusing to retry is tested as carefully as retrying.** A `blocked` verdict produces exactly
**one** generation call: retrying a design defect burns the whole budget and yields three worse
versions of the same code. `MAX_HEAL_ATTEMPTS` (3) is asserted to differ from
`MAX_TRANSPORT_ATTEMPTS` (5) — they bound unrelated things and multiply if confused, which is
ADR-0013's stacking failure.

**Two defects found, both of the same silent shape:**

1. **G21 was reported closed and was not.** `render_program_field_facts` was added, tested, and
   **never called** — `build_engineer_prompt` kept its old return because a string-replacement patch
   did not match and nothing failed. The test written for it exercised the helper directly, so it
   passed against unwired code the whole time. Now wired, with a guard asserting through the *real*
   prompt builder: `WS-MONTHLY-INT -- BigDecimal, precision 11, scale 2, signed` reaches the prompt
   a model actually receives.
2. **`run_generate` never rendered the domain records.** Processors are generated *against* those
   types, so nothing would have compiled regardless of ADR-0020. Found by running the pipeline, not
   by reading it.

**The contract gap ADR-0020 closes was found by building the subcommand.** An `ItemProcessor` is two
types and `BatchStepDesign` named neither. A real `solution_architect` run is what settled the fix:
it chains three processor steps (`resolveAccountContext` → `resolveInterestRate` →
`computeInterest`), and the values flowing between them are **not** in `domain_entities` because
they do not exist as entities. There was nothing to derive from, so composites are declared.

**What this does NOT establish**, stated plainly against a green suite:

1. **The compiling processor body is `return item;`** — a scripted pass-through, not translated
   business logic. No real model has written a body through this path.
2. **`0 of 4 programs round-trip` is unchanged.** That metric means COBOL → compiling Java →
   **passing differential test**, and step 45's equivalence test does not exist, nor does step 40a's
   data loader that it needs.
3. **The real `solution_architect` output predates this contract**, so a fresh `design` run is
   needed before any real design.json carries step types.

**A coverage regression caught before merge, worth recording as a pattern.** CI's first run on this
branch reported 96.6% against the usual 99% -- and every uncovered line was ADR-0020's own code:
`render_composite` at **69%**, plus the resolution helpers and the architect's composite parsing.
The round-trip test used a plain entity, so **no composite was ever constructed in a test**: the
feature the ADR exists for was the one part unverified, behind a green suite and a passing
`--cov-fail-under=90`. Closed with a step whose input type *is* a composite, generated and compiled
for real. `contracts.py`, `solution_architect.py` and `java_records.py` are now at 100%.

## Not yet covered (honest gaps, not silently skipped)

- *(corrected 2026-08-08 — this entry was stale, not merely incomplete)* This previously read
  **"No full `design` run has been executed against real models yet."** That is false: real
  four-program `design` runs happened (~$2.31 at API rates), `tests/fixtures/narrations/CBCUS01C/
  spec.md` is verbatim output from one of them, and two benchmarks scored real narrations by hand
  (PRs #20, #21 — see the `spec_critic` and `spec_extractor` entries above). What remains
  genuinely uncovered is narrower and worth stating on its own terms: **output quality is
  spot-measured, not continuously evaluated.** `CBACT04C` was scored on six hand-verified facts and
  `CBCUS01C`'s critique on three planted errors; no standing harness re-scores narrations as the
  prompts or models change, and `solution_architect`'s batch/REST design has never been scored at
  all (Open Issue 6 — the hard part is that no golden unified design exists to score against).
  The LLM-as-judge harness that would close this is Milestone C4.
- **RAG is a storage layer, not a capability.** `tools/knowledge_store.py` is real and verified
  against real Postgres+pgvector, and has zero production callers. ADR-0016 decides the provider
  (Voyage AI, `voyage-code-3`) and then declines to wire retrieval on two gates: no
  `VOYAGE_API_KEY` exists here, and nobody has shown retrieval improves extraction over a corpus of
  four programs. Neither gate is closed, so no claim is made about retrieval quality — there is
  nothing to measure yet. `--db-credentials-file` is unimplemented for the same reason: its only
  consumer sits behind those gates (ADR-0005's amendment note).
- *(historical, closed — kept because the original wording named the wrong cause)* The original
  form of this gap was that `_default_narrate`/`_default_critique`/`_default_architect` had never
  been invoked at all, with every test injecting a fake in their place, and it said to "revisit
  once a real credential is available." **No credential was ever needed** — the `claude` CLI on a
  Pro subscription was a usable backend the whole time (ADR-0013). All three defaults have now run
  against real models over real Track C source, ADR-0004's deferred question about `spec_critic`'s
  cheaper tier is answered by measurement (PR #20), and the deterministic steps upstream of each
  remain verified as described above. Left here rather than deleted: the gap was real and the
  stated cause was not, which is the specific mistake worth not repeating.
- **A fixed `OCCURS` cannot be represented, only flagged.** `PicMapping` has no cardinality field
  and `DomainField` cannot express a collection, so `CBACT01C`'s `ARR-ACCT-BAL OCCURS 5 TIMES`
  group is routed to the human gate rather than mapped (ADR-0011). That is the correct behavior
  given the current types — flattening an array to a scalar would be a wrong answer that looks
  right — but it is a capability gap, not a solved problem. Track C has exactly one occurrence and
  no node consumes that file yet; revisit when Milestone C4 generates a reader for it, or with
  Track B's B3 alias-analysis module.
- **The template has a scaffold, not a program.** `templates/target-spring-boot-baseline/` compiles
  and its stack is proven on JDK 25, but it contains one arithmetic helper and an application class.
  **No line of Java has been generated from COBOL yet** — `modernization_engineer` (step 39) is what
  makes the template earn its place, and until then a green `template-build` proves the target
  stack works, not that anything can be modernized into it.
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
  narrative *prose*. The original reason for deferring them — that no credential existed to produce
  real narrations to compare against — **no longer holds**, since real narrations now exist
  (`tests/fixtures/narrations/`). The deferral stands on a different and better reason: a golden
  fixture is only as good as the hand cross-check that produced it, `CBACT04C`'s took a careful
  paragraph-by-paragraph read of real source, and that read is what is missing for the other three
  — not model output. `CBACT04C`'s own fixture was also **factually wrong** about EOF posting until
  a model caught it, which is the strongest available argument against mass-producing three more on
  a schedule. Milestone C2's gate is closed on its literal numeric-field wording, which is
  checkable without a model, not on prose.
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
