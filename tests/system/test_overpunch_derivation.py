"""What a trailing sign overpunch is worth, derived by hand and then checked against the runtime.

**Why this exists.** `CBTRN02C`'s round trip disagrees with its oracle on seven decisions, and every
one of them traces to a `DALYTRAN-AMT` whose final byte is an overpunch. Deciding which side is
right is a question about a *number*, so it gets ADR-0021's treatment: derive the answer from the
source and the standard by hand, write the literals down, and make the runtime match them -- never
the reverse.

**The literals below are hand-written, not computed.** That is the whole point. Deriving them with
the same decoder the test then exercises would compare two renderings of one interpretation -- the
check-that-cannot-fail pattern ADR-0021 refused as option (c), and the reason this module contains
no arithmetic of its own.

**The corpus corroborates the table without running anything**, which is what makes twenty hand
literals more than twenty assertions of my own arithmetic: in every one of these records the digit
immediately *before* the overpunch equals the digit the overpunch carries, so the cents read as a
repeated pair. That is a property of how this corpus was generated, and it agrees with the standard
table for all twenty characters. A wrong table would break the pattern for eighteen of them.

See ADR-0043 for the derivation, the compiler probe that settles what GnuCOBOL does instead, and
where the fix belongs.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from cobol_modernizer.tools.data_loader import decode_zoned_decimal

CORPUS = (
    Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample" / "app" / "data" / "ASCII"
)
ORACLE = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "oracle"

#: One real `DALYTRAN-AMT` per overpunch character, with the value derived by hand.
#:
#: `PIC S9(09)V99` is eleven digit positions with the sign carried on the last one. `{` is `+0`,
#: `A`-`I` are `+1`..`+9`, `}` is `-0`, `J`-`R` are `-1`..`-9`; the letter *replaces* the final
#: digit rather than sitting beside it, so `0000005047G` is `00000050477` scaled by two -- 504.77 --
#: and not 504.70.
#:
#: Every one of the twenty is present in this corpus, so the table is exercised exhaustively rather
#: than sampled.
DERIVED: tuple[tuple[str, str], ...] = (
    ("0000003250{", "325.00"),
    ("0000004161A", "416.11"),
    ("0000002502B", "250.22"),
    ("0000000943C", "94.33"),
    ("0000000294D", "29.44"),
    ("0000008295E", "829.55"),
    ("0000004546F", "454.66"),
    ("0000005047G", "504.77"),
    ("0000000678H", "67.88"),
    ("0000008499I", "849.99"),
    ("0000009190}", "-919.00"),
    ("0000008351J", "-835.11"),
    ("0000000752K", "-75.22"),
    ("0000002153L", "-215.33"),
    ("0000003584M", "-358.44"),
    ("0000004455N", "-445.55"),
    ("0000009456O", "-945.66"),
    ("0000000567P", "-56.77"),
    ("0000005358Q", "-535.88"),
    ("0000000709R", "-70.99"),
)


@pytest.mark.parametrize(("raw", "expected"), DERIVED)
def test_the_decoder_reproduces_the_hand_derived_value(raw: str, expected: str):
    """The runtime is measured against the derivation, not the derivation against the runtime."""
    assert decode_zoned_decimal(raw, scale=2, signed=True) == Decimal(expected)


def test_every_overpunch_character_appears_in_the_corpus():
    """So the table above is exhaustive over the standard, not a sample of the easy half.

    Ten positive characters and ten negative ones, each with a real record behind it. A table that
    covered only `{` and `}` would agree with a decoder that dropped the digit entirely, which is
    precisely the behaviour in dispute.
    """
    amounts = {
        line[132:143][-1]
        for line in (CORPUS / "dailytran.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    }
    assert amounts == set("{ABCDEFGHI}JKLMNOPQR")
    assert {raw[-1] for raw, _ in DERIVED} == amounts


def test_the_corpus_corroborates_the_table_from_its_own_construction():
    """In every record the digit before the overpunch equals the digit the overpunch carries.

    `...4161A` reads 416.**11**, `...5047G` reads 504.**77**, `...0709R` reads -70.**99**. That is
    how this corpus was generated, and it is evidence *about the table* rather than about the
    decoder: under a wrong table the repeated pair would hold only for `{` and `}` and break for the
    other eighteen characters.
    """
    table = "{ABCDEFGHI}JKLMNOPQR"
    for raw, _ in DERIVED:
        carried = str(table.index(raw[-1]) % 10)
        assert raw[-2] == carried, f"{raw} breaks the repeated-digit pattern"


def test_the_oracle_disagrees_with_the_derivation_and_the_disagreement_is_the_lost_digit():
    """`transact-stage1.dat` holds the same amounts with the overpunch byte replaced by `0`.

    Not a formatting difference on the way out: ADR-0043's compiler probe shows the same value
    arriving *into* GnuCOBOL's arithmetic, and an account whose single posted transaction was
    `0000000294D` ends the run with a cycle-credit total of 29.40 rather than 29.44.

    Asserted here so the oracle's own limits are a checked fact rather than a note: whatever else
    changes, this file is not evidence about `TRAN-AMT`.
    """
    daily = {
        line[0:16]: line[132:143]
        for line in (CORPUS / "dailytran.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    }
    raw = (ORACLE / "transact-stage1.dat").read_bytes().decode("latin-1")
    written = {raw[i : i + 16]: raw[i + 132 : i + 143] for i in range(0, len(raw), 350)}

    carrying_a_digit = [
        tran_id
        for tran_id, source in daily.items()
        if tran_id in written and source[-1] not in "{}0123456789"
    ]
    assert len(carrying_a_digit) > 200

    for tran_id in carrying_a_digit:
        source = daily[tran_id]
        assert written[tran_id] == source[:-1] + "0", (
            "the oracle kept the leading digits and zeroed the one the overpunch carried"
        )
        assert decode_zoned_decimal(written[tran_id], scale=2, signed=True) != decode_zoned_decimal(
            source, scale=2, signed=True
        ), "so the value it recorded is not the value the corpus holds"
