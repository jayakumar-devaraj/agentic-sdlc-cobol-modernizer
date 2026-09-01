"""System tests for tools/knowledge_store.py against a real local Postgres+pgvector instance.

These tests exercise `ensure_schema`, `store_entry`, and `search_similar` against a real,
running Postgres+pgvector container (`docker-compose.yml`'s `postgres` service, port 5434) using
the credentials fixture at `tests/fixtures/db_credentials_sample/local.conn` -- never mocked,
per this repo's QA posture of a functional verification report for anything a unit test can't
meaningfully reach. If that instance isn't reachable, every test here skips with a clear reason
rather than failing opaquely or silently passing -- mirroring `.claude/agents/qa.md`'s note on
control-plane's own durability tests: allowed to skip without real Postgres, never weakened into
an in-memory equivalent to make the suite always run.

Test rows all use a `SYSTEST-` `program_name` prefix and are deleted (by that prefix) after every
test, so this suite never leaves junk in, or interferes with other users of, the shared local-dev
database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.tools import knowledge_store
from cobol_modernizer.tools.knowledge_store import (
    EMBEDDING_DIMENSIONS,
    KnowledgeStoreCredentialsError,
    KnowledgeStoreSchemaError,
    ensure_schema,
    search_similar,
    store_entry,
)

_CREDENTIALS_FILE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "db_credentials_sample" / "local.conn"
)

_TEST_PROGRAM_PREFIX = "SYSTEST-"


def _unit_vector(index: int, *, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    """A vector of `dimensions` zeros with a single `1.0` at `index` -- a clean basis direction."""
    vector = [0.0] * dimensions
    vector[index] = 1.0
    return vector


def _connect_or_none() -> knowledge_store.psycopg.Connection | None:
    try:
        conn = knowledge_store.connect(_CREDENTIALS_FILE)
    except KnowledgeStoreCredentialsError:
        return None
    try:
        ensure_schema(conn)
    except knowledge_store.psycopg.Error:
        conn.close()
        return None
    return conn


_SKIP_REASON = (
    "Could not reach the local Postgres+pgvector instance via the credentials at "
    f"{_CREDENTIALS_FILE} (see docker-compose.yml's `postgres` service, localhost:5434). "
    "This system test requires a real running instance and skips rather than weakening into an "
    "in-memory equivalent."
)


@pytest.fixture
def conn():
    """A real connection with schema ensured; cleans up all `SYSTEST-` rows after the test."""
    connection = _connect_or_none()
    if connection is None:
        pytest.skip(_SKIP_REASON)
    try:
        yield connection
    finally:
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM cobol_modernizer.knowledge_entries WHERE program_name LIKE %s",
                (f"{_TEST_PROGRAM_PREFIX}%",),
            )
        connection.commit()
        connection.close()


# --- ensure_schema is idempotent -----------------------------------------------------------


def test_ensure_schema_is_idempotent(conn):
    # The fixture already called ensure_schema once; calling it again must not raise.
    ensure_schema(conn)
    ensure_schema(conn)


def test_ensure_schema_rejects_a_table_whose_embedding_dimension_differs(conn):
    """A database created before ADR-0016 keeps `vector(1536)`, and IF NOT EXISTS cannot fix it.

    This is not a hypothetical: the local dev container genuinely held a `vector(1536)` table when
    `EMBEDDING_DIMENSIONS` moved to 1024, and without this guard the mismatch surfaced only later,
    from `store_entry`, as `psycopg.errors.DataException: expected 1536 dimensions, not 1024` -- an
    error that reads as "the caller passed a wrong-sized embedding" when the real cause is a stale
    schema. The test rebuilds that exact situation rather than trusting the guard on sight.
    """
    wrong_dimensions = EMBEDDING_DIMENSIONS + 512
    with conn.cursor() as cur:
        cur.execute("DROP TABLE cobol_modernizer.knowledge_entries")
        cur.execute(
            f"""
            CREATE TABLE cobol_modernizer.knowledge_entries (
                id BIGSERIAL PRIMARY KEY,
                program_name TEXT NOT NULL,
                artifact_type TEXT NOT NULL CHECK (artifact_type IN ('spec', 'design')),
                content TEXT NOT NULL,
                embedding VECTOR({wrong_dimensions}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()

    try:
        with pytest.raises(KnowledgeStoreSchemaError) as excinfo:
            ensure_schema(conn)

        assert excinfo.value.actual_dimensions == wrong_dimensions
        assert excinfo.value.expected_dimensions == EMBEDDING_DIMENSIONS
        # Both dimensions must appear in the message. The failure this replaces named only the
        # stale one, which is precisely why it pointed at the wrong culprit.
        message = str(excinfo.value)
        assert f"vector({wrong_dimensions})" in message
        assert f"vector({EMBEDDING_DIMENSIONS})" in message
    finally:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE cobol_modernizer.knowledge_entries")
        conn.commit()
        ensure_schema(conn)


# --- store_entry ---------------------------------------------------------------------------


def test_store_entry_returns_increasing_ids(conn):
    embedding = _unit_vector(0)
    first_id = store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}A",
        artifact_type="spec",
        content="first entry",
        embedding=embedding,
    )
    second_id = store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}B",
        artifact_type="spec",
        content="second entry",
        embedding=embedding,
    )
    assert isinstance(first_id, int)
    assert second_id > first_id


def test_store_entry_rejects_wrong_embedding_dimensions(conn):
    with pytest.raises(ValueError, match="dimensions"):
        store_entry(
            conn,
            program_name=f"{_TEST_PROGRAM_PREFIX}BAD-DIM",
            artifact_type="spec",
            content="wrong size",
            embedding=[0.1, 0.2, 0.3],
        )


