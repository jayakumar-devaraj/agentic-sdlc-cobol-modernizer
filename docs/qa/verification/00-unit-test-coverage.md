# Unit test coverage

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Unit test coverage

```
pytest --cov=cobol_modernizer --cov-report=term-missing --cov-fail-under=90
```

As of this report: **622 tests passed (4 skipped — the opt-in live-CLI tests), 99.03% overall
coverage** (17 of 1,748 statements uncovered). These are **CI's numbers**, from the run on the
change that added this line — not a local figure. Locally the Postgres-backed
`tools/knowledge_store.py` suite skips without a running Docker daemon, which is why the
authoritative count is taken from CI, where a real service container makes it skip nothing.

`templates/target-spring-boot-baseline/` is Java and is not in that figure. It has its own suite —
13 tests, 0 skipped — run by CI on the JDK it pins; see the entry below.

| Module | Coverage |
|---|---|
| `cli.py` | 98% |
| `core/complexity.py` | 100% |
| `core/contracts.py` | 100% |
| `core/design_outputs.py` | 100% |
| `core/guardrails.py` | 100% |
| `core/model_catalog.py` | 93% |
| `core/model_client.py` | 98% |
| `core/model_routing.py` | 98% |
| `core/schema_export.py` | 100% |
| `core/source_units.py` | 100% |
| `core/structured_output.py` | 100% |
| `graph/design_graph.py` | 100% |
| `nodes/solution_architect.py` | 100% |
| `nodes/spec_critic.py` | 100% |
| `nodes/spec_extractor.py` | 100% |
| `parsing/cobol_parser.py` | 98% |
| `prompts_registry_client/loader.py` | 100% |
| `tools/knowledge_store.py` | 100% |
| `tools/pic_mapper.py` | 99% |
| `tools/tenant_repo.py` | 100% |
| `telemetry/logging_config.py` | 100% |

This table previously listed `core/model_routing.py` twice (at 98% and 100%) and omitted
`core/model_catalog.py` entirely — a transcription error, corrected against a real
`--cov-report=term` run rather than by picking one of the two rows.

`cli.py`'s one uncovered line is the `sys.exit(main())` under `if __name__ == "__main__"`, which
only runs when the module is executed directly rather than through the installed console script —
that script's real behavior is verified as a real process instead (see the `design` end-to-end
entry below).
The three node modules reached 100% with ADR-0013: their `_default_*` bodies used to be untested
live-API calls and are now one-liners delegating to `core/model_client.call_model`, which the
SDK-backend tests exercise. `core/model_client.py`'s single uncovered line is an
`AssertionError("unreachable")` guarding the end of the retry loop.
