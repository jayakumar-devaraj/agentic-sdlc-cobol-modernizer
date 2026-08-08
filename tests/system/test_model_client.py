"""Tests for `core/model_client.py` -- backend selection, resilience, and usage capture (ADR-0013).

The `claude_cli` backend is exercised by faking `subprocess.run`, which keeps the real envelope
parsing, error classification, retry loop, and temp-file prompt handling in the test while the
child process is not. One test at the bottom does hit the real `claude` CLI, marked
`live_claude_cli` and skipped unless explicitly opted in -- see `tests/conftest.py` for why that
opt-in is mandatory rather than polite.

Backoff is patched to a no-op in the retry tests. Real jittered sleeps would add seconds of dead
wall time to every run and make the suite's duration depend on a random number, which is a bad
trade for testing a policy that is verified by call count instead.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import anthropic
import httpx
import pytest

from cobol_modernizer.core import model_client
from cobol_modernizer.core.model_client import (
    BACKEND_ENV_VAR,
    MAX_ATTEMPTS,
    ModelCallError,
    call_model,
    resolve_backend,
)

SUCCESS_ENVELOPE = {
    "type": "result",
    "is_error": False,
    "result": "the narration",
    "duration_ms": 3321,
    "session_id": "sess-1",
    "total_cost_usd": 0.0207,
    "usage": {
        "input_tokens": 10,
        "output_tokens": 104,
        "cache_creation_input_tokens": 9819,
        "cache_read_input_tokens": 0,
    },
}


def completed(stdout: str, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def no_backoff(monkeypatch):
    """Skip the real jittered sleep; the retry policy is asserted by call count, not by duration."""
    monkeypatch.setattr(model_client, "_sleep_for_attempt", lambda attempt: 0.0)


@pytest.fixture
def cli_backend(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV_VAR, "claude_cli")
    monkeypatch.setattr(model_client.shutil, "which", lambda name: r"C:\fake\claude.exe")


# --- Backend selection ---------------------------------------------------------------------------


def test_backend_defaults_to_claude_cli_when_unset(monkeypatch):
    # The default matters: it is what a real invocation with no configuration will do.
    monkeypatch.delenv(BACKEND_ENV_VAR, raising=False)
    assert resolve_backend() == "claude_cli"


def test_explicit_backend_beats_the_environment(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV_VAR, "claude_cli")
    assert resolve_backend("anthropic_sdk") == "anthropic_sdk"


def test_an_unknown_backend_fails_loudly_rather_than_falling_back(monkeypatch):
    # Silently falling back to a default would mean a typo in deployment config sends traffic to a
    # different provider than intended -- exactly the class of quiet wrong answer this repo rejects.
    monkeypatch.setenv(BACKEND_ENV_VAR, "openai")
    with pytest.raises(ModelCallError, match="must be 'claude_cli' or 'anthropic_sdk'"):
        resolve_backend()


# --- claude CLI backend: the happy path ------------------------------------------------------------


def test_cli_success_returns_text_and_captures_real_usage(cli_backend, monkeypatch):
    monkeypatch.setattr(
        model_client.subprocess, "run", lambda *a, **k: completed(json.dumps(SUCCESS_ENVELOPE))
    )
    result = call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")

    assert result.text == "the narration"
    assert result.backend == "claude_cli"
    assert result.attempts == 1
    assert (result.input_tokens, result.output_tokens) == (10, 104)
    assert result.cache_creation_input_tokens == 9819
    assert result.notional_cost_usd == 0.0207
    assert result.session_id == "sess-1"


def test_cli_sends_the_prompt_on_stdin_not_argv(cli_backend, monkeypatch):
    # A real prompt is Known Facts plus every wrapped COBOL source unit -- tens of KB, past the
    # ~32 KB Windows argv limit. If this ever regresses to argv, CBACT04C fails and CBCUS01C
    # doesn't, which is a maddening bug to chase. Pin it here instead.
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return completed(json.dumps(SUCCESS_ENVELOPE))

    monkeypatch.setattr(model_client.subprocess, "run", fake_run)
    huge_user_turn = "X" * 100_000
    call_model("spec_extractor", "claude-opus-5", "SYSTEM", huge_user_turn)

    assert captured["input"] == huge_user_turn
    assert huge_user_turn not in " ".join(captured["argv"])
    assert "--output-format" in captured["argv"]
    assert "json" in captured["argv"]


def test_cli_passes_the_system_prompt_as_a_file_that_is_cleaned_up(cli_backend, monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        path = Path(argv[argv.index("--system-prompt-file") + 1])
        seen["path"] = path
        seen["contents"] = path.read_text(encoding="utf-8")
        return completed(json.dumps(SUCCESS_ENVELOPE))

    monkeypatch.setattr(model_client.subprocess, "run", fake_run)
    call_model("spec_critic", "claude-haiku-4-5-20251001", "REAL SYSTEM PROMPT", "USER")

    assert seen["contents"] == "REAL SYSTEM PROMPT"
    # The temp file must not outlive the call -- one per model call across a four-program run.
    assert not seen["path"].exists()


def test_cli_disables_tools(cli_backend, monkeypatch):
    # This module wants a completion, not an agent loose in a real repository.
    captured = {}
    monkeypatch.setattr(
        model_client.subprocess,
        "run",
        lambda argv, **k: (captured.update(argv=argv), completed(json.dumps(SUCCESS_ENVELOPE)))[1],
    )
    call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")

    disallowed = captured["argv"][captured["argv"].index("--disallowed-tools") + 1]
    for tool in ("Bash", "Write", "Edit", "WebFetch"):
        assert tool in disallowed


# --- claude CLI backend: failures ------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 503])
def test_cli_retries_retryable_statuses_then_succeeds(cli_backend, monkeypatch, no_backoff, status):
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return completed(json.dumps({"is_error": True, "api_error_status": status}))
        return completed(json.dumps(SUCCESS_ENVELOPE))

    monkeypatch.setattr(model_client.subprocess, "run", fake_run)
    result = call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")

    assert calls["n"] == 3
    assert result.attempts == 3  # the reported attempt count is real, not always 1


def test_cli_gives_up_after_max_attempts(cli_backend, monkeypatch, no_backoff):
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        return completed(json.dumps({"is_error": True, "api_error_status": 429}))

    monkeypatch.setattr(model_client.subprocess, "run", fake_run)
    with pytest.raises(ModelCallError, match="after retries"):
        call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")

    assert calls["n"] == MAX_ATTEMPTS  # bounded, not infinite


def test_cli_does_not_retry_a_non_retryable_error(cli_backend, monkeypatch, no_backoff):
    # A 400 is our bug (a malformed request), not the provider's transient state. Retrying it
    # four more times just burns quota to get the same answer.
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        return completed(json.dumps({"is_error": True, "api_error_status": 400, "result": "bad request"}))

    monkeypatch.setattr(model_client.subprocess, "run", fake_run)
    with pytest.raises(ModelCallError, match="claude reported an error"):
        call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")

    assert calls["n"] == 1


def test_cli_retries_a_timeout(cli_backend, monkeypatch, no_backoff):
    calls = {"n": 0}

    def fake_run(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="claude", timeout=300)
        return completed(json.dumps(SUCCESS_ENVELOPE))

    monkeypatch.setattr(model_client.subprocess, "run", fake_run)
    assert call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER").attempts == 2


def test_cli_nonzero_exit_fails_loudly(cli_backend, monkeypatch):
    monkeypatch.setattr(
        model_client.subprocess, "run", lambda *a, **k: completed("", returncode=1, stderr="boom")
    )
    with pytest.raises(ModelCallError, match="claude exited 1"):
        call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")


def test_cli_unparseable_output_fails_loudly(cli_backend, monkeypatch):
    # Never guess at a partial response -- same posture as pic_mapper's refusal to guess a PIC.
    monkeypatch.setattr(model_client.subprocess, "run", lambda *a, **k: completed("not json"))
    with pytest.raises(ModelCallError, match="parseable JSON"):
        call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")


def test_a_missing_claude_executable_says_how_to_fix_it(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV_VAR, "claude_cli")
    monkeypatch.setattr(model_client.shutil, "which", lambda name: None)
    with pytest.raises(ModelCallError, match="anthropic_sdk"):
        call_model("spec_extractor", "claude-opus-5", "SYSTEM", "USER")


# --- Anthropic SDK backend ---------------------------------------------------------------------


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Usage:
    input_tokens = 100
    output_tokens = 20
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _Response:
    def __init__(self):
        self.content = [_Block("sdk narration")]
        self.usage = _Usage()


def make_sdk(monkeypatch, behaviour):
    class Messages:
        def create(self, **kwargs):
            return behaviour(kwargs)

    class Client:
        def __init__(self, *a, **k):
            self.init_kwargs = k
            self.messages = Messages()
            Client.last = self

    monkeypatch.setattr(anthropic, "Anthropic", Client)
    return Client


def test_sdk_success_captures_usage(monkeypatch):
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")
    make_sdk(monkeypatch, lambda kwargs: _Response())

    result = call_model("spec_critic", "claude-haiku-4-5-20251001", "SYSTEM", "USER")
    assert result.text == "sdk narration"
    assert result.backend == "anthropic_sdk"
    assert (result.input_tokens, result.output_tokens) == (100, 20)
    # No rate card is hardcoded here, so cost is honestly absent rather than invented.
    assert result.notional_cost_usd is None


def test_sdk_own_retries_are_disabled_so_the_two_policies_cannot_multiply(monkeypatch):
    # 5 attempts here x the SDK's own default 2 would be 10 real requests, and neither layer's
    # logs would show the true count.
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")
    client_cls = make_sdk(monkeypatch, lambda kwargs: _Response())
    call_model("spec_critic", "claude-haiku-4-5-20251001", "SYSTEM", "USER")
    assert client_cls.last.init_kwargs["max_retries"] == 0
    assert client_cls.last.init_kwargs["timeout"] == model_client.REQUEST_TIMEOUT_SECONDS


def test_sdk_rate_limit_is_retried(monkeypatch, no_backoff):
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")
    calls = {"n": 0}

    def behaviour(kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise anthropic.RateLimitError(
                "rate limited",
                response=httpx.Response(429, request=httpx.Request("POST", "https://x")),
                body=None,
            )
        return _Response()

    make_sdk(monkeypatch, behaviour)
    assert call_model("spec_critic", "claude-haiku-4-5-20251001", "SYSTEM", "USER").attempts == 2


@pytest.mark.parametrize(
    "exception_factory",
    [
        pytest.param(
            lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")),
            id="timeout",
        ),
        pytest.param(
            lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")),
            id="connection",
        ),
        pytest.param(
            lambda: anthropic.InternalServerError(
                "boom",
                response=httpx.Response(503, request=httpx.Request("POST", "https://x")),
                body=None,
            ),
            id="5xx",
        ),
    ],
)
def test_sdk_transient_failures_are_retried(monkeypatch, no_backoff, exception_factory):
    # Each of these is the provider being temporarily unavailable, not a malformed request.
    # Classifying one of them as fatal would fail a whole four-program design run on a blip.
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")
    calls = {"n": 0}

    def behaviour(kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise exception_factory()
        return _Response()

    make_sdk(monkeypatch, behaviour)
    assert call_model("spec_critic", "claude-haiku-4-5-20251001", "SYSTEM", "USER").attempts == 2


def test_sdk_4xx_is_not_retried(monkeypatch, no_backoff):
    # A 400/404 is our request being wrong; retrying gets the same answer four more times.
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")
    calls = {"n": 0}

    def behaviour(kwargs):
        calls["n"] += 1
        raise anthropic.NotFoundError(
            "no such model",
            response=httpx.Response(404, request=httpx.Request("POST", "https://x")),
            body=None,
        )

    make_sdk(monkeypatch, behaviour)
    with pytest.raises(ModelCallError):
        call_model("spec_critic", "claude-haiku-4-5-20251001", "SYSTEM", "USER")
    assert calls["n"] == 1


# --- The backoff policy itself ---------------------------------------------------------------


def test_backoff_is_bounded_and_jittered(monkeypatch):
    # Patched out everywhere else, so tested directly here -- with time.sleep stubbed, because
    # exercising the real thing would have this one test sleep for minutes.
    slept: list[float] = []
    monkeypatch.setattr(model_client.time, "sleep", slept.append)

    # Two properties matter. It never exceeds the cap (a multi-minute sleep inside a bounded CLI
    # invocation is indistinguishable from a hang), and it is actually random -- concurrent
    # branches retrying in lockstep would re-collide on the same rate limit, which is the entire
    # reason for full jitter rather than fixed backoff.
    delays = [model_client._sleep_for_attempt(1) for _ in range(30)]
    assert all(0.0 <= d <= model_client.MAX_BACKOFF_SECONDS for d in delays)
    assert len(set(delays)) > 1, "backoff is not jittered"
    assert slept == delays, "the computed delay and the slept delay must be the same number"

    # A large attempt number must still be capped, not 2**20 seconds.
    assert all(model_client._sleep_for_attempt(20) <= model_client.MAX_BACKOFF_SECONDS for _ in range(5))


def test_sdk_non_retryable_error_is_wrapped_not_leaked(monkeypatch, no_backoff):
    # Callers should not have to know which backend produced a failure.
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")

    def behaviour(kwargs):
        raise ValueError("something structural")

    make_sdk(monkeypatch, behaviour)
    with pytest.raises(ModelCallError, match="ValueError: something structural"):
        call_model("spec_critic", "claude-haiku-4-5-20251001", "SYSTEM", "USER")


# --- The real thing, opt-in only -----------------------------------------------------------------


@pytest.mark.live_claude_cli
def test_live_claude_cli_round_trip():
    """Hits the real `claude` CLI. Skipped unless COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1.

    Everything above is a fake wearing the CLI's shape; this is the only test that proves the
    shape is right -- that the flags are accepted, the envelope keys exist, and usage really comes
    back. Uses the cheapest routed model and a trivial prompt to keep the cost near zero.
    """
    result = call_model(
        "spec_critic",
        "claude-haiku-4-5-20251001",
        "You are a terse test responder. Reply with exactly the word OK and nothing else.",
        "Reply now.",
        backend="claude_cli",
    )
    assert "OK" in result.text
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert result.notional_cost_usd is not None
