# Tracing — spans, a real collector, and the token convention that was inverted

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry states the exact command run
> and its real output, not a paraphrase.

Covers `telemetry/tracing.py` and its wiring into `core/model_client.call_model` and `cli.main`
(ADR-0046, implemented by ADR-0060).

## Unit tests — the three properties ADR-0046 calls non-negotiable

```
.venv/Scripts/python.exe -m pytest tests/system/test_tracing.py -q
```

```
.....................                                                    [100%]
21 passed in 16.49s
```

The properties are asserted rather than assumed. Spans are read back through an
`InMemorySpanExporter` attached to the provider `configure_tracing` actually built, not one the
test constructs — the thing most likely to be wrong is the construction, and a test that builds
its own provider would assert only that the SDK works.

Every test in that file exports to `http://127.0.0.1:1`, where nothing listens. That is the
point: `test_spans_are_recorded_with_an_unreachable_collector` proves recording is unaffected,
and no test in the suite fails because of it.

**Two defects found by these tests, both silent in production:**

1. The OpenTelemetry SDK records an exception on the span itself. Leaving its handling on *and*
   recording explicitly produced two `exception` events per failure — caught by an assertion of
   exactly one. `start_as_current_span` is now called with `record_exception=False` and
   `set_status_on_exception=False`.
2. `trace.set_tracer_provider` may be set once per process and silently keeps the first provider.
   Taking the tracer from the global would have left `shutdown_tracing` holding a provider that
   was never used, flushing nothing. The tracer now comes from the provider the function built.

## Functional verification — a real Langfuse, not a mock

Unit tests cannot answer whether a collector accepts these attributes or renders them as a
generation. A probe emitted one nested pair of spans through the package's own module, against
the running self-hosted Langfuse, calling no model.

Read back from ClickHouse rather than `GET /api/public/traces` — Langfuse v4 stores spans in
`events_core`/`events_full`, and the legacy REST endpoint returns 0 even while ingestion works:

```
docker exec langfuse-clickhouse-1 clickhouse-client --user clickhouse --password "$CH" --query "
select name, type, provided_usage_details, usage_details, provided_model_name, session_id,
       substring(input, 1, 120) as input_head, substring(output, 1, 80) as output_head
from default.events_full where name = 'call_model.spec_extractor'
order by start_time desc limit 1 format Vertical"
```

```
name:                   call_model.spec_extractor
type:                   GENERATION
provided_usage_details: {'input':0,'output':4096,'input_cached_tokens':32014,'input_cache_creation':11667}
usage_details:          {'input':0,'output':4096,'input_cached_tokens':32014,'input_cache_creation':11667,'total':47777}
provided_model_name:    claude-opus-5
session_id:             session-probe-165539
input_head:             [{"role": "system", "content": "You extract a specification."}, ...]
output_head:            probe response for probe-165539
```

Confirmed working: the span is typed `GENERATION`, the model name, session id, prompt and
completion all arrive, and both cache counters map (Langfuse renames them `input_cached_tokens`
and `input_cache_creation`).

### The defect this found: `input: 0` for 36,320 tokens

The probe sent `input_tokens=36320`. It was stored as **0**.

**Anthropic and OpenTelemetry mean opposite things by "input tokens."** Anthropic's `input_tokens`
*excludes* cache reads and cache creations. OpenTelemetry's `gen_ai.usage.input_tokens` is the
whole prompt, and Langfuse derives the uncached part by *subtracting* the cache counters from it.
Passing Anthropic's number straight through therefore under-reports input by exactly the cached
total — and on a subscription run, where cache reads are most of the prompt, the subtraction goes
negative and lands at zero.

Isolated with three probes rather than reasoned about:

| Probe | Sent | Stored `input` |
|---|---|---|
| A | `input=1111`, no cache keys | `1111` |
| B | `input=1111`, `cache_read=333`, `cache_creation=444` | `334` |
| C | `input=1888` (= 1111+333+444), same cache keys | `1111`, `total=2110` |

