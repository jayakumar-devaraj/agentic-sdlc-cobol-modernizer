"""OpenTelemetry spans for one CLI invocation and every model call inside it (ADR-0046).

**What this closes.** Correlation already existed: `run_id` flows through both subcommands and
every log record carries it (ADR-0018, and `logging_config` beside this file). What did not exist
was a way to follow one request *across a process boundary*, or to see what a model call actually
sent, received and cost without reading stderr and joining it by hand.

**Why the instrumentation point is `call_model` and not the graph.** Only half this repo is a
LangGraph run: `graph/design_graph.py` is a real `StateGraph`, `graph/generate_pipeline.py` is
plain Python. Graph-level auto-instrumentation covers `design` and gives nothing for `generate` -
the half carrying the self-healing loop and the compile retries. Every model call in both halves
goes through `core/model_client.call_model`, so one span there covers 100% of them. ADR-0046
argues this at length; it is repeated here because it is the reason this module is shaped the way
it is rather than being a `langgraph` callback.

**Vendor-neutral, deliberately.** Almost nothing here names a backend. Configuration is the
standard `OTEL_*` environment, so where this repo exports to is a deployment decision and swapping
it costs a variable rather than an instrumentation rewrite - which is what ADR-0046 traded for
when it chose an OTel-compatible collector over a proprietary SDK.

Three properties are not negotiable, and each one is a rule this platform has already broken once:

1. **`stdout` stays clean.** The `--json` contract is one object on stdout and nothing else. No
   exporter here writes to stdout - the OTLP exporter writes to a socket, and every diagnostic in
   this module goes through `logging`, which `logging_config` pins to stderr.
2. **An unreachable collector must never fail a run.** Telemetry that can abort a migration is
   worse than no telemetry. Every public function here swallows its own failures and degrades to
   doing nothing.
3. **Whether tracing is on is decided by configuration, never by a probe.** An earlier local
   harness gated on a client health check; one transient timeout at startup disabled tracing for
   the whole process, silently, and a complete six-minute run produced zero spans with valid
   credentials. Configured means "an endpoint was named", and nothing observed at runtime may
   revoke that.

**Spans must flush before this process exits.** This repo is a subprocess that runs for minutes
and then returns - there is no long-lived service to drain a queue in the background. `main` calls
`shutdown_tracing` in a `finally`, and an `atexit` hook backstops any other entry path.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

#: The service this repo reports itself as, unless the deployment overrides it the standard way.
DEFAULT_SERVICE_NAME = "cobol-modernizer"

#: Either of the two standard endpoint variables turns tracing on. Both are checked because the
#: signal-specific one legitimately appears without the general one, and a deployment that set
#: only `..._TRACES_ENDPOINT` and got silence would have every reason to call that a bug.
_ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")

#: Standard OTel switch for whether prompt and completion text may be recorded. Default **on**:
#: ADR-0046 refused a hosted SaaS backend precisely so this repo could record the prompts, which
#: carry the tenant's proprietary source. A deployment exporting somewhere less trusted turns this
#: off rather than losing the rest of the span.
_CAPTURE_CONTENT_VAR = "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"

#: W3C traceparent handed in by a parent process. `cobol-modernizer` is normally a subprocess of
#: the control plane, and a trace that restarted at this process boundary would answer none of the
#: cross-process questions tracing exists for. Read from the environment because that is the only
#: channel a `subprocess.run` caller and this CLI already share.
_TRACEPARENT_VAR = "TRACEPARENT"

_provider: Any = None
_tracer: Any = None


def _truthy(value: str | None, *, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def capture_content() -> bool:
    """Whether prompt and completion text may be attached to spans."""
    return _truthy(os.environ.get(_CAPTURE_CONTENT_VAR), default=True)


def is_configured() -> bool:
    """Whether a collector endpoint was named. Configuration only - never a live probe."""
    return any(os.environ.get(var, "").strip() for var in _ENDPOINT_VARS)


def is_enabled() -> bool:
    """Whether spans are actually being recorded right now."""
    return _tracer is not None


def configure_tracing(service_name: str | None = None) -> bool:
    """Build the tracer provider, if and only if an endpoint is configured.

    Returns whether tracing is on, so a caller can log it once - a run that silently produced no
    spans is the failure mode this platform has spent the most time misdiagnosing, and one line at
    startup is what distinguishes "off" from "broken".

    Never raises. A missing dependency, an unparseable endpoint, or an exporter that will not
    construct all mean the same thing: the run continues, untraced.
    """
    global _provider, _tracer

    if _tracer is not None:
        return True
    if not is_configured():
        logger.debug("Tracing off: neither %s is set", " nor ".join(_ENDPOINT_VARS))
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        name = service_name or os.environ.get("OTEL_SERVICE_NAME") or DEFAULT_SERVICE_NAME
        provider = TracerProvider(resource=Resource.create({"service.name": name}))
        # The exporter reads `OTEL_EXPORTER_OTLP_*` itself, endpoint and headers alike. Passing it
        # nothing is what keeps this vendor-neutral: there is no place here for a backend's name.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        # Published globally so any third-party instrumentation in this process joins the same
        # trace, but never depended on: OpenTelemetry allows the global provider to be set once
        # and silently keeps the first, so a second call here would leave `_tracer` pointing at
        # a provider `shutdown_tracing` does not hold. The tracer therefore comes from the
        # provider this function built.
        trace.set_tracer_provider(provider)

        _provider = provider
        _tracer = provider.get_tracer(DEFAULT_SERVICE_NAME)
        atexit.register(shutdown_tracing)
        endpoint = next(os.environ[var] for var in _ENDPOINT_VARS if os.environ.get(var, "").strip())
        logger.info("Tracing on: exporting spans to %s as service %s", endpoint, name)
        return True
    except Exception as exc:  # noqa: BLE001 - see this module's rule 2
        logger.warning(
            "Tracing could not be configured (%s: %s); the run continues untraced",
            type(exc).__name__,
            exc,
        )
        _provider = None
        _tracer = None
        return False


def shutdown_tracing() -> None:
    """Flush pending spans. Idempotent, bounded by the SDK's own timeout, never raises.

    A subprocess that exits with spans still batched has, from the collector's point of view, not
    run at all - which is why this is called from a `finally` rather than left to interpreter
    teardown.
    """
    global _provider, _tracer
    provider, _provider, _tracer = _provider, None, None
    if provider is None:
        return
    try:
        provider.shutdown()
    except Exception as exc:  # noqa: BLE001 - see this module's rule 2
        logger.warning("Tracing shutdown failed (%s: %s)", type(exc).__name__, exc)


def _parent_context() -> Any:
    """The caller's trace context, if this process was handed one.

    Returns None when there is no parent, which makes the CLI span a root - the correct shape for
    a direct invocation, and the one every test sees.
    """
    traceparent = os.environ.get(_TRACEPARENT_VAR, "").strip()
    if not traceparent:
        return None
    try:
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

        carrier = {"traceparent": traceparent}
        tracestate = os.environ.get("TRACESTATE", "").strip()
        if tracestate:
            carrier["tracestate"] = tracestate
        return TraceContextTextMapPropagator().extract(carrier=carrier)
    except Exception as exc:  # noqa: BLE001 - see this module's rule 2
        logger.debug("Ignoring unusable %s (%s: %s)", _TRACEPARENT_VAR, type(exc).__name__, exc)
        return None


class _NoOpSpan:
    """What every span-taking caller gets when tracing is off.

    A real object rather than `None`, so call sites carry no `if span is not None` - the branch
    that eventually gets forgotten at one of them.
    """

    def set(self, attributes: Mapping[str, Any]) -> None:
        return None

    def record_error(self, exc: BaseException) -> None:
        return None


class _RecordingSpan:
    """A live span, with every failure of its own contained."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set(self, attributes: Mapping[str, Any]) -> None:
        try:
            for key, value in attributes.items():
                if value is not None:
                    self._span.set_attribute(key, value)
        except Exception:
            logger.debug("Could not set span attributes", exc_info=True)

    def record_error(self, exc: BaseException) -> None:
        try:
            from opentelemetry.trace import Status, StatusCode

            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, f"{type(exc).__name__}: {exc}"))
        except Exception:
            logger.debug("Could not record span error", exc_info=True)


