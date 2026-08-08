# 0005 - Database credentials arrive as a mounted file, not an environment value

> **Amendment note (2026-08-08, ADR-0016).** The decision below stands unchanged — a mounted file
> path, never an environment value. What was wrong is the tense: this ADR and four documents that
> cite it read as though `--db-credentials-file` exists, and it does not. `cli.py` has no such
> flag, and `knowledge_store.py` is called only by its own tests, which pass a `Path` directly.
> The flag lands with the first production caller, which ADR-0016 gates on an embedding credential
> that does not exist here. Adding it now would mean a CLI argument the parser accepts, validates,
> and hands to nobody — a thing that looks finished and is not. Recorded as a correction rather
> than edited away, since the gap between a decision and its implementation is exactly what a
> reader of this ADR needs to see.

## Context

`tools/knowledge_store.py` (Milestone C2) needs a Postgres connection to
`agentic-sdlc-control-plane`'s existing instance — the Integration Boundary section of this
platform's plan reuses control-plane's Postgres (a new `cobol_modernizer` schema) rather than
standing up a second database. Something has to hand this bounded CLI subprocess a real credential
to open that connection.

Control-plane's own `development.md` states the rule this platform runs on: "Never put a credential
in a remote URL, an environment value, a log line, or the process argument list. PATs arrive as
file-based Docker secrets and reach git through a credential helper invoked at request time." This
repo inherits that same discipline — a specialist subprocess control-plane invokes should not be a
weaker link than control-plane's own credential handling.

The naive approach — an env var like `DATABASE_URL=postgres://user:pass@host/db`, a common
convention in many tools — puts the password directly in an environment value, exactly what the
rule forbids. Environment values are a real leak surface: inherited by child processes, sometimes
visible in process listings, and easy to accidentally capture in crash dumps or debug logging that
snapshots the environment without thinking about what's in it.

## Decision

**A mounted credentials file, its path passed via a CLI flag — never the credential itself.**
`--db-credentials-file <path>` (default path convention decided once real deployment exists in
Milestone C5, matching Docker secrets' own mounting convention). `tools/knowledge_store.py` reads
this file's contents only at the point a database connection is actually needed — lazily, not at
CLI startup. Most of this repo's work (parsing, `pic_mapper`, guardrails) has no database
dependency at all; making the whole CLI require a DB credential up front would be a needless
coupling for work that never touches Postgres.

The file's internal format (a connection string, or a small structured key/value form) is not
decided here — that's an implementation detail for when `knowledge_store.py` is actually built.
What this ADR locks in is the *delivery mechanism*: a file path, never an embedded credential.

**Least privilege is control-plane's responsibility, stated here as a dependency**: the Postgres
role this connection uses should be scoped to only the `cobol_modernizer` schema, not broad
instance access. This repo can't enforce that itself — it only ever sees whatever credential it's
handed — so it's recorded here as a requirement this repo's security posture depends on, not
something this ADR can guarantee on its own.

## Consequences

This repo's CLI must never log, echo, or include the credentials file's contents in any output,
including error messages — a connection failure reports "could not connect using the credentials at
`<path>`", never the connection string itself. This is a real discipline requirement for whoever
implements `knowledge_store.py`, not automatic.

Local development needs its own credentials-file convention for testing against a local Postgres
before Milestone C5's real integration exists. That's a real, open gap — deliberately left to be
closed when `knowledge_store.py`'s own tests are written (they'll need *something* to connect to),
not solved speculatively here.

This mirrors control-plane's own file-based-secret pattern rather than inventing a new one — the
same consistency reasoning as ADR-0004's config-driven model routing: a credential-delivery
mechanism shaped like the platform's existing one is easier to reason about than a novel one, and
harder to get subtly wrong by reinventing something that already works.
