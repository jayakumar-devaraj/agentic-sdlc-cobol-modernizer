# 0009 - Generated Java targets a new repo, `card-service` — not this one, not the tenant's

## Context

Milestone C4 (Generate Phase) will make `modernization_engineer` produce real Java source per
Track C program, validated by `local_compiler`/`build_validator` through the self-healing compile
loop, then written wherever `generate --output <path>` points. Nothing has built that yet, but the
question of *where that output actually lives long-term* is architectural, not an implementation
detail — it decides what `--output` resolves to, what this repo's own diagrams should show, and
what a human means by "the modernized CardDemo" once this is real. Three candidates exist:

1. **Inside this repo** (`agentic-sdlc-cobol-modernizer`). Wrong on this repo's own terms: per
   ADR-0001 this is a stateless specialist *tool*, and the platform is built to modernize multiple
   tenants' COBOL estates — the specialist repo can never hold any single tenant's generated
   output without becoming tenant-coupled, which is exactly the shape ADR-0001 rejects.
2. **Inside `carddemo-tenant-service`'s own worktree.** This is what this repo's own README
   diagram has implied since Milestone C1 (`SPEC -.->|reads/writes worktree via control-plane's
   clone| TENANT`) — worth naming plainly as a real, if implicit, prior decision this ADR
   supersedes, not something to quietly leave inconsistent with what follows. It's wrong for two
   reasons: `carddemo-tenant-service` is the **legacy source of truth**, with its own lifecycle
   (frozen, eventually decommissioned once modernization completes) — a live, independently
   deployable Spring Boot service has a fundamentally different one (active development, its own
   CI/CD, its own production releases). And structurally: every Track C program
   (`CBACT04C`, `CBCUS01C`, `CBACT01C`, `CBTRN02C`) is a real **batch** job — file-loop,
   checkpoint-and-abend-on-error, no request/response shape anywhere in any of their `PROCEDURE
   DIVISION`s — so the natural Java execution model is Spring Batch, not REST controllers, and
   colocating that target with the read-only legacy source blurs a boundary this platform's other
   repos all keep clean (`agentic-sdlc-eventbus` reused as-is and untouched; `carddemo-tenant-service`
   itself already a clean fork with no platform code mixed in).
3. **A new, dedicated repo.**

## Decision

**A new repo: `card-service`.** Naming mirrors `carddemo-tenant-service`'s own shorthand (`card`,
not `credit-card`) so the pairing — source repo, target repo — is legible at a glance in the
platform's repo list.

**Execution model: Spring Batch for the migrated business logic; a thin REST layer for
control/observability and genuine point queries — one deployable Spring Boot application, not two
services.** Spring Batch's `Job`/`Step`/`ItemReader`/`ItemProcessor`/`ItemWriter` model is the
direct equivalent of what every Track C program already does structurally (a read-loop, per-record
processing, a write, checkpoint/restart semantics) — it is a translation of shape, not a
reinterpretation of behavior, which matters for the zero-data-drift claim this platform is built
around. The REST layer's job is triggering/monitoring those jobs (start a run, check status, view
execution history) and any operation that is genuinely request/response shaped (e.g. a balance
lookup against the same domain model) — never the bulk batch logic itself.

Batch and REST stay in **one deployable app, one repo** — not split into separate services. The
real reasons enterprises do split batch and API tiers (different scaling profiles, different
release cadences, different owning teams) don't apply here: this is one bounded modernization
target, sharing one domain model and one schema, with no demonstrated need yet to scale or release
either tier independently of the other. Splitting now would be exactly the premature abstraction
this platform's own conventions consistently avoid elsewhere (ADR-0004's static config instead of
a routing engine; ADR-0008's untyped `unified_design` instead of guessing `solution_architect`'s
shape early). Internal package boundaries (`batch/`, `web/`, `domain/`) carry the separation of
concerns instead, so a future genuine need to split has a clean extraction point without this
decision having guessed at it prematurely.

**Repository boundary, restated precisely:**
- `carddemo-tenant-service` — read-only source of truth for legacy COBOL. Nothing in this
  decision changes that; `agentic-sdlc-cobol-modernizer` never writes to it (already true today).
- `card-service` — the generated Java target, created and cloned by control-plane the same way it
  already clones `carddemo-tenant-service` (ADR-0001's existing clone-per-run pattern, applied to
  a second repo). `generate --output <path>` resolves to this clone's worktree path.
- `agentic-sdlc-cobol-modernizer` — the specialist tool. Owns no generated output of its own,
  before or after this decision.

**Cross-repo provenance** — this repo's own stated auditing concern is provenance, not a ledger
(`CLAUDE.md`; the hash-chained audit ledger itself stays control-plane's job, never duplicated
here). Splitting source and target across two repos means that provenance chain now has to cross a
repo boundary explicitly, where same-repo colocation would have made it implicit:
- Every generated Java file carries a short header comment naming the exact COBOL program (and
  paragraph, where traceably 1:1) it was generated from — extending `spec_extractor`/`spec_critic`'s
  existing source-label provenance (ADR-0006) one hop further, into the generated artifact itself,
  not just the intermediate `spec.md`/`design.json`.
- Each `card-service` commit a real `generate` invocation produces should record, in its own
  commit message: the source program(s), the exact `carddemo-tenant-service` commit SHA
  `--tenant-repo` was cloned at, and control-plane's own audit-log run identifier for that
  invocation. That's enough for a human (or an auditor) to go from one line of generated Java, to
  the exact COBOL it came from, to the exact control-plane run and HITL approval that authorized
  it — without this repo maintaining a second copy of that trail itself.

## Consequences

This repo's own architecture diagrams (`README.md`) need correcting to match: `SPEC` reads
`TENANT` read-only (unchanged in effect, corrected in the diagram, which previously implied
writes); a new node for `card-service` receives the writes control-plane's clone enables, the same
shape as the existing `TENANT` edge, not a new kind of relationship.

`card-service` does not exist yet — this ADR is a forward architecture decision guiding Milestone
C4's design, not a claim that the repo has been created. Per this platform's own "don't build
ahead of demonstrated need" discipline (the same reasoning `docker-compose.yml` waited for
`knowledge_store.py` to actually need it), the real repo gets created when Milestone C4 first has
real generated Java to put in it — not speculatively now.

This is scoped entirely to Track C's four batch programs. Track B (CICS/BMS online transaction
programs, out of scope until Track C completes) may need this decision revisited: an *online*
transaction program has a genuine request/response shape Spring Batch doesn't fit — likely landing
as real REST/domain logic in the same `card-service` repo rather than another batch job, but that
is Track B's own design question to answer when it starts, not pre-decided here.
