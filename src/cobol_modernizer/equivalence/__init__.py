"""ADR-0029's differential, as a library rather than a test module (ADR-0064).

**Why this moved.** Everything below was proven and unreachable. `compare` and the two layouts sat
in `tests/unit/test_cobol_oracle_comparison.py`, so the only thing that could run the equivalence
check was the test suite, against Java this repository writes by hand. `generate` could not reach
it, and so the release gate could only report *"Generated and compiled N processor step(s)"* -- a
quantity, not evidence. Two live runs then shipped wrong money past that gate: one processor that
computed a value and discarded it, and one that set a per-account running total to a single row's
amount. **The account half below detects a one-cent error across fifty accounts**, and would have
caught the second in seconds.

**The move is behaviour-free by construction.** The bodies are byte-verbatim slices of the test
module, which now imports them from here and passes unchanged -- that is the proof, not a claim.

**The oracle directory is a parameter, deliberately.** The loaders took a module-level `ORACLE_DIR`
resolved relative to the test tree. Keeping that would have forced a packaging decision now -- ship
CardDemo data in the wheel, or reach outside it -- and that decision belongs to whoever wires this
into `generate`, not to the extraction. Callers pass the directory; nothing here knows where it
lives.

The original module docstring follows, unchanged, because it explains the decisions this code
embodies and those are unaffected by where the code sits.

---

ADR-0029's differential: field-for-field against COBOL's own output, with exclusions priced.

**What this is.** The comparison half of ADR-0028. The oracle directory holds what the *unmodified*
`CBACT04C` wrote when run under GnuCOBOL over the shipped corpus, and this module parses it into
fields and compares a candidate set of records against it.

**Why field-for-field** (ADR-0029). The target persists to PostgreSQL (ADR-0036) and nothing renders
a fixed-width writer, so there is no file to compare byte-for-byte. And byte equality is unreachable
by *accepted decision*: ADR-0026 leaves `TRAN-ID` unpopulated and supplies one run timestamp against
COBOL's per-record clock.

**Each field is compared at its full declared width**, so `PIC X(50)` compares fifty characters and
padding counts. What is excluded is record *framing*, never field contents.

**Exclusions are priced, not trusted.** Every entry in `EXCLUSIONS` names the ADR that makes the
field unproducible, and the test module's `test_every_exclusion_cites_a_decision` fails if one does
not. Exclusion creep is what turns a differential toothless.
"""


from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

#: The oracle's file names, so a caller supplies a directory rather than four paths.
TRANSACT_NAME = "transact.dat"
ACCOUNTS_POSTED_NAME = "acctdata-posted.dat"
PROVENANCE_NAME = "PROVENANCE.md"

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


def load_oracle(oracle_dir: Path) -> list[dict[str, FieldValue]]:
    """The transaction half of the oracle, parsed from `oracle_dir`.

    Takes a directory rather than resolving one: see the module docstring on why the packaging
    decision belongs to the caller.
    """
    raw = (oracle_dir / TRANSACT_NAME).read_bytes().decode("latin-1")
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


def load_account_oracle(oracle_dir: Path) -> list[dict[str, FieldValue]]:
    """The account file `CBACT04C` left behind, parsed with `ACCOUNT_LAYOUT`."""
    return parse_fixed_records(oracle_dir / ACCOUNTS_POSTED_NAME, ACCOUNT_LAYOUT, 300)


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


