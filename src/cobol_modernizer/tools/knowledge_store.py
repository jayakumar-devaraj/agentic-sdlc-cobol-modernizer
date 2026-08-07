"""pgvector-backed store of prior specs/designs, retrieved as few-shot context.

Per the platform plan, this is "a pgvector index over prior specs/designs, retrieved as few-shot
context by `spec_extractor`/`solution_architect`". This module owns storage and similarity
retrieval only -- it deliberately does **not** compute embeddings. Choosing an embedding
model/provider is an undecided, separate decision nothing in this repo has made yet; conflating
that choice into this module would be scope creep this module's own docstring should not paper
over. Every function here takes an already-computed `embedding: list[float]` and treats it as
opaque data -- the caller (a future node) is responsible for however it obtains that vector.

**Credentials (ADR-0005).** Per
`docs/adr/0005-database-credentials-arrive-as-a-mounted-file-not-an-environment-value.md`, a
database credential is delivered as a mounted file whose *path* is passed to this repo's CLI
(`--db-credentials-file <path>`) -- never as an environment value or embedded in code. That ADR
deliberately leaves the file's internal format open; this module's choice is the simplest thing
that could work: **a single line holding a libpq connection string** (a `postgresql://...` URI,
or an equivalent `key=value` conninfo string -- anything `psycopg.connect()` accepts directly).
Blank lines and lines starting with `#` are ignored, so a local-dev fixture file can carry an
explanatory comment above the connection string without that comment being treated as part of it.
`connect()` reads this file lazily, only when a connection is actually needed -- most of this
repo's work (parsing, `pic_mapper`, guardrails) never touches Postgres, so nothing here runs at
CLI startup. Mirroring `tools/tenant_repo.py`'s `TenantRepoFileNotFoundError` style,
`KnowledgeStoreCredentialsError` never echoes the file's contents (the connection string, which
may embed a password) -- a connection failure reports the credentials file's *path*, never what
was read from it, in accordance with ADR-0005's consequence that this repo's CLI "must never log,
echo, or include the credentials file's contents in any output, including error messages".

**Schema.** `ensure_schema()` is idempotent -- `CREATE EXTENSION IF NOT EXISTS`,
`CREATE SCHEMA IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS` -- and is meant to be called on every
invocation, not as a one-time setup script. This is consistent with ADR-0001: this repo has no
migration tooling and no durable checkpointer of its own, so there is no "already ran once"
state to remember between invocations; re-asserting the schema exists is the only sound approach.
`EMBEDDING_DIMENSIONS` (1536) is a **placeholder**, chosen because it's a common dimension for
several current embedding models (e.g. OpenAI `text-embedding-3-small`) -- it is not itself an
embedding-model decision. Whatever model is eventually chosen must confirm this dimension or this
schema will need a migration; this module does not guess further than that.

No approximate-nearest-neighbor index (`ivfflat`/`hnsw`) is created yet. Table size at this
milestone is small enough that an exact sequential scan with the `<=>` cosine-distance operator is
correct and fast; adding an approximate index is a tuning decision (list/probe counts, or `m`/
`ef_construction`) to make once retrieval latency actually requires it, not preemptively.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from pydantic import BaseModel

_SCHEMA_NAME = "cobol_modernizer"
_TABLE_NAME = "knowledge_entries"
_QUALIFIED_TABLE = f"{_SCHEMA_NAME}.{_TABLE_NAME}"

#: Placeholder embedding dimension -- see module docstring. Any embedding passed to
#: `store_entry`/`search_similar` must have exactly this many components.
EMBEDDING_DIMENSIONS = 1536

#: The only `artifact_type` values this store accepts, per the plan's "prior specs/designs".
VALID_ARTIFACT_TYPES = frozenset({"spec", "design"})


class KnowledgeStoreCredentialsError(Exception):
    """A credentials file could not be read, was empty, or could not open a connection.

    Per ADR-0005's consequence that "this repo's CLI must never log, echo, or include the
    credentials file's contents in any output, including error messages", this exception carries
    only the credentials file's *path* and a human-readable reason -- never the file's contents,
    never the connection string, never whatever `psycopg` reported (which could echo conninfo
    fragments). Callers must not catch this and fall back to a hardcoded/default connection --
    that would silently defeat ADR-0005's file-based delivery mechanism.
    """

    def __init__(self, credentials_file: Path, reason: str) -> None:
        self.credentials_file = credentials_file
        super().__init__(
            f"Could not connect using the credentials at {credentials_file}: {reason}"
        )


class KnowledgeEntry(BaseModel):
    """One stored spec/design entry, as returned by `search_similar`.

    `distance` is the pgvector cosine distance (`<=>`) to the query embedding at search time --
    smaller is more similar, `0.0` is identical direction. It is not a property of the stored
    row itself, only of a particular search result.
    """

    id: int
    program_name: str
    artifact_type: str
    content: str
    distance: float


def _validate_artifact_type(artifact_type: str) -> None:
    if artifact_type not in VALID_ARTIFACT_TYPES:
        raise ValueError(
            f"artifact_type must be one of {sorted(VALID_ARTIFACT_TYPES)}, got {artifact_type!r}"
        )


def _validate_embedding(embedding: list[float]) -> None:
    if len(embedding) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"embedding must have exactly {EMBEDDING_DIMENSIONS} dimensions (the placeholder "
            f"schema dimension -- see module docstring), got {len(embedding)}"
        )


def _read_connection_string(credentials_file: Path) -> str:
    try:
        raw = credentials_file.read_text(encoding="utf-8")
    except OSError:
        raise KnowledgeStoreCredentialsError(credentials_file, "file could not be read") from None

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped

    raise KnowledgeStoreCredentialsError(
        credentials_file, "file contains no connection string (only blank/comment lines)"
    )


def connect(credentials_file: Path) -> psycopg.Connection[Any]:
    """Open a `psycopg` connection using the connection string in `credentials_file`.

    Per ADR-0005 this is the only place a database credential is read -- lazily, only when a
    connection is actually needed. See the module docstring for the file's format.

    Raises:
        KnowledgeStoreCredentialsError: the file could not be read, was empty, or the connection
            attempt itself failed. The exception message never includes the file's contents.
    """
    conninfo = _read_connection_string(credentials_file)
    try:
        return psycopg.connect(conninfo)
    except psycopg.OperationalError:
        raise KnowledgeStoreCredentialsError(credentials_file, "connection attempt failed") from None


def ensure_schema(conn: psycopg.Connection[Any]) -> None:
    """Idempotently ensure the `vector` extension, schema, and table exist, then commit.

    Safe -- required, per the module docstring -- to call on every invocation; this repo has no
    migration tooling or durable "already set up" state (ADR-0001) to check instead. Also
    registers this connection's pgvector type adapters (`pgvector.psycopg.register_vector`),
    which requires the `vector` extension to already exist -- callers must call this before
    `store_entry`/`search_similar` on a given connection.
    """
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"CREATE SCHEMA IF NOT EXISTS {_SCHEMA_NAME}")
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_QUALIFIED_TABLE} (
                id BIGSERIAL PRIMARY KEY,
                program_name TEXT NOT NULL,
                artifact_type TEXT NOT NULL CHECK (artifact_type IN ('spec', 'design')),
                content TEXT NOT NULL,
                embedding VECTOR({EMBEDDING_DIMENSIONS}) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    conn.commit()
    register_vector(conn)


def store_entry(
    conn: psycopg.Connection[Any],
    *,
    program_name: str,
    artifact_type: str,
    content: str,
    embedding: list[float],
) -> int:
    """Insert one spec/design entry and return its new row id.

    Raises:
        ValueError: `artifact_type` is not `"spec"`/`"design"`, or `embedding` does not have
            `EMBEDDING_DIMENSIONS` components.
    """
    _validate_artifact_type(artifact_type)
    _validate_embedding(embedding)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {_QUALIFIED_TABLE} (program_name, artifact_type, content, embedding)
            VALUES (%s, %s, %s, %s)
            RETURNING id
            """,
            (program_name, artifact_type, content, Vector(embedding)),
        )
        row = cur.fetchone()
    conn.commit()
    assert row is not None  # RETURNING id always yields exactly one row on a successful INSERT
    return row[0]