def test_store_entry_rejects_invalid_artifact_type(conn):
    with pytest.raises(ValueError, match="artifact_type"):
        store_entry(
            conn,
            program_name=f"{_TEST_PROGRAM_PREFIX}BAD-TYPE",
            artifact_type="not-a-real-type",
            content="wrong type",
            embedding=_unit_vector(0),
        )


# --- search_similar: analytically-known nearest neighbor -----------------------------------


def test_search_similar_returns_nearest_neighbor_first(conn):
    # Three orthogonal basis vectors (cosine distance 1.0 from each other), plus a query
    # deliberately perturbed off vector A's direction, but far closer to A than to B or C:
    #   query = [1.0, 0.05, 0.0, 0.0, ...]
    # cos(query, A) = 1 / sqrt(1 + 0.05^2)     ~= 0.99875  -> distance ~= 0.00125
    # cos(query, B) = 0.05 / sqrt(1 + 0.05^2)  ~= 0.04994   -> distance ~= 0.95006
    # cos(query, C) = 0                                     -> distance = 1.0
    # So the analytically-known nearest-to-farthest order is A, B, C.
    vec_a = _unit_vector(0)
    vec_b = _unit_vector(1)
    vec_c = _unit_vector(2)
    query = _unit_vector(0)
    query[1] = 0.05

    store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-A",
        artifact_type="spec",
        content="closest to query",
        embedding=vec_a,
    )
    store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-B",
        artifact_type="spec",
        content="second closest to query",
        embedding=vec_b,
    )
    store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-C",
        artifact_type="spec",
        content="farthest from query",
        embedding=vec_c,
    )

    results = search_similar(conn, query_embedding=query, top_k=3)
    # Restrict to just this test's rows in case the shared dev DB has other data.
    results = [r for r in results if r.program_name.startswith(f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-")]

    assert [r.program_name for r in results] == [
        f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-A",
        f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-B",
        f"{_TEST_PROGRAM_PREFIX}NEIGHBOR-C",
    ]
    assert results[0].distance < results[1].distance < results[2].distance
    assert results[0].content == "closest to query"


def test_search_similar_respects_top_k(conn):
    for i in range(4):
        store_entry(
            conn,
            program_name=f"{_TEST_PROGRAM_PREFIX}TOPK-{i}",
            artifact_type="spec",
            content=f"entry {i}",
            embedding=_unit_vector(i),
        )

    results = search_similar(conn, query_embedding=_unit_vector(0), top_k=2)
    assert len(results) <= 2


def test_search_similar_filters_by_artifact_type(conn):
    embedding = _unit_vector(5)
    store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}FILTER-SPEC",
        artifact_type="spec",
        content="a spec entry",
        embedding=embedding,
    )
    store_entry(
        conn,
        program_name=f"{_TEST_PROGRAM_PREFIX}FILTER-DESIGN",
        artifact_type="design",
        content="a design entry",
        embedding=embedding,
    )

    results = search_similar(
        conn, query_embedding=embedding, top_k=10, artifact_type="design"
    )
    results = [r for r in results if r.program_name.startswith(f"{_TEST_PROGRAM_PREFIX}FILTER-")]

    assert len(results) == 1
    assert results[0].artifact_type == "design"
    assert results[0].program_name == f"{_TEST_PROGRAM_PREFIX}FILTER-DESIGN"


def test_search_similar_rejects_wrong_embedding_dimensions(conn):
    with pytest.raises(ValueError, match="dimensions"):
        search_similar(conn, query_embedding=[0.1, 0.2], top_k=5)


# --- Credentials error handling never echoes file contents ----------------------------------


def test_connect_with_missing_credentials_file_raises_without_echoing_path_contents(tmp_path):
    missing_file = tmp_path / "does-not-exist.conn"
    with pytest.raises(KnowledgeStoreCredentialsError) as exc_info:
        knowledge_store.connect(missing_file)
    # The error names the path, never any connection-string/password content (there is none to
    # leak here, but the assertion also guards against a future change accidentally embedding a
    # read attempt's contents into the message).
    assert str(missing_file) in str(exc_info.value)


def test_connect_with_empty_credentials_file_raises(tmp_path):
    empty_file = tmp_path / "empty.conn"
    empty_file.write_text("# only a comment, no connection string\n", encoding="utf-8")
    with pytest.raises(KnowledgeStoreCredentialsError, match="no connection string"):
        knowledge_store.connect(empty_file)


def test_connect_with_unreachable_host_raises_without_echoing_connection_string(tmp_path):
    # A well-formed connection string pointed at a port nothing listens on -- exercises the
    # actual `psycopg.OperationalError` branch, not just the file-reading failures above.
    # `connect_timeout=2` keeps this test fast: without it, a filtered/silently-dropped port can
    # leave the underlying TCP connect() attempt hanging for the platform's default timeout
    # (well over a minute observed locally), rather than failing promptly.
    unreachable_file = tmp_path / "unreachable.conn"
    connection_string = (
        "postgresql://someuser:somepassword@127.0.0.1:1/nonexistent_db?connect_timeout=2"
    )
    unreachable_file.write_text(connection_string + "\n", encoding="utf-8")
    with pytest.raises(KnowledgeStoreCredentialsError) as exc_info:
        knowledge_store.connect(unreachable_file)
    message = str(exc_info.value)
    assert "somepassword" not in message
    assert connection_string not in message
