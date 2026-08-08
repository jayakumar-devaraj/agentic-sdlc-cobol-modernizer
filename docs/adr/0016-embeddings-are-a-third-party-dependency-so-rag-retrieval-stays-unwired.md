# ADR-0016: Embeddings are a third-party dependency, so RAG retrieval stays unwired

## Status

Accepted (2026-08-08). Closes the embedding-provider question `tools/knowledge_store.py`'s module
docstring has deliberately left open since PR #4. Amends **ADR-0005**, whose
`--db-credentials-file` flag is re-dated rather than reversed (see Consequences).

## Context

`tools/knowledge_store.py` has been complete since PR #4 — 215 lines, 100% covered, verified
against a real Postgres+pgvector container locally and in CI. It has **zero production callers**. A
grep across `src/` finds one mention, in a `spec_extractor.py` docstring. So the platform pillar
matrix's "Knowledge & RAG: Implemented" is true of the module and misleading about the system: no
node retrieves anything, and nothing ever will until something can turn text into a vector.

That gap has been attributed to "an undecided embedding choice" for four milestones without anyone
actually making the decision. The module even says so, and then hedges its own schema:

> `EMBEDDING_DIMENSIONS` (1536) is a **placeholder**, chosen because it's a common dimension for
> several current embedding models (e.g. OpenAI `text-embedding-3-small`) — it is not itself an
> embedding-model decision.

Two things forced this now. First, ADR-0013 established that the `claude` CLI on a Pro subscription
is a usable model backend, which invalidated every prior "no credential exists" caveat in this
platform's plan. The obvious follow-up question is whether that same backend closes the RAG gap
too. Second, an unresolved decision that blocks a pillar claim is worse than a resolved one that
blocks it, because only the second can be argued with.

### The backend that made every other call real does not close this one

**Anthropic does not offer an embedding model.** Verified against Anthropic's own embeddings
documentation, not assumed: it states this directly and recommends Voyage AI, and no embeddings
endpoint appears anywhere in the API surface (Messages, Batches, Files, Token Counting, Models).
The `claude` CLI is a text-completion interface over that same surface; it has no embedding
capability to expose.

This is worth stating plainly because it is the *opposite* shape of the finding in ADR-0013. There,
the blocker was real but its stated cause ("no API credential exists") was wrong — a usable backend
had been sitting on this machine the whole time. Here the blocker is real **and** the cause is real:
there is no credential-free embedding path through the backend this repo already has, and no amount
of re-checking the `claude` CLI will produce one.

## Decision

**1. The embedding provider is Voyage AI, and the model is `voyage-code-3`.** Voyage is Anthropic's
own recommended provider, which keeps this repo's third-party surface aligned with the platform it
extends rather than introducing an unrelated vendor. `voyage-code-3` is chosen over the
general-purpose `voyage-4` family for the same reason `pic_mapper` exists: the corpus is COBOL
source and prose derived line-by-line from it, and a model documented as optimized for code
retrieval is a better prior than a general one. This is a *stated* prior, not a measured result —
see decision 3, which is what would measure it.

**2. `EMBEDDING_DIMENSIONS` becomes 1024, Voyage's default output dimension**, replacing 1536 — a
placeholder picked for OpenAI `text-embedding-3-small`, a provider this ADR now decides against.
Correcting it costs one constant and a schema re-assert today, because the table is empty
everywhere it exists. Correcting it after the first real vector is stored costs a migration this
repo has no tooling for (ADR-0001: no migration tooling, no durable state). Fixing a placeholder
the moment it is known wrong is cheaper than every later moment.

**3. Retrieval stays unwired, gated on two conditions, not one.** The obvious condition is a
`VOYAGE_API_KEY`, which does not exist in this environment. The second is the one worth writing
down: **nobody has shown retrieval would help.** The retrievable corpus is four Track C programs.
Nearest-neighbour search over four documents, three of which share the `CVACT01Y` copybook, is
theatre — it would return a neighbour every time and prove nothing about whether the neighbour
improved the narration. Wiring it would satisfy the pillar and not the capability, which is the
exact confusion this ADR exists to stop repeating. The gate is ADR-0015's, applied to a different
question: a benchmark showing retrieval changes `spec_extractor` output for the better, not an
assumption wearing a config entry.

**4. The pillar claim is restated to match the code.** "Knowledge & RAG: Implemented" becomes
"storage layer built and verified; retrieval not wired." The module keeps its tests and its CI
service container — it is finished work, not dead code, and deleting it would trade an honest gap
for a lost one.

## Consequences

**`--db-credentials-file` is re-dated, not reversed (amends ADR-0005).** ADR-0005 decided the
*delivery mechanism* — a mounted file path, never an environment value — and that decision stands
unchanged; it is the security posture this repo inherits from control-plane. What was wrong is
every document that implied the flag exists: it is absent from `cli.py`, and a grep confirms the
string appears only in this repo's docs and `knowledge_store.py`'s docstring. Implementing it now
would add a CLI flag whose only consumer is gated behind decision 3 — an argument the parser
accepts, validates, and hands to nobody. That is a worse artifact than a documented gap, because it
looks finished. The flag lands in the same change as the first production caller, and until then
every reference to it says so.

**This repo now has a named third-party dependency it did not have before.** `voyageai` is not
added to `pyproject.toml` here — nothing imports it — but the decision commits a future version of
this CLI to reaching a second vendor's API from inside a bounded subprocess control-plane invokes.
That is a real widening of the trust and failure surface, and it will need the same treatment
`core/model_client.py` already gives the Anthropic path (timeout, retry classification, usage
capture) rather than a bare SDK call.

**A credential-free local alternative was considered and rejected.** `voyage-4-nano` is open-weight
under Apache 2.0 on Hugging Face, so a local embedding path needs no credential at all and would
make retrieval testable in this environment today. It is rejected because it pulls a full
`torch`/`sentence-transformers` stack into a repo whose entire premise (ADR-0001) is a small,
bounded specialist CLI that control-plane invokes as a subprocess — hundreds of megabytes and a
model-loading cost paid on every invocation, to serve a four-document corpus that decision 3 says
is not worth serving yet. Worth revisiting if the corpus grows or if egress to a second vendor is
ruled out at deployment.

**Making the change found a real defect, which is the argument for making it now rather than
later.** `ensure_schema` uses `CREATE TABLE IF NOT EXISTS` — the right primitive for a repo with no
migration tooling, but against an existing table it is a no-op that silently keeps the old column
type. The local dev container had held `vector(1536)` since PR #4, so the first run at 1024 failed
with `psycopg.errors.DataException: expected 1536 dimensions, not 1024`, raised from `store_entry`
— several calls downstream of the cause, with a message that blames the caller's embedding rather
than a schema older than the code. `ensure_schema` now reads the live column's dimension from
`pg_attribute.atttypmod` and raises `KnowledgeStoreSchemaError` naming both dimensions and the
manual recovery. Recovery stays manual on purpose: dropping or altering the table would discard
stored vectors, and this module cannot know whether they matter. The guard was observed firing
against the genuinely stale table before being pinned by a test that rebuilds that situation; the
analytically-known nearest-neighbour test then re-ran green at 1024. See
`docs/qa/verification-report.md`.

**Anyone with a pre-existing local database must drop the table once.** CI is unaffected — its
Postgres service container is fresh per run — so this cost falls only on development machines, and
it now announces itself with an actionable error instead of a misleading one.

**Nothing here claims RAG is close.** Two gates stand between this decision and a working
capability, and only one of them is a credential. Recording that honestly is the point: the
previous state of this question was a module that looked done, a pillar that read "Implemented",
and a blocker nobody had named.
