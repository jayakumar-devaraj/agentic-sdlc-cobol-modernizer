"""ADR-0029's differential: field-for-field against COBOL's own output, with exclusions priced.

**What this is.** The comparison half of ADR-0028. `tests/fixtures/golden/CBACT04C/oracle/` holds
what the *unmodified* `CBACT04C` wrote when run under GnuCOBOL over the shipped corpus, and this
module parses it into fields and compares a candidate set of records against it.

**Why field-for-field** (ADR-0029). The target persists to PostgreSQL (ADR-0036) and nothing renders
a fixed-width writer, so there is no file to compare byte-for-byte -- building a serialiser whose only
consumer is the assertion about it would be a check written to match whatever it needed to match. And
byte equality is unreachable by *accepted decision*: ADR-0026 leaves `TRAN-ID` unpopulated and
supplies one run timestamp against COBOL's per-record clock. The oracle run demonstrated the point
independently -- rewriting an account record turned the overpunch `940{` into `9400`, identical in
value and different in bytes.

**Each field is compared at its full declared width**, so `PIC X(50)` compares fifty characters and
padding counts. What is excluded is record *framing*, never field contents.

**Exclusions are priced, not trusted.** Every entry in `EXCLUSIONS` names the ADR that makes the
field unproducible, and `test_every_exclusion_cites_a_decision` fails if one does not. Exclusion
creep is the failure mode that turns a differential toothless, and an ADR citation is what stands
between this and a comparison that passes by excluding whatever disagrees.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

ORACLE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "oracle"
TRANSACT = ORACLE_DIR / "transact.dat"
PROVENANCE = ORACLE_DIR / "PROVENANCE.md"

#: `CVTRA05Y`'s TRAN-RECORD, 350 bytes. Offsets are 0-based, widths are the copybook's.
#: Written out rather than derived from the copybook on purpose: this is the *comparison's* view of
#: the record, and if it ever disagrees with `pic_mapper`'s that disagreement should be visible here
#: rather than silently shared.
TRAN_LAYOUT: tuple[tuple[str, int, int, int | None], ...] = (
    # (field, offset, width, scale)  -- scale None means alphanumeric
    ("TRAN-ID", 0, 16, None),
    ("TRAN-TYPE-CD", 16, 2, None),
    ("TRAN-CAT-CD", 18, 4, 0),
    ("TRAN-SOURCE", 22, 10, None),
    ("TRAN-DESC", 32, 100, None),
    ("TRAN-AMT", 132, 11, 2),
    ("TRAN-MERCHANT-ID", 143, 9, 0),
    ("TRAN-MERCHANT-NAME", 152, 50, None),
    ("TRAN-MERCHANT-CITY", 202, 50, None),
    ("TRAN-MERCHANT-ZIP", 252, 10, None),
    ("TRAN-CARD-NUM", 262, 16, None),
    ("TRAN-ORIG-TS", 278, 26, None),
    ("TRAN-PROC-TS", 304, 26, None),
)

TRAN_RECORD_LEN = 350

#: `CVACT01Y`'s ACCOUNT-RECORD, 300 bytes -- the *other* file `CBACT04C` writes. `1050-UPDATE-ACCOUNT`
#: rewrites it, so a round trip that compares only `transact.dat` leaves half the program's
#: observable output unmeasured.
#:
#: **No field is excluded.** Every one of these is producible by the generated pipeline, which is why
#: the account half is the stricter of the two comparisons despite being the smaller record.
ACCOUNT_LAYOUT: tuple[tuple[str, int, int, int | None], ...] = (
    ("ACCT-ID", 0, 11, 0),
    ("ACCT-ACTIVE-STATUS", 11, 1, None),
    ("ACCT-CURR-BAL", 12, 12, 2),
    ("ACCT-CREDIT-LIMIT", 24, 12, 2),
    ("ACCT-CASH-CREDIT-LIMIT", 36, 12, 2),
    ("ACCT-OPEN-DATE", 48, 10, None),
    ("ACCT-EXPIRAION-DATE", 58, 10, None),
    ("ACCT-REISSUE-DATE", 68, 10, None),
    ("ACCT-CURR-CYC-CREDIT", 78, 12, 2),
    ("ACCT-CURR-CYC-DEBIT", 90, 12, 2),
    ("ACCT-ADDR-ZIP", 102, 10, None),
    ("ACCT-GROUP-ID", 112, 10, None),
)

#: Fields the generated pipeline cannot produce, each with the decision that says so.
#: **A field may not be added here without an ADR that makes it unproducible.**
EXCLUSIONS: dict[str, str] = {
    "TRAN-ID": (
        "ADR-0026: STRING PARM-DATE, WS-TRANID-SUFFIX needs a per-run counter, and a stateless "
        "ItemProcessor cannot reproduce a monotonic suffix under restart or partitioning. Scoped "
        "out rather than faked."
    ),
    "TRAN-ORIG-TS": (
        "ADR-0026: the run timestamp is supplied once per run where COBOL reads FUNCTION "
        "CURRENT-DATE per record, a divergence taken so a batch record is reproducible."
    ),
    "TRAN-PROC-TS": ("ADR-0026: the same run-timestamp divergence as TRAN-ORIG-TS -- COBOL reads the "
        "clock per record and the generated processor is handed one instant per run, so both "
        "timestamp fields differ by construction rather than by defect."),
}

#: Zoned-decimal overpunch: the last byte carries the final digit *and* the sign.
_OVERPUNCH = {
    **{c: ("+", str(i)) for i, c in enumerate("{ABCDEFGHI")},
    **{c: ("-", str(i)) for i, c in enumerate("}JKLMNOPQR")},
}


def decode_signed(raw: str, scale: int) -> Decimal:
    """A zoned-decimal DISPLAY field as a `Decimal`.

    The corpus really uses overpunches -- `00000001940{` is +194.00, not 19400 (audit G16) -- so a
    comparison that read these as plain digits would be wrong by a factor of ten *and* lose the sign,
    which is precisely the defect the data loader was built to avoid.
    """
    body, last = raw[:-1], raw[-1]
    sign, digit = _OVERPUNCH.get(last, ("+", last))
    digits = f"{body}{digit}"
    value = Decimal(digits or "0")
    if scale:
        value = value.scaleb(-scale)
    return -value if sign == "-" else value


@dataclass(frozen=True)
class FieldValue:
    name: str
    raw: str
    scale: int | None

    @property
    def value(self):
        """Alphanumerics compare as their full declared width; numerics as a decimal."""
        return self.raw if self.scale is None else decode_signed(self.raw, self.scale)


def parse_record(record: str) -> dict[str, FieldValue]:
    if len(record) != TRAN_RECORD_LEN:
        raise ValueError(f"expected {TRAN_RECORD_LEN}-byte record, got {len(record)}")
    return {
        name: FieldValue(name, record[off : off + width], scale)
        for name, off, width, scale in TRAN_LAYOUT
    }


def load_oracle() -> list[dict[str, FieldValue]]:
    raw = TRANSACT.read_bytes().decode("latin-1")
    if len(raw) % TRAN_RECORD_LEN:
        raise ValueError(f"oracle is not a whole number of records: {len(raw)} bytes")
    return [
        parse_record(raw[i : i + TRAN_RECORD_LEN])
        for i in range(0, len(raw), TRAN_RECORD_LEN)
    ]


def parse_fixed_records(
    path: Path, layout: tuple[tuple[str, int, int, int | None], ...], record_length: int
) -> list[dict[str, FieldValue]]:
    """Parse a fixed-width file with `layout`, whatever wrote it.

    Used for the oracle *and* for a candidate produced by a rendered writer, deliberately: two
    parsers would be two places for one of the sides to be misread, and a difference in how the
    files are read would show up as a difference in what the programs computed.
    """
    raw = path.read_bytes().decode("latin-1")
    if len(raw) % record_length:
        raise ValueError(f"{path.name} is not a whole number of records: {len(raw)} bytes")
    return [
        {
            name: FieldValue(name, raw[i + off : i + off + width], scale)
            for name, off, width, scale in layout
        }
        for i in range(0, len(raw), record_length)
    ]


def load_account_oracle() -> list[dict[str, FieldValue]]:
    """The account file `CBACT04C` left behind, parsed with `ACCOUNT_LAYOUT`."""
    return parse_fixed_records(ORACLE_DIR / "acctdata-posted.dat", ACCOUNT_LAYOUT, 300)


@dataclass(frozen=True)
class ComparisonResult:
    """What ADR-0029 requires a result to carry: matches, mismatches, and what was excluded."""

    compared: int
    matched: int
    mismatches: tuple[str, ...]
    excluded: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.mismatches

    def render(self) -> str:
        """The qualifier ADR-0029 says the metric never appears without."""
        return (
            f"{self.matched} of {self.compared} fields matched; "
            f"{len(self.excluded)} excluded by decision"
        )


def compare(
    candidate: list[dict[str, FieldValue]],
    oracle: list[dict[str, FieldValue]],
    layout: tuple[tuple[str, int, int, int | None], ...] = TRAN_LAYOUT,
    exclusions: dict[str, str] | None = None,
) -> ComparisonResult:
    """Field-for-field, skipping only what `exclusions` names.

    `layout` and `exclusions` are parameters rather than constants because `CBACT04C` writes **two**
    files -- the interest transactions and the rewritten account master -- and comparing one of them
    measures half the program. The semantics are identical for both: full declared width, exclusions
    citing a decision, record framing out of scope.
    """
    exclusions = EXCLUSIONS if exclusions is None else exclusions
    if len(candidate) != len(oracle):
        return ComparisonResult(
            compared=0,
            matched=0,
            mismatches=(f"record count {len(candidate)} != oracle {len(oracle)}",),
            excluded=tuple(exclusions),
        )

    compared = matched = 0
    mismatches: list[str] = []
    for index, (got, want) in enumerate(zip(candidate, oracle)):
        for name, *_ in layout:
            if name in exclusions:
                continue
            compared += 1
            if got[name].value == want[name].value:
                matched += 1
            else:
                mismatches.append(
                    f"record {index} {name}: got {got[name].value!r} want {want[name].value!r}"
                )
    return ComparisonResult(compared, matched, tuple(mismatches), tuple(exclusions))


# --- the fixture is real -------------------------------------------------------------------------


def test_the_oracle_fixture_is_a_whole_number_of_records():
    records = load_oracle()
    assert len(records) == 50
    assert TRANSACT.stat().st_size == 50 * TRAN_RECORD_LEN


def test_the_oracle_discriminates_rather_than_being_all_zeros():
    """The first oracle run produced 50 records of 0.00 and would have passed for any implementation.

    PR #34 found every shipped TRAN-CAT-BAL is zero, so CBACT04C over the raw corpus computes zero
    interest throughout. The fixture is produced by the two-stage pipeline instead -- CBTRN02C posts
    dailytran first -- and this asserts the result actually varies, because a fixture that cannot
    distinguish two implementations is worse than none.
    """
    amounts = [r["TRAN-AMT"].value for r in load_oracle()]
    assert len(set(amounts)) == 50, (
        f"only {len(set(amounts))} distinct amounts; the oracle cannot discriminate"
    )
    # Exactly one record truncates to 0.00, and that is *wanted*, not tolerated: a sub-cent interest
    # is ADR-0021's R5 case, the row where a rounding implementation invents a penny. Its presence
    # means the fixture exercises that path against real data. The failure this test guards is the
    # first run's, where all fifty were zero and identical.
    assert sum(1 for a in amounts if a == 0) <= 1
    assert len([a for a in amounts if a != 0]) >= 49


def test_the_overpunch_decoder_handles_the_real_encodings():
    # `{` is +0 and `}` is -0; a decoder that read them as digits would be out by a factor of ten.
    assert decode_signed("00000001940{", 2) == Decimal("194.00")
    assert decode_signed("0000000194J", 2) == Decimal("-19.41")
    assert decode_signed("00000000000", 2) == Decimal("0.00")


def test_cobol_pads_tran_source_to_its_declared_width():
    """The judge said the real PR #44 body was wrong to write a bare "System"; COBOL agrees.

    `TRAN-SOURCE` is `PIC X(10)` (`CVTRA05Y:8`) and every oracle record carries ten characters. This
    is the machine confirming a finding that came from a model and was then checked against the
    copybook -- the third independent agreement on the same defect.
    """
    for record in load_oracle():
        assert record["TRAN-SOURCE"].raw == "System    "


# --- the comparison discriminates ------------------------------------------------------------------


def test_an_identical_candidate_passes():
    oracle = load_oracle()
    result = compare(oracle, oracle)
    assert result.passed
    assert result.matched == result.compared
    assert "excluded by decision" in result.render()


def test_a_wrong_amount_is_caught():
    """Shown to fail before the pass above is trusted -- step 45's discipline.

    One cent on one record out of fifty, in the field the whole translation exists to compute.
    """
    oracle = load_oracle()
    candidate = [dict(r) for r in oracle]
    bad = dict(candidate[7])
    bad["TRAN-AMT"] = FieldValue("TRAN-AMT", "00000000001", 2)
    candidate[7] = bad

    result = compare(candidate, oracle)
    assert not result.passed
    assert any("record 7 TRAN-AMT" in m for m in result.mismatches)


def test_a_short_alphanumeric_is_caught_because_width_is_compared():
    """ADR-0029 excludes record *framing*, not field contents -- so padding still counts.

    This is the `TRAN-SOURCE` defect exactly: `"System"` where COBOL writes `"System    "`. If the
    comparison trimmed or normalised, G28's entire body of work would stop being enforced.
    """
    oracle = load_oracle()
    candidate = [dict(r) for r in oracle]
    bad = dict(candidate[0])
    bad["TRAN-SOURCE"] = FieldValue("TRAN-SOURCE", "System    ".rstrip().ljust(10, "\0"), None)
    candidate[0] = bad

    assert not compare(candidate, oracle).passed


def test_a_mismatch_in_an_excluded_field_is_not_reported():
    # The other half of the exclusion contract: TRAN-ID differs by construction (ADR-0026), and a
    # comparison that failed on it would never pass however correct the translation was.
    oracle = load_oracle()
    candidate = [dict(r) for r in oracle]
    bad = dict(candidate[0])
    bad["TRAN-ID"] = FieldValue("TRAN-ID", " " * 16, None)
    candidate[0] = bad

    assert compare(candidate, oracle).passed


def test_a_record_count_mismatch_fails_rather_than_comparing_a_prefix():
    oracle = load_oracle()
    result = compare(oracle[:-1], oracle)
    assert not result.passed
    assert "record count" in result.mismatches[0]


# --- the exclusions are priced ---------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(EXCLUSIONS))
def test_every_exclusion_cites_a_decision(field):
    """The rule that stands between this and a differential that passes by excluding disagreements.

    ADR-0029 requires each exclusion to name an accepted decision that makes the field unproducible.
    A field excluded for being inconvenient would make the whole comparison meaningless, and the
    honest response to a new disagreement is to fail and open a decision -- not to extend this dict.
    """
    reason = EXCLUSIONS[field]
    assert "ADR-" in reason, f"{field} is excluded without citing a decision"
    assert len(reason) > 60, f"{field}'s exclusion reason states no substance"


def test_every_excluded_field_actually_exists_in_the_layout():
    # An exclusion naming a field the record does not have would silently exclude nothing, and read
    # as coverage that was never given up.
    names = {name for name, *_ in TRAN_LAYOUT}
    assert set(EXCLUSIONS) <= names


def test_the_excluded_set_is_a_minority_of_the_record():
    # Not a bar so much as a tripwire: if most of the record were excluded, a green comparison would
    # mean almost nothing, and the ratio in `render()` would be the only thing saying so.
    assert len(EXCLUSIONS) * 2 < len(TRAN_LAYOUT)


def test_the_fixture_carries_its_provenance():
    """ADR-0028 requires the artifact to say what produced it, including what is not corroborated."""
    text = PROVENANCE.read_text(encoding="utf-8")
    for required in ("cobc", "indexed handler", "-std=ibm", "Known-unverified", "sha256"):
        assert required in text, f"provenance is missing {required!r}"
    # The producing programs are inputs too; without their hashes an edited program leaves a stale
    # fixture with no signal.
    assert "CBACT04C.cbl" in text and "CBTRN02C.cbl" in text


# --- the comparison's input, captured because it exists only inside the run ------------------------

TCATBAL_POSTED = ORACLE_DIR / "tcatbal-posted.dat"
TCATBAL_RECORD_LEN = 50
TCATBAL_POSTED_RECORDS = 100


def test_the_posted_input_fixture_exists_and_is_whole():
    """The state *between* the two stages, without which nothing can be compared.

    The shipped `tcatbal.txt` is the pre-posting state (audit R2.9); CBTRN02C overwrites it and
    CBACT04C computes interest on the result. Those balances existed only inside the container. A
    candidate started from the shipped file instead would compute zero interest and fail against this
    oracle for a reason that has nothing to do with the code under test.
    """
    raw = TCATBAL_POSTED.read_bytes()
    assert len(raw) % TCATBAL_RECORD_LEN == 0
    assert len(raw) // TCATBAL_RECORD_LEN == TCATBAL_POSTED_RECORDS


def test_the_posted_input_is_not_the_shipped_pre_posting_state():
    """100 records, not the 50 that were loaded -- and the difference is real program behaviour.

    CBTRN02C creates a balance row whenever a daily transaction posts to an (account, type, category)
    combination that has none: exactly 50 of them, so 50 + 50 = 100. Asserting the 50 I assumed
    failed immediately; asserting nothing would have shipped a fixture missing half its rows.

    It was 94, from 44 creates, until ADR-0047 gave the oracle the corpus's real amounts. Six of the
    transactions its run used to reject on a lost digit each post to a combination with no balance
    row, so rejecting them suppressed the row as well as the posting.
    """
    raw = TCATBAL_POSTED.read_bytes().decode("latin-1")
    balances = {
        raw[i * TCATBAL_RECORD_LEN + 17 : i * TCATBAL_RECORD_LEN + 28]
        for i in range(TCATBAL_POSTED_RECORDS)
    }
    assert len(balances) == TCATBAL_POSTED_RECORDS, (
        "posted balances should all differ; a repeat suggests a bad unload"
    )
    # Exactly one row is still zero, which is why exactly one interest amount is 0.00.
    assert sum(1 for b in balances if b == "0000000000{") == 1


def test_the_guard_is_exercised_by_this_data():
    """100 balance rows produce 50 transactions, so `IF DIS-INT-RATE NOT = 0` really fires.

    Worth pinning: a candidate ignoring the guard would emit 100 records and fail the record-count
    check, which means this fixture tests ADR-0022's guard against real data rather than against the
    single hand-written row the oracle table carries.
    """
    posted = TCATBAL_POSTED.stat().st_size // TCATBAL_RECORD_LEN
    written = len(load_oracle())
    assert (posted, written) == (TCATBAL_POSTED_RECORDS, 50)
    assert written < posted, "if every balance produced a transaction the guard would be untested"


# --- the oracle checks itself, because looking plausible was not enough ----------------------------

ACCTDATA_STAGE1 = ORACLE_DIR / "acctdata-stage1.dat"
ACCTDATA_POSTED = ORACLE_DIR / "acctdata-posted.dat"
ACCOUNT_RECORD_LEN = 300
#: `ACCT-ID` and `ACCT-CURR-BAL` (`CVACT01Y`), the only two fields this identity needs.
_ACCT_ID = (0, 11)
_ACCT_CURR_BAL = (12, 12)
#: `1300-B-WRITE-TX` does `STRING 'Int. for a/c ', ACCT-ID` into `TRAN-DESC`, so the account a
#: transaction belongs to is readable from the record COBOL wrote rather than reconstructed.
_DESC_ACCT_ID = (32 + len("Int. for a/c "), 11)


def _account_balances(path: Path) -> dict[str, Decimal]:
    raw = path.read_bytes().decode("latin-1")
    assert len(raw) % ACCOUNT_RECORD_LEN == 0, f"{path.name} is not whole records"
    records = [
        raw[i : i + ACCOUNT_RECORD_LEN] for i in range(0, len(raw), ACCOUNT_RECORD_LEN)
    ]
    return {
        r[_ACCT_ID[0] : sum(_ACCT_ID)]: decode_signed(
            r[_ACCT_CURR_BAL[0] : sum(_ACCT_CURR_BAL)], 2
        )
        for r in records
    }


def _interest_written_per_account() -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for record in load_oracle():
        acct = record["TRAN-DESC"].raw[
            _DESC_ACCT_ID[0] - 32 : _DESC_ACCT_ID[0] - 32 + _DESC_ACCT_ID[1]
        ]
        totals[acct] = totals.get(acct, Decimal(0)) + record["TRAN-AMT"].value
    return totals


def test_each_account_was_credited_exactly_the_interest_it_was_written():
    """Two of COBOL's own outputs, checked against each other. **This is why it exists.**

    PR #56's committed oracle carried `900014.55` in the first record's `TRAN-AMT` where five runs
    of the identical pipeline over byte-identical inputs write `14.55` -- and the same run's own
    account file agreed with `14.55`, so the fixture disagreed with itself. Every check the fixture
    had asked whether it looked plausible *on its own*: fifty records, fifty distinct amounts, one
    zero. All of those passed on the wrong file.

    `1050-UPDATE-ACCOUNT` does `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` and nothing else in stage 2
    touches a balance, so for each account the balance it gained is the interest it was written.
    Both sides are values COBOL produced; Python only sums them, and summing exact two-decimal
    values introduces no rounding -- so this is not the re-derivation ADR-0021 forbids. Re-deriving
    would mean recomputing `(TRAN-CAT-BAL * DIS-INT-RATE) / 1200` here, which this deliberately
    does not do.
    """
    before, after = _account_balances(ACCTDATA_STAGE1), _account_balances(ACCTDATA_POSTED)
    assert set(before) == set(after)
    written = _interest_written_per_account()
    last = max(written)  # see the test below; COBOL never posts this one

    mismatches = [
        f"{acct}: balance moved {after[acct] - before[acct]}, transactions total "
        f"{written.get(acct, Decimal(0))}"
        for acct in sorted(before)
        if acct != last and after[acct] - before[acct] != written.get(acct, Decimal(0))
    ]
    assert not mismatches, "\n".join(mismatches)
    # A vacuous pass would be an identity over an empty set: every account must have earned some.
    assert len(written) == len(before) == 50


def test_the_last_account_is_written_interest_it_is_never_credited():
    """A real defect in `CBACT04C`, pinned rather than smoothed over.

    The main loop is `PERFORM UNTIL END-OF-FILE = 'Y'` with `PERFORM 1050-UPDATE-ACCOUNT` in the
    `ELSE` of `IF END-OF-FILE = 'N'`. `1000-TCATBALF-GET-NEXT` sets the flag at EOF, the loop
    condition is then true, and the loop exits -- so that `ELSE` never runs and the final account's
    accumulated interest is written as a transaction and never added to its balance.

    Pinned because it is exactly the kind of behaviour a modernized target would "fix" silently:
    ADR-0027's `postAccountInterest` step posts every account, including the last, so a
    round-trip that compares account balances (this one compares transactions) would differ here
    for a reason that is COBOL's defect rather than the translation's.
    """
    before, after = _account_balances(ACCTDATA_STAGE1), _account_balances(ACCTDATA_POSTED)
    written = _interest_written_per_account()
    last = max(written)

    assert written[last] > 0, "the last account earned no interest, so this pins nothing"
    assert after[last] - before[last] == 0, (
        f"account {last} was credited {after[last] - before[last]}; if CBACT04C's EOF branch has "
        "started running, this test and the one above both need rewriting"
    )


def test_the_stage_one_snapshot_is_not_the_shipped_account_file():
    """Without this the identity above could be run against the wrong 'before' and still pass.

    CBTRN02C rewrites `ACCTFILE` too, so the shipped `acctdata.txt` is two stages behind. The
    snapshot exists precisely because that difference is invisible in the posted file alone.
    """
    shipped = (
        Path(__file__).resolve().parents[1]
        / "fixtures" / "tenant_repo_sample" / "app" / "data" / "ASCII" / "acctdata.txt"
    ).read_bytes().decode("latin-1")
    stage1 = _account_balances(ACCTDATA_STAGE1)
    shipped_balances = {
        line[:11]: decode_signed(line[12:24], 2)
        for line in shipped.replace("\r\n", "\n").split("\n")
        if line
    }
    assert set(shipped_balances) == set(stage1)
    assert shipped_balances != stage1, "stage 1 posted nothing to any account balance"