def search_similar(
    conn: psycopg.Connection[Any],
    *,
    query_embedding: list[float],
    top_k: int = 5,
    artifact_type: str | None = None,
) -> list[KnowledgeEntry]:
    """Return the `top_k` entries nearest `query_embedding` by pgvector cosine distance (`<=>`).

    Args:
        query_embedding: Must have `EMBEDDING_DIMENSIONS` components.
        top_k: Maximum number of entries to return, nearest first.
        artifact_type: If given, restricts the search to `"spec"` or `"design"` entries only.

    Raises:
        ValueError: `query_embedding`'s dimensions don't match, or `artifact_type` (if given) is
            not `"spec"`/`"design"`.
    """
    _validate_embedding(query_embedding)
    if artifact_type is not None:
        _validate_artifact_type(artifact_type)

    query_vector = Vector(query_embedding)
    where_clause = "WHERE artifact_type = %s" if artifact_type is not None else ""
    params: list[Any] = [query_vector]
    if artifact_type is not None:
        params.append(artifact_type)
    params.append(top_k)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, program_name, artifact_type, content, embedding <=> %s AS distance
            FROM {_QUALIFIED_TABLE}
            {where_clause}
            ORDER BY distance
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()

    return [
        KnowledgeEntry(
            id=row[0],
            program_name=row[1],
            artifact_type=row[2],
            content=row[3],
            distance=row[4],
        )
        for row in rows
    ]
