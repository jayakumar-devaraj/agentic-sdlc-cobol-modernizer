"""Tests for telemetry/tracing.py -- ADR-0046's three non-negotiable properties.

Those properties are what this module is *for*, so they are what is asserted here: `stdout` stays
clean, an unreachable collector never fails a run, and whether tracing is on is decided by
configuration rather than by anything observed at runtime.

Spans are read back through an `InMemorySpanExporter` attached to the provider `configure_tracing`
actually built, rather than through a provider the test constructs itself. That distinction
matters: the thing most likely to be wrong here is the construction, and a test that builds its
own provider asserts the SDK works rather than that this module uses it correctly.
"""

from __future__ import annotations

import sys

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from cobol_modernizer.telemetry import tracing

#: A port nothing listens on. Every test that configures tracing exports here, which is the point:
#: the collector being unreachable must be indistinguishable, from the run's side, from it working.
UNREACHABLE_ENDPOINT = "http://127.0.0.1:1/v1/traces"


@pytest.fixture
def traced(monkeypatch: pytest.MonkeyPatch):
    """Tracing configured against an unreachable collector, with spans captured in memory."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", UNREACHABLE_ENDPOINT)
    # Without this the exporter retries the dead endpoint with backoff on every shutdown, which
    # cost this file 75 seconds before it was set. The retrying itself is correct behaviour and is
    # what `test_spans_are_recorded_with_an_unreachable_collector` relies on; only its patience
    # needs bounding in a test.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "1")
    monkeypatch.delenv("TRACEPARENT", raising=False)
    tracing.shutdown_tracing()

    assert tracing.configure_tracing() is True
    exporter = InMemorySpanExporter()
    tracing._provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter
    tracing.shutdown_tracing()


@pytest.fixture(autouse=True)
def _leave_tracing_off():
    """No test may leak a live tracer into the next one."""
    yield
    tracing.shutdown_tracing()


# --- configuration decides, nothing else -----------------------------------------------


def test_tracing_is_off_when_no_endpoint_is_configured(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)

    assert tracing.is_configured() is False
    assert tracing.configure_tracing() is False
    assert tracing.is_enabled() is False


def test_either_standard_endpoint_variable_turns_tracing_on(monkeypatch: pytest.MonkeyPatch):
    """A deployment that set only the signal-specific variable would call silence a bug."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", UNREACHABLE_ENDPOINT)
    assert tracing.is_configured() is True

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:1")
    assert tracing.is_configured() is True


def test_spans_are_recorded_with_an_unreachable_collector(traced: InMemorySpanExporter):
    """The collector is down for every test in this file. Recording must not care.

    Export happens on a background batch; a run that stalled or failed because nothing was
    listening would be the failure ADR-0046 calls worse than no telemetry at all.
    """
    with tracing.span("unit.work", {"kind": "test"}):
        pass

    spans = traced.get_finished_spans()
    assert [s.name for s in spans] == ["unit.work"]
    assert spans[0].attributes["kind"] == "test"


def test_a_run_continues_when_the_provider_cannot_be_built(monkeypatch: pytest.MonkeyPatch):
    """A broken exporter is a degraded run, never a failed one."""
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", UNREACHABLE_ENDPOINT)
    monkeypatch.setattr(
        "opentelemetry.sdk.trace.TracerProvider",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no provider for you")),
    )

    assert tracing.configure_tracing() is False
    assert tracing.is_enabled() is False
    with tracing.span("still.works") as handle:  # must not raise
        handle.set({"a": 1})


# --- stdout stays clean ----------------------------------------------------------------


def test_nothing_is_written_to_stdout(traced: InMemorySpanExporter, capsys: pytest.CaptureFixture):
    """The `--json` contract is one object on stdout. An exporter must never share it.

    Asserted across configure, a span, and the flush on shutdown, because each of the three is a
    place a console exporter or a stray print would plausibly be added later.
    """
    with tracing.span("unit.work"):
        pass
    tracing.shutdown_tracing()

    assert capsys.readouterr().out == ""


# --- the module observes a run, it never alters it -------------------------------------


def test_an_exception_propagates_unchanged_and_is_recorded(traced: InMemorySpanExporter):
    with pytest.raises(ValueError, match="boom"), tracing.span("unit.failing"):
        raise ValueError("boom")

    span = traced.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert [event.name for event in span.events] == ["exception"]


def test_an_exception_propagates_unchanged_when_tracing_is_off(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)

    with pytest.raises(ValueError, match="boom"), tracing.span("unit.failing"):
        raise ValueError("boom")


def test_shutdown_is_idempotent_and_never_raises():
    tracing.shutdown_tracing()
    tracing.shutdown_tracing()


# --- the process boundary --------------------------------------------------------------


