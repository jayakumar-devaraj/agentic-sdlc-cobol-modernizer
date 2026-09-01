"""Shared CBACT04C interest-design constants: the fixture the suite is built on.

These lived in `test_interest_equivalence.py` and were imported by ten other modules, which made
that file a test *and* the suite's fixture library. That is fine until the tests are split by what
they need to run: the equivalence test drives a real Maven build and belongs in the integration
tier, while `test_java_reader`, `test_java_writer`, `test_java_job`, `test_java_aggregation`,
`test_control_break` and `test_file_access_paths` do no I/O at all and belong in the unit tier.
Leaving the constants where they were would have made the unit tier import the integration tier.

So the constants live here, importable from any tier, and `test_interest_equivalence.py` imports
them back like everyone else. Nothing about their values changed -- this module is a byte-verbatim
slice of the block that used to sit between that file's imports and its first fixture.

`FIXTURE_ROOT` still resolves: `parents[1]` from `tests/support/` is `tests/`, the same directory
it resolved to from `tests/system/`.
"""

from __future__ import annotations

from pathlib import Path

from cobol_modernizer.core.contracts import (
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
)
from cobol_modernizer.graph.generate_pipeline import DEFAULT_DOMAIN_PACKAGE

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
ORACLE_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "interest-oracle.json"
)
PROGRAM = "CBACT04C"

#: What the step actually needs, arrived at in two steps and both times from a model's refusal.
#:
#: `balance` + `disclosureGroup` came first: PR #28's model refused to compute interest from a
#: `TranCatBal` alone, because `DIS-INT-RATE` is not reachable from it.
#:
#: `account` + `cardXref` are G26. The step's declared *output* is a `Tran`, standing in for
#: `1300-B-WRITE-TX`, and that paragraph moves `ACCT-ID` into `TRAN-DESC` and `XREF-CARD-NUM` into
#: `TRAN-CARD-NUM` -- neither reachable from balance-and-rate. The model left both `null` and named
#: the COBOL rather than inventing values. ADR-0020 resolves a step's types by *name*; nothing
#: checked they were **populatable**, and this is what that gap looked like in practice.
COMPOSITE = CompositeType(
    name="TranCatBalWithRate",
    components=[
        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
        CompositeComponent(field_name="disclosureGroup", entity_name="DisGroup"),
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="cardXref", entity_name="CardXref"),
    ],
)
#: What `computeInterest` hands its successor now that `1300-B-WRITE-TX` is a step of its own: the
#: transaction it computed, plus the context that step needs to finish it. A composite carries
#: existing entities only (ADR-0020), which is why the amount travels inside a `Tran` rather than
#: as a bare value.
OUTPUT_COMPOSITE = CompositeType(
    name="TranWithContext",
    components=[
        CompositeComponent(field_name="tran", entity_name="Tran"),
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="cardXref", entity_name="CardXref"),
        # **Widened for the control break** (ADR-0032's amendment). `1050-UPDATE-ACCOUNT` groups on
        # `TRANCAT-ACCT-ID`, a field of `TranCatBal`, so a step aggregating this stream has to be
        # able to see what it groups by. Without it the account id reaches the posting step only
        # inside `TRAN-DESC`'s text, which is not something anything can group on.
        #
        # The same move PR #40 made for G26: when a step needs data its declared type cannot reach,
        # the composite is where this design carries it.
        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
    ],
)

STEP = BatchStepDesign(
    step_name="computeInterest",
    source_paragraphs=["1300-COMPUTE-INTEREST"],
    role="processor",
    description="Computes monthly interest from a balance and its disclosure-group rate.",
    input_type="TranCatBalWithRate",
    output_type="TranWithContext",
    # ADR-0022, closing G25. Verbatim from `CBACT04C.cbl:214` -- and note it is *not* in
    # `1300-COMPUTE-INTEREST`, which is why `source_paragraphs` could never have carried it.
    guard_condition="IF DIS-INT-RATE NOT = 0",
)

#: `1300-B-WRITE-TX`, split on the line between logic and wiring so the logic can be generated.
#:
#: The paragraph is mostly per-item field population -- fourteen `MOVE`s and two `STRING`s -- which
#: is what an `ItemProcessor` is for. Three things in it are not: `ADD 1 TO WS-TRANID-SUFFIX` with
#: `PARM-DATE` (a per-run counter and a job parameter), `Z-GET-DB2-FORMAT-TIMESTAMP` (a clock,
#: into `REDEFINES` fields the construct matrix gates), and the `WRITE` itself. Those are
#: infrastructure and stay wiring.
#:
#: So this is a `processor` and `generate` renders it. Typing the whole paragraph as a `writer`
#: would leave its field population ungenerated for the sake of the three statements that are
#: genuinely not translatable -- the same mistake as putting the guard in `source_paragraphs`:
#: taking a boundary the COBOL draws and assuming the design must draw it in the same place.
COMPLETE_STEP = BatchStepDesign(
    step_name="completeTransaction",
    source_paragraphs=["1300-B-WRITE-TX"],
    role="processor",
    description="Populates the interest transaction record from the item and its context.",
    input_type="TranWithContext",
    output_type="Tran",
    guard_condition=None,
)

