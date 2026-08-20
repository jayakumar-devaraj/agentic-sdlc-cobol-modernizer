# Deterministic foundations and infrastructure

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

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
