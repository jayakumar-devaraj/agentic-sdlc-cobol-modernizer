# ADR-0018: Correlate every log record, and carry what a run cost in the contract

## Status

Accepted (2026-08-08). Closes Open Issue 7 and the architecture audit's gap G2. Completes what
ADR-0013 anticipated but deliberately left unwired, and unblocks control-plane's Track P3
(token/cost instrumentation), which has nothing to consume without it.

## Context

Two gaps, found by auditing what the code actually does rather than what the plan claimed.

**Only `cli.py` knew the run id.** `run_id` was resolved at `cli.py:81` and interpolated by hand
into exactly three log lines. A grep for it across `src/` returned hits in `cli.py` and
`contracts.py` and nowhere else — so the lines that matter most for diagnosis carried no
correlation at all:

```
model call node=spec_extractor backend=claude_cli model=claude-opus-5 attempts=1
  input_tokens=36320 output_tokens=13000 ... notional_cost_usd=0.507
```

With `MAX_CONCURRENT_PROGRAMS = 4` branches interleaving on a real thread pool (ADR-0012), that
line cannot be tied to a run, and cannot be tied to the *program* that caused it either. Four
extractor calls in one run are indistinguishable in the log.

**The contract could not answer "what did this cost?"** `ModelCallResult` has carried
`input_tokens`, `output_tokens`, both cache counters and `notional_cost_usd` since ADR-0013, whose
Consequences explicitly noted they existed for a future `DesignDocument` field. Every caller threw
them away: the nodes' `_default_*` functions return `.text` and drop the rest. Meanwhile
`DesignDocument` had five fields, none of them cost. Control-plane's reviewing gate — the whole
reason `design.json` is a contract rather than an internal artifact — could see 52 gate items and
7 domain entities and not what the run consumed to produce them.

The two are one problem. Cost without correlation is a number nobody can attribute; per-tenant
attribution, which the multi-enterprise audit flagged as unowned platform-wide, needs both.

## Decision

**1. `run_id` is bound to a `ContextVar` and stamped onto every record by a handler-level filter.**
`telemetry/logging_config.py` gains `bind_run_id`, `current_run_id`, and `RunIdFilter`;
`_LOG_FORMAT` gains `run_id=%(run_id)s`. `cli.py` binds once, immediately after resolving the id
and before `run_design`, then drops its three hand-interpolations as redundant.

A **filter on the handler** rather than a `LoggerAdapter` or a custom `Logger`: adapters must be
threaded to each call site, and every module here already holds a plain
`logging.getLogger(__name__)`. Installing the filter once means existing call sites gain
correlation with no edit — which is the point, because the uncorrelated lines were in
`model_client` and the nodes, not in `cli.py`.

A **`ContextVar` and not a module global** because `design` fans out on a real
`ThreadPoolExecutor`: `contextvars` copies the calling context into each worker, so every branch
inherits the binding without it being threaded through node signatures or graph state.

Records emitted before binding render `run_id=-` rather than raising in the formatter. A missing
correlation id must never be the reason a log line is lost.

**2. `DesignDocument` gains `cost: RunCost | None`, summed from real `ModelCallResult`s.**
`model_client.collect_usage()` is a scoped context manager yielding a `UsageAccumulator`;
`run_design` binds one around `app.invoke` and reads the totals afterwards.

**The accumulator is a mutable object behind the `ContextVar`, and that is the load-bearing
detail.** `contextvars` copies the *binding* into each worker, not the object — so a parent that
binds one accumulator before fan-out and children that call `record()` all mutate the same
instance, and the parent sees the totals. Had this been a `ContextVar[int]` of running totals,
every child would have incremented a private copy and the parent would have read zero from the
branch calls. That failure is **invisible to a single-threaded test**, which is why the test
asserts totals after a real concurrent run and separately asserts more than one thread was used.
The `Lock` is not decorative either: `+=` on an int field is a read-modify-write, and four
branches finishing together would lose updates.

`collect_usage` is **scoped rather than global** so two graphs in one process, or two tests, cannot
bleed totals into each other. Outside the scope `call_model` records nothing, keeping the
accounting opt-in.

**3. `RunCost` carries actuals only, and says when the dollar figure is partial.** It is
deliberately not merged with `RoutingDecision.estimated_cost_usd`, which is what *selection
predicted* from a measured token profile before any call ran. Both are worth having and the gap
between them is worth surfacing — but one field holding either would be unfalsifiable. Estimated
cost is **named as a follow-up, not half-built**.

`notional_cost_usd` is `None` when no backend reported one and a **partial** sum when
`calls_without_reported_cost > 0`. The SDK backend reports no cost by design (ADR-0013 keeps no
rate card here so it cannot go stale), so on that path the token counts are the real signal and the
dollar figure is *absent rather than wrong*. On a subscription the CLI's own `total_cost_usd` is
notional in the first place: what the call would cost at API rates, not what anyone was billed.

## Consequences

**A real bug surfaced in the test suite and was fixed as a guard, not a workaround.**
`bind_run_id` mutates the ambient context and never restores it — correct for a CLI process that
binds once and exits, wrong under pytest where every test shares one context. A test asserting the
unbound placeholder passed alone and failed in suite order. `tests/conftest.py` now resets the
binding per test, autouse and unconditional, for the same reason `pin_model_backend` is: an opt-in
guard only protects the tests that remembered to ask, and order-dependent failures are the worst
kind to debug.

**`telemetry/logging_config.py` had 100% coverage and no tests.** Every line ran because
`cli.main()` calls `configure_logging`, so coverage reported the module exercised while nothing
asserted what the logging *did*. `tests/system/test_logging_config.py` now exists. Worth carrying
forward: coverage measures execution, not verification, and a module can be fully covered and
completely unverified.

**`cost` is optional on the contract, and that is a real seam.** A `DesignDocument` built outside a
`collect_usage` scope — by a test constructing one directly — has `cost=None`. Making it required
would have forced every such construction through an accumulator for no benefit. The cost of the
choice is that a consumer must handle `None`, so the schema says so rather than implying a
guarantee the type cannot make.

**Cost is reported but not enforced.** Nothing budgets, caps, or fails a run for being expensive,
and `DesignCliResult` — the `--json` stdout summary — still does not carry cost, only
`gate_item_count`. Surfacing a number for a human gate to read is a different thing from
governing spend, and per ADR-0008 whether a figure should pause anything is control-plane's policy,
not this repo's. Track P3 is where enforcement belongs.

**Per-tenant attribution is now possible and still absent.** This makes a run's cost attributable
to a `run_id`; it does not make it attributable to a *tenant*, because no tenant identifier exists
anywhere in this repo or in control-plane. The audit records that as gap G8. The concrete next step
is a `--tenant-id` flag echoed into `DesignCliResult` and every log line — small, and deliberately
not bundled here, since it is only useful once Track P1 has something to route.
