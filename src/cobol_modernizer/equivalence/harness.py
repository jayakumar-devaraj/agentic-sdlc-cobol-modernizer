"""Turning a built project's output into an `EquivalenceVerdict` (ADR-0064).

**What this is, and what it deliberately is not.** Given a target project that has been built and
run, this compares the fixed-width files it wrote against the COBOL oracle and returns a verdict a
gate can render. It does not build, does not stage inputs, and does not decide whether a comparison
*should* run -- those belong to whoever owns the project directory, and folding them in here would
make a function that cannot be tested without Maven.

**Why a verdict rather than an assertion.** The identical comparison already runs as a test, where
failing the run is the right response. At a gate it is not: the reviewer needs the finding, the
counts, and what was excluded, in a form that survives into `design.json` and the audit trail. The
same `compare` produces both; only the reporting differs.

**The honest limit, stated because it decides what a caller can claim.** `generate` renders
processors (ADR-0019), not readers, writers or job configuration, so it cannot by itself produce a
project that runs. For `CBACT04C`'s real design, `plan_steps` reports 6 of 9 steps renderable. Until
that closes, a caller has nothing to point this at and should report `not_run` naming the unrendered
steps -- which is what `not_run` carrying a reason is for. The round trip in `tests/integration/`
runs today only because it copies hand-written wiring in.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from cobol_modernizer.core.contracts import EquivalenceVerdict
from cobol_modernizer.equivalence import (
    ACCOUNT_LAYOUT,
    EXCLUSIONS,
    TRAN_LAYOUT,
    ComparisonResult,
    compare,
    load_account_oracle,
    load_oracle,
    parse_fixed_records,
)

#: Where the job writes, relative to the project root. Fixed by the rendered writers rather than
#: configurable: a caller that could point this anywhere could point it at the oracle.
TRANSACTION_OUTPUT = Path("roundtrip") / "output" / "transact.dat"
ACCOUNT_OUTPUT = Path("roundtrip") / "input" / "acctdata-stage1.dat"

TRAN_RECORD_BYTES = 350
ACCOUNT_RECORD_BYTES = 300


def _mismatch_lines(transactions: ComparisonResult, accounts: ComparisonResult) -> list[str]:
    """Both halves' mismatches, labelled, because "record 3" means two different things."""
    return [f"transactions: {m}" for m in transactions.mismatches] + [
        f"accounts: {m}" for m in accounts.mismatches
    ]


def compare_project_output(project: Path, oracle_dir: Path) -> EquivalenceVerdict:
    """Compare what a built-and-run project wrote against COBOL's own output.

    Both halves are compared and reported together. The account half is the more exacting one --
    **no field is excluded**, where the transaction half gives up `TRAN-ID` and two timestamps by
    ADR-0026 -- and it is the half that would have caught the accumulator defect, since it compares
    `ACCT-CURR-BAL` for every account and detects a one-cent divergence.

    A missing output file is `not_run`, not `mismatched`. The two are different findings: one says
    the generated code is wrong, the other says nothing ran, and reporting the second as the first
    would put a false accusation in the audit trail.
    """
    transactions_path = project / TRANSACTION_OUTPUT
    accounts_path = project / ACCOUNT_OUTPUT

    missing = [p for p in (transactions_path, accounts_path) if not p.is_file()]
    if missing:
        return EquivalenceVerdict(
            status="not_run",
            reason=(
                "the project produced no output to compare: "
                + ", ".join(str(p.relative_to(project)) for p in missing)
                + " (the job did not run, or wrote somewhere else)"
            ),
        )

    transactions = compare(
        parse_fixed_records(transactions_path, TRAN_LAYOUT, TRAN_RECORD_BYTES),
        load_oracle(oracle_dir),
    )
    accounts = compare(
        parse_fixed_records(accounts_path, ACCOUNT_LAYOUT, ACCOUNT_RECORD_BYTES),
        load_account_oracle(oracle_dir),
        ACCOUNT_LAYOUT,
        {},
    )

    compared = transactions.compared + accounts.compared
    records = len(load_oracle(oracle_dir)) + len(load_account_oracle(oracle_dir))
    mismatches = _mismatch_lines(transactions, accounts)

    if mismatches:
        return EquivalenceVerdict(
            status="mismatched",
            reason=(
                f"{len(mismatches)} field(s) differ from what CBACT04C wrote over the same corpus"
            ),
            records_compared=records,
            fields_compared=compared,
            mismatches=mismatches,
            excluded_fields=sorted(EXCLUSIONS),
        )

    return EquivalenceVerdict(
        status="matched",
        reason=(
            "every compared field equals what CBACT04C wrote over the same corpus; the account "
            "half excludes nothing, and the transaction half excludes only fields ADR-0026 makes "
            "unproducible"
        ),
        records_compared=records,
        fields_compared=compared,
        excluded_fields=sorted(EXCLUSIONS),
    )


def unrenderable_reason(skipped: Sequence[tuple[object, str]]) -> str:
    """Why a comparison could not run, naming the steps rather than counting them.

    A gate item saying "3 steps unrendered" tells a reviewer something is missing without telling
    them what, and the name is the part that says whether the gap matters.
    """
    named = ", ".join(f"{getattr(step, 'step_name', step)} ({why})" for step, why in skipped[:3])
    more = f" (+{len(skipped) - 3} more)" if len(skipped) > 3 else ""
    return (
        f"the job is not fully renderable, so no runnable project exists to compare: {named}{more}"
    )
