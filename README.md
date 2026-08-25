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
- **LLM client**: `anthropic` SDK — model per node resolved from `cobol_modernizer/data/config/model_routing.yaml`
  (static, not a routing engine; see ADR-0004)
- **Config**: PyYAML (`cobol_modernizer/data/config/model_routing.yaml`)
- **Testing**: pytest, pytest-cov

## Architecture

**Both halves of the diagram below run today.**
`cobol-modernizer design` executes a compiled LangGraph (ADR-0012): one concurrent branch per
requested program running `spec_extractor` then `spec_critic`, joining into a single
`solution_architect` pass that unifies every program's shared copybooks (`Account`/`CVACT01Y` alone
spans three of the four Track C programs) into one domain model (ADR-0010). It writes a real
`design.json` — including `gate_items`, the concrete payload control-plane's gate reviews (see "How
human review actually happens" below) — plus one `spec.md` per program, and reports a summary on
stdout. Every tool underneath (`parsing/`, `pic_mapper`, `tenant_repo`, `guardrails`,
`knowledge_store`, `model_routing`, `source_units`, `structured_output`) is independently tested
against real CardDemo source (`docs/qa/verification-report.md`).

Every model call goes through one client (`core/model_client.py`, ADR-0013) that owns backend
choice, timeout, bounded retry with jittered backoff, and per-call token/cost capture — so no node
reimplements any of that, and a rate limit is handled once rather than three times. Program
branches are capped rather than fanning out without limit.

**Which model runs is computed, not hardcoded** (ADR-0014, ADR-0015). `core/complexity.py` measures
the work before any call — paragraph count, field counts, and the exact prompt about to be sent —
and classifies it into a tier. `cobol_modernizer/data/config/model_routing.yaml` then states what that tier *needs*
(minimum capability, effort, output ceiling, and a measured token profile) and names no model at
all; `cobol_modernizer/data/config/model_catalog.yaml` holds each model's price, capability rank, and `verified_for` —
the nodes it has real benchmark evidence for. Selection is the cheapest catalogued model that
clears the bar and is verified for that node.

