"""`CBTRN02C` generated, wired, built and run -- the second program through the whole path.

**What this runs.** `run_generate` produces the domain records and the one `ItemProcessor`
`CBTRN02C`'s design declares. The reader, both halves of its output, the working set and the job are
rendered from `design.json`. `tests/fixtures/handwritten/CBTRN02C/` supplies what ADR-0030 says is
left -- **file paths only** -- and real Maven builds and runs the result over the shipped corpus,
which is the pre-posting state the program was written against.

**Why this program needed four contract facts that `CBACT04C` did not**, each one measured rather
than argued:

| fact | why | record |
|---|---|---|
| `write_mode` `upsert` | `TCATBAL` is `WRITE`n and `REWRITE`n; the design kept the first | ADR-0037 |
| `reads_own_writes` | the acceptance decision reads account state the posting writes | ADR-0039/0040 |
| a shared working set | so item *n* sees items *1..n-1* | ADR-0041 |
| `optional_lookups` | the `TCATBAL` read creates a row on `INVALID KEY`, 50 times | ADR-0042 |

**The bodies here are scripted, not model-authored**, the same line `test_hand_written_round_trip`
draws for the same reason: what this module demonstrates is that the path works end to end and that
the comparison sees a real candidate. Whether a *model* writes a body that passes is a separate
question and needs a live call.
"""

from __future__ import annotations

