"""The differential, as a gate verdict rather than an assertion (ADR-0064).

The comparison itself is proven in `test_cobol_oracle_comparison.py`. What is proven here is the
translation from a result into something a release gate can render and a reviewer can act on -- and
in particular that the two findings a gate must never confuse stay distinct:

- **nothing ran** is `not_run` with a reason
- **the generated code is wrong** is `mismatched` with the fields

Reporting the first as the second would put a false accusation in the audit trail; reporting the
second as the first would hide wrong money, which is the failure this whole effort exists to close.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.core.package_data import ORACLE_ROOT
from cobol_modernizer.equivalence.harness import (
    ACCOUNT_OUTPUT,
    TRANSACTION_OUTPUT,
    compare_project_output,
    unrenderable_reason,
)

ORACLE = ORACLE_ROOT / "CBACT04C"


def test_the_oracle_is_reachable_from_the_package(tmp_path) -> None:
    """It ships in the wheel now, so this resolves without the test tree (ADR-0064)."""
    assert (ORACLE / "transact.dat").is_file()
    assert (ORACLE / "acctdata-posted.dat").is_file()


def test_a_project_that_produced_nothing_is_not_run_rather_than_mismatched(tmp_path) -> None:
    """An empty project is not a wrong one, and a gate must not read it as one."""
    verdict = compare_project_output(tmp_path, ORACLE)

    assert verdict.status == "not_run"
    assert "produced no output" in verdict.reason
    assert "transact.dat" in verdict.reason
    assert verdict.mismatches == []


def test_the_oracles_own_output_matches_itself(tmp_path) -> None:
    """The comparison's floor: COBOL's output compared against COBOL's output is a match.

    Run against the packaged oracle rather than a constructed file, so a broken loader or a wrong
    record length shows up here as a mismatch rather than passing vacuously.
    """
    (tmp_path / TRANSACTION_OUTPUT.parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / ACCOUNT_OUTPUT.parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / TRANSACTION_OUTPUT).write_bytes((ORACLE / "transact.dat").read_bytes())
    (tmp_path / ACCOUNT_OUTPUT).write_bytes((ORACLE / "acctdata-posted.dat").read_bytes())

    verdict = compare_project_output(tmp_path, ORACLE)

    assert verdict.status == "matched", verdict.mismatches[:5]
    assert verdict.records_compared == 100, "50 transactions + 50 accounts"
    assert verdict.fields_compared > 0
    assert verdict.excluded_fields, "a match must still report what it did not compare"


def test_a_wrong_account_balance_is_caught_and_labelled(tmp_path) -> None:
    """**The defect this gate exists for**, shown against real data rather than argued.

    Step 51's processor set a per-account running total to one row's amount, so the posted balance
    was wrong on every multi-category account. Here one byte of one balance is changed and the
    verdict names the field -- which is what the release gate would have rendered instead of
    "Generated and compiled 4 processor step(s)."
    """
    (tmp_path / TRANSACTION_OUTPUT.parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / ACCOUNT_OUTPUT.parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / TRANSACTION_OUTPUT).write_bytes((ORACLE / "transact.dat").read_bytes())

    accounts = bytearray((ORACLE / "acctdata-posted.dat").read_bytes())
    # ACCT-CURR-BAL sits inside the third record; changing one digit is a smaller error than the
    # accumulator defect produced.
    accounts[300 * 3 + 60] = ord("9") if accounts[300 * 3 + 60] != ord("9") else ord("1")
    (tmp_path / ACCOUNT_OUTPUT).write_bytes(bytes(accounts))

    verdict = compare_project_output(tmp_path, ORACLE)

    assert verdict.status == "mismatched"
    assert any(m.startswith("accounts:") for m in verdict.mismatches), verdict.mismatches[:3]
    assert "differ from what CBACT04C wrote" in verdict.reason


def test_the_account_half_excludes_nothing(tmp_path) -> None:
    """The exacting half. `EXCLUSIONS` apply to transactions only (ADR-0026), and a differential
    that quietly excluded an account field would be the way this check goes toothless."""
    (tmp_path / TRANSACTION_OUTPUT.parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / ACCOUNT_OUTPUT.parent).mkdir(parents=True, exist_ok=True)
    (tmp_path / TRANSACTION_OUTPUT).write_bytes((ORACLE / "transact.dat").read_bytes())
    (tmp_path / ACCOUNT_OUTPUT).write_bytes((ORACLE / "acctdata-posted.dat").read_bytes())

    verdict = compare_project_output(tmp_path, ORACLE)

    assert all(not f.startswith("ACCT-") for f in verdict.excluded_fields), verdict.excluded_fields


@pytest.mark.parametrize("count", [1, 3, 5])
def test_an_unrenderable_job_names_the_steps_rather_than_counting_them(count) -> None:
    """ "3 steps unrendered" says something is missing without saying what."""

    class _Step:
        def __init__(self, name):
            self.step_name = name

    skipped = [
        (_Step(f"step{i}"), "its output is written to no declared file") for i in range(count)
    ]
    reason = unrenderable_reason(skipped)

    assert "step0" in reason
    assert "not fully renderable" in reason
    if count > 3:
        assert f"(+{count - 3} more)" in reason
