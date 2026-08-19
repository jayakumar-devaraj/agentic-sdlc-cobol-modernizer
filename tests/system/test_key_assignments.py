"""What a keyed lookup is looked up *by* -- the join predicate (G31), parsed rather than declared.

**Why this is the last missing fact.** `FILE-CONTROL` says `ACCOUNT-FILE` is read by `FD-ACCT-ID`.
It does not say what value goes in there, and without that a renderer knows a lookup exists and not
how to perform it. ADR-0030 refused an LLM-declared join because a wrong one produces plausible rows
and a silently wrong comparison; it turns out nobody has to declare it, because
`MOVE TRANCAT-ACCT-ID TO FD-ACCT-ID` already does.

**The `'DEFAULT'` fallback is the case worth reading.** It was finding F4 -- business logic the
hand-written wiring had to carry because the design could not express it -- and it arrives here as
an ordinary second assignment to the same key field, with a literal instead of a source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import ProgramDesignEntry
from cobol_modernizer.nodes.solution_architect import build_file_access_paths
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.parsing.key_assignments import extract_key_assignments, key_components

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
CBACT04C = (FIXTURE_ROOT / "app" / "cbl" / "CBACT04C.cbl").read_text(encoding="latin-1")


@pytest.fixture(scope="module")
def paths():
    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    return {path.select_name: path for path in build_file_access_paths(FIXTURE_ROOT, [entry])}


# --- resolving a key into the fields that make it -----------------------------------------------------


def test_a_group_key_resolves_to_its_components_in_record_order():
    """`FD-DISCGRP-KEY` is a group of three, and the program fills each separately.

    Resolved from the `FD` record's level numbers, not from name prefixes: a field that merely looks
    like a member of the group is not evidence that it is one, and a key assembled from the wrong
    fields looks entirely plausible while matching nothing.
    """
    assert key_components(CBACT04C, "FD-DISCGRP-KEY") == [
        "FD-DIS-ACCT-GROUP-ID",
        "FD-DIS-TRAN-TYPE-CD",
        "FD-DIS-TRAN-CAT-CD",
    ]


def test_an_elementary_key_resolves_to_itself():
    assert key_components(CBACT04C, "FD-ACCT-ID") == ["FD-ACCT-ID"]


def test_an_unknown_key_resolves_to_itself_rather_than_to_nothing():
    """A key this parse cannot find is still a key. Returning `[]` would silently drop the lookup."""
    assert key_components(CBACT04C, "FD-NO-SUCH-KEY") == ["FD-NO-SUCH-KEY"]


# --- the assignments themselves -----------------------------------------------------------------------


def test_the_moves_that_fill_the_keys_are_found_with_their_sources():
    found = {
        (a.key_field, a.source_field or a.literal)
        for a in extract_key_assignments(
            CBACT04C,
            {"FD-ACCT-ID", "FD-XREF-ACCT-ID", "FD-DIS-ACCT-GROUP-ID", "FD-DIS-TRAN-CAT-CD"},
        )
    }
    assert found == {
        ("FD-ACCT-ID", "TRANCAT-ACCT-ID"),
        ("FD-XREF-ACCT-ID", "TRANCAT-ACCT-ID"),
        ("FD-DIS-ACCT-GROUP-ID", "ACCT-GROUP-ID"),
        ("FD-DIS-ACCT-GROUP-ID", "DEFAULT"),
        ("FD-DIS-TRAN-CAT-CD", "TRANCAT-CD"),
    }


def test_a_move_into_a_field_that_is_not_a_key_is_ignored():
    """The scope is deliberately narrow: what fills a lookup key, not a general move graph.

    `CBACT04C` moves into dozens of fields; a parse that collected them all would be making a much
    larger claim than a reader needs, and every extra edge is one more thing to be wrong about.
    """
    every_move = re.findall(r"\bMOVE\b", CBACT04C, re.IGNORECASE)
    assignments = extract_key_assignments(CBACT04C, {"FD-ACCT-ID"})

    assert {a.key_field for a in assignments} == {"FD-ACCT-ID"}
    # The filter has to have something to filter, or this passes for a parse that finds nothing.
    assert len(every_move) > 10 * len(assignments), (
        f"{len(every_move)} MOVEs in the program and {len(assignments)} matched; if those numbers "
        "are close, this test is not demonstrating that anything was excluded"
    )


def test_a_literal_is_distinguished_from_a_field():
    """`MOVE 'DEFAULT' TO ...` fills the key with a constant, and a renderer must not look for a
    field of that name."""
    default = next(
        a
        for a in extract_key_assignments(CBACT04C, {"FD-DIS-ACCT-GROUP-ID"})
        if a.is_literal
    )
    assert default.literal == "DEFAULT"
    assert default.source_field is None


def test_double_quoted_literals_are_read_too():
    """COBOL allows either quote character, and a parse that knew only one would miss the other."""
    moved = extract_key_assignments('           MOVE "DEFAULT" TO FD-KEY.', {"FD-KEY"})
    assert [a.literal for a in moved] == ["DEFAULT"]


def test_each_assignment_carries_its_source_line():
    for assignment in extract_key_assignments(CBACT04C, {"FD-ACCT-ID"}):
        line = CBACT04C.splitlines()[assignment.source_line - 1]
        assert "MOVE" in line.upper() and assignment.key_field in line.upper()


# --- what reaches design.json -------------------------------------------------------------------------


def test_the_account_lookup_carries_its_join(paths):
    """`account.acctId == tranCatBal.trancatAcctId`, stated by the COBOL and now by the design."""
    parts = paths["ACCOUNT-FILE"].key_parts
    assert [(p.key_field, p.source_field) for p in parts] == [
        ("FD-ACCT-ID", "TRANCAT-ACCT-ID")
    ]
    assert not any(part.is_fallback for part in parts)


def test_the_xref_lookup_carries_only_the_key_it_is_actually_read_by(paths):
    """`XREF-FILE` declares two keys. Emitting parts for both would leave a renderer choosing.

    The read already chose -- `KEY IS FD-XREF-ACCT-ID` -- so only the alternate's fill appears.
    """
    parts = paths["XREF-FILE"].key_parts
    assert [p.key_field for p in parts] == ["FD-XREF-ACCT-ID"]
    assert parts[0].source_field == "TRANCAT-ACCT-ID"


def test_the_disclosure_group_lookup_carries_three_parts_and_its_fallback(paths):
    """Finding F4, as data. The retry under `'DEFAULT'` is a marked second fill, not a lost rule."""
    parts = paths["DISCGRP-FILE"].key_parts
    primary = [(p.key_field, p.source_field) for p in parts if not p.is_fallback]
    assert primary == [
        ("FD-DIS-ACCT-GROUP-ID", "ACCT-GROUP-ID"),
        ("FD-DIS-TRAN-TYPE-CD", "TRANCAT-TYPE-CD"),
        ("FD-DIS-TRAN-CAT-CD", "TRANCAT-CD"),
    ]
    fallbacks = [p for p in parts if p.is_fallback]
    assert [(p.key_field, p.literal) for p in fallbacks] == [
        ("FD-DIS-ACCT-GROUP-ID", "DEFAULT")
    ]


def test_the_group_lookup_depends_on_the_account_lookup(paths):
    """The ordering a renderer has to respect, derivable from the sources rather than from prose.

    `FD-DIS-ACCT-GROUP-ID` is filled from `ACCT-GROUP-ID`, which is a field of the *account* record
    -- so the account lookup has to run first. Nothing states that ordering anywhere else.
    """
    group_sources = {p.source_field for p in paths["DISCGRP-FILE"].key_parts if p.source_field}
    assert "ACCT-GROUP-ID" in group_sources
    account_fields = {"ACCT-ID", "ACCT-GROUP-ID", "ACCT-CURR-BAL"}
    assert group_sources & account_fields, "the discgrp key depends on the account record"


def test_a_driving_stream_carries_no_key_parts(paths):
    """`TCATBAL-FILE` is walked in order; a key on it would be a claim the program does not make."""
    assert paths["TCATBAL-FILE"].key_parts == []