`334 = 1111 − 333 − 444` names the mechanism, and probe C confirms the fix: `generation_attributes`
now sends the whole prompt, so the collector's subtraction recovers the true uncached count and
the total is right. Regression tests
`test_input_tokens_are_converted_to_the_whole_prompt`,
`test_input_tokens_are_unchanged_when_nothing_was_cached` and
`test_absent_cache_counts_do_not_break_the_conversion` hold it.

This is the class of defect a green unit suite cannot catch: every attribute was emitted, the span
arrived, and the number was wrong.

### An unreachable collector, observed rather than asserted

During the probes Langfuse became briefly unresponsive under load and the exporter logged:

```
Failed to export span batch code: None, reason: HTTPConnectionPool(host='localhost', port=3000):
Read timed out. (read timeout=10.0)
```

The probe still ran to completion and exited normally. That is ADR-0046's second constraint
holding outside a test — telemetry that can abort a migration is worse than no telemetry.

## The containerised run — a real `design`, traced end to end

Released as `v0.1.2`, pinned by control-plane's `config/scenario_specialists.yaml`, installed into
its specialist image, and driven by a real Kafka drift event against the tenant repository. The
`design` phase ran for 432.3s and parked at its review gate. Spans read back from ClickHouse:

```
┌─name──────────────────────────┬─type───────┬─provided_model_name───────┬─tok_in─┬─tok_out─┬─cache_read─┬─cache_new─┬──secs─┐
│ cobol-modernizer.design       │ SPAN       │                           │      0 │       0 │          0 │         0 │ 428.6 │
│ call_model.spec_extractor     │ GENERATION │ claude-opus-5             │      2 │   12526 │          0 │     30673 │ 130.8 │
│ call_model.spec_critic        │ GENERATION │ claude-haiku-4-5-20251001 │     10 │   22472 │          0 │     32195 │ 236.9 │
│ call_model.solution_architect │ GENERATION │ claude-opus-5             │      2 │    5801 │       4418 │     18754 │  60.7 │
└───────────────────────────────┴────────────┴───────────────────────────┴────────┴─────────┴────────────┴───────────┴───────┘
```

All three nodes, one trace, correctly nested — every generation's `parent_span_id` is the design
span's `span_id` (`88be9cb7204ad5dd`), and the three durations sum to 428.4s against the root's
428.6s. Model routing is visible in the trace rather than only in a log line: `spec_critic` ran on
Haiku while the other two ran on Opus.

**`stdout` stayed clean, proven by the caller rather than by inspection.** Control-plane parsed the
`--json` contract and reported *"Design complete for 1 program(s); 11 gate item(s) for review."* A
single stray byte on stdout would have failed that parse. This is ADR-0046's first constraint,
verified under the condition it was written for.

**One deployment finding, not a code one.** Exporting to the collector through Docker Desktop's
published port from inside a container fails: TCP connects and the server closes without a
response (`RemoteDisconnected`), while the same host gateway carries Kafka on another port
perfectly. Container-to-container on the collector's own network answers 200 every time. The run
above exports over that path. Nothing in this repo changed for it — the endpoint is a variable —
but an operator who points a container at `host.docker.internal` and sees silence should look here
first. The run itself was unaffected while the export was failing, which is the second constraint
holding outside a test.

## Not yet verified

- **`TRACEPARENT`'s other end.** The root span joins a parent trace when the environment carries
  one, and every test covers that half. Nothing sets it yet: control-plane's specialist invocation
  does not export it, so the trace above is the specialist's own rather than part of the caller's.
  Stated in ADR-0060 § 3 rather than left to be discovered.
- **`generate`.** Every model call in both halves goes through `call_model`, so the instrumentation
  covers it by construction — but no traced `generate` run has been executed. Per this repo's own
  rule, that is *"covered for `design`"*, not *"covered"*.
