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


@pytest.fixture(autouse=True)
def pin_model_backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Force every test onto the SDK backend, which the suite's fakes actually intercept.

    Autouse and unconditional: an opt-in guard would only protect the tests that remembered to ask
    for it, and the failure mode here is silent. Tests marked `live_claude_cli` are exempt, and
    skipped entirely unless the environment opts in.
    """
    if request.node.get_closest_marker("live_claude_cli"):
        if os.getenv(LIVE_CLI_ENV_VAR) != "1":
            pytest.skip(f"live claude CLI test: set {LIVE_CLI_ENV_VAR}=1 to run")
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
