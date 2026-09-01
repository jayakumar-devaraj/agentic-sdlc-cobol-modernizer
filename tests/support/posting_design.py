"""Shared CBTRN02C account-break posting constants (ADR-0027).

Extracted from `test_account_break_posting.py` for the reason given in `interest_design`: that
module drives a real Maven build and belongs in an integration tier, while `test_java_job`,
`test_java_writer`, `test_java_aggregation`, `test_control_break` and `test_file_access_paths`
import `POSTING`, `STEP`, `_FAITHFUL` and `_IMPORTS` from it and do no I/O at all.

`FIXTURE_ROOT` and `PROGRAM` are defined here as well as in `interest_design`. That duplication is
not new -- both test modules already defined their own copies with identical values -- and it is
carried across verbatim rather than unified, so this move stays a relocation and nothing else.
"""

from __future__ import annotations

from pathlib import Path

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
)
from cobol_modernizer.graph.generate_pipeline import DEFAULT_DOMAIN_PACKAGE

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBACT04C"

#: The pre-aggregated item. A composite carries **existing entities only** (ADR-0020), so the summed
#: interest travels inside a `Tran` -- the same accommodation `TranWithContext` makes for the same
#: reason, and the same one option (b) of ADR-0027 would remove if arrays were ever supported.
POSTING = CompositeType(
    name="AccountInterestPosting",
    components=[
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="interest", entity_name="Tran"),
    ],
)

#: A **processor**, which is the whole point of ADR-0027. Under ADR-0023 this step had to carry a
#: non-processor role and `generate` reported it `not_generated`; with the item pre-aggregated it is
#: an ordinary per-item transform and the pipeline renders it like any other.
STEP = BatchStepDesign(
    step_name="postAccountInterest",
    source_paragraphs=["1050-UPDATE-ACCOUNT"],
    role="processor",
    description="Posts an account's accumulated interest and clears the cycle totals.",
    input_type="AccountInterestPosting",
    output_type="Account",
    guard_condition=None,
    job_parameters=[],
)

#: `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` plus `MOVE 0 TO` the two cycle fields, and nothing else --
#: the `REWRITE` is persistence and stays wiring.
_FAITHFUL = """\
com.modernized.batch.domain.Account a = item.account();
return new Account(
    a.acctId(),
    a.acctActiveStatus(),
    a.acctCurrBal().add(item.interest().tranAmt()),
    a.acctCreditLimit(),
    a.acctCashCreditLimit(),
    a.acctOpenDate(),
    a.acctExpiraionDate(),
    a.acctReissueDate(),
    java.math.BigDecimal.ZERO,
    java.math.BigDecimal.ZERO,
    a.acctAddrZip(),
    a.acctGroupId());"""

#: Posts correctly and leaves the cycle totals alone. Compiles; wrong by two fields.
_FORGETS_CYCLE_RESET = _FAITHFUL.replace(
    "    java.math.BigDecimal.ZERO,\n    java.math.BigDecimal.ZERO,",
    "    a.acctCurrCycCredit(),\n    a.acctCurrCycDebit(),",
)

#: `MOVE` where COBOL says `ADD`. The single most plausible mistranslation of this paragraph.
_REPLACES_BALANCE = _FAITHFUL.replace(
    "a.acctCurrBal().add(item.interest().tranAmt())", "item.interest().tranAmt()"
)

_IMPORTS = [
    "java.math.BigDecimal",
    f"{DEFAULT_DOMAIN_PACKAGE}.Account",
    f"{DEFAULT_DOMAIN_PACKAGE}.AccountInterestPosting",
    f"{DEFAULT_DOMAIN_PACKAGE}.Tran",
]
