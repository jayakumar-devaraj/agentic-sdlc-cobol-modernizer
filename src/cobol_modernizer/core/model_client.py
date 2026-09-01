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

from cobol_modernizer.telemetry import tracing
from cobol_modernizer.telemetry.logging_config import current_run_id

logger = logging.getLogger(__name__)

Backend = Literal["claude_cli", "anthropic_sdk"]

#: Backend used when a caller does not pass one explicitly. A non-secret operational toggle, so
#: an environment variable is appropriate here -- unlike a credential, which ADR-0005 requires be
#: delivered as a mounted file path instead.
BACKEND_ENV_VAR = "COBOL_MODERNIZER_MODEL_BACKEND"
DEFAULT_BACKEND: Backend = "claude_cli"

REQUEST_TIMEOUT_SECONDS = float(os.getenv("COBOL_MODERNIZER_LLM_TIMEOUT", "300"))

#: How many times one HTTP call is retried against a *transport* failure -- a 429, a 5xx, a dropped
#: connection. Named for what it bounds, because step 42's self-healing loop introduces a second,
#: unrelated attempt cap (how many times a model may be asked to repair code that does not compile)
#: and the two multiply if they are ever confused for each other. That is the same failure ADR-0013
#: describes for SDK-level retry stacking on top of this module's own loop: invisible in both
#: layers' logs, and quadratic in cost. The previous name, a bare `MAX_ATTEMPTS`, was one careless
#: import away from becoming the heal cap as well.
MAX_TRANSPORT_ATTEMPTS = int(os.getenv("COBOL_MODERNIZER_LLM_MAX_ATTEMPTS", "5"))
MAX_BACKOFF_SECONDS = 30.0

#: Ceiling used only when a caller supplies none. Deliberately generous rather than the previous
#: hardcoded 4096: every real response measured so far runs 5,485-23,366 output tokens, so 4096
#: would have truncated all of them -- silently on the CLI backend (which never sent the value)
#: and as a mid-JSON parse error on the SDK backend. Real callers pass the per-tier ceiling from
#: `core/model_routing.py` instead of relying on this.
DEFAULT_MAX_OUTPUT_TOKENS = 32_000

#: The input ceiling, in **characters**, applied to every model call this repo makes (step 39a,
#: pillar 3, audit gap G11). See `docs/adr/0031-the-prompt-budget-is-measured-in-characters.md`.
#:
#: **Characters, not tokens, and that is the decision.** This repo has no tokenizer and will not
#: acquire one just to count: the SDK's token counter is a network call, and a guard that costs a
#: round trip to a service is a guard that gets disabled. Characters are exact, free, deterministic
#: and available on both backends.
#:
#: **Where the number comes from.** Measured, not guessed. The largest real `generate` prompt is
#: **85,215 characters** for `CBACT04C` (`tests/integration/test_context_budget.py` re-measures it on
#: every run, so this comment cannot quietly go stale), and the `design` side measured 81,975 at
#: step 37g. The ceiling is ~7x that: enough headroom for a Track B program several times larger
#: than anything in Track C, and low enough that unbounded growth -- an accumulating repair prompt,
#: a pathological copybook expansion -- fails here rather than as an opaque API error or, worse, a
#: silent truncation nobody notices in the output.
#:
#: At the conservative 2 characters/token this repo has used since step 37g, this is ~300k tokens
#: against a 1M window; at a more typical 3.5 it is ~170k. Both are comfortably inside, which is
#: the point: this bounds *growth*, it does not approximate the window.
MAX_PROMPT_CHARS = 600_000

#: Every tool the CLI could otherwise expose. Listed explicitly rather than via a wildcard so a
#: newly-added tool is a visible omission in review, not silently permitted.
_DISALLOWED_TOOLS = (
    "Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,Task,TodoWrite,NotebookEdit"
)


class PromptBudgetExceededError(Exception):
    """A prompt exceeded `MAX_PROMPT_CHARS` and was not sent (step 39a, pillar 3).

    Joins the `UnsupportedPicConstructError` family: an unambiguous case that fails loudly rather
    than being papered over. **The alternative that is never taken is truncation.** Dropping the
    tail of a prompt to fit produces a model call that succeeds, costs money, and answers a
    question nobody asked -- the copybook whose fields were cut, the paragraph whose second half is
    missing -- and nothing downstream can tell that from a model that simply did worse. A wrong
    answer that looks right is the outcome this repo spends the most effort refusing.

    Raised **before** the call, so an oversized prompt costs nothing.
    """


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


def prompt_size_chars(system_prompt: str, user_content: str) -> int:
    """What the budget is measured over: both turns, as sent.

    One function rather than two `len()` calls at the check site, because the *reported* size and
    the *checked* size have to be the same quantity -- a diagnostic that names a number the guard
    did not use is worse than no number.
    """
    return len(system_prompt) + len(user_content)


