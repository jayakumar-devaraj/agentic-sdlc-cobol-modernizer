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

import contextvars
import json
import logging
import os
import random
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
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

#: Ceiling used only when a caller supplies none. Deliberately generous rather than the previous
#: hardcoded 4096: every real response measured so far runs 5,485-23,366 output tokens, so 4096
#: would have truncated all of them -- silently on the CLI backend (which never sent the value)
#: and as a mid-JSON parse error on the SDK backend. Real callers pass the per-tier ceiling from
#: `core/model_routing.py` instead of relying on this.
DEFAULT_MAX_OUTPUT_TOKENS = 32_000

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

    `text` is what every existing caller wants. The usage fields were added by ADR-0013 against a
    future need and sat unread for four PRs; ADR-0018 wires them through `UsageAccumulator` into
    `DesignDocument.cost`, so a run's cost now reaches control-plane's gate instead of existing
    only in a stderr line.
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


#: Ceiling on model calls in one invocation. Derived, not guessed: a four-program `generate` run
#: costs at most 7 calls per program (one generation plus three validate/heal rounds at step 42's
#: cap of 3), so 28 is the worst legitimate run and 32 leaves margin without hiding a runaway. A
#: `design` run is 9 calls, well under. Re-derive after the first real `generate` run.
DEFAULT_MAX_MODEL_CALLS = int(os.getenv("COBOL_MODERNIZER_MAX_MODEL_CALLS", "32"))

#: Ceiling on total tokens (input + output) in one invocation. **A placeholder derived from
#: placeholder inputs**, and labelled that way for the same reason `config/model_routing.yaml`
#: labels the C4 token profiles: a `design` run is ~453k tokens against measured profiles, and a
#: `generate` run is ~576k against the *renderer* design (entities are rendered, so the model
#: writes rule bodies rather than whole files). 1M is roughly 1.7x the larger of those -- tight
#: enough to trip on a loop that will not stop, loose enough not to fire on a legitimate run.
#: The first real `generate` run replaces this with a measurement.
DEFAULT_MAX_TOTAL_TOKENS = int(os.getenv("COBOL_MODERNIZER_MAX_TOTAL_TOKENS", "1000000"))


class RunBudgetExceededError(Exception):
    """A run crossed its call or token ceiling and was aborted.

    Joins the fail-loudly family rather than degrading: a run that has blown its ceiling is, by
    construction, doing something nobody predicted, and the useful behaviour is to stop with the
    numbers attached rather than to continue more cheaply.
    """


@dataclass(frozen=True)
class RunBudget:
    """Ceilings for one CLI invocation, counted in calls and tokens -- deliberately not dollars.

    **Why not dollars.** `RunCost.notional_cost_usd` is what a call *would* cost at API rates, not
    what anyone was billed; on the `claude_cli` backend against a subscription, nobody is billed
    per call at all, and the SDK backend reports no cost by design (this module keeps no rate card
    so it cannot go stale). A dollar ceiling would therefore cap a figure that is notional on one
    backend and absent on the other. Calls and tokens are real on both, and they are what a
    subscription's own limits are denominated in.

    **What this is and is not.** It is a circuit breaker on a run, not a per-call pre-flight gate:
    the check runs after a call is recorded, so a run overshoots by at most the one call that
    tripped it. Gating before the call would mean predicting a response's token count, which is
    the estimate-versus-actual conflation `RunCost` exists to avoid.
    """

    max_model_calls: int = DEFAULT_MAX_MODEL_CALLS
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS


@dataclass
class UsageAccumulator:
    """Running usage totals for one CLI invocation, shared across every concurrent branch.

    `ModelCallResult` has carried these fields since ADR-0013 and every caller threw them away --
    the nodes return `.text` and drop the rest -- so a run's real cost existed for a microsecond
    per call and was never summed. This is the sum.

    **Why a mutable object behind a `ContextVar`, and the trap it avoids.** `design` fans out on a
    real `ThreadPoolExecutor` (ADR-0012). `contextvars` copies the *binding* into each worker, not
    the object, so a parent that binds one accumulator before fan-out and children that call
    `record()` are all mutating the same instance -- and the parent sees the totals afterwards. Had
    this been a `ContextVar[int]` of running totals instead, every child would have incremented its
    own private copy and the parent would have read zero. That failure is invisible to a
    single-threaded test, which is exactly why `test_design_graph.py` asserts the totals after a
    real concurrent run rather than after a sequential one.

    The `Lock` is not optional: `+=` on an `int` field is a read-modify-write, and four branches
    finishing at once would lose updates.
    """

    # compare=False as well as repr=False: without it the generated __eq__ compares Lock objects
    # by identity, so two accumulators with identical totals are never equal -- and repr=False
    # hides the culprit, leaving a failing assertion whose diff shows every number matching.
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    #: Summed only over calls whose backend actually reported a cost. `None` when none did.
    notional_cost_usd: float | None = None
    #: How many recorded calls reported no cost, so a consumer can tell a real total from a
    #: partial one. The SDK backend never reports cost (no rate card here, by design), so on that
    #: backend this equals `model_calls` and `notional_cost_usd` stays `None`.
    calls_without_reported_cost: int = 0
    #: `None` disables the ceiling entirely -- the shape a test or a deliberate long run wants.
    budget: RunBudget | None = None

    @property
    def total_tokens(self) -> int:
        """Input plus output. Cache reads/creations are already counted in `input_tokens`."""
        return self.input_tokens + self.output_tokens

    def record(self, result: ModelCallResult) -> None:
        with self.lock:
            self.model_calls += 1
            self.input_tokens += result.input_tokens
            self.output_tokens += result.output_tokens
            self.cache_creation_input_tokens += result.cache_creation_input_tokens
            self.cache_read_input_tokens += result.cache_read_input_tokens
            if result.notional_cost_usd is None:
                self.calls_without_reported_cost += 1
            else:
                self.notional_cost_usd = (self.notional_cost_usd or 0.0) + result.notional_cost_usd
            self._enforce_budget()

    def _enforce_budget(self) -> None:
        """Raise if this run has crossed a ceiling. Called under `lock`, never on its own.

        Deliberately inside the same critical section as the increments: checking outside it would
        let four concurrent branches each read a pre-increment total and all decide they were
        under the limit, which is the same lost-update race the `Lock` exists for.
        """
        if self.budget is None:
            return
        if self.model_calls > self.budget.max_model_calls:
            raise RunBudgetExceededError(
                f"run aborted: {self.model_calls} model calls exceeds the ceiling of "
                f"{self.budget.max_model_calls}"
            )
        if self.total_tokens > self.budget.max_total_tokens:
            raise RunBudgetExceededError(
                f"run aborted: {self.total_tokens} tokens ({self.input_tokens} in + "
                f"{self.output_tokens} out) exceeds the ceiling of "
                f"{self.budget.max_total_tokens}"
            )


