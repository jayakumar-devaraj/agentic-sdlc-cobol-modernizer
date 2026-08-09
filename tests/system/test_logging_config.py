"""Tests for telemetry/logging_config.py -- chiefly the `run_id` correlation added by ADR-0018.

This module did not exist before ADR-0018, which is itself worth noting: `logging_config.py`
reported 100% coverage because `cli.main()` calls `configure_logging` in the CLI tests, so every
line ran without anything ever asserting what the logging *did*. Coverage said the module was
exercised; nothing said it was correct.

The concurrency half of the `run_id` contract -- that a branch thread inherits the binding -- is
asserted in `test_design_graph.py` against a real graph run, because that is the only place the
propagation can actually fail.
"""

from __future__ import annotations

import logging

from cobol_modernizer.telemetry.logging_config import (
    RunIdFilter,
    bind_run_id,
    configure_logging,
    current_run_id,
)


def _record() -> logging.LogRecord:
    return logging.LogRecord(
        name="test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello", args=(), exc_info=None,
    )


def test_unbound_run_id_is_a_placeholder_not_an_error():
    # A record emitted before bind_run_id -- or from a thread that never inherited the context --
    # must still format. A missing correlation id is never a reason to lose a log line.
    assert current_run_id() == "-"

    record = _record()
    assert RunIdFilter().filter(record) is True
    assert record.run_id == "-"


def test_filter_stamps_the_bound_run_id(monkeypatch):
    bind_run_id("abc123")
    record = _record()
    RunIdFilter().filter(record)
    assert record.run_id == "abc123"


def test_configured_handler_renders_run_id_to_stderr(capsys):
    """The format string and the filter have to agree -- `%(run_id)s` with no filter raises."""
    configure_logging()
    bind_run_id("deadbeef")

    logging.getLogger("cobol_modernizer.test").info("a message")

    err = capsys.readouterr().err
    assert "run_id=deadbeef" in err
    assert "a message" in err


def test_a_module_that_never_mentions_run_id_still_gets_it(capsys):
    """The whole point of a handler-level filter: existing call sites gain correlation unedited.

    Before ADR-0018 only `cli.py`'s own three lines interpolated the id by hand, so the lines that
    mattered most for cost diagnosis -- `model_client`'s per-call usage line -- had none.
    """
    configure_logging()
    bind_run_id("run-42")

    # A logger standing in for any library module: it passes no run_id and knows nothing about it.
    logging.getLogger("cobol_modernizer.core.model_client").info(
        "model call node=%s input_tokens=%d", "spec_extractor", 36320
    )

    err = capsys.readouterr().err
    assert "run_id=run-42" in err
    assert "node=spec_extractor input_tokens=36320" in err
