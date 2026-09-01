"""Every caveat the oracle's provenance names has a status, and a probe or a stated consequence.

**Why this test exists, in one sentence:** the oracle's `PROVENANCE.md` listed the zoned-decimal
sign representation as uncorroborated from the day it was generated, every downstream document
treated the oracle as ground truth anyway, and the caveat came due four revisions later as seven
wrong decisions in a round trip (ADR-0043, audit G33).

**So the list is an obligation rather than a footnote.** `docs/qa/oracle-caveats.md` gives each entry
one of two statuses -- *probed*, with an executable check named, or *accepted, untested*, with the
consequence of being wrong written out -- and this module fails if a caveat exists without one.

**What it deliberately does not do.** It does not require every caveat to be probed. Deferring is a
legitimate answer and three of the four are deferred; what is not legitimate is deferring silently.
The assertion is on the *shape* of the record, not on the verdict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[2] / "docs" / "qa"
CAVEATS = DOCS / "oracle-caveats.md"
PROVENANCE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "golden"
    / "CBACT04C"
    / "oracle"
    / "PROVENANCE.md"
)

#: The caveats `run-oracle.sh` writes into every provenance it generates, each keyed by a phrase
#: stable enough to match and specific enough not to match anything else.
#:
#: Keyed on the provenance's own words rather than on a list maintained here: a caveat this repo
#: forgot to copy across would then be invisible to the very test meant to catch it.
PROVENANCE_CAVEATS: tuple[tuple[str, str], ...] = (
    ("FUNCTION CURRENT-DATE formatting", "FUNCTION CURRENT-DATE"),
    ("STRING ... DELIMITED BY SIZE padding", "DELIMITED BY SIZE"),
    ("the sign of zero", "the sign of zero"),
    ("the zoned-decimal sign representation", "zoned-decimal sign representation"),
)

STATUSES = ("Probed", "Accepted, untested")


def _rows(caveats: str) -> list[str]:
    """The register's numbered table rows -- not its legend, which describes the same words.

    Matching on the prose would let a caveat inherit a status from the paragraph explaining what
    statuses are, which is the shape of check that passes while meaning nothing.
    """
    return [
        line
        for line in caveats.splitlines()
        if line.startswith("| ") and re.match(r"\|\s*\d+\s*\|", line)
    ]


@pytest.fixture(scope="module")
def caveats() -> str:
    return CAVEATS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def provenance() -> str:
    return PROVENANCE.read_text(encoding="utf-8")


def test_the_provenance_still_carries_the_caveats_this_register_answers(provenance):
    """The premise. If the provenance stopped listing these, this register would be answering air.

    Asserted first because every other test here reads as vacuously true otherwise -- a register
    whose subject has disappeared passes every check about its own contents.
    """
    for label, phrase in PROVENANCE_CAVEATS:
        assert phrase in provenance, f"provenance no longer names {label!r}"


@pytest.mark.parametrize(("label", "phrase"), PROVENANCE_CAVEATS)
def test_every_caveat_has_a_row_in_the_register(caveats, label: str, phrase: str):
    """A caveat the provenance names and this file does not is the failure mode that produced G33."""
    assert phrase.split(" ...")[0].lower() in caveats.lower(), (
        f"{label!r} is listed as unverified in the oracle's provenance and has no row in "
        f"{CAVEATS.name}. Add one -- probed with its check named, or accepted with the consequence "
        "of being wrong written out."
    )


def test_every_row_carries_one_of_the_two_statuses(caveats):
    """*"We are aware of it"* is not a status, and neither is an empty cell.

    Four rows, four statuses. Counted rather than spot-checked so that adding a fifth caveat without
    a status fails here instead of being read past.
    """
    rows = _rows(caveats)
    assert len(rows) == len(PROVENANCE_CAVEATS)
    for row in rows:
        assert any(status in row for status in STATUSES), f"no status in row: {row[:80]}"


def test_a_probed_caveat_names_a_check_that_exists(caveats):
    """A probe that cannot be re-run is a claim, and claims are what this register replaces."""
    for row in _rows(caveats):
        if "**Probed" not in row:
            continue
        referenced = re.findall(r"`(tools/[^`]+|tests/[^`]+)`", row)
        assert referenced, f"probed caveat names no check: {row[:80]}"
        for path in referenced:
            assert (Path(__file__).resolve().parents[2] / path).exists(), f"missing probe {path}"


def test_the_probe_that_failed_says_so(caveats):
    """The one probed caveat found the oracle wrong, and the register records the answer it got.

    Pinned because the tempting edit is the reassuring one: a register that only recorded probes
    which came back clean would be worse than no register, and this is the row that proves the
    convention survives an uncomfortable result.
    """
    assert "**Probed -- and it failed**" in caveats or "**Probed — and it failed**" in caveats
    assert "504.70" in caveats and "504.77" in caveats
    assert "G33" in caveats


def test_these_checks_are_shown_to_fail_before_they_are_believed(caveats):
    """A register test that passes on a register with a caveat missing is worse than none.

    So the same expressions are run against deliberately damaged copies: a row deleted, and a row
    whose status cell is empty. Damaged in memory rather than on disk -- the register is the record,
    and a test that rewrites it to prove a point is a test that can leave it rewritten.
    """
    without_a_row = "\n".join(
        line for line in caveats.splitlines() if "the sign of zero" not in line.lower()
    )
    assert "the sign of zero" not in without_a_row.lower(), "a caveat with no row goes unnoticed"

    statusless = caveats.replace("Accepted, untested", "we are aware of it")
    rows = [row for row in _rows(statusless) if any(s in row for s in STATUSES)]
    assert len(rows) < len(PROVENANCE_CAVEATS), (
        "a row whose status is reassurance rather than a status must not count as one"
    )