import json
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    CompositeComponent,
    CompositeType,
    ProgramDesignEntry,
    UnifiedDesign,
    build_design_document,
)
from cobol_modernizer.core.model_client import RunBudget, collect_usage
from cobol_modernizer.graph.generate_pipeline import (
    DEFAULT_DOMAIN_PACKAGE,
    DEFAULT_PACKAGE,
    run_generate,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_job import (
    configuration_class_name,
    render_job_configuration,
)
from cobol_modernizer.rendering.java_reader import reader_class_name, render_item_reader
from cobol_modernizer.rendering.java_working_set import (
    render_working_set,
    working_set_class_name,
)
from cobol_modernizer.rendering.java_writer import render_item_writer, writer_class_name
from cobol_modernizer.tools.data_loader import decode_zoned_decimal
from cobol_modernizer.tools.local_compiler import compile_project
from tests.system.test_cobol_oracle_comparison import (
    ACCOUNT_LAYOUT,
    TRAN_LAYOUT,
    FieldValue,
    compare,
    parse_fixed_records,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
HANDWRITTEN = Path(__file__).resolve().parents[1] / "fixtures" / "handwritten" / "CBTRN02C"
CORPUS = FIXTURE_ROOT / "app" / "data" / "ASCII"
PROGRAM = "CBTRN02C"

READER_PACKAGE = "com.modernized.batch.reader"
WRITER_PACKAGE = "com.modernized.batch.writer"
JOB_PACKAGE = "com.modernized.batch.job"
STATE_PACKAGE = "com.modernized.batch.state"
WIRING_PROFILE = "handwritten-wiring"

#: The corpus as CBTRN02C's own `FILE-CONTROL` expects it: fixed-length records, no terminators.
#:
#: `cardxref.txt` ships **36 bytes against a declared 50** -- the trailing `FILLER X(14)` is absent
#: (audit G16, finding 1) -- and `tcatbal.txt` is CRLF except for one line, which is why its 50
#: records occupy 2599 rather than 2600 bytes (G16, finding 3). Framing each file at its declared
#: width is what the oracle pipeline's own `LOADIDX` does before either program sees it.
STAGED = {
    "dailytran.dat": ("dailytran.txt", 350),
    "cardxref.dat": ("cardxref.txt", 50),
    "acctdata.dat": ("acctdata.txt", 300),
    "tcatbal.dat": ("tcatbal.txt", 50),
}

#: What the run leaves behind, and what each is compared against.
CANDIDATE_TRANSACTIONS = Path("roundtrip") / "output" / "transact.dat"
CANDIDATE_ACCOUNTS = Path("roundtrip") / "input" / "acctdata.dat"
CANDIDATE_BALANCES = Path("roundtrip") / "input" / "tcatbal.dat"

POSTING_INPUT = CompositeType(
    name="PostingInput",
    components=[
        CompositeComponent(field_name="dalytran", entity_name="Dalytran"),
        CompositeComponent(field_name="xref", entity_name="CardXref"),
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
    ],
)

POSTING_RESULT = CompositeType(
    name="PostingResult",
    components=[
        CompositeComponent(field_name="tran", entity_name="Tran"),
        CompositeComponent(field_name="account", entity_name="Account"),
        CompositeComponent(field_name="balance", entity_name="TranCatBal"),
    ],
)

STEP = BatchStepDesign(
    step_name="postTransaction",
    source_paragraphs=[
        "1500-VALIDATE-TRAN",
        "1500-B-LOOKUP-ACCT",
        "2000-POST-TRANSACTION",
        "2700-UPDATE-TCATBAL",
        "2800-UPDATE-ACCOUNT-REC",
    ],
    role="processor",
    description="Posts one accepted daily transaction to the master, the balance and the account.",
    input_type="PostingInput",
    output_type="PostingResult",
    guard_condition="IF WS-VALIDATION-FAIL-REASON = 0",
    reads_own_writes=True,
    optional_lookups=["TranCatBal"],
)

JOB = BatchJobDesign(
    job_name="postingJob",
    program_name=PROGRAM,
    domain_entities=["Dalytran", "CardXref", "Account", "TranCatBal", "Tran"],
    steps=[STEP],
)

#: `Z-GET-DB2-FORMAT-TIMESTAMP` reads a clock, and a body may not (ADR-0026's
#: `NonDeterministicBodyError`). A fixed instant keeps the run reproducible; the field is excluded
#: from the comparison for exactly this reason, so the value never stands in for COBOL's.
#: **Spaces, not a literal, and the judge is why.** `TRAN-PROC-TS` is `PIC X(26)` and the COBOL fills
#: it from `FUNCTION CURRENT-DATE` per record -- a source no input to this step supplies, which is
#: exactly why the differential excludes it (`CBTRN02C_EXCLUSIONS`).
#:
#: This was a hardcoded timestamp until the eval judge flagged it as an invented value on all three
#: runs of ADR-0050's benchmark, and it was right: the rubric's `no_invented_values` says a field
#: whose source is not reachable must be **left unset**, never invented. A literal passes the
#: differential precisely because the field is excluded from it, so nothing else in this repository
#: was ever going to catch it.
#:
#: The model-authored body reached the same answer unprompted and wrote spaces. This makes the
#: scripted body agree with it, and makes `posting_faithful` a clean specimen for all four criteria
#: rather than one carrying a declared impurity.
_PROC_TS = "CobolText.spaces(26)"

_IMPORTS = [
    "java.math.BigDecimal",
    "com.modernized.batch.cobol.CobolText",
    f"{DEFAULT_DOMAIN_PACKAGE}.Account",
    f"{DEFAULT_DOMAIN_PACKAGE}.PostingResult",
    f"{DEFAULT_DOMAIN_PACKAGE}.Tran",
    f"{DEFAULT_DOMAIN_PACKAGE}.TranCatBal",
]

#: `1500-B-LOOKUP-ACCT` then `2000-POST-TRANSACTION`, statement for statement.
#:
#: The two validations that can fail on this corpus return `null`, which Spring Batch drops before
#: the writer -- so a rejected transaction is absent from all three files by one mechanism rather
#: than three tests that could disagree (ADR-0038).
_BODY = f"""BigDecimal amount = item.dalytran().dalytranAmt();
BigDecimal projected =
        item.account().acctCurrCycCredit().subtract(item.account().acctCurrCycDebit()).add(amount);
if (item.account().acctCreditLimit().compareTo(projected) < 0) {{
    return null;
}}
if (item.account().acctExpiraionDate().compareTo(item.dalytran().dalytranOrigTs().substring(0, 10))
        < 0) {{
    return null;
}}
Tran tran =
        new Tran(
                item.dalytran().dalytranId(),
                item.dalytran().dalytranTypeCd(),
                item.dalytran().dalytranCatCd(),
                item.dalytran().dalytranSource(),
                item.dalytran().dalytranDesc(),
                amount,
                item.dalytran().dalytranMerchantId(),
                item.dalytran().dalytranMerchantName(),
                item.dalytran().dalytranMerchantCity(),
                item.dalytran().dalytranMerchantZip(),
                item.dalytran().dalytranCardNum(),
                item.dalytran().dalytranOrigTs(),
                {_PROC_TS});
TranCatBal balance =
        item.balance() == null
                ? new TranCatBal(
                        item.xref().xrefAcctId(),
                        item.dalytran().dalytranTypeCd(),
                        item.dalytran().dalytranCatCd(),
                        amount)
                : new TranCatBal(
                        item.balance().trancatAcctId(),
                        item.balance().trancatTypeCd(),
                        item.balance().trancatCd(),
                        item.balance().tranCatBal().add(amount));
Account account =
        new Account(
                item.account().acctId(),
                item.account().acctActiveStatus(),
                item.account().acctCurrBal().add(amount),
                item.account().acctCreditLimit(),
                item.account().acctCashCreditLimit(),
                item.account().acctOpenDate(),
                item.account().acctExpiraionDate(),
                item.account().acctReissueDate(),
                amount.signum() >= 0
                        ? item.account().acctCurrCycCredit().add(amount)
                        : item.account().acctCurrCycCredit(),
                amount.signum() >= 0
                        ? item.account().acctCurrCycDebit()
                        : item.account().acctCurrCycDebit().add(amount),
                item.account().acctAddrZip(),
                item.account().acctGroupId());
return new PostingResult(tran, account, balance);"""


def _scripted_author(routing, system_prompt: str, user_content: str) -> str:
    return json.dumps({"imports": list(_IMPORTS), "body": _BODY, "notes": ""})


def stage_as_fixed_records(source: Path, destination: Path, record_length: int) -> None:
    """One line per record, framed at `record_length` with no terminator.

    Padded rather than truncated where the distribution is short, because a `PIC X(n)` field that
    the file does not carry is spaces in COBOL rather than a missing field.
    """
    lines = [
        line.rstrip("\r\n")
        for line in source.read_text(encoding="latin-1").splitlines()
        if line.strip()
    ]
    destination.write_text(
        "".join(line.ljust(record_length)[:record_length] for line in lines), encoding="latin-1"
    )


def _write_java(project: Path, package: str, class_name: str, source: str) -> Path:
    destination = (
        project / "src" / "main" / "java" / Path(package.replace(".", "/")) / f"{class_name}.java"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    return destination


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name=PROGRAM,
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    return UnifiedDesign(
        domain_entities=build_domain_entities(FIXTURE_ROOT, [entry]),
        composite_types=[POSTING_INPUT, POSTING_RESULT],
        batch_jobs=[JOB],
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


@pytest.fixture(scope="module")
def entry() -> ProgramDesignEntry:
    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=lambda m, s, u: "narration")
    return ProgramDesignEntry(
        program_name=PROGRAM,
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )


def _render_wiring_into(project: Path, design: UnifiedDesign) -> None:
    """Everything the pipeline does not render itself: reader, writer, working set, job."""
    _write_java(
        project,
        STATE_PACKAGE,
        working_set_class_name(STEP),
        render_working_set(STEP, design, PROGRAM, package=STATE_PACKAGE),
    )
    _write_java(
        project,
        READER_PACKAGE,
        reader_class_name(STEP),
        render_item_reader(
            STEP,
            design,
            PROGRAM,
            package=READER_PACKAGE,
            domain_package=DEFAULT_DOMAIN_PACKAGE,
            working_set_package=STATE_PACKAGE,
        ),
    )
    _write_java(
        project,
        WRITER_PACKAGE,
        writer_class_name(STEP),
        render_item_writer(
            STEP,
            design,
            PROGRAM,
            package=WRITER_PACKAGE,
            domain_package=DEFAULT_DOMAIN_PACKAGE,
            working_set_package=STATE_PACKAGE,
        ),
    )
    _write_java(
        project,
        JOB_PACKAGE,
        configuration_class_name(JOB),
        render_job_configuration(
            JOB,
            design,
            PROGRAM,
            package=JOB_PACKAGE,
            domain_package=DEFAULT_DOMAIN_PACKAGE,
            processor_package=DEFAULT_PACKAGE,
            reader_package=READER_PACKAGE,
            profile=WIRING_PROFILE,
            working_set_package=STATE_PACKAGE,
        ),
    )


def generate_wire_build_and_run(project: Path, design, entry, *, expect_build=True, **generate_kwargs):
    """Generate the processor, add the wiring, stage the corpus, build and run.

    Shared by the scripted path and the live one so that **the only difference between them is who
    wrote the method body**. If the wiring, the staging or the build differed too, a disagreement
    between the two runs would not be attributable to the body -- which is the one thing the live
    run exists to measure. Same shape as `test_hand_written_round_trip.wire_build_and_run`, and for
    the same reason.
    """
    project.parent.mkdir(parents=True, exist_ok=True)

    document = build_design_document([entry], unified_design=design)
    design_path = project.parent / "design.json"
    design_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")

    outcome = run_generate(design_path, FIXTURE_ROOT, project, **generate_kwargs)
    assert outcome.succeeded, f"generation failed: {[o.reason for o in outcome.blocked]}"

    _render_wiring_into(project, design)
    shutil.copytree(HANDWRITTEN / "src", project / "src", dirs_exist_ok=True)

    staged = project / "roundtrip" / "input"
    staged.mkdir(parents=True, exist_ok=True)
    for name, (source, width) in STAGED.items():
        stage_as_fixed_records(CORPUS / source, staged / name, width)

    result = compile_project(project, goal="verify")
    # `expect_build=False` is for a **deliberately damaged** body, where the run failing is the
    # finding rather than a problem. It is not a general escape hatch: the faithful path still
    # asserts, so a real regression there cannot hide behind this parameter.
    if expect_build:
        assert result.succeeded, "\n".join(d.message for d in result.diagnostics[:10])
    return project, outcome


@pytest.fixture(scope="module")
def built(tmp_path_factory, design, entry):
    """Generate, wire, stage the corpus, and build -- once, because Maven is the cost."""
    project, _ = generate_wire_build_and_run(
        tmp_path_factory.mktemp("cbtrn02c") / "target-project",
        design,
        entry,
        author=_scripted_author,
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    return project


def test_the_rendered_sequential_job_builds_and_runs(built):
    """The claim this module exists to make: it compiles, it runs, and it writes.

    Everything before this was an argument that a sequential step *could* be rendered. `mvn verify`
    is what turns that into a fact, and it is why ADR-0040's refusal could be lifted.
    """
    output = built / CANDIDATE_TRANSACTIONS
    assert output.is_file(), "the job completed and wrote no transaction output"
    assert output.stat().st_size % 350 == 0


def test_the_create_path_runs_and_the_row_count_now_matches(built):
    """50 rows in, **100 out, which is what COBOL leaves** -- the create path runs, and correctly.

    This test used to be called `..._does_not_match_yet` and asserted 100 against an oracle holding
    94, pinned at the number the run actually produced because a test asserting the right answer
    while the pipeline produces a different one is a failing test with no diagnosis in it.

    **The diagnosis turned out to be the oracle's.** Six of the transactions its run rejected were
    rejected on amounts missing a digit (ADR-0043), and each of those posts to an (account, type,
    category) combination with no balance row -- so suppressing the acceptance suppressed the row.
    ADR-0047 converted the corpus inside the oracle pipeline and the oracle now leaves 100 too.
    """
    balances = built / CANDIDATE_BALANCES
    assert balances.stat().st_size // 50 == 100
    assert _oracle("tcatbal-posted.dat").stat().st_size // 50 == 100, (
        "the same number from the other side; if these ever diverge again, say which one moved"
    )


def test_the_accounts_are_all_there(built):
    """50 in, 50 out: no account was created, dropped, or duplicated by the working set.

    The one file whose shape matches the oracle exactly, and worth asserting separately -- it is
    what says the `replace` write mode and the shared store handle an update correctly, independent
    of whether the decision feeding them was right.
    """
    assert (built / CANDIDATE_ACCOUNTS).stat().st_size // 300 == 50


def test_the_transactions_now_agree_with_the_oracle_on_every_record_and_every_amount(built):
    """**262 of 262, and the amounts match by value** -- the disagreement is gone, from both sides.

    What this module recorded before ADR-0047: 256 of the oracle's 257 records produced, six
    transactions accepted here and rejected by `CBTRN02C`, one the reverse. Every one of the six
    carried an amount whose last byte was a negative overpunch, this pipeline read that byte as a
    sign, and GnuCOBOL's run did not -- so its credit-limit comparisons were made against amounts
    missing a digit and a negative amount looked like a large positive one.

    **The pipeline's numbers did not change to make this pass.** 262 is the count this same test
    asserted for `ours` while it was failing, and 100 is the balance-row count the test above
    asserted for the same reason. The oracle moved to meet them, because the oracle was wrong.

    **What this is.** Record identity and `TRAN-AMT` only. The full field-for-field comparison is
    below (`test_the_transactions_match_the_oracle_field_for_field`) and subsumes the amounts; this
    one is kept because it fails with a *diagnosis* -- which records are extra and which are missing
    -- where a field comparison on mismatched sets reports only a count.
    """
    candidate = built / CANDIDATE_TRANSACTIONS
    oracle = _oracle("transact-stage1.dat")

    ours = _amounts(candidate.read_bytes().decode("latin-1"))
    theirs = _amounts(oracle.read_bytes().decode("latin-1"))

    assert len(theirs) == 262
    assert len(ours) == 262
    assert set(ours) == set(theirs), (
        f"accepted here and not by COBOL: {sorted(set(ours) - set(theirs))[:5]}; "
        f"the reverse: {sorted(set(theirs) - set(ours))[:5]}"
    )

    differing = {t: (ours[t], theirs[t]) for t in ours if ours[t] != theirs[t]}
    assert not differing, f"{len(differing)} amounts differ, e.g. {list(differing.items())[:3]}"


def test_the_amounts_are_the_corpus_amounts_and_not_merely_equal_to_each_other(built):
    """Both sides agreeing proves nothing if both read the same field the same wrong way.

    `CBTRN02C` copies `DALYTRAN-AMT` to `TRAN-AMT` unchanged, so every posted amount must equal the
    corpus's own value as ADR-0043's hand-derived table reads it. Checked against the corpus rather
    than against the oracle, which is the only version of this check that could have failed before.
    """
    daily = {
        line[0:16]: line[132:143]
        for line in (CORPUS / "dailytran.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    }
    ours = _amounts((built / CANDIDATE_TRANSACTIONS).read_bytes().decode("latin-1"))

    assert len(ours) == 262
    for tran_id, amount in ours.items():
        assert amount == decode_zoned_decimal(daily[tran_id], scale=2, signed=True), (
            f"{tran_id}: wrote {amount} for a corpus amount of {daily[tran_id]!r}"
        )

    carrying = [t for t in ours if daily[t][-1] not in "{}0123456789"]
    assert len(carrying) > 200, (
        "most of these amounts carry a digit in the overpunch, so the check has something to catch"
    )


def _amounts(raw: str) -> dict[str, Decimal]:
    """`TRAN-ID` -> `TRAN-AMT` for every 350-byte record. `CVTRA05Y`: amount at 133, eleven wide."""
    return {
        raw[i : i + 16]: decode_zoned_decimal(raw[i + 132 : i + 143], scale=2, signed=True)
        for i in range(0, len(raw), 350)
    }


def _ids(raw: str) -> set[str]:
    """`TRAN-ID` of every 350-byte record in a transaction file."""
    return {raw[i : i + 16] for i in range(0, len(raw), 350)}


# --- what the disagreement turned out to be (measured after the run) -------------------------------

_POSITIVE_OVERPUNCH = "{ABCDEFGHI"
_NEGATIVE_OVERPUNCH = "}JKLMNOPQR"


def _oracle(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "oracle" / name


def test_cbact04c_was_never_affected_by_it_and_here_is_the_corrected_reason():
    """The green round trip stayed green through ADR-0047, and this says why -- **corrected**.

    The claim has always been right and its stated reason was not. This test used to assert that
    `tcatbal.txt`'s balances *"end in a plain 0 and are not overpunched at all"*, and it passed --
    because it sliced `line[17:29]`, twelve bytes for an eleven-byte `PIC S9(09)V99` field. The byte
    it checked was the FILLER after the field, not the sign. Every one of those balances ends in
    `{`.

    That does not change the conclusion, and it is exactly the kind of near-miss ADR-0047 turns on:
    `{` is **+0**, the one overpunch where the lossy reading and the correct one agree, so nothing
    was lost either way. But a check that reads the wrong byte is not evidence for the thing it is
    cited for, and this one was cited for `500 of 500`.

    Also corrected: `CVACT01Y` has **five** signed fields, not the three this checked. All five are
    `{`, measured across all 50 records.
    """
    corpus = CORPUS
    balances = [
        line[17:28]
        for line in (corpus / "tcatbal.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    ]
    assert len(balances) == 50
    assert {value[-1] for value in balances} == {"{"}, (
        "positive zero: the only overpunch whose digit is nothing to lose"
    )

    accounts = [
        line for line in (corpus / "acctdata.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    ]
    assert len(accounts) == 50
    # ACCT-CURR-BAL, ACCT-CREDIT-LIMIT, ACCT-CASH-CREDIT-LIMIT, ACCT-CURR-CYC-CREDIT,
    # ACCT-CURR-CYC-DEBIT -- every signed field CVACT01Y declares, each twelve wide.
    for lo in (12, 24, 36, 78, 90):
        assert {line[lo : lo + 12][-1] for line in accounts} == {"{"}, (
            f"the signed field at offset {lo} is not uniformly positive zero"
        )
# --- ADR-0029's differential, applied to this program ---------------------------------------------

#: **One exclusion, not the three `CBACT04C` carries** -- and the difference is the whole point of
#: pricing exclusions per program rather than inheriting them.
#:
#: `2000-POST-TRANSACTION` populates the transaction record with thirteen `MOVE`s from the daily
#: transaction it is posting. Two of the fields `CBACT04C` cannot produce are, here, **straight
#: copies of the input**:
#:
#:     MOVE DALYTRAN-ID      TO TRAN-ID          (CBTRN02C.cbl:425)
#:     MOVE DALYTRAN-ORIG-TS TO TRAN-ORIG-TS     (CBTRN02C.cbl:436)
#:
#: `CBACT04C` excludes `TRAN-ID` because it `STRING`s one from a per-run counter and excludes
#: `TRAN-ORIG-TS` because it reads the clock. Neither is true of this program, so inheriting those
#: exclusions would excuse two fields it reproduces exactly -- which is the precise shape of
#: exclusion creep ADR-0029 names as the way a differential goes toothless.
#:
#: `TRAN-PROC-TS` is the one that does transfer, and it transfers for the *original* reason rather
#: than by precedent: `PERFORM Z-GET-DB2-FORMAT-TIMESTAMP` runs inside the per-transaction paragraph
#: and that paragraph does `MOVE FUNCTION CURRENT-DATE TO COBOL-TS` (CBTRN02C.cbl:438, 693). COBOL
#: reads the clock once per record; a batch processor is handed one instant per run.
CBTRN02C_EXCLUSIONS: dict[str, str] = {
    "TRAN-PROC-TS": (
        "ADR-0026: 2000-POST-TRANSACTION performs Z-GET-DB2-FORMAT-TIMESTAMP per transaction and "
        "that paragraph reads FUNCTION CURRENT-DATE (CBTRN02C.cbl:438, 693), where the generated "
        "processor is handed one instant per run. The same divergence ADR-0026 accepted for "
        "CBACT04C, established here from this program's own source rather than inherited."
    ),
}


def _in_key_order(records: list[dict[str, FieldValue]]) -> list[dict[str, FieldValue]]:
    """Sorted by `TRAN-ID`, because record *ordering* is framing and framing is out of scope.

    The oracle is an indexed file unloaded in key order; the candidate is a sequential write in
    arrival order. On this corpus the two coincide -- `dailytran.txt` is already `TRAN-ID`-sorted --
    but relying on that would turn a future unsorted corpus into 3,144 mismatches that say nothing
    about the logic.
    """
    return sorted(records, key=lambda record: record["TRAN-ID"].raw)


def test_the_exclusion_is_earned_from_this_programs_source_not_inherited(built):
    """The check that stops `CBACT04C`'s exclusions being copied across because they were there.

    Asserts the *shape* of the list, not just its contents: exactly one field is excluded, it is
    the timestamp COBOL reads per record, and the two fields this program copies straight from its
    input are **not** in it. A future edit that widens this list fails here first.
    """
    assert set(CBTRN02C_EXCLUSIONS) == {"TRAN-PROC-TS"}
    assert "TRAN-ID" not in CBTRN02C_EXCLUSIONS, "CBTRN02C.cbl:425 copies it from DALYTRAN-ID"
    assert "TRAN-ORIG-TS" not in CBTRN02C_EXCLUSIONS, "CBTRN02C.cbl:436 copies it from the input"

    source = (FIXTURE_ROOT / "app" / "cbl" / "CBTRN02C.cbl").read_text(encoding="latin-1")
    assert "MOVE  DALYTRAN-ID            TO    TRAN-ID" in source
    assert "MOVE  DALYTRAN-ORIG-TS       TO    TRAN-ORIG-TS" in source
    assert "MOVE FUNCTION CURRENT-DATE TO COBOL-TS" in source, (
        "the one exclusion rests on this line; if it goes, the exclusion goes with it"
    )

    reason = CBTRN02C_EXCLUSIONS["TRAN-PROC-TS"]
    assert "ADR-" in reason, "an exclusion without a decision behind it is exclusion creep"


def test_the_transactions_match_the_oracle_field_for_field(built):
    """**The measurement `2 of 4` needs**: every field of every record, at full declared width.

    Twelve of thirteen fields across 262 records. `TRAN-ID` and `TRAN-ORIG-TS` are compared here and
    excluded for `CBACT04C`, so this is a *stricter* comparison than the one the round-trip count is
    currently reported against, not a weaker one wearing the same name.
    """
    candidate = parse_fixed_records(built / CANDIDATE_TRANSACTIONS, TRAN_LAYOUT, 350)
    oracle = parse_fixed_records(_oracle("transact-stage1.dat"), TRAN_LAYOUT, 350)

    result = compare(
        _in_key_order(candidate),
        _in_key_order(oracle),
        TRAN_LAYOUT,
        CBTRN02C_EXCLUSIONS,
    )
    assert result.passed, "\n".join(result.mismatches[:10])
    assert result.compared == 262 * (len(TRAN_LAYOUT) - len(CBTRN02C_EXCLUSIONS))
    print(f"\nCBTRN02C transactions: {result.render()}")


def test_the_transaction_comparison_would_catch_a_wrong_field(built):
    """Shown to fail against the real candidate, not against a copy of the oracle.

    Two mutations, because the two halves of `FieldValue.value` are different code paths: a numeric
    field decoded through the overpunch table, and an alphanumeric compared at full declared width.
    The second is the one that catches a body writing a bare "System" into a `PIC X(10)`.
    """
    candidate = _in_key_order(parse_fixed_records(built / CANDIDATE_TRANSACTIONS, TRAN_LAYOUT, 350))
    oracle = _in_key_order(parse_fixed_records(_oracle("transact-stage1.dat"), TRAN_LAYOUT, 350))

    numeric = [dict(record) for record in candidate]
    numeric[7]["TRAN-AMT"] = FieldValue("TRAN-AMT", "0000000000A", 2)
    assert not compare(numeric, oracle, TRAN_LAYOUT, CBTRN02C_EXCLUSIONS).passed

    text = [dict(record) for record in candidate]
    padded = text[11]["TRAN-SOURCE"].raw
    text[11]["TRAN-SOURCE"] = FieldValue("TRAN-SOURCE", "System" + " " * (len(padded) - 6), None)
    result = compare(text, oracle, TRAN_LAYOUT, CBTRN02C_EXCLUSIONS)
    assert not result.passed, "a short value padded to width must not compare equal to the real one"


def test_the_account_file_matches_field_for_field_with_nothing_excluded(built):
    """The other half of what this program writes, and the stricter half: **no exclusions at all.**

    `2800-UPDATE-ACCOUNT-REC` adds the amount to `ACCT-CURR-BAL`, adds it to one of the two cycle
    totals by sign, and `REWRITE`s the whole record -- so all twelve fields are producible and none
    has a decision making it unreachable. The round trip has reported "the account file exactly"
    on a record count; this is that claim measured.
    """
    candidate = parse_fixed_records(built / CANDIDATE_ACCOUNTS, ACCOUNT_LAYOUT, 300)
    oracle = parse_fixed_records(_oracle("acctdata-stage1.dat"), ACCOUNT_LAYOUT, 300)

    by_id = lambda records: sorted(records, key=lambda record: record["ACCT-ID"].raw)
    result = compare(by_id(candidate), by_id(oracle), ACCOUNT_LAYOUT, {})
    assert result.passed, "\n".join(result.mismatches[:10])
    assert result.compared == 50 * len(ACCOUNT_LAYOUT)
    print(f"\nCBTRN02C accounts: {result.render()}")


def test_the_account_comparison_would_catch_a_wrong_balance(built):
    """One cent on one account, against the real candidate."""
    candidate = sorted(
        parse_fixed_records(built / CANDIDATE_ACCOUNTS, ACCOUNT_LAYOUT, 300),
        key=lambda record: record["ACCT-ID"].raw,
    )
    oracle = sorted(
        parse_fixed_records(_oracle("acctdata-stage1.dat"), ACCOUNT_LAYOUT, 300),
        key=lambda record: record["ACCT-ID"].raw,
    )
    mutated = [dict(record) for record in candidate]
    mutated[3]["ACCT-CURR-BAL"] = FieldValue("ACCT-CURR-BAL", "00000000001{", 2)
    result = compare(mutated, oracle, ACCOUNT_LAYOUT, {})
    assert any("record 3 ACCT-CURR-BAL" in mismatch for mismatch in result.mismatches)
#: `CVTRA01Y`'s TRAN-CAT-BAL-RECORD, 50 bytes -- the **third** file this program writes.
#:
#: Written out here rather than in `test_cobol_oracle_comparison` because `CBACT04C` only ever reads
#: this record; `CBTRN02C` is the program that produces it, and the comparison's view of a layout
#: belongs with the comparison that uses it.
#:
#: The trailing `FILLER PIC X(22)` is omitted, the same way `ACCOUNT_LAYOUT` omits its `X(178)`:
#: ADR-0029 compares field contents and leaves record framing out of scope.
TCATBAL_LAYOUT: tuple[tuple[str, int, int, int | None], ...] = (
    ("TRANCAT-ACCT-ID", 0, 11, 0),
    ("TRANCAT-TYPE-CD", 11, 2, None),
    ("TRANCAT-CD", 13, 4, 0),
    ("TRAN-CAT-BAL", 17, 11, 2),
)


def _tcatbal_key(record: dict[str, FieldValue]) -> str:
    """The composite `TRAN-CAT-KEY`, which is what the indexed file is ordered by."""
    return (
        record["TRANCAT-ACCT-ID"].raw + record["TRANCAT-TYPE-CD"].raw + record["TRANCAT-CD"].raw
    )


def test_the_balance_file_matches_field_for_field_with_nothing_excluded(built):
    """**The third file, and the one that completes the claim.**

    `CBACT04C` writes two files and both are compared, which is what `1 of 4` has always meant.
    `CBTRN02C` writes three in scope -- the transaction master, the account file, and this -- so
    comparing two of them would have been a weaker measurement wearing the same name. `DALYREJS` is
    the fourth and is scoped out of generation by ADR-0038, not by convenience.

    **Nothing is excluded.** `2700-UPDATE-TCATBAL` moves all three key parts from the transaction
    and the cross-reference, then accumulates the amount into the balance -- four fields, all
    producible. It is also the half that exercises `upsert` (ADR-0037) and `optional_lookups`
    (ADR-0042) against real data: 50 rows are read and 50 more are created.
    """
    candidate = parse_fixed_records(built / CANDIDATE_BALANCES, TCATBAL_LAYOUT, 50)
    oracle = parse_fixed_records(_oracle("tcatbal-posted.dat"), TCATBAL_LAYOUT, 50)

    result = compare(
        sorted(candidate, key=_tcatbal_key), sorted(oracle, key=_tcatbal_key), TCATBAL_LAYOUT, {}
    )
    assert result.passed, "\n".join(result.mismatches[:10])
    assert result.compared == 100 * len(TCATBAL_LAYOUT)
    print(f"\nCBTRN02C balances: {result.render()}")


def test_the_balance_comparison_would_catch_a_wrong_total(built):
    """Shown to fail on the real candidate, and on the field the `upsert` accumulates into."""
    candidate = sorted(
        parse_fixed_records(built / CANDIDATE_BALANCES, TCATBAL_LAYOUT, 50), key=_tcatbal_key
    )
    oracle = sorted(
        parse_fixed_records(_oracle("tcatbal-posted.dat"), TCATBAL_LAYOUT, 50), key=_tcatbal_key
    )
    mutated = [dict(record) for record in candidate]
    mutated[5]["TRAN-CAT-BAL"] = FieldValue("TRAN-CAT-BAL", "0000000000A", 2)
    result = compare(mutated, oracle, TCATBAL_LAYOUT, {})
    assert any("record 5 TRAN-CAT-BAL" in mismatch for mismatch in result.mismatches)


def test_every_in_scope_file_this_program_writes_is_compared(built):
    """The check that stops the claim quietly narrowing to whichever files were easy.

    `CBTRN02C` writes four files. Three are compared field-for-field above; the fourth, `DALYREJS`,
    is refused by name in the job (ADR-0038) -- a decision rather than an omission, and the job
    would name it if it were generated.

    Asserted as an **exact** set rather than a subset: a fourth output appearing in the job's output
    directory should fail here, because the round-trip claim is about everything the program writes.
    """
    output_dir = built / "roundtrip" / "output"
    assert {path.name for path in output_dir.iterdir()} == {CANDIDATE_TRANSACTIONS.name}, (
        "an output this comparison does not cover would make the round-trip claim narrower than "
        "it reads"
    )

    # The other two are rewritten in place, which is what `replace` and `upsert` mean here.
    for relative in (CANDIDATE_ACCOUNTS, CANDIDATE_BALANCES):
        assert (built / relative).is_file(), f"{relative} was never produced"

    assert not list((built / "roundtrip").glob("**/dalyrejs*")), (
        "ADR-0038 scopes the reject file out of generation; if it starts being written it needs a "
        "comparison of its own before the round-trip count can include this program"
    )
# --- the same round trip, with a model writing the body -------------------------------------------


@pytest.mark.live_claude_cli
def test_a_model_authored_run_is_compared_against_the_same_oracle(tmp_path, design, entry):
    """**What `2 of 4` rests on**: the body a real model wrote, against COBOL's own output.

    Every comparison above runs on a *scripted* body -- `_BODY`, transcribed from
    `1500-B-LOOKUP-ACCT` and `2000-POST-TRANSACTION` statement for statement. That measures the
    rendered wiring and the contract facts behind it, which is what it was written to measure. It
    does not measure whether this pipeline's model writes a body that reproduces COBOL, and the
    round-trip count is a claim about *generated logic*.

    `CBACT04C` has carried both halves since ADR-0030. This is the second, for the second program,
    and it exists because counting `CBTRN02C` without it would have made the two halves of `2 of 4`
    mean different things -- the shape of claim `CLAUDE.md`'s "closes against a named instance" rule
    exists to catch.

    **Costs real money**, so it is skipped unless `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`. The
    budget is a ceiling rather than a hope: one processor step and its heal attempts, and
    `RunBudgetExceededError` stops the run rather than letting a repair loop spend without bound.

    A failure here is a **finding, not a broken test.** If a model-authored body disagrees with
    COBOL, the mismatch list names the field and both values.
    """
    project = tmp_path / "live" / "target-project"
    with collect_usage(RunBudget(max_model_calls=8)) as usage:
        built, outcome = generate_wire_build_and_run(project, design, entry)

    transactions = compare(
        _in_key_order(parse_fixed_records(built / CANDIDATE_TRANSACTIONS, TRAN_LAYOUT, 350)),
        _in_key_order(parse_fixed_records(_oracle("transact-stage1.dat"), TRAN_LAYOUT, 350)),
        TRAN_LAYOUT,
        CBTRN02C_EXCLUSIONS,
    )
    by_acct = lambda records: sorted(records, key=lambda record: record["ACCT-ID"].raw)
    accounts = compare(
        by_acct(parse_fixed_records(built / CANDIDATE_ACCOUNTS, ACCOUNT_LAYOUT, 300)),
        by_acct(parse_fixed_records(_oracle("acctdata-stage1.dat"), ACCOUNT_LAYOUT, 300)),
        ACCOUNT_LAYOUT,
        {},
    )
    balances = compare(
        sorted(parse_fixed_records(built / CANDIDATE_BALANCES, TCATBAL_LAYOUT, 50), key=_tcatbal_key),
        sorted(parse_fixed_records(_oracle("tcatbal-posted.dat"), TCATBAL_LAYOUT, 50), key=_tcatbal_key),
        TCATBAL_LAYOUT,
        {},
    )

    authored = {step.step_name: step.attempts for step in outcome.compiled}
    notes = [note for step in outcome.compiled for note in step.notes if note.strip()]
    print(
        f"\nlive CBTRN02C round trip, bodies model-authored, wiring hand-written:"
        f"\n  transactions: {transactions.render()}"
        f"\n  accounts:     {accounts.render()}"
        f"\n  balances:     {balances.render()}"
        f"\n  steps and attempts: {authored}"
        f"\n  {usage.model_calls} model call(s), {usage.input_tokens} in / {usage.total_tokens} "
        f"tokens, notional cost {usage.notional_cost_usd}"
    )
    for note in notes:
        print(f"  model note: {note}")

    for result in (transactions, accounts, balances):
        assert result.passed, "\n".join(result.mismatches[:10])
#: `_BODY` with `1500-B-LOOKUP-ACCT`'s credit-limit guard removed, and nothing else changed.
#:
#: **A corpus specimen, and the only honest way to build one.** `tests/evaluations/` grades a judge
#: against bodies whose expected verdict is *known*; the strongest form of knowing is that a real JVM
#: already returned it. This body is derived from the faithful one by deleting one `if`, so a
#: disagreement between the two runs is attributable to that guard and to nothing else -- the same
#: construction `test_interest_equivalence` uses for `_ALWAYS_WRITES_BODY`.
#:
#: The guard it drops is the one ADR-0039 measured: judged per item, 25 of `CBTRN02C`'s 38 rejections
#: are ordering rather than the transaction, so a body that never rejects at all writes strictly more
#: than the program does.
_UNGUARDED_BODY = _BODY.replace(
    """if (item.account().acctCreditLimit().compareTo(projected) < 0) {
    return null;
}
""",
    "",
)


@pytest.fixture(scope="module")
def built_unguarded(tmp_path_factory, design, entry):
    """The same job with the credit-limit guard deleted, built and run for real.

    A second Maven build, paid for deliberately: it is what turns *"dropping this guard would be a
    defect"* from a reading of the COBOL into a verdict a JVM returned.
    """
    project, _ = generate_wire_build_and_run(
        tmp_path_factory.mktemp("cbtrn02c-unguarded") / "target-project",
        design,
        entry,
        expect_build=False,
        author=lambda routing, s, u: json.dumps(
            {"imports": list(_IMPORTS), "body": _UNGUARDED_BODY, "notes": ""}
        ),
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    return project


def test_the_unguarded_body_differs_from_the_guard_and_nothing_else():
    """The specimen is the faithful body minus one `if`, asserted rather than assumed.

    If an edit to `_BODY` ever stops that replacement matching, this fails here rather than silently
    producing a specimen identical to the faithful one -- which would make the corpus case built on
    it a check that cannot fail.
    """
    assert _UNGUARDED_BODY != _BODY
    assert "acctCreditLimit().compareTo(projected)" not in _UNGUARDED_BODY
    assert "acctExpiraionDate()" in _UNGUARDED_BODY, "only the credit-limit guard is dropped"
    assert len(_BODY) - len(_UNGUARDED_BODY) < 120, "one `if`, not a rewrite"


def test_the_oracle_catches_the_dropped_guard(built_unguarded):
    """**The verdict that makes this an `ORACLE`-grounded corpus case** — and it is caught twice.

    Without the credit-limit check every one of the 300 daily transactions posts: the expiry guard
    rejects none of them on this corpus, so the count goes straight from 262 to 300. That trips the
    hand-written wiring's own run assertion (`written > 0 && written < 300`) and **fails the Maven
    build before the differential is ever consulted**, which is why this fixture expects the build to
    fail rather than asserting it succeeds.

    Both levels are asserted here. A judge that misses this defect has missed one a real JVM catches
    twice over, for free, on every run.
    """
    written = (built_unguarded / CANDIDATE_TRANSACTIONS).stat().st_size // 350
    assert written > 262, (
        "a body with no credit-limit guard must accept more than COBOL does; if this ever stops "
        "being true the specimen is not a specimen"
    )
    assert written == 300, (
        "and it accepts *everything* -- the expiry guard rejects nothing on this corpus, which is "
        "what makes the count assertion in the wiring trip as well as the differential"
    )

    result = compare(
        _in_key_order(parse_fixed_records(built_unguarded / CANDIDATE_TRANSACTIONS, TRAN_LAYOUT, 350)),
        _in_key_order(parse_fixed_records(_oracle("transact-stage1.dat"), TRAN_LAYOUT, 350)),
        TRAN_LAYOUT,
        CBTRN02C_EXCLUSIONS,
    )
    assert not result.passed, "the differential has to see it, or the ground is not ORACLE"
    print(f"\nunguarded CBTRN02C: {written} records against the oracle's 262")
