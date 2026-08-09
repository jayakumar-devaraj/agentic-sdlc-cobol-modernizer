"""Leveled, correlated logging for this repo's operational diagnosis.

Deliberately separate from the audit/provenance concern (see `CLAUDE.md`, "This repo's own
auditing concern is provenance, not a ledger" -- that's a future concern of the nodes that
produce `spec.md`/`design.json`, tracing generated output back to source, not this module's job).
This module is the operational logging: what happened during one CLI invocation, for a human
debugging a failed or slow run.

Logs go to **stderr, never stdout** -- the CLI's `--json` flag contract (`cli.py`) is that stdout
carries exactly one JSON object and nothing else; a log line on stdout would corrupt that contract
for any caller (chiefly `agentic-sdlc-control-plane`) parsing the CLI's output programmatically.

**Every record carries `run_id` (ADR-0018).** Before this, only the three lines `cli.py` emitted
itself interpolated it by hand, so the lines that matter most for cost and failure diagnosis --
`core/model_client.py`'s per-call usage line, and everything the nodes emit -- carried no
correlation id at all. With `MAX_CONCURRENT_PROGRAMS` branches interleaving on a real thread pool,
a `model call node=spec_extractor input_tokens=36320` line could not be tied to a run, let alone to
the program that caused it. A `ContextVar` rather than a module global because `design` fans out
across a real `ThreadPoolExecutor`: `contextvars` is copied into each worker, so every branch
inherits the binding without it being threaded through node signatures. Records emitted before
`bind_run_id` (or from an unrelated thread that never inherited the context) render `run_id=-`
rather than blowing up -- a missing correlation id must never be the reason a log line is lost.
"""

from __future__ import annotations

import contextvars
import logging
import sys

_LOG_FORMAT = "%(asctime)s %(levelname)-8s run_id=%(run_id)s %(name)s: %(message)s"

#: Rendered in place of a correlation id when none is bound. Exported so callers that need to
#: clear the binding (chiefly `tests/conftest.py`, since `bind_run_id` deliberately mutates the
#: ambient context and a real CLI process never needs to undo that) do not hardcode the sentinel.
UNBOUND_RUN_ID = "-"

#: Correlation id for the current invocation. `UNBOUND_RUN_ID` until `bind_run_id` is called, so a
#: record emitted outside a bound context formats cleanly instead of raising in the formatter.
_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("run_id", default=UNBOUND_RUN_ID)


def bind_run_id(run_id: str) -> None:
    """Bind `run_id` to every log record emitted from this context onward.

    Call once, in `cli.main()`, immediately after the id is resolved. Any thread the graph spawns
    afterwards inherits the binding by way of the copied context; threads started *before* this
    call do not, which is why it happens before `run_design`.
    """
    _run_id.set(run_id)


def current_run_id() -> str:
    """The bound correlation id, or `"-"` if none is bound. Exposed for tests and for callers
    that need to stamp the id onto something other than a log record."""
    return _run_id.get()


class RunIdFilter(logging.Filter):
    """Attaches `run_id` to every record so `_LOG_FORMAT` can render it.

    A filter rather than a `LoggerAdapter` or a custom `Logger` subclass: adapters have to be
    threaded to each call site, and every module here already holds a plain
    `logging.getLogger(__name__)`. Installing this on the handler means existing call sites gain
    correlation with no edit -- which is the point, since the uncorrelated lines were the ones in
    `model_client` and the nodes, not in `cli.py`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = current_run_id()
        return True


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
    handler.addFilter(RunIdFilter())
    root.addHandler(handler)
