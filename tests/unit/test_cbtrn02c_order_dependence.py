"""`CBTRN02C` decides what to accept from state it is itself writing, and the corpus proves it.

**Why this module exists.** The renderer now handles this program's files (ADR-0037), and the next
step would be to declare its steps and generate them. This is the measurement that says what such a
job could and could not reproduce -- taken before building it, because the answer changes what is
worth building.

**The mechanism, from the source.** `1500-B-LOOKUP-ACCT` decides whether a daily transaction is
accepted by computing `ACCT-CURR-CYC-CREDIT - ACCT-CURR-CYC-DEBIT + DALYTRAN-AMT` and comparing it
to `ACCT-CREDIT-LIMIT`. Those cycle fields are the ones `2800-UPDATE-ACCOUNT-REC` `ADD`s to and
`REWRITE`s for every accepted transaction. So the decision for transaction *n* reads what
transactions *1..n-1* wrote. `2700-UPDATE-TCATBAL` is the same shape against a different file:
`ADD DALYTRAN-AMT TO TRAN-CAT-BAL` on the row it just read.

**The proof is from committed artifacts, not from a replay.** `transact-stage1.dat` holds exactly
the transactions the program accepted, each carrying its `DALYTRAN-ID`, so the rejected set is known
exactly rather than modelled. Each rejected transaction is then judged the way a *stateless*
processor would have to judge it -- against the account's initial state, on its own. **25 of the 38
pass that check.** A per-item implementation writes 287 records where `CBTRN02C` writes 262, and
every one of the 287 is individually correct.

**287 did not move when ADR-0047 fixed the oracle, and that is a check rather than a coincidence.**
Before the fix the same arithmetic read 30 of 43 and 257 + 30. The split moved by five because
`accepted_ids` comes from the oracle, whose run had been deciding on amounts missing a digit; the
total did not move because the standalone check has always been computed from the *corpus* through
`decode_zoned_decimal`, which was right all along. A change in the total would have meant this
module's own inputs had shifted.

**What that rules out.** Not a particular decomposition -- *every* order-independent one. Grouping
by account, by balance key, or by anything else computes sums over a transaction set that is itself
wrong, because membership of that set is what the ordering decides.

ADR-0039 records the decision this measurement produced: the posting path is declared and refused by
name rather than generated. The finding is unaffected by ADR-0047 -- order dependence is a property
of the program, not of how its amounts were read.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CORPUS = FIXTURES / "tenant_repo_sample" / "app" / "data" / "ASCII"
ORACLE = FIXTURES / "golden" / "CBACT04C" / "oracle"

RECORD_LENGTH = 350
#: `CVTRA06Y` and `CVACT01Y` offsets, both verified against the copybooks in the repo.
DALYTRAN_ID = (0, 16)
DALYTRAN_AMT = (132, 11)
DALYTRAN_CARD = (262, 16)
XREF_CARD = (0, 16)
XREF_ACCT = (25, 11)
ACCT_ID = (0, 11)
ACCT_LIMIT = (24, 12)
ACCT_CYC_CREDIT = (78, 12)
ACCT_CYC_DEBIT = (90, 12)

_OVERPUNCH = {"{": ("0", 1), "}": ("0", -1)}
for _i, _letter in enumerate("ABCDEFGHI", start=1):
    _OVERPUNCH[_letter] = (str(_i), 1)
for _i, _letter in enumerate("JKLMNOPQR", start=1):
    _OVERPUNCH[_letter] = (str(_i), -1)


def _signed(raw: str, scale: int) -> Decimal:
    """Zoned decimal with a trailing sign overpunch, the form the corpus ships (G16, finding 2)."""
    text = raw.strip()
    sign = 1
    if text and text[-1] in _OVERPUNCH:
        digit, sign = _OVERPUNCH[text[-1]]
        text = text[:-1] + digit
    return Decimal(sign) * Decimal(text or 0) / (Decimal(10) ** scale)


def _field(record: str, where: tuple[int, int]) -> str:
    offset, width = where
    return record[offset : offset + width]


def _lines(name: str) -> list[str]:
    return [
        line for line in (CORPUS / name).read_text(encoding="latin-1").splitlines() if line.strip()
    ]


@pytest.fixture(scope="module")
def accounts() -> dict[str, dict[str, Decimal]]:
    """Each account's **initial** limit and cycle totals, before the run posts anything."""
    return {
        _field(line, ACCT_ID): {
            "limit": _signed(_field(line, ACCT_LIMIT), 2),
            "cyc_credit": _signed(_field(line, ACCT_CYC_CREDIT), 2),
            "cyc_debit": _signed(_field(line, ACCT_CYC_DEBIT), 2),
        }
        for line in _lines("acctdata.txt")
    }


