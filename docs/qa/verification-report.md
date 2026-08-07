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

As of this report: **164 tests passed, 98.20% overall coverage.**

| Module | Coverage |
|---|---|
| `cli.py` | 92% |
| `core/contracts.py` | 100% |
| `core/guardrails.py` | 100% |
| `core/model_routing.py` | 100% |
| `core/schema_export.py` | 100% |
| `core/source_units.py` | 100% |
| `nodes/spec_critic.py` | 97% |
| `nodes/spec_extractor.py` | 96% |
| `parsing/cobol_parser.py` | 98% |
| `prompts_registry_client/loader.py` | 100% |
| `tools/knowledge_store.py` | 100% |
| `tools/pic_mapper.py` | 99% |
| `tools/tenant_repo.py` | 100% |
| `telemetry/logging_config.py` | 100% |

`cli.py`'s uncovered lines are the `not_implemented` skeleton branches for `design`/`generate`
that Milestones C2–C4 replace with real logic — not a gap in what currently exists.
`nodes/spec_extractor.py` and `nodes/spec_critic.py`'s uncovered lines are `_default_narrate`'s
and `_default_critique`'s bodies respectively (the real Anthropic API calls) — see "Not yet
covered" below for why, and what covers the rest of each module instead.

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
all four Track C programs; only `CBACT04C` has a tenant-repo fixture and a golden spec today (the
other three — `CBCUS01C`, `CBACT01C`, `CBTRN02C` — are Milestone C3+ work, once their own fixtures
exist), so this closes the gate for `CBACT04C` specifically, not the full four-program claim.

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

### CI itself — verified on GitHub, not just locally

Every module above was also verified green on a **real GitHub Actions run**, not assumed from a
local pass, for every PR merged so far (PRs #1–#4): lint, coverage-floor-gated test suite, and the
mermaid-diagram-parse check reused from `agentic-sdlc-control-plane`.

**Command**: `gh run list --repo jayakumar-devaraj/agentic-sdlc-cobol-modernizer --limit 1`
**Result**: `completed success` on every merge to date.

## Not yet covered (honest gaps, not silently skipped)

- **Neither `spec_extractor` nor `spec_critic`'s real model calls are tested against a live
  Anthropic API.** This development environment has no `ANTHROPIC_API_KEY` (or equivalent)
  available to `_default_narrate`/`_default_critique`, so neither has ever actually been invoked —
  only every deterministic step upstream of each (field mapping, paragraph extraction, prompt
  construction, guardrail wrapping, fidelity checks, JSON parsing) is verified against real data;
  every test injects a fake `narrate`/`critique` in its place. This is a real gap, not a mock
  standing in for a "done" claim — revisit once a real credential is available (Milestone C5
  integration, or whenever this repo is actually invoked with one), by running `extract_spec` then
  `critique_spec` against `CBACT04C` with the default callables and manually reviewing both the
  resulting `spec.md` prose and the critic's per-rule scores against the source, the way
  `.claude/agents/qa.md` requires for anything a unit test can't meaningfully reach. This also
  means ADR-0004's own deferred question — whether `spec_critic`'s cheaper model tier holds up
  empirically once real critiques exist — is still unanswered.
- **Auditing/provenance** (`CLAUDE.md`'s stated concern: every generated artifact traces back to
  the exact COBOL source line it came from) is now source-label-precise (every fact
  `spec_extractor` emits names the exact program or copybook it came from) but not yet
  line-precise — see ADR-0006 for why, and what closing this gap would require
  (`parsing/cobol_parser.py` carrying line numbers through field/paragraph extraction).
- **Structured logging** exists (`telemetry/logging_config.py`, wired into `cli.py`'s invocation
  lifecycle) but `spec_extractor` isn't wired into the CLI's `design` subcommand yet (that's
  Milestone C3's `cli.py` wiring, plan step 36) — so it has no real `run_id`/correlation concept
  to log against yet, and doesn't log internally itself, consistent with every other tool module
  in this repo (`pic_mapper`, `cobol_parser`, `tenant_repo`, `guardrails`, `knowledge_store`) being
  pure functions with no logging of their own.
