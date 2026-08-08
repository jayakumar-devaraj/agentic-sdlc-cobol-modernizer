"""The one place this repo talks to a model: backend choice, resilience, and usage capture.

Before this module, each node held its own bare `anthropic.Anthropic()` call with no timeout, no
retry, and no record of what it cost -- three copies of the same gap (pillars 20, 24, 25 in the
platform plan's coverage matrix). Consolidating them here means the retry policy is written once
and every node inherits it, rather than three chances to get backoff subtly wrong.

**Two backends, one interface (ADR-0013).**

- `claude_cli` (default) shells out to the `claude` CLI in print mode. It authenticates from an
  existing Claude subscription, so no API credential is needed, and it is the same mechanism
  `agentic-sdlc-control-plane`'s own `coder` node uses to reach a model -- ADR-0001 describes this
  repo as shipping "the same shape as that repo's existing `coder.py` -> `claude` CLI call", so
  using it here makes the platform consistent rather than introducing a second convention.
- `anthropic_sdk` calls the Anthropic API directly. Kept because a subscription is the right tool
  for a developer verifying work on their own machine and the wrong one for a multi-tenant service
  billing other enterprises, which needs per-tenant quotas, isolation, and real cost attribution.
  See ADR-0013's Consequences for that boundary stated properly.

**On `notional_cost_usd`.** The CLI reports `total_cost_usd` per call. On a subscription that is an
equivalent-price estimate, not a charge -- the field is named `notional_` here so nothing downstream
mistakes it for billing data. The SDK backend leaves it `None` rather than computing a price from a
hardcoded rate card that would silently go stale.

**Prompt delivery.** The user turn goes over **stdin**, never argv: a real prompt is Known Facts
plus every wrapped COBOL source unit, which for `CBACT04C` alone is tens of kilobytes -- well past
the ~32 KB Windows command-line limit. Verified with a 40 KB payload before this module was written.
The system prompt goes to a temporary file via `--system-prompt-file` for the same reason; passing
it as argv would work at today's prompt sizes and quietly break as they grow.

**Tools are disabled.** This module wants a text completion, not an agent: the `claude` CLI would
otherwise carry tool definitions the model could act on, inside a process pointed at a real
repository. `--system-prompt-file` already replaces the CLI's default instructions, and
`_DISALLOWED_TOOLS` removes the capability as well, so neither layer is relied on alone.
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anthropic

logger = logging.getLogger(__name__)

Backend = Literal["claude_cli", "anthropic_sdk"]

#: Backend used when a caller does not pass one explicitly. A non-secret operational toggle, so
#: an environment variable is appropriate here -- unlike a credential, which ADR-0005 requires be
#: delivered as a mounted file path instead.
BACKEND_ENV_VAR = "COBOL_MODERNIZER_MODEL_BACKEND"
DEFAULT_BACKEND: Backend = "claude_cli"

REQUEST_TIMEOUT_SECONDS = float(os.getenv("COBOL_MODERNIZER_LLM_TIMEOUT", "300"))
MAX_ATTEMPTS = int(os.getenv("COBOL_MODERNIZER_LLM_MAX_ATTEMPTS", "5"))
MAX_BACKOFF_SECONDS = 30.0
MAX_OUTPUT_TOKENS = 4096

#: Every tool the CLI could otherwise expose. Listed explicitly rather than via a wildcard so a
#: newly-added tool is a visible omission in review, not silently permitted.
_DISALLOWED_TOOLS = (
    "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,NotebookEdit"
)


class ModelCallError(Exception):
    """A model call failed and is not worth retrying, or exhausted every attempt.

    Deliberately not a subclass of the SDK's exception types: callers (and `cli.py`'s boundary
    handler) should not have to know which backend produced the failure. `backend` and `attempts`
    are carried so a log line or gate item can say which path was taken and how hard it tried.
    """

    def __init__(self, backend: str, attempts: int, reason: str) -> None:
        self.backend = backend
        self.attempts = attempts
        super().__init__(f"model call failed via {backend} after {attempts} attempt(s): {reason}")


@dataclass(frozen=True)
class ModelCallResult:
    """One completed model call: its text, and what it cost to get.

    `text` is what every existing caller wants; the usage fields exist so a future
    `DesignDocument` field can carry per-run cost without another round of plumbing (see
    ADR-0013's Consequences -- deliberately not wired into the contract yet).
    """

    text: str
    model: str
    backend: Backend
    attempts: int
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    notional_cost_usd: float | None = None
    duration_ms: int | None = None
    session_id: str | None = None


def resolve_backend(explicit: Backend | None = None) -> Backend:
    """Pick the backend: an explicit argument, else the environment, else `claude_cli`."""
    if explicit is not None:
        return explicit
    configured = os.getenv(BACKEND_ENV_VAR)
    if configured is None:
        return DEFAULT_BACKEND
    if configured not in ("claude_cli", "anthropic_sdk"):
        raise ModelCallError(
            configured, 0, f"{BACKEND_ENV_VAR} must be 'claude_cli' or 'anthropic_sdk'"
        )
    return configured  # type: ignore[return-value]


def _sleep_for_attempt(attempt: int) -> float:
    """Exponential backoff with full jitter, capped. Returns the delay actually slept.

    Full jitter (`uniform(0, backoff)`) rather than fixed backoff plus a small random component:
    with several program branches running concurrently, a fixed schedule would have them all retry
    in step and re-collide on the same rate limit.
    """
    delay = random.uniform(0, min(2.0**attempt, MAX_BACKOFF_SECONDS))
    time.sleep(delay)
    return delay


# --- claude CLI backend ---------------------------------------------------------------------


def _claude_executable() -> str:
    path = shutil.which("claude")
    if path is None:
        raise ModelCallError(
            "claude_cli",
            0,
            "the 'claude' CLI is not on PATH; install it or set "
            f"{BACKEND_ENV_VAR}=anthropic_sdk",
        )
    return path


def _call_claude_cli(model: str, system_prompt: str, user_content: str) -> tuple[dict, int | None]:
    """One `claude -p` invocation. Returns (parsed result envelope, retryable HTTP status or None).

    A retryable status is returned rather than raised so the caller owns the retry loop -- keeping
    the backoff policy in one place for both backends.
    """
    executable = _claude_executable()

    # mkstemp, written and closed before the child starts: on Windows a still-open handle cannot
    # be reopened by another process, so a context-managed NamedTemporaryFile would fail here.
    descriptor, prompt_path_str = tempfile.mkstemp(suffix=".md", text=True)
    os.close(descriptor)
    prompt_file = Path(prompt_path_str)
    try:
        prompt_file.write_text(system_prompt, encoding="utf-8", newline="\n")
        completed = subprocess.run(
            [
                executable,
                "-p",
                "--output-format", "json",
                "--model", model,
                "--system-prompt-file", str(prompt_file),
                "--disallowed-tools", _DISALLOWED_TOOLS,
            ],
            input=user_content,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=REQUEST_TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        prompt_file.unlink(missing_ok=True)

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()[:500]
        raise ModelCallError("claude_cli", 1, f"claude exited {completed.returncode}: {stderr}")

    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ModelCallError(
            "claude_cli", 1, f"claude did not emit parseable JSON: {exc}"
        ) from None

    if envelope.get("is_error"):
        status = envelope.get("api_error_status")
        if isinstance(status, int) and (status == 429 or status >= 500):
            return envelope, status
        raise ModelCallError("claude_cli", 1, f"claude reported an error: {envelope.get('result')}")

    return envelope, None


def _result_from_cli_envelope(envelope: dict, model: str, attempts: int) -> ModelCallResult:
    usage = envelope.get("usage") or {}
    return ModelCallResult(
        text=envelope.get("result", ""),
        model=model,
        backend="claude_cli",
        attempts=attempts,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens", 0)),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens", 0)),
        notional_cost_usd=envelope.get("total_cost_usd"),
        duration_ms=envelope.get("duration_ms"),
        session_id=envelope.get("session_id"),
    )


# --- Anthropic SDK backend ------------------------------------------------------------------


def _call_anthropic_sdk(model: str, system_prompt: str, user_content: str) -> ModelCallResult:
    # max_retries=0: the SDK has its own retry loop, which would silently multiply against this
    # module's -- 5 attempts here x 2 there is 10 real requests, and neither layer's logs would
    # show the true count. One retry policy, owned here.
    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    response = client.messages.create(
        model=model,
        max_tokens=MAX_OUTPUT_TOKENS,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    usage = response.usage
    return ModelCallResult(
        text="".join(block.text for block in response.content if block.type == "text"),
        model=model,
        backend="anthropic_sdk",
        attempts=1,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        notional_cost_usd=None,  # see the module docstring: no stale rate card here
    )


def _sdk_retryable_status(exc: Exception) -> int | None:
    if isinstance(exc, anthropic.RateLimitError):
        return 429
    if isinstance(exc, (anthropic.APITimeoutError, anthropic.APIConnectionError)):
        return 503
    if isinstance(exc, anthropic.APIStatusError) and exc.status_code >= 500:
        return exc.status_code
    return None


# --- The public call ---------------------------------------------------------------------------


def call_model(
    node: str,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    backend: Backend | None = None,
) -> ModelCallResult:
    """Call a model for `node`, retrying transient failures, and record what it cost.

    Args:
        node: The calling node's name (`"spec_extractor"` etc.) -- used only for log correlation,
            never to choose a model. Model selection stays `core/model_routing.py`'s job (ADR-0004).
        model: The already-resolved model identifier.
        system_prompt/user_content: The two turns. `user_content` may be very large; see the
            module docstring on why it travels over stdin.
        backend: Overrides the configured backend -- tests and explicit callers only.

    Raises:
        ModelCallError: a non-retryable failure, or every attempt exhausted. Never returns a
            partial or fabricated response -- consistent with `pic_mapper`'s posture that a wrong
            answer which looks right is the worst outcome available.
    """
    chosen = resolve_backend(backend)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        retryable_status: int | None = None
        try:
            if chosen == "claude_cli":
                envelope, retryable_status = _call_claude_cli(model, system_prompt, user_content)
                if retryable_status is None:
                    result = _result_from_cli_envelope(envelope, model, attempt)
                    _log_success(node, result)
                    return result
            else:
                result = _call_anthropic_sdk(model, system_prompt, user_content)
                result = ModelCallResult(**{**result.__dict__, "attempts": attempt})
                _log_success(node, result)
                return result
        except subprocess.TimeoutExpired:
            retryable_status = 504
        except ModelCallError:
            raise
        except Exception as exc:
            retryable_status = _sdk_retryable_status(exc)
            if retryable_status is None:
                raise ModelCallError(chosen, attempt, f"{type(exc).__name__}: {exc}") from exc

        if attempt == MAX_ATTEMPTS:
            raise ModelCallError(
                chosen, attempt, f"still failing with status {retryable_status} after retries"
            )
        delay = _sleep_for_attempt(attempt)
        logger.warning(
            "model call retry node=%s backend=%s status=%s attempt=%d/%d backoff=%.1fs",
            node, chosen, retryable_status, attempt, MAX_ATTEMPTS, delay,
        )

    raise AssertionError("unreachable: the loop either returns or raises")


def _log_success(node: str, result: ModelCallResult) -> None:
    logger.info(
        "model call node=%s backend=%s model=%s attempts=%d input_tokens=%d output_tokens=%d "
        "cache_creation=%d cache_read=%d notional_cost_usd=%s duration_ms=%s",
        node,
        result.backend,
        result.model,
        result.attempts,
        result.input_tokens,
        result.output_tokens,
        result.cache_creation_input_tokens,
        result.cache_read_input_tokens,
        result.notional_cost_usd,
        result.duration_ms,
    )
