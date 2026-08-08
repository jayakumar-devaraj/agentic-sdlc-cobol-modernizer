# agentic-sdlc-cobol-modernizer

COBOL-to-Java modernization specialist for the `agentic-sdlc-*` platform. Invoked as a CLI
subprocess by [`agentic-sdlc-control-plane`](https://github.com/jayakumar-devaraj/agentic-sdlc-control-plane)
— it has no ingress API, no durable checkpointer, and no human-in-the-loop gates of its own; see
[`docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md`](docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md)
for why.

## Tech stack

- **Language**: Python 3.12
- **Internal sub-pipeline**: LangGraph (in-process, in-memory checkpointer — not durable; see
  ADR-0001)
- **State/validation**: Pydantic
- **LLM client**: `anthropic` SDK — model per node resolved from `config/model_routing.yaml`
  (static, not a routing engine; see ADR-0004)
- **Config**: PyYAML (`config/model_routing.yaml`)
- **Testing**: pytest, pytest-cov

## Architecture

This is the design the implementation is built against, not a record of what already exists —
the specialist nodes below land incrementally across Milestones C2–C4 (tracked in
[`docs/adr/`](docs/adr/) and the repo's task list), but the shape doesn't change as they do.
Today the CLI skeleton and every tool the first two `design`-phase nodes depend on (`parsing/`,
`pic_mapper`, `tenant_repo`, `guardrails`, `knowledge_store`, `model_routing`, `source_units`) are
real and independently tested, and `spec_extractor` + `spec_critic` are both implemented and
verified standalone against real CardDemo source (`docs/qa/verification-report.md`). The
`design.json` contract itself — including `gate_items`, the concrete payload control-plane's gate
reviews (see "How human review actually happens" below) — is also real and schema-checked
(`core/contracts.py`, `schemas/*.schema.json`, ADR-0008). None of this is wired into the CLI's
`design` subcommand yet (Milestone C3, plan step 36), so there is no end-to-end run today — the
diagrams below describe the target composition, verified piece by piece as each lands.

### How this repo fits in the platform

```mermaid
flowchart TB
    TENANT["carddemo-tenant-service<br/>forked CardDemo, real legacy COBOL"]

    subgraph platform["agentic-sdlc-* platform"]
        EB["agentic-sdlc-eventbus<br/>Kafka broker, shared contract"]
        MLOPS["agentic-sdlc-mlops<br/>drift detection (unrelated to this repo)"]
        CP["agentic-sdlc-control-plane<br/>orchestration, durable checkpoints, HITL gates"]
        SPEC["agentic-sdlc-cobol-modernizer<br/>this repo: specialist CLI, no durable state"]
    end

    TENANT -.->|"batch-drift event, Milestone C5"| EB
    EB -->|"drift and decisions"| CP
    CP -->|"invokes as subprocess<br/>one bounded call"| SPEC
    SPEC -->|"structured JSON:<br/>spec, design, generated files,<br/>compile diagnosis"| CP
    CP -.->|"read-only clone per run"| TENANT
    SPEC -.->|"reads/writes worktree<br/>via control-plane's clone"| TENANT

    style TENANT fill:#f5f5f5,stroke-dasharray:5 5
    style SPEC fill:#e8f0ff
```

Every solid arrow into or out of this repo is a single bounded subprocess call. This repo never
talks to Kafka or the tenant repo's git remote directly — control-plane owns the clone and hands
this repo a worktree path (`docs/adr/0001`). It does talk to Postgres directly, but only for the
knowledge store's own schema (`tools/knowledge_store.py`), via a credentials file path, never an
embedded credential (`docs/adr/0005`) — durable orchestration state stays entirely control-plane's
concern.

### This repo's internal pipeline — two separate, independently bounded invocations

Not one continuous flow: this repo has no durable state (ADR-0001), so it cannot pause mid-run for
a human gate and resume later. Control-plane's gate sits *between* two separate process
invocations, never inside one (ADR-0003).

```mermaid
flowchart TB
    subgraph phase1["Invocation 1: cobol-modernizer design (exits at design.json)"]
        SUP1["orchestration_supervisor"]
        SPEC_CUS["spec_extractor: CBCUS01C"]
        SPEC_ACT["spec_extractor: CBACT01C"]
        CRIT_CUS["spec_critic"]
        CRIT_ACT["spec_critic"]
        JOIN["join<br/>both branches complete"]
        SPEC_TRN["spec_extractor: CBTRN02C"]
        CRIT_TRN["spec_critic"]
        SPEC_INT["spec_extractor: CBACT04C"]
        CRIT_INT["spec_critic"]
        ARCH["solution_architect<br/>emits one design.json for all four programs"]

        SUP1 --> SPEC_CUS --> CRIT_CUS --> JOIN
        SUP1 --> SPEC_ACT --> CRIT_ACT --> JOIN
        JOIN --> SPEC_TRN --> CRIT_TRN --> SPEC_INT --> CRIT_INT --> ARCH
    end

    GATE["control-plane's own durable gate<br/>human reviews design.json<br/>persists across a restart if needed"]

    subgraph phase2["Invocation 2: cobol-modernizer generate (fresh process, no memory of invocation 1)"]
        CODE["modernization_engineer<br/>generates Java from design.json"]
        COMPILE["local_compiler<br/>sandboxed mvn compile"]
        VALID["build_validator<br/>diagnoses failure, patches"]
        DONE(["structured JSON result to control-plane"])

        CODE --> COMPILE
        COMPILE -->|"success"| DONE
        COMPILE -->|"fail"| VALID --> CODE
        VALID -.->|"3rd failed attempt"| DONE
    end

    ARCH -->|"design.json written to disk,<br/>process exits"| GATE
    GATE -->|"approved: control-plane<br/>starts a new process"| CODE
```

`CBCUS01C` (customer) and `CBACT01C` (account) share no copybook dependency (verified in
`docs/cobol-construct-support-matrix.md`) and run as parallel branches; `CBTRN02C` and `CBACT04C`
both depend on account data and run after the join. The self-healing loop is a bounded retry, not
an open-ended one — a third failed compile returns a result to control-plane rather than looping
forever. `design.json` must be fully self-contained (ADR-0003): invocation 2 has no access to
anything invocation 1 reasoned about that isn't written into that file.

### How human review actually happens (the HITL story, in one place)

This repo has **no** human-in-the-loop gate of its own, and that's a deliberate strength, not a
missing feature — a gate needs to be durable (survive a crash, not just this bounded subprocess),
sit at a complete checkpoint boundary, and never be judged by the same component that produced the
work. `agentic-sdlc-control-plane` already has all three: a Postgres-checkpointed graph, 5
`interrupt()`-backed gate types, and a hash-chained audit log. Building a second, weaker version of
that here would be exactly the "second control plane" `docs/adr/0001` exists to prevent.

What this repo *does* own is making sure that gate has something real to review:

1. `spec_extractor` and `spec_critic` never guess past an ambiguous case — a `REDEFINES` field is
   flagged (`docs/adr/0002`, `docs/adr/0006`), a narration defect forces confidence to `0.0`
   rather than being averaged away (`docs/adr/0007`), a prompt-injection heuristic match is
   surfaced, never silently acted on.
2. `core/contracts.py`'s `build_gate_items()` consolidates all four of those signal types, across
   every program in one `design` run, into one `gate_items` list in `design.json`
   (`docs/adr/0008`) — a human reviewing the gate reads one list, not four separate structures per
   program.
3. This repo never decides what happens next with a gate item. No approve/reject, no blocking —
   `gate_items` states facts; whether `gate_item_count > 0` pauses anything is control-plane's gate
   policy, decided over there, not baked into this repo's CLI contract.

`design.json`'s full shape (and the CLI's own summary-only stdout contract, `DesignCliResult`) is
formally schema'd — `schemas/design_document.schema.json`, generated from the Pydantic models and
checked for drift in CI (`tests/system/test_schemas.py`), so an external consumer never has to
trust a hand-written copy of the contract.

### What this design deliberately doesn't buy

- **No resume mid-pipeline.** A crash during this repo's own invocation loses that invocation's
  progress; control-plane recovers by re-invoking the CLI, not by anything built here
  (`docs/adr/0001`, Consequences).
- **`spec_critic`'s confidence score is the only independent check on extraction quality** for
  Track C. It is not a second, adversarial review — see the same ADR for why `REDEFINES`/
  `OCCURS DEPENDING ON` (Track B) get a stricter, mandatory gate instead of a confidence score.
- **The parser has a hard, enforced boundary.** `REDEFINES`, `OCCURS DEPENDING ON`, and
  `COPY REPLACING` are detected and rejected, never partially interpreted (`docs/adr/0002`).

## Quickstart

```bash
py -3.12 -m venv .venv
./.venv/Scripts/pip install -e ".[dev]"
./.venv/Scripts/cobol-modernizer design --programs CBACT04C --tenant-repo <path> --output <path> --json
./.venv/Scripts/cobol-modernizer generate --design <path>/design.json --tenant-repo <path> --output <path> --json
```

Both currently return a `not_implemented` status — this is expected until Milestones C2/C3 (`design`)
and C4 (`generate`) land. Two subcommands, not one — see `docs/adr/0003` for why.

## Local development

```bash
docker compose up -d postgres
```

Stands up a local Postgres+pgvector instance (`localhost:5434`) for `tools/knowledge_store.py` —
deliberately isolated from `agentic-sdlc-control-plane`'s own Postgres instance, which real
deployment reuses instead (`docs/adr/0005`). This is a throwaway dev/test resource this repo fully
owns; production never points at it. `tests/fixtures/db_credentials_sample/local.conn` has the
matching local credentials pre-filled (a docker-compose default password for a local-only,
unreachable-from-outside container, not a real secret — see that file's own header comment). No
sandboxed-compiler stack yet — that's Milestone C4, added when there's real generated Java to
compile.

## Testing

```bash
./.venv/Scripts/python -m pytest --cov=cobol_modernizer --cov-report=term-missing --cov-fail-under=90
```

191 tests passing, 98% coverage as of this change — the number a real run produces, not a claim.
Some tests (`tools/knowledge_store.py`'s) need the local Postgres+pgvector instance above; they
skip with a clear reason rather than failing if it isn't running, and CI runs them for real against
its own service container rather than letting them skip silently there too.

## Deployment / CI

`.github/workflows/ci.yml` runs lint + the test suite with the coverage floor above on every push
and pull request. No deployment pipeline yet — this repo has nothing running in production until
control-plane's specialist router (Track P) can invoke it, and Kubernetes/Terraform manifests
(Track P4, in `agentic-sdlc-control-plane`) exist to run it against.
