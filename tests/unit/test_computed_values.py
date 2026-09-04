"""The design carries what the program computes -- `pic_mapper`'s facts, not a model's reading.

`build_domain_entities` and `build_computed_values` iterate the same groups from
`group_field_mappings_by_source`. The first skips the program's own group because a domain entity
is copybook-sourced by definition; the second is what stops that group from being *discarded*.
Together they are the whole of what the deterministic layer knows about a program's fields.

The numbers asserted below are the ones that reached step 49's generated Java as
`requireFits(monthlyInterest, 11, 2)` -- correct, and inferred by a model off a `PIC` clause in
untrusted narration, which said so itself. They are now computed and handed over.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import SCHEMA_VERSION, ProgramDesignEntry, UnifiedDesign
from cobol_modernizer.nodes.solution_architect import build_computed_values
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBACT04C"


@pytest.fixture(scope="module")
def values():
    """Built from a real `ProgramDesignEntry`, with the two model calls stubbed out.

    `build_computed_values` reads only `program_name` from the entry -- it re-resolves the program
    from the worktree, exactly as `build_domain_entities` does and for the same reason -- so the
    narration and critique are stubs rather than fixtures pretending to be model output.
    """

    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    entry = ProgramDesignEntry(program_name=PROGRAM, spec_extraction=extraction, critique=critique)
    return build_computed_values(FIXTURE_ROOT, [entry])


def test_the_monthly_interest_carries_the_precision_a_model_used_to_infer(values) -> None:
    """`WS-MONTHLY-INT` is `PIC S9(09)V99` -- 9 integer digits and 2 decimals, so 11 and 2."""
    monthly = next(v for v in values if v.cobol_field_name == "WS-MONTHLY-INT")

    assert monthly.program_name == "CBACT04C"
    assert (monthly.java_type, monthly.precision, monthly.scale) == ("BigDecimal", 11, 2)
    assert monthly.signed is True
    assert monthly.computed_in_paragraphs == ["1300-COMPUTE-INTEREST"]


def test_the_values_are_charged_to_the_paragraph_that_computes_them(values) -> None:
    """The accumulator and the interest are one step's; the transaction counter is another's.

    This is what makes the check in `undeliverable_computed_values` possible at all. Were these
    attributed to the job, `computeMonthlyInterest` and `writeInterestTransaction` would answer for
    each other's values and no design could satisfy both.
    """
    by_field = {v.cobol_field_name: v.computed_in_paragraphs for v in values}

    assert by_field == {
        "WS-MONTHLY-INT": ["1300-COMPUTE-INTEREST"],
        "WS-TOTAL-INT": ["1300-COMPUTE-INTEREST"],
        "WS-TRANID-SUFFIX": ["1300-B-WRITE-TX"],
    }


def test_no_io_plumbing_reaches_the_design(values) -> None:
    """`CBACT04C` has 52 own fields and 17 numeric ones; 3 of them are values it computes.

    The 14 numeric fields left out are `FD-*` file-key aliases, `IO-STATUS-0401`/`0403`,
    `APPL-RESULT`, `ABCODE`, `TIMING`, `TWO-BYTES-BINARY` and `PARM-LENGTH` -- none of which the
    program's arithmetic writes. A filter on Java type would have admitted all of them, and a filter
    on `scale > 0` would have worked here by luck and lied on the first integer quantity.
    """
    assert {v.cobol_field_name for v in values} == {
        "WS-MONTHLY-INT",
        "WS-TOTAL-INT",
        "WS-TRANID-SUFFIX",
    }


def test_resolution_is_scoped_to_the_program(values) -> None:
    """`WS-MONTHLY-INT` resolves within `CBACT04C` and nowhere else."""
    design = UnifiedDesign(
        domain_entities=[], batch_jobs=[], rest_endpoints=[], computed_values=values
    )

    assert design.resolve_computed_value("CBACT04C", "WS-MONTHLY-INT") is not None
    assert design.resolve_computed_value("CBTRN02C", "WS-MONTHLY-INT") is None
    assert design.resolve_computed_value("CBACT04C", "WS-NO-SUCH-FIELD") is None


def test_a_design_without_computed_values_still_validates() -> None:
    """Additive, so schema 3.10.0 does not break a design written against 3.9.0."""
    design = UnifiedDesign(domain_entities=[], batch_jobs=[], rest_endpoints=[])

    assert design.computed_values == []
    assert SCHEMA_VERSION == "3.10.0"
