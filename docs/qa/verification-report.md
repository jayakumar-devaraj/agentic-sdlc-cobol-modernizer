# QA Verification Report

Running record of this repo's unit-test coverage and functional verification. Updated in the same
change as whatever it reports on — per `CLAUDE.md`, "a doc claim not backed by a command actually
run... is a bug, not documentation." Every entry below states the exact command run and its real
output, not a paraphrase. Per `.claude/agents/qa.md`: unit-test coverage and functional
verification are reported side by side, never one standing in for the other — a green test suite
was never treated as proof on its own.

This file is the **hub**: the index and the framing. It carries no logs, commands or
metrics of its own — every verified claim lives in exactly one spoke under
[`docs/qa/verification/`](verification/), so a reader (or a downstream pipeline) loads only
the scope it needs. Add a new entry to the spoke that owns its scope, in the same PR as
whatever it reports on; add a row here only when a new spoke is created.

## Functional verification

Unit tests prove a module does what it says in isolation. The entries below are the separate
question: does the real thing behave as documented against real data, real infrastructure, real
external systems — not a mock standing in for one.

## Index

| # | Scope | What it covers |
|---|---|---|
| 00 | [Unit test coverage](verification/00-unit-test-coverage.md) | The `pytest --cov` command, CI's authoritative pass/coverage numbers, and the per-module coverage table. |
| 01 | [Deterministic foundations and infrastructure](verification/01-deterministic-foundations-and-infrastructure.md) | `pic_mapper`, `cobol_parser`, `tenant_repo`, `guardrails`, `knowledge_store` against real Postgres+pgvector, and `model_routing`. |
| 02 | [Spec extraction, critique, and Milestone C2's numeric-field gate](verification/02-spec-extraction-critique-and-the-c2-numeric-gate.md) | `spec_extractor` and `spec_critic` against real Track C source, `source_units`, the CBACT04C golden fixture, `gate_items`, the generated schemas, Milestone C2's numeric-field gate, and the untrusted boundary asserted against the prompts the nodes actually send — including the billed run confirming the critic still discriminates with the narration inside it, and the structured-output repair loop with the two defects its damage probes found. |
| 03 | [The design phase as a real LangGraph run](verification/03-the-design-phase-as-a-real-run.md) | Every `DATA DIVISION` section and the fixed-`OCCURS` decision, `solution_architect`'s cross-program unification, the `design` subcommand as a real process, and `run_id`/`RunCost` under real concurrency. |
| 04 | [Model backends, prompt economics, and measured routing](verification/04-model-backends-prompt-economics-and-measured-routing.md) | Prompt duplication and cache behaviour, `model_client` against the real `claude` CLI, bounded fan-out, complexity-based routing, the two model benchmarks, and CI verified on GitHub. |
| 05 | [Track C data, the Java target, and the generate split](verification/05-track-c-data-the-java-target-and-the-generate-split.md) | Track C's real file I/O, the Spring Boot baseline compiled on Java 25, the `modernization_engineer` generate split, the first three real model calls, and the wheel a consumer installs — tested as a consumer would get it. |
| 06 | [The compile loop, real data, and the interest oracle](verification/06-the-compile-loop-real-data-and-the-interest-oracle.md) | The pinned Java toolchain, `local_compiler` against a real Maven build, the self-healing loop and the `generate` subcommand, `data_loader` into real PostgreSQL, and the hand-computed interest oracle. |
| 07 | [Equivalence, and the first real model-authored business logic](verification/07-equivalence-and-the-first-real-business-logic.md) | The equivalence test, G25, the judge comparison, and gate closures G27 (generation half), G29 and G30. |
| 08 | [Evaluation harnesses: the judge, the injected-error run, and the handoff](verification/08-evaluation-harnesses-judge-injected-error-and-the-handoff.md) | The LLM-as-judge harness as an instrument and then run for real, the injected-error harness, G7 exercised from the receiving side, and `build_validator`'s corpus — built, grounded and deliberately unrun. |
| 09 | [The write path, the systemic gate fixes, and the round trips](verification/09-the-write-path-and-the-round-trips.md) | Splitting logic from wiring, `1300-B-WRITE-TX` as its own step, the systemic halves of G26/G27/G28, the oracle's first record, the round trips run with and without a model writing the bodies, and the overpunch conversion that made the oracle faithful. |
| 10 | [G31 — the data access path, stage by stage](verification/10-g31-the-data-access-path.md) | The measured context budget, G31 stages 2 through 3e, the control break recognised, and the control-break aggregation rendered. |
| 11 | [Not yet covered — the honest gaps](verification/11-not-yet-covered-open-gaps.md) | The gaps stated on their own terms — spot-measured output quality, unwired RAG retrieval, the unrun judge, fixed `OCCURS`, `FD` record layouts, line-level provenance, `low_confidence_rule` calibration, and control-plane's unexercised gate. |
| 12 | [Tracing — spans, a real collector, and the token convention](verification/12-tracing-and-the-token-convention.md) | `telemetry/tracing.py` and its wiring into `call_model` and the CLI: the three properties ADR-0046 calls non-negotiable, a real Langfuse ingest read back from ClickHouse, and the Anthropic/OpenTelemetry token-convention inversion that stored 36,320 input tokens as 0. |
| 13 | [The structure test and its damage probes](verification/13-the-structure-test-and-its-damage-probes.md) | `tests/contract/test_repository_structure.py`: the three real dead-path defects that motivated it, and seven damage probes showing every assertion fails on the defect it names — probe 1 against the exact `.gitattributes` bytes that were on `main` that morning. |
| 14 | [The value that had nowhere to go](verification/14-the-value-that-had-nowhere-to-go.md) | The design-language gap the first end-to-end run exposed: what `pic_mapper` computed and `build_domain_entities` discarded, the refusal that now catches a step computing a value it cannot return, and six probes — two of which corrected a test and a docstring rather than confirming them. |
| 15 | [The gate that catches half a class](verification/15-the-gate-that-catches-half-a-class.md) | ADR-0065: `generate` rendering the equivalence test, resolving its result field from the design rather than the oracle's one decomposition, and running it — five tests through the real entry point with real Maven builds, a rounding defect and a discarded interest value both coming back labelled, and the accumulator class stated as still uncaught. |
| 16 | [A generated project that runs](verification/16-a-generated-project-that-runs.md) | ADR-0066 and ADR-0067: `generate` rendering the readers, writers, staging, job configuration and the beans binding them to files — a project the pipeline wired by itself that builds, runs to `COMPLETED`, matches the transaction oracle exactly and matches 597 of 600 account fields, with the three remaining mismatches pinned to CBACT04C's own unreachable EOF branch through the hand-written round trip's assertion. |