def _require_within_prompt_budget(
    node: str, model: str, system_prompt: str, user_content: str, ceiling: int
) -> None:
    """Refuse an oversized prompt, naming what to look at.

    The split matters in the message. A system prompt over budget is a prompt-template defect and
    a `user_content` over budget is a data problem -- a program larger than anything measured, a
    copybook that expanded, a repair context that kept accumulating -- and those have completely
    different fixes. A single total would leave a reader to guess which.
    """
    total = prompt_size_chars(system_prompt, user_content)
    if total <= ceiling:
        return
    raise PromptBudgetExceededError(
        f"{node}: prompt is {total:,} characters against a ceiling of {ceiling:,} "
        f"(system {len(system_prompt):,}, user {len(user_content):,}) for model {model}. "
        "Not sent, and deliberately not truncated: a shortened prompt would produce a confident "
        "answer to a question missing its tail. Either the input is larger than anything this "
        "budget was measured against -- re-measure and raise MAX_PROMPT_CHARS on the record -- or "
        "something is accumulating that should not be."
    )


def call_model(
    node: str,
    model: str,
    system_prompt: str,
    user_content: str,
    *,
    effort: str | None = None,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    backend: Backend | None = None,
    max_prompt_chars: int = MAX_PROMPT_CHARS,
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
        max_prompt_chars: Input ceiling for this call, in characters (step 39a). Defaults to
            `MAX_PROMPT_CHARS` and is a parameter rather than an environment variable on purpose:
            an env var lets a run quietly raise its own ceiling, and the ADR requires that raising
            it be a decision someone made in code with a fresh measurement behind it.
        max_output_tokens: Safety ceiling. **Applied on the SDK backend only** -- the `claude` CLI
            exposes no max-tokens flag, so the two backends are not at parity here. Stated rather
            than hidden: a workload that depends on a hard output cap must use the SDK backend.

    Raises:
        PromptBudgetExceededError: the prompt is over `max_prompt_chars`. Raised before the call,
            so it costs nothing, and never handled by truncating -- see the exception's docstring.
        ModelCallError: a non-retryable failure, or every attempt exhausted. Never returns a
            partial or fabricated response -- consistent with `pic_mapper`'s posture that a wrong
            answer which looks right is the worst outcome available.
    """
    _require_within_prompt_budget(node, model, system_prompt, user_content, max_prompt_chars)
    chosen = resolve_backend(backend)

    # ADR-0046's instrumentation point. Around the whole retry loop rather than around each
    # attempt, because one span per logical call is what a reader wants - the attempt count
    # reaches the span as an attribute, so a call that succeeded on its third try is still
    # visible as one that struggled. The prompt is attached before the first attempt so that a
    # call which never returns still shows what was asked.
    with tracing.span(
        f"call_model.{node}",
        tracing.generation_attributes(
            model=model,
            backend=chosen,
            prompt=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        )
        | {"cobol_modernizer.node": node, "cobol_modernizer.run_id": current_run_id()},
    ) as span:
        for attempt in range(1, MAX_TRANSPORT_ATTEMPTS + 1):
            retryable_status: int | None = None
            try:
                if chosen == "claude_cli":
                    envelope, retryable_status = _call_claude_cli(
                        model, system_prompt, user_content, effort
                    )
                    if retryable_status is None:
                        return _finish(
                            node, _result_from_cli_envelope(envelope, model, attempt), span
                        )
                else:
                    result = _call_anthropic_sdk(
                        model, system_prompt, user_content, effort, max_output_tokens
                    )
                    return _finish(
                        node, ModelCallResult(**{**result.__dict__, "attempts": attempt}), span
                    )
            except subprocess.TimeoutExpired:
                retryable_status = 504
            except ModelCallError:
                raise
            except Exception as exc:
                retryable_status = _sdk_retryable_status(exc)
                if retryable_status is None:
                    raise ModelCallError(chosen, attempt, f"{type(exc).__name__}: {exc}") from exc

            if attempt == MAX_TRANSPORT_ATTEMPTS:
                raise ModelCallError(
                    chosen, attempt, f"still failing with status {retryable_status} after retries"
                )
            delay = _sleep_for_attempt(attempt)
            logger.warning(
                "model call retry node=%s backend=%s status=%s attempt=%d/%d backoff=%.1fs",
                node, chosen, retryable_status, attempt, MAX_TRANSPORT_ATTEMPTS, delay,
            )

    raise AssertionError("unreachable: the loop either returns or raises")


def _finish(node: str, result: ModelCallResult, span=None) -> ModelCallResult:
    """Log the call, record its usage, and describe it -- the single exit both backends share.

    One helper rather than three lines repeated at each `return`, so a future third backend cannot
    log without accounting, or account without tracing, by forgetting one of them. That was
    already this function's stated reason for existing; the span is the third thing it now cannot
    be skipped for.

    `span` defaults to None so the existing callers in tests keep working unchanged, and because
    a call outside a span is a real case rather than a mistake: tracing is off unless a collector
    is configured.
    """
    _log_success(node, result)
    accumulator = _usage_accumulator.get()
    if accumulator is not None:
        accumulator.record(result)
    if span is not None:
        span.set(
            tracing.generation_attributes(
                model=result.model,
                backend=result.backend,
                attempts=result.attempts,
                duration_ms=result.duration_ms,
                session_id=result.session_id,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                cache_read_input_tokens=result.cache_read_input_tokens,
                cache_creation_input_tokens=result.cache_creation_input_tokens,
                notional_cost_usd=result.notional_cost_usd,
                completion=result.text,
            )
        )
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