#: The completion step's body: faithful where it can be, explicitly `null` where the paragraph
#: reads something no `ItemProcessor` has. `TRAN-ID` needs `PARM-DATE` and a per-run counter, and
#: the timestamps need a clock -- the two fields G26 recorded as structurally out of reach.
#:
#: `MOVE SPACES TO TRAN-MERCHANT-NAME` is G28's case, and why `CobolText.spaces` exists: the field
#: is `PIC X(50)`, so an empty string is not the same record on disk.
#:
#: `TRAN-SOURCE` is padded for the same reason, and that correction has three independent sources:
#: the eval judge flagged the bare `"System"` in the real PR #44 body (audit R2.27), the copybook
#: says `PIC X(10)` (`CVTRA05Y:8`), and the round-trip differential then failed fifty records on it
#: against COBOL's own output -- the first defect the round trip found that no other check here
#: could, since the equivalence test asserts on `tranAmt` alone.
#:
#: The `ACCT-ID` formatting is a correction **the model made to this fixture**, not the reverse.
#: `STRING 'Int. for a/c ', ACCT-ID DELIMITED BY SIZE` takes an unsigned 11-digit *display* field,
#: so it contributes all eleven zero-padded positions. This body originally concatenated the bare
#: value and would have written `Int. for a/c 194` where the COBOL writes `Int. for a/c 00000000194`.
_COMPLETE_BODY = """\
com.modernized.batch.domain.Tran source = item.tran();
return new Tran(
    null,
    "01",
    new BigDecimal("5"),
    CobolText.pad("System", 10),
    CobolText.pad("Int. for a/c " + String.format("%011d",
        item.account().acctId().toBigInteger()), 100),
    source.tranAmt(),
    BigDecimal.ZERO,
    CobolText.spaces(50),
    CobolText.spaces(50),
    CobolText.spaces(10),
    item.cardXref().xrefCardNum(),
    null,
    null);"""

#: `Tran`'s components in declaration order, with the amount left as a `{}` slot. Written out rather
#: than generated so this fixture reads as the Java it is.
_TRAN = (
    'new Tran("", "01", new BigDecimal("5"), "System", "Int.", {amount}, BigDecimal.ZERO,'
    ' "", "", "", "", "", "")'
)

_PRELUDE = """\
java.math.BigDecimal balance = item.balance().tranCatBal();
java.math.BigDecimal rate = item.disclosureGroup().disIntRate();
if (rate.compareTo(java.math.BigDecimal.ZERO) == 0) {
    return null;
}
"""

#: The faithful translation. `divide(..., 2)` truncates toward zero, which is what `COMPUTE`
#: without `ROUNDED` does.
_CORRECT_BODY = _PRELUDE + (
    "java.math.BigDecimal monthlyInterest = CobolArithmetic.divide("
    'balance.multiply(rate), new java.math.BigDecimal("1200"), 2);\n'
    f"return new TranWithContext({_TRAN.format(amount='monthlyInterest')},"
    " item.account(), item.cardXref(), item.balance());"
)

#: One token different, and wrong. `divideRounded` is `HALF_UP`, so it disagrees with COBOL on every
#: row whose exact quotient is not already a whole number of cents.
_ROUNDING_BODY = _PRELUDE + (
    "java.math.BigDecimal monthlyInterest = CobolArithmetic.divideRounded("
    'balance.multiply(rate), new java.math.BigDecimal("1200"), 2);\n'
    f"return new TranWithContext({_TRAN.format(amount='monthlyInterest')},"
    " item.account(), item.cardXref(), item.balance());"
)

#: Emits a zero-amount transaction for a zero rate instead of none. Every arithmetic row still
#: passes; only the zero-rate case catches it.
_ALWAYS_WRITES_BODY = (
    "java.math.BigDecimal balance = item.balance().tranCatBal();\n"
    "java.math.BigDecimal rate = item.disclosureGroup().disIntRate();\n"
    "java.math.BigDecimal monthlyInterest = CobolArithmetic.divide("
    'balance.multiply(rate), new java.math.BigDecimal("1200"), 2);\n'
    f"return new TranWithContext({_TRAN.format(amount='monthlyInterest')},"
    " item.account(), item.cardXref(), item.balance());"
)

#: The imports the body needs. Supplied with the body because that is the real contract: the
#: renderer never reads the body, so it cannot derive them (`java_processor`'s docstring). The
#: `Tran` import is here because the processor's *signature* is rendered fully-qualified while the
#: body constructs the type by simple name -- the first compile failed on exactly that.
_IMPORTS = [
    "java.math.BigDecimal",
    "com.modernized.batch.cobol.CobolArithmetic",
    f"{DEFAULT_DOMAIN_PACKAGE}.Tran",
    f"{DEFAULT_DOMAIN_PACKAGE}.TranWithContext",
    "com.modernized.batch.cobol.CobolText",
]


