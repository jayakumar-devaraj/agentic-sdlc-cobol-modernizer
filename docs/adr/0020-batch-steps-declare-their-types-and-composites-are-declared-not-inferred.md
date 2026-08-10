# ADR-0020: Batch steps declare their own types, and composite types are declared rather than inferred

## Status

Accepted (2026-08-10). Blocked the first end-to-end migration: `generate` was fully wired and
refused every processor step, because the design did not say what a processor operates on.

Amends [ADR-0008](0008-design-json-schema-and-the-gate-items-contract.md), which froze
`design.json`'s envelope, and extends [ADR-0010](0010-unified-design-shape-and-the-deterministic-llm-split.md),
which decided `unified_design`'s shape and drew the deterministic/LLM line this ADR keeps in the
same place.

## Context

Steps 39 through 42 built the whole `generate` pipeline: a renderer, a generator, a compiler, a
validator, and a self-healing loop that all work against real Maven builds. Wiring the CLI to them
surfaced the one thing nobody had needed until something tried to generate from `design.json`
unaided.

**A Spring Batch `ItemProcessor` is parameterised by two types, and `BatchStepDesign` names
neither.** It records `step_name`, `source_paragraphs`, `role`, and `description`. There is no
signature anywhere in the document.

### Why the job's entity list cannot supply them

The obvious repair — take the types from `BatchJobDesign.domain_entities` — does not work, and a
real `solution_architect` run is what shows it. Run 2026-08-10 against `CBACT04C` produced one job
whose `domain_entities` are `[TranCatBal, CardXref, DisGroup, Account, Tran]` and whose steps are:

| Role | Step | From paragraphs |
|---|---|---|
| tasklet | `openInterestSourcesTasklet` | `0000-TCATBALF-OPEN` … |
| reader | `readTranCatBalStep` | `1000-TCATBALF-GET-NEXT` |
| **processor** | `resolveAccountContextStep` | `1100-GET-ACCT-DATA`, `1110-GET-XREF-DATA` |
| **processor** | `resolveInterestRateStep` | `1200-GET-INTEREST-RATE`, `1200-A-GET-DEFAULT-INT-RATE` |
| **processor** | `computeInterestStep` | `1300-COMPUTE-INTEREST`, `1400-COMPUTE-FEES` |
| writer | `writeInterestTransactionStep` | `1300-B-WRITE-TX` |
| writer | `postAccountInterestStep` | `1050-UPDATE-ACCOUNT` |
| tasklet | `closeInterestSourcesTasklet` | `9000-TCATBALF-CLOSE` … |
| tasklet | `ioFailureAbortTasklet` | `9910-DISPLAY-IO-STATUS`, `9999-ABEND-PROGRAM` |

The three processors form a **chain**. Each enriches what the previous one produced:
`readTranCatBalStep` yields a `TranCatBal`; `resolveAccountContextStep` adds the `Account` and
`CardXref` it looked up; `resolveInterestRateStep` adds the `DisGroup` rate;
`computeInterestStep` finally produces a `Tran`.

**The types flowing between those steps are not in `domain_entities`, because they do not exist as
entities.** "A `TranCatBal` with its `Account` and `CardXref` resolved" is a real type the target
needs and no copybook declares. A five-entity list cannot say which of its members a given step
consumes, and cannot name a type that is not one of its members at all. There is nothing to derive
from.

This is not a new discovery so much as a second sighting. The first real `generate` call, on
2026-08-09, refused to implement `1300-COMPUTE-INTEREST` from a `TranCatBal` alone and asked for
exactly this: *"an input type carrying both the category balance and the resolved rate (e.g. a
joined TranCatBal + DisGroup record)."* The model identified the missing contract before the
pipeline did.

### Why this cannot be guessed at generation time

Guessing is available and cheap: pick the first entity as input, the last as output, and generate.
It would produce compiling Java for many steps. This repo does not do that, for the reason
`pic_mapper` may not call a model — **a wrong answer here looks exactly like a right one.** A
processor with the wrong input type still compiles if the fields happen to line up, and the defect
surfaces as wrong money in a batch run rather than as a red build. The whole architecture is
arranged so that unknowns fail loudly at a gate instead of quietly in production.