`verified_for` is a hard gate: adding a model to the catalog does not make it eligible, running the
benchmark does. That gate has teeth — both Sonnet models narrated an unreachable branch in
`CBACT04C` as live (the last account's interest is never posted), so neither is eligible for
`spec_extractor` despite being ~2.5× cheaper than Opus. Conversely `spec_critic` runs on Haiku,
selected on price, because a benchmark showed it catches planted defects as well as Opus does.

The decision, its estimated cost, and what it beat all ride into `design.json`, so a reviewer at
the gate can see which model produced a spec and why.

Three honest limits. **The `generate` half runs, and what it has produced is narrow.**
`cobol-modernizer generate` reads an approved `design.json`, scaffolds the target project, renders
the domain records, asks a model for one `process(...)` method body per step, compiles the project,
and — while `build_validator` judges that a rewrite could help — asks for one, at most three times
(ADR-0020). The model-authored region is marked in every generated file so a reviewer can see which
lines a model wrote, and a compile error outside that region blocks rather than being handed back to
a model. A real model has now written business logic through this
path, and one calculation is checked against its COBOL: `CBACT04C`'s interest `COMPUTE` passes an
equivalence test **10 of 10** against expected values derived by hand from the source (ADR-0021),
rendered into the generated project and run by real Maven.

**Two programs round-trip** — and the qualifier travels with the number: **`2 of 4`, wiring
hand-written.** Every file each program writes is compared against what the **unmodified** program
wrote under GnuCOBOL (`tools/cobol-oracle/`, `docs/adr/0028`), field-for-field at full declared
width (`docs/adr/0029`):

`CBACT04C` — the interest calculator:

| | matched | excluded |
|---|---|---|
| interest transactions (50 records) | **500 of 500** | 3 fields, `docs/adr/0026` |
| rewritten account file (50 records) | **597 of 600** | none |

`CBTRN02C` — transaction posting, and the **stricter** of the two (`docs/adr/0048`):

| | matched | excluded |
|---|---|---|
| transaction master (262 records) | **3144 of 3144** | 1 field, `docs/adr/0026` |
| rewritten account file (50 records) | **600 of 600** | none |
| transaction category balances (100 records) | **400 of 400** | none |

**Its exclusions are earned, not inherited.** `CBACT04C` cannot produce `TRAN-ID` or `TRAN-ORIG-TS`;
`CBTRN02C` copies both straight from its input (`CBTRN02C.cbl:425`, `:436`), so both are compared
here. Only `TRAN-PROC-TS` transfers, and for the original reason — that program reads
`FUNCTION CURRENT-DATE` once per transaction (`:438`, `:693`). Inheriting the other two would have
excused fields it reproduces exactly.

**Both programs are measured twice: with scripted bodies and with a real `claude-opus-5` run
writing them**, and the numbers above hold for both. `CBACT04C`'s three bodies compiled on the first
attempt and the self-healing loop never ran; `CBTRN02C`'s took one model call, first attempt, no
heal. The live runs cost real money and are skipped unless
`COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, so CI proves the scripted half continuously and the
model-authored half is reproducible on demand.

**What the qualifier means, and it is not decoration.** The reader, both writers, the
control-break aggregation, the staging, all three steps and the job are **rendered** from
`design.json` — access paths, keys, joins, record layouts, `WRITE`/`REWRITE` modes and the group-by
the COBOL performs at an account break, all parsed from the source (G31, `docs/adr/0032`) — so the
candidate compared above is the program's own output in COBOL's record format. What stays
hand-written is **the file paths**, and they are arguably not design at all: the COBOL says
`ASSIGN TO TCATBALF`, an environment name, and nothing anywhere says what it resolves to
(`tests/fixtures/handwritten/CBACT04C/`, `docs/adr/0030`). The claim is therefore
*generated logic matches COBOL's own output inside wiring a human wrote* — **not** *this platform
generated a working program*. A test fails if this file states the count without the qualifier in
the same paragraph.

**The three account fields that differ are COBOL's own defect**, pinned rather than smoothed over:
its account-break post sits in an unreachable `ELSE`, so the last account is never credited, and the
divergence is exactly the three fields `1050-UPDATE-ACCOUNT` writes. Skipping that account in the
wiring would have made the comparison green by encoding a bug, and is refused on the record. It was
**598 of 600** until `docs/adr/0047`: with the corpus's real amounts the last account now carries a
non-zero total in *both* cycle fields, where one of them used to be zero on both sides and matched
by coincidence. Same single cause, now showing in every field it can reach.

**Still true.** Nothing has been written to the real `card-service` repository, and generating a
stateful control-break writer is out of scope. **`2 of 4`, wiring hand-written, is the reachable
maximum** (G17, `docs/adr/0035`): `CBCUS01C` and `CBACT01C` contribute a sequential read and a
print, so the remaining two cannot round-trip in this sense at all. That boundary is now part of
the round-trip denominator rather than an open question about it (`docs/adr/0051`).
Rendering `CBTRN02C` needed four
facts `CBACT04C` never did: an `upsert` write mode (`docs/adr/0037`), a declared `reads_own_writes`
step (`docs/adr/0040`), a working set its reader and writer share at chunk 1 (`docs/adr/0041`), and
a declared optional lookup for the read that *creates* a balance row (`docs/adr/0042`). With those,
the rendered job completes under real Maven and produces **262 of its oracle's 262 transactions,
every amount equal by value, and the account file exactly**.

`reads_own_writes` is the one those numbers turn on, and it was measured before it was built: the
program decides what to accept from cycle fields its own posting rewrites, so judged per item, 25 of
its 38 rejections disappear and a stateless implementation writes **287** records where the program
writes 262 — each individually correct, which is what makes the error invisible to a field
comparison (`docs/adr/0039`).

**It produced those same numbers while the comparison was failing**, which is the part worth
stating. The run wrote 262 records and 100 balance rows against an oracle holding 257 and 94, and
seven decisions differed. Every one of them traced to an amount whose last byte is an IBM sign
overpunch, which the oracle's runtime read as digit `0` — dropping a digit and the sign, so a
negative amount looked like a large positive one and failed a credit-limit check
(`docs/adr/0043`, four independent lines of evidence). `docs/adr/0047` converts the representation
inside the oracle pipeline, in both directions, and the oracle moved to 262 and 100. **The pipeline
did not move.**

**What it took to count it** (`docs/adr/0048`). Agreement on record identity and one field is not
the measurement the number names, so three things had to be true first: every field compared at full
declared width with exclusions established from *this* program's source, **every** file it writes
compared rather than the convenient two, and a real model — not a scripted transcription — writing
the body. `CBTRN02C` writes three files in scope where `CBACT04C` writes two; `DALYREJS` is the
fourth and is refused by name in the job (`docs/adr/0038`), which is a decision rather than an
omission.

The round trip found a real defect on its first pass — `TRAN-SOURCE` is `PIC X(10)` and the body
wrote a bare `"System"`, disagreeing with COBOL on fifty records while every amount matched.

Step 40a's loader
(`tools/data_loader.py`) reads CardDemo's fixed-width files into PostgreSQL with
`pic_mapper`-derived types; its finding that every `TRAN-CAT-BAL` in the shipped data is zero turned
out to be a property of *that* file rather than the corpus — `dailytran.txt` carries 299 distinct
signed amounts, and the zeros are the state before posting has run.
**Retrieval is not wired**: `tools/knowledge_store.py` is a real,
tested pgvector storage layer with no production caller, because embeddings need a second vendor
this environment has no credential for — and because nobody has shown retrieval would help a
four-program corpus (`docs/adr/0016` decides both halves of that). And **output quality is evaluated by an
instrument that is not yet reliable**: `tests/evaluations/` scores model-authored bodies against a
committed corpus (`docs/adr/0024`), and across three billed runs `claude-opus-5` has never missed a
defect a real JVM catches — but its false-positive rate is *not stable*, scoring 0.00 on one run and
0.50 on another over identical input. Twice its disagreements turned out to be correct about the
code rather than about the judge, so the corpus now declares which criteria each case is a clean
specimen for.

### How this repo fits in the platform

```mermaid
flowchart TB
    TENANT["carddemo-tenant-service<br/>forked CardDemo, real legacy COBOL"]
    TARGET["card-service<br/>generated Java, real deployable target"]

    subgraph platform["agentic-sdlc-* platform"]
        EB["agentic-sdlc-eventbus<br/>Kafka broker, shared contract"]
        MLOPS["agentic-sdlc-mlops<br/>drift detection (unrelated to this repo)"]
        CP["agentic-sdlc-control-plane<br/>orchestration, durable checkpoints, HITL gates"]
        SPEC["agentic-sdlc-cobol-modernizer<br/>this repo: specialist CLI, no durable state"]
    end

    TENANT -.->|"batch-drift event, Milestone C5"| EB
    EB -->|"drift and decisions"| CP
    CP -->|"invokes as subprocess<br/>one bounded call"| SPEC
    SPEC -->|"structured JSON:<br/>spec, design, gate items,<br/>compile diagnosis"| CP
    CP -.->|"read-only clone per run<br/>(design + generate)"| TENANT
    SPEC -.->|"reads COBOL source<br/>(read-only)"| TENANT
    CP -.->|"clone/create per run<br/>(generate only)"| TARGET
    SPEC -.->|"writes generated Java<br/>via control-plane's clone"| TARGET

    style TENANT fill:#f5f5f5,stroke-dasharray:5 5
    style TARGET fill:#f5f5f5,stroke-dasharray:5 5
    style SPEC fill:#e8f0ff
```

Every solid arrow into or out of this repo is a single bounded subprocess call. This repo never
talks to Kafka, or either repo's git remote, directly — control-plane owns both clones and hands
this repo worktree paths (`docs/adr/0001`).
[`card-service`](https://github.com/jayakumar-devaraj/card-service) (ADR-0009) is the `generate`
phase's write target — not this repo's own output, and not `carddemo-tenant-service`'s, which
stays read-only source of truth throughout. **The repository itself is still an empty scaffold** —
`generate` can now write a compiling project into a target directory, but nothing has been committed
to `card-service` itself, and doing that is control-plane's job through its own clone (ADR-0001). What that target looks like is decided: Java 25 on Spring Batch over **PostgreSQL**
(`docs/adr/0034`, `docs/adr/0036`),
with CardDemo's data files as a one-time migration source rather than the runtime store, and
`CBACT01C`'s fixed-`OCCURS` demo outputs scoped out of generation entirely (`docs/adr/0035`). This repo has **no Postgres edge in the diagram on purpose**: two modules can talk to
PostgreSQL — `tools/knowledge_store.py` for the knowledge store's own schema, and
`tools/data_loader.py` for migrating CardDemo's data files into the target's tables — but neither is
called from the `design` or `generate` path (`docs/adr/0016`, `docs/adr/0036`), so today both
connections are exercised only by their own tests against a local container. When it is wired, the credential arrives as a mounted file path and never
as an embedded value or an environment variable (`docs/adr/0005`). Durable orchestration state
stays entirely control-plane's concern either way.

### This repo's internal pipeline — two separate, independently bounded invocations

Not one continuous flow: this repo has no durable state (ADR-0001), so it cannot pause mid-run for
a human gate and resume later. Control-plane's gate sits *between* two separate process
invocations, never inside one (ADR-0003).

```mermaid
flowchart TB
    subgraph phase1["Invocation 1: cobol-modernizer design (exits at design.json)"]
        SUP1["supervisor<br/>fans out one branch per --programs entry"]
        SPEC_CUS["spec_extractor: CBCUS01C"]
        SPEC_ACT["spec_extractor: CBACT01C"]
        SPEC_TRN["spec_extractor: CBTRN02C"]
        SPEC_INT["spec_extractor: CBACT04C"]
        CRIT_CUS["spec_critic"]
        CRIT_ACT["spec_critic"]
        CRIT_TRN["spec_critic"]
        CRIT_INT["spec_critic"]
        ARCH["solution_architect<br/>joins every branch<br/>emits one design.json for all programs"]

        SUP1 --> SPEC_CUS --> CRIT_CUS --> ARCH
        SUP1 --> SPEC_ACT --> CRIT_ACT --> ARCH
        SUP1 --> SPEC_TRN --> CRIT_TRN --> ARCH
        SUP1 --> SPEC_INT --> CRIT_INT --> ARCH
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

**Every** program runs as its own concurrent branch, not just the `CBCUS01C`/`CBACT01C` pair. An
earlier revision of this diagram ran those two in parallel and chained `CBTRN02C` and `CBACT04C`
after the join, on the reasoning that the latter two "both depend on account data". Building it
showed that reasoning does not apply here: the dependency is on the `CVACT01Y` *copybook*, which
each branch reads independently from the tenant worktree — no branch consumes another branch's
output, so there is nothing to serialize. The only real join is `solution_architect`, which by
definition needs all of them (ADR-0010). Fan-out is dynamic over whatever `--programs` is passed,
so the topology is not tied to these four names. Branches are genuinely concurrent, on a real
thread pool — measured, not assumed (ADR-0012).

The self-healing loop is a bounded retry, not an open-ended one — a third failed compile returns a
result to control-plane rather than looping forever. `design.json` must be fully self-contained (ADR-0003): invocation 2 has no access to
anything invocation 1 reasoned about that isn't written into that file. `CODE`'s output lands in
`card-service`, not this repo or `carddemo-tenant-service` (ADR-0009) — `generate --output <path>`
resolves to control-plane's clone of that target repo's worktree.

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
./.venv/Scripts/cobol-modernizer design --programs CBCUS01C CBACT01C CBTRN02C CBACT04C --tenant-repo <path> --output <path> --json
./.venv/Scripts/cobol-modernizer generate --design <path>/design.json --tenant-repo <path> --output <path> --json
```

`design` is real and runs the full pipeline. By default it reaches a model through the **`claude`
CLI** (ADR-0013), which authenticates from an existing Claude subscription — **no API key
required**. Set `COBOL_MODERNIZER_MODEL_BACKEND=anthropic_sdk` to use the Anthropic API directly
instead (needs `ANTHROPIC_API_KEY`); that is the right choice for a deployment that needs
per-tenant quotas and real cost attribution. It writes
`<output>/design.json` and `<output>/<PROGRAM>/spec.md` per program, and exits non-zero with a
`status: "error"` object on stdout if anything fails. Pass `--run-id` to reuse control-plane's own
audit-log run id, so its records and this CLI's stderr logs share one identifier; omit it and one
is generated and reported back. `generate` still returns an error status — it lands in Milestone C4.

With `--json`, stdout carries exactly one JSON object and nothing else; all logging goes to stderr.
Two subcommands, not one — see `docs/adr/0003` for why.

## Local development

```bash
docker compose up -d postgres
```

Stands up a local Postgres+pgvector instance (`localhost:5434`) for `tools/knowledge_store.py` —
deliberately isolated from `agentic-sdlc-control-plane`'s own Postgres instance, which real
deployment reuses instead (`docs/adr/0005`). This is a throwaway dev/test resource this repo fully
owns; production never points at it. `tests/fixtures/db_credentials_sample/local.conn` has the
matching local credentials pre-filled (a docker-compose default password for a local-only,
unreachable-from-outside container, not a real secret — see that file's own header comment).

`cobol_modernizer/data/templates/target-spring-boot-baseline/` is a real Maven project, not a scaffold of placeholders —
`mvn -B verify` inside it needs **JDK 25** and a running Docker daemon, and CI builds it on every
push. There is still no sandboxed-compiler stack driving it from Python; that's the rest of
Milestone C4.

## Testing

```bash
./.venv/Scripts/python -m pytest --cov=cobol_modernizer --cov-report=term-missing --cov-fail-under=90
```

1146 tests passing (12 skipped — the opt-in live-CLI tests), 98.81% coverage — CI's own numbers from
the run on this change, not a local approximation of them. The Postgres-backed
`tools/knowledge_store.py` suite is included and skips nothing there, because CI provides a real
service container. The target template's own 36 Java tests are not in that
figure; CI runs them separately on JDK 25 (`mvn -B verify`, job `template-build`). Some tests (`tools/knowledge_store.py`'s) need the local
Postgres+pgvector instance above; they skip with a clear reason rather than failing if it isn't
running, and CI runs them for real against its own service container rather than letting them skip
silently there too. If that local instance predates `docs/adr/0016`, its `knowledge_entries` table
still declares `vector(1536)` and `ensure_schema` will refuse it by name — drop the table once and
re-run. CI is unaffected; its Postgres service container is fresh every run.

### The evaluation harness

`tests/evaluations/` scores model-authored method bodies against a committed corpus, so translation
quality is a number that can be recomputed after a prompt edit rather than a measurement with a date
on it (`docs/adr/0024`). It runs in the ordinary suite at no cost — the judge's model call sits
behind an injectable seam, and the tests above exercise the prompt, the response contract and the
scoring with scripted judges.

The corpus is graded by things that are not opinions: five of its nine cases are the exact bodies a
real JVM has already returned a verdict on — three through `test_interest_equivalence` against the
hand-computed oracle, and two through `test_cbtrn02c_round_trip` against COBOL's own output. A judge
that misses one has been shown to miss a defect a JVM catches.

**It spans two programs** (`docs/adr/0050`), which is what `docs/adr/0044`'s rule requires before any
result is a property of the judge rather than of one program's idioms. `CBTRN02C`'s pair is a
different shape, not more of the same: its guard decides whether a record exists at all, and its
unreachable field is a per-record clock read rather than a per-run counter.

**A run is `n` runs, and every metric carries its spread** (`docs/adr/0049`). Reporting one sample of
a non-deterministic instrument as a measurement is the defect that cost this harness its green
status: it scored 6 of 6 at a 0.00 false-positive rate once and 4 of 6 at 0.50 the next time. The
bars now apply to **every run rather than the mean** — a judge that catches every defect in two runs
of three has a mean of 0.67 and an eligibility of none.

Measured over three runs, on the seven-case corpus that preceded the second program:

| | `claude-opus-5` | `claude-haiku-4-5` |
|---|---|---|
| responses holding the contract | **21 of 21** | 16 of 21 |
| oracle-grounded detection | **1.00 ± 0.00** | 1.00 ± 0.00 † |
| false-positive rate | **0.00 ± 0.00** | 0.33 ± 0.29 |
| reproducible | **yes** | no |
| output tokens | **15,981** | 259,591 |

† over the subset it answered, and so not a measurement of that candidate.

**Haiku 4.5 was struck from the candidate list** on all three grounds, and it was not even cheaper —
$1.93 against $2.18 for sixteen times the output. The Opus pin `docs/adr/0024` called scaffolding
has now survived its own comparison.

**Its disagreements have three times turned out to be correct about the code** rather than about the
judge: a carried record's placeholder fields, `TRAN-SOURCE` written unpadded into a `PIC X(10)`, and
— on the two-program corpus — a `TRAN-PROC-TS` invented from a literal. **That last one is the case
for keeping a judge beside the oracle**: a literal passes the differential *precisely because*
`docs/adr/0048` excludes that field from it, so the only instrument that could see the defect was the
one that does not look at the differential.

Cases therefore declare which criteria they are clean specimens for, because a metric that scores an
instrument against a fallible reference reports the reference's errors as the instrument's. Chasing
that third disagreement found the rubric holds a pair no body can satisfy at once: an unreachable
**alphanumeric** field can be left unset *or* written at its declared width, never both.

**What this does not say.** The pillar this fixes is **not closed**. `docs/adr/0044`'s second-instance
requirement is now met, but the false-positive rate over the corrected corpus is unmeasured, and a
rate no run produced is not a result. And *reproducible* means the numbers repeat, not that the judge
says the same thing — Opus returned identical rates across runs while its raw verdicts moved, which is
recorded as **verdict churn** and deliberately not barred on.

The benchmark is opt-in because it spends real subscription quota — **one call per case per run, so
27 at the default `n=3` over nine cases: $2.40 measured**:

```bash
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 ./.venv/Scripts/python -m pytest tests/evaluations/test_judge_benchmark.py -q -s
```

Every other test in the repo is free.

## Deployment / CI

`.github/workflows/ci.yml` runs lint + the test suite with the coverage floor above on every push
and pull request, and a second job (`template-build`) compiles and tests
`cobol_modernizer/data/templates/target-spring-boot-baseline/` on JDK 25 against a real PostgreSQL container. That job
exists so an ecosystem dependency that has not caught up to the pinned JDK fails here rather than
inside the self-healing compile loop, where a compile-error-driven loop cannot diagnose it
(`docs/adr/0019`). No deployment pipeline yet — this repo has nothing running in production until
control-plane's specialist router (Track P) can invoke it, and Kubernetes/Terraform manifests
(Track P4, in `agentic-sdlc-control-plane`) exist to run it against.
