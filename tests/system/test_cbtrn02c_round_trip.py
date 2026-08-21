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
| `optional_lookups` | the `TCATBAL` read creates a row on `INVALID KEY`, 44 times | ADR-0042 |

**The bodies here are scripted, not model-authored**, the same line `test_hand_written_round_trip`
draws for the same reason: what this module demonstrates is that the path works end to end and that
the comparison sees a real candidate. Whether a *model* writes a body that passes is a separate
question and needs a live call.
"""

from __future__ import annotations

import json
import shutil
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
from cobol_modernizer.tools.local_compiler import compile_project

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
_PROC_TS = '"2026-08-12-00.00.00.000000"'

_IMPORTS = [
    "java.math.BigDecimal",
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


@pytest.fixture(scope="module")
def built(tmp_path_factory, design, entry):
    """Generate, wire, stage the corpus, and build -- once, because Maven is the cost."""
    project = tmp_path_factory.mktemp("cbtrn02c") / "target-project"
    project.parent.mkdir(parents=True, exist_ok=True)

    document = build_design_document([entry], unified_design=design)
    design_path = project.parent / "design.json"
    design_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")

    outcome = run_generate(
        design_path,
        FIXTURE_ROOT,
        project,
        author=_scripted_author,
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    assert outcome.succeeded, f"generation failed: {[o.reason for o in outcome.blocked]}"

    _render_wiring_into(project, design)
    shutil.copytree(HANDWRITTEN / "src", project / "src", dirs_exist_ok=True)

    staged = project / "roundtrip" / "input"
    staged.mkdir(parents=True, exist_ok=True)
    for name, (source, width) in STAGED.items():
        stage_as_fixed_records(CORPUS / source, staged / name, width)

    result = compile_project(project, goal="verify")
    assert result.succeeded, "\n".join(d.message for d in result.diagnostics[:10])
    return project


def test_the_rendered_sequential_job_builds_and_runs(built):
    """The claim this module exists to make: it compiles, it runs, and it writes.

    Everything before this was an argument that a sequential step *could* be rendered. `mvn verify`
    is what turns that into a fact, and it is why ADR-0040's refusal could be lifted.
    """
    output = built / CANDIDATE_TRANSACTIONS
    assert output.is_file(), "the job completed and wrote no transaction output"
    assert output.stat().st_size % 350 == 0


def test_the_create_path_runs_and_the_row_count_does_not_match_yet(built):
    """50 rows in, **100 out where COBOL leaves 94** -- the create path runs, and six times too often.

    The 44 rows `run-oracle.sh` asserts of the real program are what `optional_lookups` made
    reachable at all, so this is not the create path failing. The six extra rows are the same six
    transactions the next test characterises: accepted here and rejected by COBOL, each one posting
    to an (account, type, category) combination that then gets a balance row it should not have.

    Pinned at the number this run actually produces rather than at 94, because a test asserting the
    right answer while the pipeline produces a different one is a failing test with no diagnosis in
    it -- and the diagnosis is the finding.
    """
    balances = built / CANDIDATE_BALANCES
    assert balances.stat().st_size // 50 == 100


def test_the_accounts_are_all_there(built):
    """50 in, 50 out: no account was created, dropped, or duplicated by the working set.

    The one file whose shape matches the oracle exactly, and worth asserting separately -- it is
    what says the `replace` write mode and the shared store handle an update correctly, independent
    of whether the decision feeding them was right.
    """
    assert (built / CANDIDATE_ACCOUNTS).stat().st_size // 300 == 50


def test_the_transactions_differ_from_the_oracle_only_on_overpunched_amounts(built):
    """**The finding.** 256 of the oracle's 257 records are produced; 7 decisions differ.

    Six transactions are accepted here and rejected by `CBTRN02C`, and one the reverse. Every one of
    the six carries an amount whose last byte is a **negative zoned-decimal overpunch**
    (`}JKLMNOPQR`) -- the corpus has 50 such records. This pipeline reads that byte as a negative
    sign (audit G16, finding 2, and `decode_signed`'s own docstring), which makes the projected
    balance *fall* and the credit-limit check pass. GnuCOBOL's run rejected them as `0102 OVERLIMIT`,
    which is only possible if it added the amount rather than subtracting it.

    **The oracle's bytes cannot settle it**, and that is why this is recorded rather than fixed: the
    fixture's own `PROVENANCE.md` lists *"the zoned-decimal sign representation on REWRITE"* as
    known-unverified against IBM Enterprise COBOL, and the written records bear that out -- an input
    `0000009190}` comes back as `00000091900`, the same magnitude with no overpunch at all. Whose
    reading is right is a question about the tenant's compiler, not about this code.

    The seventh divergence carries no overpunch and follows from the other six: once a transaction is
    accepted that should not have been, the running balance every later decision reads is different.
    """
    candidate = built / CANDIDATE_TRANSACTIONS
    oracle = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "golden"
        / "CBACT04C"
        / "oracle"
        / "transact-stage1.dat"
    )
    ours = _ids(candidate.read_bytes().decode("latin-1"))
    theirs = _ids(oracle.read_bytes().decode("latin-1"))

    assert len(theirs) == 257
    assert len(ours) == 262
    assert len(ours & theirs) == 256
    assert len(ours - theirs) == 6, "accepted here, rejected by CBTRN02C"
    assert len(theirs - ours) == 1, "accepted by CBTRN02C, rejected here"

    daily = {
        line[0:16]: line[132:143]
        for line in (CORPUS / "dailytran.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    }
    assert all(daily[tran_id][-1] in "}JKLMNOPQR" for tran_id in ours - theirs), (
        "every extra record is one whose amount carries a negative overpunch"
    )


def _ids(raw: str) -> set[str]:
    """`TRAN-ID` of every 350-byte record in a transaction file."""
    return {raw[i : i + 16] for i in range(0, len(raw), 350)}


# --- what the disagreement turned out to be (measured after the run) -------------------------------

_POSITIVE_OVERPUNCH = "{ABCDEFGHI"
_NEGATIVE_OVERPUNCH = "}JKLMNOPQR"


def _oracle(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "oracle" / name


def test_the_oracle_wrote_every_amount_with_its_overpunch_digit_replaced_by_zero():
    """**The disagreement is data loss in the oracle, and this is the proof.**

    A trailing zoned-decimal overpunch carries the final digit *and* the sign: `G` is `+7`, `P` is
    `-7`, `{` is `+0`, `}` is `-0`. `dailytran.txt` amounts are overpunched in every posted record,
    and GnuCOBOL wrote **all 257 of them back with the last byte replaced by `0`** -- so
    `0000005047G`, which is `504.77`, came out as `00000050470`, which is `504.70`.

    No reading of the standard produces that. It is what happens when a runtime does not recognise
    the overpunch byte as a digit and coerces it to zero, and it is exactly the caveat the fixture's
    own `PROVENANCE.md` records as *"the zoned-decimal sign representation"* being unverified
    against IBM Enterprise COBOL.

    So the six transactions this pipeline accepts and `CBTRN02C` rejects are not this pipeline being
    wrong about a sign: the oracle's own decisions were computed from amounts missing a digit.
    """
    daily = {
        line[0:16]: line[132:143]
        for line in (CORPUS / "dailytran.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    }
    raw = _oracle("transact-stage1.dat").read_bytes().decode("latin-1")
    written = {raw[i : i + 16]: raw[i + 132 : i + 143] for i in range(0, len(raw), 350)}

    overpunched = {
        tran_id: source
        for tran_id, source in daily.items()
        if tran_id in written and source[-1] in _POSITIVE_OVERPUNCH + _NEGATIVE_OVERPUNCH
    }
    assert len(overpunched) == 257, "every posted amount in this corpus carries an overpunch"

    zeroed = [t for t, source in overpunched.items() if written[t] == source[:-1] + "0"]
    assert len(zeroed) == 257

    # `{` and `}` carry the digit zero, so replacing them with `0` loses only the sign. Every other
    # overpunch carries 1-9, and for those a digit is simply gone.
    carrying_a_digit = [t for t, source in overpunched.items() if source[-1] not in "{}"]
    assert len(carrying_a_digit) > 200, (
        "most posted amounts end in an overpunch carrying 1-9, so the loss is the common case "
        "rather than an edge of the corpus"
    )


def test_cbact04c_is_not_affected_by_it():
    """The green round trip stays green, and this is why -- checked rather than assumed.

    Zeroing the overpunch byte only loses something when that byte carries a **non-zero** digit.
    `CBACT04C` reads `tcatbal.txt`, whose balances end in a plain `0` and are not overpunched at
    all, and `acctdata.txt`, whose three signed fields all end in `{` -- positive zero, where the
    lossy reading and the correct one agree exactly.

    The only field in this corpus where the loss bites is `DALYTRAN-AMT`, which `CBACT04C` never
    reads. So `500 of 500` and `598 of 600` are untouched by the finding above.
    """
    corpus = CORPUS
    balances = [
        line[17:29]
        for line in (corpus / "tcatbal.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    ]
    assert all(value[-1] not in _POSITIVE_OVERPUNCH + _NEGATIVE_OVERPUNCH for value in balances)

    accounts = [
        line for line in (corpus / "acctdata.txt").read_text(encoding="latin-1").splitlines()
        if line.strip()
    ]
    for lo, hi in ((12, 24), (78, 90), (90, 102)):
        assert all(line[lo:hi][-1] == "{" for line in accounts), (
            "positive zero: the only overpunch whose digit is nothing to lose"
        )