@contextmanager
def span(
    name: str, attributes: Mapping[str, Any] | None = None, *, root: bool = False
) -> Iterator[Any]:
    """One span around the block, or nothing at all when tracing is off.

    `root=True` attaches to a `TRACEPARENT` handed in by a parent process, when there is one.

    Exceptions are recorded on the span and re-raised unchanged: this module observes a run, it
    never alters its control flow.
    """
    if _tracer is None:
        yield _NoOpSpan()
        return

    context = _parent_context() if root else None
    try:
        # The SDK's own exception handling is turned off because this module does it below, and
        # leaving both on records the same exception twice - caught by a test asserting one
        # `exception` event and finding two. Ours is kept rather than the SDK's because it puts
        # the exception type into the status message, which is what a reader of a failed span
        # actually wants.
        started = _tracer.start_as_current_span(
            name, context=context, record_exception=False, set_status_on_exception=False
        )
    except Exception:
        logger.debug("Could not start span %s", name, exc_info=True)
        yield _NoOpSpan()
        return

    with started as raw:
        handle = _RecordingSpan(raw)
        if attributes:
            handle.set(attributes)
        try:
            yield handle
        except BaseException as exc:
            handle.record_error(exc)
            raise


def _json_or_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - an unserialisable value is still worth its repr
        return str(value)