@pytest.fixture(scope="module")
def account_of() -> dict[str, str]:
    return {_field(line, XREF_CARD): _field(line, XREF_ACCT) for line in _lines("cardxref.txt")}


@pytest.fixture(scope="module")
def daily() -> list[dict[str, object]]:
    return [
        {
            "id": _field(line, DALYTRAN_ID),
            "amt": _signed(_field(line, DALYTRAN_AMT), 2),
            "card": _field(line, DALYTRAN_CARD),
        }
        for line in _lines("dailytran.txt")
    ]


@pytest.fixture(scope="module")
def accepted_ids() -> set[str]:
    """The `TRAN-ID` of every transaction the program actually wrote -- the oracle's own record."""
    raw = (ORACLE / "transact-stage1.dat").read_bytes().decode("latin-1")
    return {raw[i : i + 16] for i in range(0, len(raw), RECORD_LENGTH)}


def _passes_on_its_own(transaction, accounts, account_of) -> bool:
    """`1500-B-LOOKUP-ACCT`'s check, evaluated against the account's **initial** state.

    This is what a stateless `ItemProcessor` can compute: the item, plus lookups against the files
    as the job found them. It is the whole of the decision except for the part that is not
    available to it.
    """
    account = accounts[account_of[transaction["card"]]]
    projected = account["cyc_credit"] - account["cyc_debit"] + transaction["amt"]
    return account["limit"] >= projected


def test_the_corpus_and_the_oracle_agree_on_how_many_were_accepted(daily, accepted_ids):
    """300 in, 262 written, 38 rejected -- the premise everything below rests on."""
    assert len(daily) == 300
    assert len(accepted_ids) == 262
    assert len({t["id"] for t in daily} - accepted_ids) == 38


def test_most_rejected_transactions_would_pass_if_judged_on_their_own(
    daily, accepted_ids, accounts, account_of
):
    """**The finding.** 25 of the 38 rejections are caused by ordering, not by the transaction.

    Each of these passes the credit-limit check against the account as the job *found* it, and was
    rejected only because earlier transactions in the same run had already consumed the limit. A
    stateless implementation therefore writes **287** records -- 262 plus these 25 -- and every one
    of them is individually correct, which is what makes this invisible to a field comparison.

    The 287 is asserted separately from the 25 on purpose: it survived ADR-0047 unchanged while the
    25 moved from 30, so the two assertions fail for different reasons and say different things.
    """
    rejected = [t for t in daily if t["id"] not in accepted_ids]
    order_only = [t for t in rejected if _passes_on_its_own(t, accounts, account_of)]

    assert len(order_only) == 25
    assert len(accepted_ids) + len(order_only) == 287


def test_no_accepted_transaction_needed_the_ordering_to_pass(
    daily, accepted_ids, accounts, account_of
):
    """The error runs one way only, which is worth pinning because it is not obvious.

    Every accumulation here moves the projected balance toward the limit, so ordering can only turn
    an acceptance into a rejection. A stateless implementation is therefore a strict superset -- it
    never *misses* a transaction, it writes extra ones. If this ever fails, the corpus has gained a
    case where the two disagree in both directions and the finding above understates the problem.
    """
    accepted = [t for t in daily if t["id"] in accepted_ids]
    assert [t for t in accepted if not _passes_on_its_own(t, accounts, account_of)] == []


def test_the_check_is_shown_to_discriminate(daily, accounts, account_of):
    """An acceptance test that accepts everything would pass the two above without meaning anything.

    So: the standalone check must reject *something* on this corpus, and it must be evaluating the
    limit rather than a constant. Both are asserted against a copy with an impossible amount.
    """
    over = dict(daily[0])
    over["amt"] = Decimal("999999999.99")
    assert not _passes_on_its_own(over, accounts, account_of)

    under = dict(daily[0])
    under["amt"] = Decimal("-999999999.99")
    assert _passes_on_its_own(under, accounts, account_of)
