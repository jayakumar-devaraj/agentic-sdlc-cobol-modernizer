"""Suite-wide guards.

This file exists because of a real accident, not a hypothetical one. When `core/model_client.py`
landed with `claude_cli` as the default backend, `tests/system/test_cli_design.py` kept faking
`anthropic.Anthropic` -- which is no longer the path the default backend takes. The tests did not
fail; they started spawning real `claude` subprocesses against a live subscription, and the suite
hung. A test that quietly costs money and calls a live model is worse than one that fails.

So the backend is pinned for every test by default, and a test that genuinely wants the real CLI
has to say so out loud via the `live_claude_cli` marker.
"""

from __future__ import annotations

import os

import pytest

from cobol_modernizer.core.model_client import BACKEND_ENV_VAR
from cobol_modernizer.telemetry.logging_config import UNBOUND_RUN_ID, bind_run_id

#: Set this to `1` to allow tests marked `live_claude_cli` to actually run. They are skipped
#: otherwise, so neither CI nor an ordinary local run ever spends subscription quota.
LIVE_CLI_ENV_VAR = "COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "live_claude_cli: hits the real `claude` CLI; skipped unless "
        f"{LIVE_CLI_ENV_VAR}=1 is set",
    )
    config.addinivalue_line(
        "markers",
        "slow: builds a wheel or a throwaway virtualenv. Costs minutes, not money -- unlike "
        "`live_claude_cli`, this one always runs, because the defect it catches (ADR-0055) is "
        "invisible to every other test in the suite.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip live tests at **collection** time, before any fixture of any scope can run.

    This used to live in the autouse fixture below, and that was not soon enough. A fixture marked
    `autouse` is still function-scoped, and pytest sets higher-scoped fixtures up *first* -- so a
    module-scoped fixture that calls a model runs before the guard gets a chance to skip anything.

    Found the way this file's own docstring predicts: `tests/evaluations/test_judge_benchmark.py`
    put its six judge calls in a module-scoped fixture, and an ordinary `pytest tests/evaluations`
    spent 67 seconds calling a real model with the opt-in variable unset. Same failure the fixture
    below was written for, arriving by the one route it structurally cannot cover.

    Collection-time skipping has no such hole: an item marked here never reaches fixture setup at
    all. Keeping it here rather than patching the one module also means the next live test to need a
    module-scoped fixture is protected without having to know any of this.
    """
    if os.getenv(LIVE_CLI_ENV_VAR) == "1":
        return
    skip = pytest.mark.skip(reason=f"live claude CLI test: set {LIVE_CLI_ENV_VAR}=1 to run")
    for item in items:
        if item.get_closest_marker("live_claude_cli"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def pin_model_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every test onto the SDK backend, which the suite's fakes actually intercept.

    Autouse and unconditional: an opt-in guard would only protect the tests that remembered to ask
    for it, and the failure mode here is silent. Tests marked `live_claude_cli` are exempt -- they
    are skipped at collection time by the hook above, so anything reaching here with that marker was
    opted in deliberately.
    """
    if request.node.get_closest_marker("live_claude_cli"):
        return
    monkeypatch.setenv(BACKEND_ENV_VAR, "anthropic_sdk")


@pytest.fixture(autouse=True)
def reset_run_id() -> None:
    """Clear the `run_id` binding between tests (ADR-0018).

    `bind_run_id` deliberately mutates the ambient context and never restores it: a real CLI
    process binds once and exits, so there is nothing to undo. Under pytest every test shares one
    context, so without this a test that binds an id leaks it into every test that runs after --
    and the failure is order-dependent, which is the worst kind to debug. Caught immediately by a
    test asserting the unbound placeholder, which passed alone and failed in suite order.

    Same reasoning as `pin_model_backend` above: autouse and unconditional, because an opt-in guard
    only protects the tests that remembered to ask for it.
    """
    bind_run_id(UNBOUND_RUN_ID)
