"""ORACHK.cbl must stay in step with ADR-0021's table.

The dialect check is only meaningful if it tests the table that exists. ORACHK is generated from
`interest-oracle.json`, and a corrected or added row would otherwise leave it asserting values that
were superseded -- still reporting agreement, with a table nobody holds any more.

Same drift-check idiom the repo already applies to its exported JSON schemas.
"""

from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2] / "tools" / "cobol-oracle"
sys.path.insert(0, str(TOOLS))

from cobol_modernizer.core.package_data import ORACLE_ROOT

import generate_orachk


def test_the_committed_orachk_matches_the_oracle_table():
    committed = (TOOLS / "ORACHK.cbl").read_text(encoding="utf-8")
    assert committed == generate_orachk.render(), (
        "ORACHK.cbl is stale against interest-oracle.json. Regenerate it with "
        "`python tools/cobol-oracle/generate_orachk.py` -- do not hand-edit it, or the dialect "
        "check starts asserting a table that no longer exists."
    )


def test_orachk_compares_rather_than_only_displaying():
    """The finding this file exists because of.

    The first ORACHK printed `expected=X got=Y` and exited 0, so it could not fail however badly
    GnuCOBOL disagreed -- a check-that-cannot-fail sitting under the one claim the oracle's
    credibility rests on. These assertions pin the comparison, the non-zero exit, and the guard
    against an empty table silently passing.
    """
    text = (TOOLS / "ORACHK.cbl").read_text(encoding="utf-8")
    assert "IF W-INT NOT = W-EXP" in text, "ORACHK does not compare its result to the expectation"
    assert "MOVE 16 TO RETURN-CODE" in text, "ORACHK cannot fail the run"
    assert "IF W-CHECKS = 0" in text, "an empty table would pass silently"


def test_every_oracle_row_reaches_the_generated_program():
    import json

    rows = json.loads(
        (ORACLE_ROOT / "CBACT04C" / "interest-oracle.json").read_text(encoding="utf-8")
    )["rows"]
    text = (TOOLS / "ORACHK.cbl").read_text(encoding="utf-8")
    for row in rows:
        assert f'MISMATCH {row["id"]}:' in text, f"{row['id']} is not checked by ORACHK"
