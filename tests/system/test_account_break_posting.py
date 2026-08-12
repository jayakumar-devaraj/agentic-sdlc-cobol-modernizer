"""ADR-0027: `1050-UPDATE-ACCOUNT` as a processor over pre-aggregated items, generated and run.

**What G27 was.** `ADD WS-MONTHLY-INT TO WS-TOTAL-INT` accumulates per account and
`1050-UPDATE-ACCOUNT` posts the total to `ACCT-CURR-BAL` on each account break. A stateless
`ItemProcessor` cannot hold that total, and Spring Batch's chunk boundaries do not align with COBOL's
account breaks -- so ADR-0023 reported the step `not_generated` rather than rendering something that
looked right. This is the other half: the logic, generated.

**The move ADR-0027 makes** is to stop trying to hold the state. If the item arriving at the
processor is *already* one account with its interest already summed, there is no cross-item state to
manage and the generated body is exactly COBOL's two statements. The summation moves into the
reader's query, which is infrastructure -- the same line PR #44 drew inside `1300-B-WRITE-TX` and
ADR-0026 drew for job parameters: the model writes translated business rules, not wiring.

**And the sum is provably the right number.** `WS-TOTAL-INT` accumulates `WS-MONTHLY-INT`, and every
`WS-MONTHLY-INT` is written to `TRAN-AMT` -- both inside `1300-COMPUTE-INTEREST`, under the same
`IF DIS-INT-RATE NOT = 0` guard, so they cannot diverge. `SUM(tran_amt)` per account *is*
`WS-TOTAL-INT`. That is what makes this a re-ordering rather than a re-implementation.

**Shown to discriminate before it is trusted**, per step 45. Three wrong bodies -- one that forgets
the cycle reset, one that replaces the balance instead of adding to it, one that posts to the wrong
field -- must each fail the rendered check. Without that, a body that did nothing at all would pass a
"it compiled" test.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    ProgramDesignEntry,
    UnifiedDesign,
    build_design_document,
)
from cobol_modernizer.graph.generate_pipeline import (
    DEFAULT_DOMAIN_PACKAGE,
    DEFAULT_PACKAGE,
    run_generate,
)
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.tools.local_compiler import compile_project

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

POSTING_TEST = f"""\
package {DEFAULT_PACKAGE};

import static org.junit.jupiter.api.Assertions.assertEquals;

import {DEFAULT_DOMAIN_PACKAGE}.Account;
import {DEFAULT_DOMAIN_PACKAGE}.AccountInterestPosting;
import {DEFAULT_DOMAIN_PACKAGE}.Tran;
import java.math.BigDecimal;
import org.junit.jupiter.api.Test;

/** Rendered by tests/system/test_account_break_posting.py -- see that module for why. */
class PostAccountInterestTest {{

    private static Account account(String balance, String cycCredit, String cycDebit) {{
        return new Account(
            new BigDecimal("194"), "Y", new BigDecimal(balance),
            new BigDecimal("5000.00"), new BigDecimal("1000.00"),
            "2020-01-01", "2030-01-01", "2025-01-01",
            new BigDecimal(cycCredit), new BigDecimal(cycDebit),
            "12345", "ZEROAPR");
    }}

    private static Tran interest(String amount) {{
        return new Tran("", "01", new BigDecimal("5"), "System", "Int.",
            new BigDecimal(amount), BigDecimal.ZERO, "", "", "", "", "", "");
    }}

    @Test
    void theAccumulatedInterestIsAddedToTheCurrentBalance() throws Exception {{
        var out = new PostAccountInterestProcessor().process(
            new AccountInterestPosting(account("1000.00", "25.00", "40.00"), interest("2.42")));
        // ADD WS-TOTAL-INT TO ACCT-CURR-BAL -- add, not replace.
        assertEquals(0, new BigDecimal("1002.42").compareTo(out.acctCurrBal()));
    }}

    @Test
    void bothCycleTotalsAreCleared() throws Exception {{
        var out = new PostAccountInterestProcessor().process(
            new AccountInterestPosting(account("1000.00", "25.00", "40.00"), interest("2.42")));
        // MOVE 0 TO ACCT-CURR-CYC-CREDIT / ACCT-CURR-CYC-DEBIT -- both, not one.
        assertEquals(0, BigDecimal.ZERO.compareTo(out.acctCurrCycCredit()));
        assertEquals(0, BigDecimal.ZERO.compareTo(out.acctCurrCycDebit()));
    }}

    @Test
    void aNegativeTotalPostsAsADecrease() throws Exception {{
        // `dailytran` really carries negative amounts, so a negative accumulated total is reachable.
        var out = new PostAccountInterestProcessor().process(
            new AccountInterestPosting(account("1000.00", "0", "0"), interest("-2.42")));
        assertEquals(0, new BigDecimal("997.58").compareTo(out.acctCurrBal()));
    }}

