"""`design_solution` refuses the step-49 design where it is produced, and accepts the repair.

ADR-0020 decision 5 already stated the rule this follows -- *a design that cannot be generated from
should fail before a human approves it at the gate, not after* -- and ADR-0059 established its
shape: state the rule in the prompt, enforce it on the way out, and let `parse_with_repair` spend
one attempt carrying a message that names the fix.

What makes this worth an end-to-end test rather than only a unit one is the timing, which is the
whole defect. The step-49 design passed every check, a human approved it, `generate` rendered a
processor that was structurally correct, and the discarded value surfaced only when a person read
the generated Java at the release gate. These tests assert the refusal happens at the *first* of
those points.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import ProgramDesignEntry
from cobol_modernizer.nodes.solution_architect import (
    SolutionArchitectParseError,
    design_solution,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBACT04C"

RATED = {
    "name": "RatedCategoryBalance",
    "components": [
        {"field_name": "categoryBalance", "entity_name": "TranCatBal"},
        {"field_name": "disclosureGroup", "entity_name": "DisGroup"},
    ],
}

ACCRUED = {
    "name": "AccruedCategoryInterest",
    "components": RATED["components"],
    "computed_fields": [
        {"field_name": "monthlyInterest", "cobol_field_name": "WS-MONTHLY-INT"},
        {"field_name": "accountTotalInterest", "cobol_field_name": "WS-TOTAL-INT"},
    ],
}


def _response(*, output_type: str, composites: list[dict]) -> str:
    return json.dumps(
        {
            "composite_types": composites,
            "batch_jobs": [
                {
                    "program_name": PROGRAM,
                    "job_name": "interestJob",
                    "domain_entities": ["TranCatBal", "DisGroup"],
                    "steps": [
                        {
                            "step_name": "computeMonthlyInterest",
                            "source_paragraphs": ["1300-COMPUTE-INTEREST", "1400-COMPUTE-FEES"],
                            "role": "processor",
                            "description": "Compute monthly interest for a rated category balance.",
                            "input_type": "RatedCategoryBalance",
                            "output_type": output_type,
                            "guard_condition": None,
                        }
                    ],
                }
            ],
            "rest_endpoints": [],
        }
    )


@pytest.fixture(scope="module")
def programs() -> list[ProgramDesignEntry]:
    extraction = extract_spec(
        FIXTURE_ROOT,
        PROGRAM,
        narrate=lambda m, s, u: u.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0],
    )
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return [ProgramDesignEntry(program_name=PROGRAM, spec_extraction=extraction, critique=critique)]


def test_the_step_49_design_is_refused_and_the_repair_names_the_fix(programs) -> None:
    """Two attempts, both bad, and the second one carried the instruction that would have helped.

    `parse_with_repair` re-asks with the failure appended, so the repair prompt is where the model
    is told what to do. Asserting on it rather than only on the exception is the difference between
    a check that refuses and one that teaches -- and the message is read by a model, not only by a
    person.
    """
    prompts: list[str] = []

    def architect(routing, system_prompt, user_content):
        prompts.append(user_content)
        return _response(output_type="RatedCategoryBalance", composites=[RATED])

    with pytest.raises(SolutionArchitectParseError) as raised:
        design_solution(FIXTURE_ROOT, programs, architect=architect)

    assert "WS-MONTHLY-INT, WS-TOTAL-INT" in str(raised.value)
    assert len(prompts) == 2, "the repair attempt was not made"
    repair = prompts[1]
    assert "computes WS-MONTHLY-INT, WS-TOTAL-INT" in repair
    assert "computed_fields entry" in repair


def test_the_repaired_design_is_accepted_and_carries_the_computed_field(programs) -> None:
    """The fix the message describes, and what it produces: a value with somewhere to go."""
    attempts: list[str] = []

    def architect(routing, system_prompt, user_content):
        attempts.append(user_content)
        if len(attempts) == 1:
            return _response(output_type="RatedCategoryBalance", composites=[RATED])
        return _response(output_type="AccruedCategoryInterest", composites=[RATED, ACCRUED])

    design = design_solution(FIXTURE_ROOT, programs, architect=architect)

    accrued = next(c for c in design.composite_types if c.name == "AccruedCategoryInterest")
    assert [c.cobol_field_name for c in accrued.computed_fields] == [
        "WS-MONTHLY-INT",
        "WS-TOTAL-INT",
    ]
    assert design.unresolvable_type_names() == []

    # And the precision reaches the design as pic_mapper's number, not the model's.
    monthly = design.resolve_computed_value(PROGRAM, "WS-MONTHLY-INT")
    assert monthly is not None
    assert (monthly.precision, monthly.scale) == (11, 2)


def test_the_architect_is_told_the_precision_rather_than_left_to_infer_it(programs) -> None:
    """Run 3 of step 39 inferred `precision 11, scale 2` off a PIC clause and said so itself.

    The model was right and flagged that it was guessing: *"WS-MONTHLY-INT should be added to the
    pic_mapper fact list rather than left to be read off the PIC clause here."* This asserts it now
    is.
    """
    seen: list[str] = []

    def architect(routing, system_prompt, user_content):
        seen.append(user_content)
        return _response(output_type="AccruedCategoryInterest", composites=[RATED, ACCRUED])

    design_solution(FIXTURE_ROOT, programs, architect=architect)

    facts = seen[0]
    assert "## Computed values per program" in facts
    assert "| CBACT04C | WS-MONTHLY-INT | BigDecimal | 11 | 2 | 1300-COMPUTE-INTEREST |" in facts
    # And the column that says a value must survive the step.
    assert "(nothing -- local to its paragraph)" in facts
