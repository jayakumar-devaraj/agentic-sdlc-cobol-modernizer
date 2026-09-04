"""A `generate` result always says something about correctness (ADR-0064).

Two live runs shipped wrong money past the release gate. In both, the gate rendered:

    Generated and compiled N processor step(s).

That sentence is true, contains no claim about correctness, and reads as success. A gate that
reports quantities trains the approver to approve, and the only thing that actually caught either
defect was a person reading the generated Java.

These tests pin the property that makes that sentence honest: **the verdict is never absent.** A run
that could not execute the comparison says `not_run` and says why, which is a materially different
thing for a human to weigh than a summary that leaves the subject out.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.cli import _describe_equivalence
from cobol_modernizer.core.contracts import NOT_RUN, EquivalenceVerdict, GenerateCliResult


def _result(**kwargs) -> GenerateCliResult:
    return GenerateCliResult(status="ok", run_id="r", output_path="/out", detail="d", **kwargs)


def test_a_result_that_says_nothing_about_equivalence_cannot_be_constructed() -> None:
    """The default is a verdict, not `None`. Silence is the failure mode being removed."""
    result = _result()

    assert result.equivalence.status == "not_run"
    assert result.equivalence.reason, "a not_run verdict must say why"


def test_not_run_survives_a_json_round_trip() -> None:
    """control-plane reads this as JSON; a default that vanished on serialisation would be silence."""
    payload = _result().model_dump_json()

    assert '"status":"not_run"' in payload
    assert "equivalence" in GenerateCliResult.model_validate_json(payload).model_dump()


def test_the_default_is_not_shared_between_results() -> None:
    """`NOT_RUN` is a module-level instance; a result mutating it would poison every later run."""
    first, second = _result(), _result()
    first.equivalence.mismatches.append("poisoned")

    assert second.equivalence.mismatches == []
    assert NOT_RUN.mismatches == []


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        (NOT_RUN, "NOT RUN"),
        (EquivalenceVerdict(status="matched", reason="r"), "MATCHED"),
        (EquivalenceVerdict(status="mismatched", reason="r"), "MISMATCHED"),
    ],
)
def test_every_status_renders_a_line_a_reviewer_can_act_on(verdict, expected) -> None:
    """Including `matched` -- the qualifiers belong beside the answer, not in a document."""
    assert _describe_equivalence(verdict).startswith(expected)


def test_a_match_states_what_was_compared_and_what_was_not() -> None:
    """A differential's exclusions are decisions (ADR-0029), and hiding them overstates the result."""
    rendered = _describe_equivalence(
        EquivalenceVerdict(
            status="matched",
            reason="r",
            records_compared=50,
            fields_compared=550,
            excluded_fields=["TRAN-ID", "TRAN-ORIG-TS"],
        )
    )

    assert "50 record(s)" in rendered
    assert "550 field(s)" in rendered
    assert "2 field(s) excluded by decision" in rendered


def test_a_mismatch_shows_the_first_few_and_says_how_many_more() -> None:
    """A gate item nobody can read is not evidence either -- one entry per field per record is
    what an early divergence produces."""
    rendered = _describe_equivalence(
        EquivalenceVerdict(
            status="mismatched", reason="r", mismatches=[f"m{i}" for i in range(9)]
        )
    )

    assert "m0; m1; m2" in rendered
    assert "(+6 more)" in rendered
    assert "m8" not in rendered
