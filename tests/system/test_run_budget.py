"""`RunBudget` -- the run-level circuit breaker, counted in calls and tokens.

Deliberately not denominated in dollars: `notional_cost_usd` is what a call *would* cost at API
rates rather than what anyone was billed, and the SDK backend reports no cost at all, so a dollar
ceiling would cap a figure that is notional on one backend and absent on the other. These tests
assert the ceiling fires on the two quantities that are real on both.

The concurrency test is the one that matters most. `design` fans out on a real `ThreadPoolExecutor`
(ADR-0012), and a ceiling checked outside the accumulator's lock would let several branches each
read a pre-increment total, all conclude they were under the limit, and sail past it -- the same
lost-update race the lock already exists for, reintroduced by the check rather than the counter.
"""

from __future__ import annotations

import threading

import pytest

from cobol_modernizer.core.model_client import (
    DEFAULT_MAX_MODEL_CALLS,
    DEFAULT_MAX_TOTAL_TOKENS,
    ModelCallResult,
    RunBudget,
    RunBudgetExceededError,
    UsageAccumulator,
    collect_usage,
)


def _result(*, input_tokens: int = 10, output_tokens: int = 10) -> ModelCallResult:
    return ModelCallResult(
        text="{}",
        model="claude-haiku-4-5-20251001",
        backend="anthropic_sdk",
        attempts=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# --- No budget means no ceiling ---------------------------------------------------------------


def test_without_a_budget_nothing_is_enforced():
    accumulator = UsageAccumulator()
    for _ in range(500):
        accumulator.record(_result())
    assert accumulator.model_calls == 500


def test_collect_usage_defaults_to_no_budget():
    # The accounting helper must not become quietly load-bearing: the ceiling is applied at the
    # real entrypoints, not by the thing tests use to count tokens.
    with collect_usage() as usage:
        assert usage.budget is None


# --- The call ceiling -------------------------------------------------------------------------


def test_the_call_ceiling_aborts_the_run():
    accumulator = UsageAccumulator(budget=RunBudget(max_model_calls=3, max_total_tokens=10**9))
    for _ in range(3):
        accumulator.record(_result())

    with pytest.raises(RunBudgetExceededError, match="4 model calls exceeds the ceiling of 3"):
        accumulator.record(_result())


def test_the_call_ceiling_does_not_fire_exactly_at_the_limit():
    # An off-by-one here would abort a legitimate worst-case run, which is worse than useless:
    # the breaker would fire on the runs it was sized to permit.
    accumulator = UsageAccumulator(budget=RunBudget(max_model_calls=28, max_total_tokens=10**9))
    for _ in range(28):
        accumulator.record(_result())
    assert accumulator.model_calls == 28


# --- The token ceiling ------------------------------------------------------------------------


def test_the_token_ceiling_aborts_the_run():
    accumulator = UsageAccumulator(budget=RunBudget(max_model_calls=10**6, max_total_tokens=100))
    accumulator.record(_result(input_tokens=60, output_tokens=30))  # 90, under

    with pytest.raises(RunBudgetExceededError, match="120 tokens"):
        accumulator.record(_result(input_tokens=20, output_tokens=10))


def test_the_token_message_separates_input_from_output():
    # A run that blew its ceiling on output needs a different fix than one that blew it on input;
    # a single total would hide which.
    accumulator = UsageAccumulator(budget=RunBudget(max_model_calls=10**6, max_total_tokens=10))
    with pytest.raises(RunBudgetExceededError, match=r"\(7 in \+ 9 out\)"):
        accumulator.record(_result(input_tokens=7, output_tokens=9))


def test_total_tokens_is_input_plus_output():
    accumulator = UsageAccumulator()
    accumulator.record(_result(input_tokens=11, output_tokens=5))
    assert accumulator.total_tokens == 16


# --- Overshoot is bounded, and stated -----------------------------------------------------------


def test_a_run_overshoots_by_at_most_the_call_that_tripped_it():
    # The documented semantics: this is a circuit breaker on a run, not a pre-flight gate on a
    # call. Gating before the call would mean predicting a response's token count.
    accumulator = UsageAccumulator(budget=RunBudget(max_model_calls=10**6, max_total_tokens=100))
    with pytest.raises(RunBudgetExceededError):
        accumulator.record(_result(input_tokens=5000, output_tokens=5000))
    assert accumulator.total_tokens == 10000
    assert accumulator.model_calls == 1


# --- The concurrency case the lock exists for ---------------------------------------------------


def test_the_ceiling_holds_under_concurrent_branches():
    # Eight threads racing to record against a ceiling of 4. Checking outside the lock would let
    # several read the same pre-increment total and all pass; exactly one recorded call may take
    # the count from 4 to 5, so exactly four must raise.
    accumulator = UsageAccumulator(budget=RunBudget(max_model_calls=4, max_total_tokens=10**9))
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            accumulator.record(_result())
        except RunBudgetExceededError as exc:
            with lock:
                failures.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert accumulator.model_calls == 8, "every call is still counted; the ceiling stops the run"
    assert len(failures) == 4, f"expected the last four to raise, got {len(failures)}"


# --- The defaults are the derived ones ----------------------------------------------------------


def test_the_defaults_are_tight_enough_to_catch_a_runaway():
    budget = RunBudget()
    assert budget.max_model_calls == DEFAULT_MAX_MODEL_CALLS == 32
    assert budget.max_total_tokens == DEFAULT_MAX_TOTAL_TOKENS == 1_000_000

    # 28 calls is the worst legitimate four-program generate run (7 per program: one generation
    # plus three validate/heal rounds). The ceiling must sit above that and well below a loop
    # that never stops.
    assert budget.max_model_calls > 28
    assert budget.max_model_calls < 28 * 2


def test_no_dollar_ceiling_exists():
    # The decision, pinned: dollars are reported, never enforced. A field here would reintroduce
    # a ceiling on a figure that is notional on claude_cli and absent on the SDK backend.
    assert not any("usd" in name.lower() for name in RunBudget.__dataclass_fields__)