_usage_accumulator: contextvars.ContextVar[UsageAccumulator | None] = contextvars.ContextVar(
    "usage_accumulator", default=None
)


@contextmanager
def collect_usage(budget: RunBudget | None = None) -> Iterator[UsageAccumulator]:
    """Collect usage from every `call_model` in this context (and any thread it spawns).

    Scoped rather than global so tests, and any future caller that runs two graphs in one process,
    cannot bleed totals into each other. Outside this context `call_model` records nothing, which
    keeps the accounting opt-in: a node called directly from a test should not silently accumulate
    into whatever ran before it.

    `budget` defaults to `None` -- no ceiling -- because this context manager is also what tests
    use, and a default ceiling here would make the accounting helper quietly load-bearing. The
    ceiling is applied at the real entrypoints (`graph/design_graph.py`, and `generate`'s
    sub-pipeline once it exists), which is where a runaway actually has to be stopped.
    """
    accumulator = UsageAccumulator(budget=budget)
    token = _usage_accumulator.set(accumulator)
    try:
        yield accumulator
    finally:
        _usage_accumulator.reset(token)


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


def _call_claude_cli(
    model: str, system_prompt: str, user_content: str, effort: str | None
) -> tuple[dict, int | None]:
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
        argv = [
            executable,
            "-p",
            "--output-format", "json",
            "--model", model,
            "--system-prompt-file", str(prompt_file),
            "--disallowed-tools", _DISALLOWED_TOOLS,
        ]
        # The CLI has no max-tokens flag, so the ceiling is SDK-only -- see call_model's docstring
        # on why that asymmetry is documented rather than papered over.
        if effort is not None:
            argv += ["--effort", effort]
        completed = subprocess.run(
            argv,
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


def _call_anthropic_sdk(
    model: str, system_prompt: str, user_content: str, effort: str | None, max_output_tokens: int
) -> ModelCallResult:
    # max_retries=0: the SDK has its own retry loop, which would silently multiply against this
    # module's -- 5 attempts here x 2 there is 10 real requests, and neither layer's logs would
    # show the true count. One retry policy, owned here.
    client = anthropic.Anthropic(timeout=REQUEST_TIMEOUT_SECONDS, max_retries=0)
    extra: dict = {}
    if effort is not None:
        extra["output_config"] = {"effort": effort}
    response = client.messages.create(
        model=model,
        max_tokens=max_output_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
        **extra,
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
    effort: str | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    backend: Backend | None = None,
) -> ModelCallResult:
    """Call a model for `node`, retrying transient failures, and record what it cost.

    Args:
        node: The calling node's name (`"spec_extractor"` etc.) -- used only for log correlation,
            never to choose a model. Model selection stays `core/model_routing.py`'s job
            (ADR-0004, amended by ADR-0014).
        model: The already-resolved model identifier.
        system_prompt/user_content: The two turns. `user_content` may be very large; see the
            module docstring on why it travels over stdin.
        effort: `low`|`medium`|`high`|`xhigh`|`max`, from the resolved `RoutingDecision`. Passed
            to the CLI as `--effort` and to the SDK as `output_config.effort`. Left unset only by
            callers that genuinely have no routing decision, in which case each backend applies
            its own default -- which is not free: on Claude Opus 5 thinking is *on* by default and
            effort defaults to `high`, so an unset value is the most expensive setting, not a
            neutral one.
        max_output_tokens: Safety ceiling. **Applied on the SDK backend only** -- the `claude` CLI
            exposes no max-tokens flag, so the two backends are not at parity here. Stated rather
            than hidden: a workload that depends on a hard output cap must use the SDK backend.

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
                envelope, retryable_status = _call_claude_cli(
                    model, system_prompt, user_content, effort
                )
                if retryable_status is None:
                    return _finish(node, _result_from_cli_envelope(envelope, model, attempt))
            else:
                result = _call_anthropic_sdk(
                    model, system_prompt, user_content, effort, max_output_tokens
                )
                return _finish(node, ModelCallResult(**{**result.__dict__, "attempts": attempt}))
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


def _finish(node: str, result: ModelCallResult) -> ModelCallResult:
    """Log the call and record its usage -- the single exit both backends share.

    One helper rather than two lines repeated at each `return`, so a future third backend cannot
    log without accounting (or vice versa) by forgetting one of them.
    """
    _log_success(node, result)
    accumulator = _usage_accumulator.get()
    if accumulator is not None:
        accumulator.record(result)
    return result


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
