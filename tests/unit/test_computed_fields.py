"""Which working-storage fields a program computes -- the fact that had nowhere to go.

**What this closes.** `pic_mapper` computed `WS-MONTHLY-INT` at precision 11, scale 2;
`build_domain_entities` discarded it with the other 51 fields of `CBACT04C`'s own group, because it
keeps copybook-sourced fields only. The design therefore could not name the value the program's
central `COMPUTE` produces, and the generated processor computed the monthly interest and threw it
away -- legal Java, a correct renderer, and a design language with no word for the value.

The tests below run against the real `CBACT04C` and `CBACT01C` rather than hand-written COBOL
wherever the real program has the construct, because the two mistakes worth catching here are both
mistakes about real code: matching a `MOVE` that only resets an accumulator, and reading
`ADD 8 TO ZERO GIVING APPL-RESULT` as computing `ZERO`.
"""

from __future__ import annotations

from pathlib import Path

from cobol_modernizer.nodes.solution_architect import resolve_program
from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.parsing.computed_fields import computed_fields

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"


def _program(name: str) -> tuple[str, set[str]]:
    """A real program's source and its own working-storage vocabulary, as `pic_mapper` named it."""
    resolved = resolve_program(FIXTURE_ROOT, name)
    mappings, _unsupported = group_field_mappings_by_source(resolved)[name]
    return resolved.source_text, {mapping.field_name for mapping in mappings}


def test_the_interest_program_computes_exactly_three_fields() -> None:
    """The two the defect is about, plus the writer's counter -- and nothing else.

    `WS-MONTHLY-INT` and `WS-TOTAL-INT` are both computed in `1300-COMPUTE-INTEREST`, which
    `computeMonthlyInterest` owns; `WS-TRANID-SUFFIX` is computed in `1300-B-WRITE-TX`, which the
    writer owns. That split is the whole point: it is what lets a caller charge a value to the step
    that produces it rather than to the job.
    """
    source, vocabulary = _program("CBACT04C")

    assert computed_fields(source, vocabulary) == {
        "WS-MONTHLY-INT": {"1300-COMPUTE-INTEREST"},
        "WS-TOTAL-INT": {"1300-COMPUTE-INTEREST"},
        "WS-TRANID-SUFFIX": {"1300-B-WRITE-TX"},
    }


def test_arithmetic_in_the_main_loop_belongs_to_no_paragraph() -> None:
    """`ADD 1 TO WS-RECORD-COUNT` sits in the `PERFORM UNTIL` loop, outside every paragraph.

    **This is the function's real boundary, and it was found by probing rather than assumed.** A
    first version of this test claimed the `MOVE 0 TO WS-TOTAL-INT` beside it was excluded because
    a `MOVE` is not arithmetic. Adding a `MOVE` pattern at runtime changed nothing, which showed
    the true reason: both statements are in `CBACT04C`'s main loop, *before the first paragraph
    header*, and `extract_paragraphs` attributes them to no paragraph at all.

    The boundary is the right one for what a caller asks. A step declares `source_paragraphs`, so a
    value computed outside every paragraph belongs to no step by construction -- there is no step to
    charge it to. But it is a real limit, not an exclusion doing work, and it is stated as one:
    `WS-RECORD-COUNT` genuinely is computed, and this function does not report it.
    """
    source, vocabulary = _program("CBACT04C")

    assert "ADD 1 TO WS-RECORD-COUNT" in source
    assert "WS-RECORD-COUNT" in vocabulary
    assert "WS-RECORD-COUNT" not in computed_fields(source, vocabulary)

    # And the accumulator is reported only where `ADD ... TO` computes into it, not where the same
    # main loop zeroes it at a group boundary.
    assert "MOVE 0 TO WS-TOTAL-INT" in source
    assert computed_fields(source, vocabulary)["WS-TOTAL-INT"] == {"1300-COMPUTE-INTEREST"}


def test_giving_names_the_target_and_the_to_operand_is_not_one() -> None:
    """`ADD 8 TO ZERO GIVING APPL-RESULT` computes `APPL-RESULT`. `ZERO` receives nothing.

    Real code in this corpus, not a constructed case. Without the `GIVING` exclusion the `TO`
    pattern claims `ZERO` as well, so one statement would report two targets and one of them would
    be a figurative constant.
    """
    source, vocabulary = _program("CBACT01C")

    assert "ADD 8 TO ZERO GIVING APPL-RESULT" in source
    assert computed_fields(source, vocabulary) == {"APPL-RESULT": {"9000-ACCTFILE-CLOSE"}}


def test_subtract_from_names_its_receiving_field() -> None:
    """`SUBTRACT APPL-RESULT FROM APPL-RESULT` -- the corpus's only `SUBTRACT`, in the same paragraph."""
    source, vocabulary = _program("CBACT01C")

    assert "SUBTRACT APPL-RESULT FROM APPL-RESULT" in source
    assert "9000-ACCTFILE-CLOSE" in computed_fields(source, vocabulary)["APPL-RESULT"]


def test_a_name_outside_the_vocabulary_is_not_reported() -> None:
    """The vocabulary is what keeps this from having to understand literals, verbs or record fields.

    `TRAN-CAT-BAL` and `DIS-INT-RATE` are read by the same `COMPUTE` that writes `WS-MONTHLY-INT`,
    and `ACCT-CURR-BAL` receives an `ADD` of its own -- all record fields, none of them working
    storage, and none of this function's business.
    """
    source, _vocabulary = _program("CBACT04C")

    assert computed_fields(source, {"WS-MONTHLY-INT"}) == {
        "WS-MONTHLY-INT": {"1300-COMPUTE-INTEREST"}
    }


def test_giving_takes_the_target_away_from_a_declared_to_operand() -> None:
    """`ADD WS-FEE TO WS-BASE GIVING WS-TOTAL` computes `WS-TOTAL` only. `WS-BASE` is read.

    **Constructed, and labelled as such**: this corpus's four programs use `GIVING` only as
    `ADD 8 TO ZERO GIVING APPL-RESULT`, where the `TO` operand is a figurative constant the
    vocabulary already excludes -- so the real code cannot distinguish a correct exclusion from a
    missing one. Removing `_NO_GIVING` leaves every test above passing and fails this one, which is
    the only reason to keep the exclusion at all.
    """
    # Fixed-format columns are load-bearing: `extract_paragraphs` reads a header from Area A
    # (columns 8-11) and a statement from Area B (column 12+), so this fragment is indented the
    # way real COBOL is rather than the way Python would prefer.
    source = (
        "       PROCEDURE DIVISION.\n"
        "       1600-TOTAL-THE-FEES.\n"
        "           ADD WS-FEE TO WS-BASE GIVING WS-TOTAL\n"
        "           EXIT.\n"
    )

    assert computed_fields(source, {"WS-FEE", "WS-BASE", "WS-TOTAL"}) == {
        "WS-TOTAL": {"1600-TOTAL-THE-FEES"}
    }