## Decision

**1. `BatchStepDesign` gains required `input_type` and `output_type`.** Both are strings, both are
LLM-authored by `solution_architect`, and both must resolve — a type name that matches nothing is a
hard failure, not a warning.

```
step_name, source_paragraphs, role, description, input_type, output_type
```

For non-processor roles the fields still exist and still resolve; a reader's `output_type` is what
it produces, and step 43's wiring will need it.

**2. A type name resolves against domain entities *or* a new `UnifiedDesign.composite_types`.**
A composite is a named record whose components are existing domain entities:

```
CompositeType: name, components: list[CompositeComponent]
CompositeComponent: field_name, entity_name
```

`resolveAccountContextStep`'s output becomes, say, `TranCatBalWithAccount` — components
`balance: TranCatBal`, `account: Account`, `xref: CardXref`. Every component references an entity
that already exists; a composite never introduces a field a copybook did not produce.

**3. Composites are rendered deterministically, not generated.** They are records whose components
are other records — a mechanical transform of structured data, and therefore
`rendering/java_records.py`'s job, not a model's. This keeps ADR-0010's line exactly where it is:
the *shape* is deterministic, and naming the composite is the judgment call reserved for the LLM.

**4. `design.json`'s `schema_version` goes to `2.0.0`.** Adding required fields is a breaking
change, and control-plane reads this document.

**5. Resolution is validated where the design is produced, not only where it is consumed.**
`solution_architect` checks that every `input_type`/`output_type` names a real entity or a declared
composite, and that every composite component names a real entity. A design that cannot be
generated from should fail before a human approves it at the gate, not after.

## Consequences

**The round-trip becomes possible.** `generate` currently blocks every processor step; with types
present it can render, compile, and heal them. Nothing else in the pipeline changes — this is the
missing input, not a missing capability.

**`solution_architect` gets a harder job, and one it has never been scored on.** It must now decide
where a chain's intermediate types begin and end, which is genuine design judgment. Open Issue 6
already records that this node has never been evaluated; this ADR raises what it is being trusted
with, and that argues for the LLM-as-judge harness (step 44) sooner rather than later.

**Some COBOL will not fit the chain model, and that is worth finding out.** `CBACT04C`'s processors
happen to enrich a single item; a program whose logic fans out or accumulates across records will
not decompose this way. The failure will be visible — a step whose types cannot be named — rather
than silent, which is the point.

**A composite is a target-side invention with no COBOL counterpart**, and provenance must say so.
`TranCatBalWithAccount` corresponds to no copybook, so the rendered record's Javadoc names the
entities it composes rather than claiming a source copybook it does not have. Anything else would
break `CLAUDE.md`'s requirement that a generated artifact trace to its COBOL source, by tracing to
one that does not exist.

**Existing `design.json` files stop being readable**, deliberately. There is one in a test fixture
and none in production; a silent-compatibility shim would let a v1 document reach `generate` and
fail three layers down, which is worse than refusing it at the door.

## Alternatives considered

**Derive the types from `domain_entities`.** Rejected on evidence rather than principle: the real
architect output above shows the flowing types are not in that list, so there is nothing to derive.
Even where a derivation existed it would be a guess that compiles.

**One `ItemProcessor` per job instead of per step.** Collapses the chain and removes the need for
intermediate types entirely. Rejected because it discards `source_paragraphs` granularity — the
provenance link from a Java method back to the COBOL paragraphs it implements is the thing that
makes generated code reviewable, and a single processor for nine paragraphs is a black box.

**Let the model choose types at generation time.** Rejected: it moves a design decision into the
step that has the least context, produces a different answer per run, and puts it after the human
gate rather than before it.

**Keep `BatchStepDesign` frozen and pass types as CLI arguments.** Rejected: it makes a
per-step design decision an operator's problem, and control-plane invoking this CLI has no way to
know them either.