def generation_attributes(
    *,
    model: str,
    backend: str | None = None,
    attempts: int | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_input_tokens: int | None = None,
    cache_creation_input_tokens: int | None = None,
    notional_cost_usd: float | None = None,
    prompt: Any = None,
    completion: Any = None,
) -> dict[str, Any]:
    """Attributes for one model call, in OpenTelemetry's GenAI conventions.

    Kept here rather than at the call site so the convention is spelled out once. `model_client`
    knows what a model call cost; which attribute names carry that to a collector is this module's
    problem, and a second copy of these strings is a second thing to get subtly wrong.

    The three `langfuse.*` keys are the one concession to a specific renderer, and they are
    additive: a collector that does not know them ignores three strings and still receives every
    `gen_ai.*` attribute above. That is a far smaller price than the alternative ADR-0046 rejected,
    where the whole payload shape would have been proprietary.
    """
    attributes: dict[str, Any] = {
        "gen_ai.system": "anthropic",
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": model,
        "gen_ai.response.model": model,
        "langfuse.observation.type": "generation",
    }
    numeric = {
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        "gen_ai.usage.cache_read_input_tokens": cache_read_input_tokens,
        "gen_ai.usage.cache_creation_input_tokens": cache_creation_input_tokens,
        "gen_ai.request.attempts": attempts,
        "gen_ai.response.duration_ms": duration_ms,
        # Notional on a subscription backend - nobody is billed per call. Named so that nobody
        # reads a dashboard total as an invoice.
        "gen_ai.usage.notional_cost_usd": notional_cost_usd,
    }
    attributes.update({key: value for key, value in numeric.items() if value is not None})
    if backend is not None:
        attributes["gen_ai.backend"] = backend
    if session_id is not None:
        attributes["gen_ai.conversation.id"] = session_id

    if capture_content():
        if prompt is not None:
            attributes["langfuse.observation.input"] = _json_or_text(prompt)
        if completion is not None:
            attributes["langfuse.observation.output"] = _json_or_text(completion)
    return attributes
