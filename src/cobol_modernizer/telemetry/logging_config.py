"""Structured, leveled logging for this repo's operational diagnosis.

Deliberately separate from the audit/provenance concern (see `CLAUDE.md`, "This repo's own
auditing concern is provenance, not a ledger" -- that's a future concern of the nodes that
produce `spec.md`/`design.json`, tracing generated output back to source, not this module's job).
This module is the operational logging: what happened during one CLI invocation, for a human
debugging a failed or slow run.

Logs go to **stderr, never stdout** -- the CLI's `--json` flag contract (`cli.py`) is that stdout
carries exactly one JSON object and nothing else; a log line on stdout would corrupt that contract
for any caller (chiefly `agentic-sdlc-control-plane`) parsing the CLI's output programmatically.
"""

from __future__ import annotations

import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once, for the lifetime of one CLI invocation.

    Idempotent-ish in practice (safe to call more than once; later calls just reset the handler
    list) but intended to be called exactly once, early in `cli.main()` -- not by library modules
    themselves, which should only ever call `logging.getLogger(__name__)` and never configure
    handlers on their own (a library module configuring global logging state is a common source
    of duplicate or conflicting log output when it's imported by something that already did).
    """
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root.addHandler(handler)
