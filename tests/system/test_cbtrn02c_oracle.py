"""`CBTRN02C`'s own transaction master, captured so a round trip for it has something to measure.

**What was missing.** The oracle pipeline has always run `CBTRN02C` first -- the shipped `tcatbal`
is the pre-posting state, so stage 1 is what gives `CBACT04C` balances to compute interest on. Two
of that stage's outputs were already captured, because `CBACT04C` needs them as *inputs*: the posted
`tcatbal` and the account file between the stages. Its **primary** output, the transaction master it
`OPEN OUTPUT`s and writes every accepted daily transaction to, went to the container's work
directory and vanished with the container.

So the program's own comparison had two of its three in-scope targets and no way to check the third.
`UNLOADTR.cbl` captures it, the way `UNLOADTC` and `UNLOADAC` capture the other two.
(`DALYREJS` is the fourth output and is out of scope for generation by ADR-0038, so it is neither
captured nor compared.)

**The count is checked against the run's own report rather than against a number typed here.** The
program prints how many transactions it processed and how many it rejected; the difference is how
many it wrote. Asserting `257` directly would be asserting a number produced by running the code --
the shape that let `test_spec_extractor.py` encode a defect as its expectation (`len(mappings) ==
75`), and the shape `run-oracle.sh`'s own count assertions exist to avoid. Here the fixture and the
provenance are two artifacts of one run, and each is evidence about the other.
"""

from __future__ import annotations

import re
from pathlib import Path

ORACLE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "oracle"
TRANSACT_STAGE1 = ORACLE_DIR / "transact-stage1.dat"
PROVENANCE = ORACLE_DIR / "PROVENANCE.md"

#: `CVTRA05Y`'s record, and the `FD` `CBTRN02C` writes through: `FD-TRANS-ID PIC X(16)` then
#: `FD-ACCT-DATA PIC X(334)`.
RECORD_LENGTH = 350
KEY_WIDTH = 16


def _records() -> list[str]:
    raw = TRANSACT_STAGE1.read_bytes().decode("latin-1")
    assert len(raw) % RECORD_LENGTH == 0, (
        f"{TRANSACT_STAGE1.name} is not a whole number of records: {len(raw)} bytes"
    )
    return [raw[i : i + RECORD_LENGTH] for i in range(0, len(raw), RECORD_LENGTH)]


def _reported_counts() -> tuple[int, int]:
    """`(processed, rejected)` as `CBTRN02C` itself reported them, from the run's provenance."""
    line = next(
        line for line in PROVENANCE.read_text(encoding="utf-8").splitlines() if "PROCESSED" in line
    )
    processed, rejected = (int(value) for value in re.findall(r":(\d{9})", line))
    return processed, rejected


def test_the_master_holds_every_transaction_the_program_says_it_accepted():
    """Records written == processed - rejected, both read off the run's own report.

    This is the identity that makes the fixture falsifiable. A truncated unload, a partially posted
    run, or a stage that silently stopped early all leave a file full of individually correct
    records -- and all of them break this equality.
    """
    processed, rejected = _reported_counts()
    assert (processed, rejected) == (300, 43), "the corpus this oracle was produced from"
    assert len(_records()) == processed - rejected


def test_the_master_is_in_key_order_because_an_indexed_unload_reads_it_that_way():
    """`UNLOADTR` reads `ACCESS MODE IS SEQUENTIAL` over an INDEXED file, so records come out sorted.

    Pinned because it is a property of the *fixture* that a candidate has to account for, not a
    property of the program: a writer that appends records as it produces them emits the same
    records in a different order. ADR-0037 leaves that comparison decision open deliberately, and
    this test is what makes the premise it rests on a checked fact rather than an assumption.
    """
    keys = [record[:KEY_WIDTH] for record in _records()]
    assert keys == sorted(keys)
    assert len(set(keys)) == len(keys), "TRAN-ID is the record key, so it cannot repeat"


def test_both_checks_above_are_shown_to_fail_before_they_are_believed():
    """The same two expressions, run against deliberately damaged copies of the real records.

    A count assertion and an ordering assertion both pass trivially on the file that produced them,
    so neither is evidence until it has been seen to fail. Damaged here in memory rather than on
    disk: the fixture is the artifact, and a test that rewrites it to prove a point is a test that
    can leave it rewritten.
    """
    records = _records()
    processed, rejected = _reported_counts()

    truncated = records[:-1]
    assert len(truncated) != processed - rejected, "an unload that stopped one record early"

    swapped = list(records)
    swapped[0], swapped[-1] = swapped[-1], swapped[0]
    keys = [record[:KEY_WIDTH] for record in swapped]
    assert keys != sorted(keys), "records emitted in production order rather than key order"


def test_the_provenance_lists_the_new_artifact_with_its_size():
    """The fixture directory is one run, and its provenance has to describe all of it.

    `run-oracle.sh` writes this file rather than a human, so an artifact the run produced and the
    provenance omits would mean the two had come from different runs -- which is exactly the state
    this commit had to avoid when adding a file to a directory that already had five.
    """
    text = PROVENANCE.read_text(encoding="utf-8")
    assert f"- transact-stage1.dat  {TRANSACT_STAGE1.stat().st_size} bytes" in text
    assert "CBTRN02C's comparable outputs are transact-stage1.dat" in text