def test_a_root_span_joins_a_traceparent_handed_in_by_a_parent(
    traced: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    """This CLI is normally a subprocess. A trace that restarted here would answer nothing.

    The parent id is the one the control plane would export in the environment; the assertion is
    that this process's root span lands in that same trace rather than starting its own.
    """
    parent_trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    monkeypatch.setenv(
        "TRACEPARENT", f"00-{parent_trace_id}-00f067aa0ba902b7-01"
    )

    with tracing.span("cobol-modernizer.design", root=True):
        pass

    span = traced.get_finished_spans()[0]
    assert format(span.context.trace_id, "032x") == parent_trace_id
    assert format(span.parent.span_id, "016x") == "00f067aa0ba902b7"


def test_an_unusable_traceparent_is_ignored_rather_than_fatal(
    traced: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TRACEPARENT", "this is not a traceparent")

    with tracing.span("cobol-modernizer.design", root=True):
        pass

    assert traced.get_finished_spans()[0].name == "cobol-modernizer.design"


def test_without_a_traceparent_the_cli_span_is_a_root(
    traced: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("TRACEPARENT", raising=False)

    with tracing.span("cobol-modernizer.design", root=True):
        pass

    assert traced.get_finished_spans()[0].parent is None


# --- what a model-call span carries ----------------------------------------------------


def _attributes(**overrides):
    base = {
        "model": "claude-opus-5",
        "backend": "claude_cli",
        "attempts": 2,
        "duration_ms": 135_000,
        "session_id": "abc-123",
        "input_tokens": 36_320,
        "output_tokens": 4_096,
        "cache_read_input_tokens": 32_014,
        "cache_creation_input_tokens": 11_667,
        "notional_cost_usd": 0.054,
        "prompt": [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}],
        "completion": "the answer",
    }
    base.update(overrides)
    return tracing.generation_attributes(**base)


def test_every_token_field_reaches_the_span():
    """All four, not just input and output. The cache fields are most of a subscription run."""
    attributes = _attributes()

    assert attributes["gen_ai.usage.output_tokens"] == 4_096
    assert attributes["gen_ai.usage.cache_read_input_tokens"] == 32_014
    assert attributes["gen_ai.usage.cache_creation_input_tokens"] == 11_667


def test_input_tokens_are_converted_to_the_whole_prompt():
    """The two conventions are inverted, and the mismatch is silent.

    Anthropic's `input_tokens` excludes cache; OpenTelemetry's is the whole prompt, from which a
    collector subtracts the cache counters to recover the uncached part. Measured against a real
    collector: 1111 sent with cache 333/444 was stored as 334. The first real span this module
    emitted reported 36,320 input tokens as 0, which is the same subtraction going negative.
    """
    attributes = _attributes()

    assert attributes["gen_ai.usage.input_tokens"] == 36_320 + 32_014 + 11_667


def test_input_tokens_are_unchanged_when_nothing_was_cached():
    attributes = _attributes(cache_read_input_tokens=0, cache_creation_input_tokens=0)

    assert attributes["gen_ai.usage.input_tokens"] == 36_320


def test_absent_cache_counts_do_not_break_the_conversion():
    attributes = tracing.generation_attributes(model="m", input_tokens=500)

    assert attributes["gen_ai.usage.input_tokens"] == 500


def test_the_call_context_reaches_the_span():
    attributes = _attributes()

    assert attributes["gen_ai.request.model"] == "claude-opus-5"
    assert attributes["gen_ai.backend"] == "claude_cli"
    assert attributes["gen_ai.request.attempts"] == 2
    assert attributes["gen_ai.response.duration_ms"] == 135_000
    assert attributes["gen_ai.conversation.id"] == "abc-123"


def test_prompt_and_completion_are_captured_by_default():
    """ADR-0046 refused a SaaS backend precisely so these could be recorded."""
    attributes = _attributes()

    assert "sys" in attributes["langfuse.observation.input"]
    assert attributes["langfuse.observation.output"] == "the answer"


def test_content_capture_can_be_turned_off_without_losing_the_rest(
    monkeypatch: pytest.MonkeyPatch,
):
    """A deployment exporting somewhere less trusted drops the prompts, not the whole span."""
    monkeypatch.setenv("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false")
    attributes = _attributes()

    assert "langfuse.observation.input" not in attributes
    assert "langfuse.observation.output" not in attributes
    assert attributes["gen_ai.usage.input_tokens"] == 36_320 + 32_014 + 11_667
    assert attributes["gen_ai.request.model"] == "claude-opus-5"


def test_absent_optional_fields_are_omitted_rather_than_sent_as_none():
    """The SDK rejects a None attribute value; an absent session id must not cost the span."""
    attributes = tracing.generation_attributes(model="claude-sonnet-5")

    assert attributes["gen_ai.request.model"] == "claude-sonnet-5"
    assert "gen_ai.conversation.id" not in attributes
    assert "gen_ai.usage.input_tokens" not in attributes
    assert all(value is not None for value in attributes.values())


def test_attributes_are_all_types_the_sdk_accepts(traced: InMemorySpanExporter):
    """The assertion the unit checks above cannot make: that the SDK actually takes these.

    A dict or a None slipped into an attribute value is dropped by the SDK with a warning, which
    is exactly the kind of silent partial success this platform keeps rediscovering.
    """
    attributes = _attributes()

    with tracing.span("call_model.spec_extractor", attributes):
        pass

    recorded = traced.get_finished_spans()[0].attributes
    assert set(attributes) == set(recorded)
    assert recorded["gen_ai.usage.input_tokens"] == 36_320 + 32_014 + 11_667


def test_stdout_is_untouched_by_a_full_generation_span(
    traced: InMemorySpanExporter, capsys: pytest.CaptureFixture
):
    with tracing.span("call_model.spec_extractor", _attributes()):
        sys.stderr.write("diagnostics belong here\n")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "diagnostics belong here" in captured.err