    @Test
    void everyOtherFieldIsCarriedThroughUnchanged() throws Exception {{
        var in = account("1000.00", "25.00", "40.00");
        var out = new PostAccountInterestProcessor().process(
            new AccountInterestPosting(in, interest("2.42")));
        // 1050-UPDATE-ACCOUNT touches three fields. A body that rebuilt the record from defaults
        // would pass the assertions above and silently blank the rest of the account.
        assertEquals(in.acctId(), out.acctId());
        assertEquals(in.acctActiveStatus(), out.acctActiveStatus());
        assertEquals(in.acctCreditLimit(), out.acctCreditLimit());
        assertEquals(in.acctGroupId(), out.acctGroupId());
    }}
}}
"""


@pytest.fixture(scope="module")
def entry() -> ProgramDesignEntry:
    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return ProgramDesignEntry(program_name=PROGRAM, spec_extraction=extraction, critique=critique)


@pytest.fixture(scope="module")
def entities(entry) -> list:
    return build_domain_entities(FIXTURE_ROOT, [entry])


def _author(body: str):
    def author(routing, system_prompt: str, user_content: str) -> str:
        return json.dumps({"imports": _IMPORTS, "body": body, "notes": ""})

    return author


def _generate(tmp_path: Path, entry, entities, body: str):
    document = build_design_document(
        [entry],
        unified_design=UnifiedDesign(
            domain_entities=entities,
            composite_types=[POSTING],
            batch_jobs=[
                BatchJobDesign(
                    program_name=PROGRAM,
                    job_name="interestJob",
                    description="Monthly interest calculation.",
                    domain_entities=[e.name for e in entities],
                    steps=[STEP],
                )
            ],
            rest_endpoints=[],
        ),
    )
    design_path = tmp_path / "design.json"
    design_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    output_dir = tmp_path / "target-project"

    outcome = run_generate(
        design_path,
        FIXTURE_ROOT,
        output_dir,
        author=_author(body),
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    return outcome, output_dir


#: Surefire's per-class summary line, e.g.
#: `[ERROR] Tests run: 4, Failures: 2, Errors: 0, ... -- in ...PostAccountInterestTest`
_SUREFIRE = re.compile(
    r"Tests run: (\d+), Failures: (\d+), Errors: (\d+).*?PostAccountInterestTest"
)


def _posting_result(raw_output: str) -> tuple[int, int, int]:
    """`(run, failures, errors)` for the posting test class alone, or `(0, 0, 0)` if it never ran."""
    for line in raw_output.splitlines():
        match = _SUREFIRE.search(line)
        if match:
            return tuple(int(g) for g in match.groups())  # type: ignore[return-value]
    return (0, 0, 0)


def _with_test(output_dir: Path) -> Path:
    destination = output_dir / "src" / "test" / "java" / Path(DEFAULT_PACKAGE.replace(".", "/"))
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "PostAccountInterestTest.java").write_text(POSTING_TEST, encoding="utf-8")

    # The template ships a Testcontainers test that pulls `postgres:16-alpine`. It is the
    # `template-build` CI job's business, not this module's, and leaving it in makes every result
    # here depend on a Docker daemon -- which is how the first run of this file reported a green
    # "the wrong bodies were rejected" while rejecting nothing: the build failed for all four bodies
    # because the image would not pull. Removed from the throwaway copy so a failure here can only
    # mean the posting logic.
    stack_test = destination.parent.parent / "BaselineStackTest.java"
    for candidate in output_dir.rglob("BaselineStackTest.java"):
        candidate.unlink()
    assert not stack_test.exists()
    return output_dir


def test_the_account_update_step_is_generated_rather_than_reported_not_generated(
    tmp_path, entry, entities
):
    """The direct inverse of ADR-0023's finding, and the thing G27's open half asked for.

    Under ADR-0023 this step had to carry a non-processor role, so `generate` reported it
    `not_generated` with its paragraph named -- honest, and no Java. Pre-aggregating the item makes
    it an ordinary processor, so the pipeline renders it like any other and the count moves.
    """
    outcome, _ = _generate(tmp_path, entry, entities, _FAITHFUL)

    assert outcome.succeeded, [o.reason for o in outcome.blocked]
    assert len(outcome.compiled) == 1
    assert not outcome.not_generated, (
        "the posting step is still being reported as one this pipeline does not render"
    )


def test_the_generated_posting_matches_the_cobol_under_real_maven(tmp_path, entry, entities):
    _, output_dir = _generate(tmp_path, entry, entities, _FAITHFUL)
    result = compile_project(_with_test(output_dir), goal="verify")

    run, failures, errors = _posting_result(result.raw_output)
    assert run == 4, f"the posting test class did not run ({run} cases); nothing was checked"
    assert (failures, errors) == (0, 0)
    assert result.succeeded, "\n".join(e.render() for e in result.errors) or result.raw_output[-2000:]


@pytest.mark.parametrize(
    ("name", "body"),
    [("forgets_cycle_reset", _FORGETS_CYCLE_RESET), ("replaces_balance", _REPLACES_BALANCE)],
)
def test_the_check_fails_the_bodies_it_is_supposed_to_fail(
    tmp_path, entry, entities, name, body
):
    """Shown to discriminate before the green result above is believed.

    Both bodies compile and read plausibly. `replaces_balance` is `MOVE` where COBOL says `ADD` --
    the single most likely mistranslation of this paragraph, and one that is wrong by the entire
    prior balance. `forgets_cycle_reset` posts correctly and leaves two fields the paragraph clears.
    """
    _, output_dir = _generate(tmp_path, entry, entities, body)
    result = compile_project(_with_test(output_dir), goal="verify")

    # **Attributed, not merely failed.** `assert not result.succeeded` was the first version of this
    # and it is worthless: a build that breaks for any reason at all satisfies it. It passed on the
    # first run of this file while rejecting nothing, because a Testcontainers image would not pull.
    # So the assertion is that *the posting class itself* reported failures.
    run, failures, errors = _posting_result(result.raw_output)
    assert run == 4, f"{name}: the posting test class did not run, so nothing rejected it"
    assert failures + errors > 0, (
        f"{name} satisfied the posting check, so the check has no teeth against it"
    )
    assert not result.succeeded
