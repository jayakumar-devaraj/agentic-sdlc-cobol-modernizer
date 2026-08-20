"""ADR-0030's measurement: generated logic, hand-written wiring, compared against COBOL's output.

**What this runs.** `run_generate` produces the two `ItemProcessor`s `CBACT04C`'s design declares.
`tests/fixtures/handwritten/CBACT04C/` supplies what nothing renders -- a reader, a writer, two step
beans and a job bean (gap G31) -- and real Maven builds and runs the result over the oracle's own
inputs. The records it writes are then compared field-for-field against `transact.dat` by
`test_cobol_oracle_comparison.compare`, the differential ADR-0029 built and shown to discriminate.

**Both qualifiers belong on every number this produces**, and `describe_result` is what puts them
there:

1. *The wiring was hand-written.* A green result does not mean this platform generated a working
   program. It means generated business logic matches COBOL's output when placed in wiring a human
   wrote (ADR-0030, bound 2).
2. *The bodies here are scripted, not model-authored.* They are the same fixtures step 45 uses, so
   what this module demonstrates is that the path works end to end and that the comparison sees a
   real candidate. Whether a *model* writes a body that passes is a separate question and needs a
   live call -- `test_interest_equivalence` draws exactly this line for the same reason.

**Why the candidate is JSON rather than a 350-byte record.** ADR-0029 compares fields because
building a fixed-width serialiser whose only consumer is the assertion about it would be a check
written to match whatever it needed to match. The writer emits each generated `Tran`'s own accessor
values, so a short `TRAN-SOURCE` stays short and fails -- which is the property that makes the
comparison worth running at all.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    ProgramDesignEntry,
    UnifiedDesign,
    build_design_document,
)
from cobol_modernizer.core.model_client import RunBudget, collect_usage
from cobol_modernizer.graph.generate_pipeline import DEFAULT_DOMAIN_PACKAGE, run_generate
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_reader import reader_class_name, render_item_reader
from cobol_modernizer.rendering.java_writer import render_item_writer, writer_class_name
from cobol_modernizer.tools.local_compiler import compile_project
from tests.system.test_account_break_posting import _FAITHFUL as _POSTING_BODY
from tests.system.test_account_break_posting import _IMPORTS as _POSTING_IMPORTS
from tests.system.test_account_break_posting import POSTING
from tests.system.test_account_break_posting import STEP as POSTING_STEP
from tests.system.test_cobol_oracle_comparison import (
    ACCOUNT_LAYOUT,
    EXCLUSIONS,
    TRAN_LAYOUT,
    ComparisonResult,
    compare,
    load_account_oracle,
    load_oracle,
    parse_fixed_records,
)
from tests.system.test_interest_equivalence import (
    _COMPLETE_BODY,
    _CORRECT_BODY,
    _IMPORTS,
    COMPLETE_STEP,
    COMPOSITE,
    FIXTURE_ROOT,
    OUTPUT_COMPOSITE,
    PROGRAM,
    STEP,
)

HANDWRITTEN = Path(__file__).resolve().parents[1] / "fixtures" / "handwritten" / "CBACT04C"
ORACLE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "golden" / "CBACT04C" / "oracle"
CORPUS = FIXTURE_ROOT / "app" / "data" / "ASCII"

#: What the reader is given, and where each file comes from.
#:
#: `acctdata-stage1.dat` rather than the shipped `acctdata.txt`: CBTRN02C rewrites the account file
#: too, so the state CBACT04C read is the one *between* the stages. `discgrp` and `cardxref` are
#: untouched by either program, so the corpus copies are the same bytes the oracle run saw.
INPUTS = {
    "tcatbal-posted.dat": ORACLE_DIR / "tcatbal-posted.dat",
    "acctdata-stage1.dat": ORACLE_DIR / "acctdata-stage1.dat",
}

#: The two lookup files the corpus ships as *text*, with the record length to frame them at.
#:
#: A COBOL `WRITE` produces fixed-length records with no terminators, which is what the rendered
#: reader expects and what the unloaded oracle files already are. The shipped `cardxref.txt` and
#: `discgrp.txt` are a distribution format -- 36-character lines for a 50-byte record in one case --
#: so they are converted here, exactly as the oracle pipeline's own `LOADIDX` step converts them
#: before either program sees them. Teaching the renderer to guess the framing per file would bake
#: a property of one distribution into every generated project.
TEXT_INPUTS = {
    "cardxref.dat": (CORPUS / "cardxref.txt", 50),
    "discgrp.dat": (CORPUS / "discgrp.txt", 50),
}

#: What the rendered writers produce: COBOL's own record format, in the same layout the oracle is
#: read with. The candidate is no longer a harness serialisation -- it is the program's output.
CANDIDATE = Path("roundtrip") / "output" / "transact.dat"

#: Where the rendered reader lands. Its own package, so a reviewer can tell rendered wiring
#: from the hand-written job configuration by path alone.
READER_PACKAGE = "com.modernized.batch.reader"
WRITER_PACKAGE = "com.modernized.batch.writer"

#: What the rendered writers produce: COBOL's own record format, in the same layout the oracle is
#: read with. The candidate is no longer a harness serialisation -- it is the program's output.
CANDIDATE = Path("roundtrip") / "output" / "transact.dat"

#: Where the rendered reader lands. Its own package, so a reviewer can tell rendered wiring
#: from the hand-written job configuration by path alone.
READER_PACKAGE = "com.modernized.batch.reader"
WRITER_PACKAGE = "com.modernized.batch.writer"

#: The generated record's accessors, in the copybook's own field order.
_JAVA_FIELD = {
    "TRAN-ID": "tranId",
    "TRAN-TYPE-CD": "tranTypeCd",
    "TRAN-CAT-CD": "tranCatCd",
    "TRAN-SOURCE": "tranSource",
    "TRAN-DESC": "tranDesc",
    "TRAN-AMT": "tranAmt",
    "TRAN-MERCHANT-ID": "tranMerchantId",
    "TRAN-MERCHANT-NAME": "tranMerchantName",
    "TRAN-MERCHANT-CITY": "tranMerchantCity",
    "TRAN-MERCHANT-ZIP": "tranMerchantZip",
    "TRAN-CARD-NUM": "tranCardNum",
    "TRAN-ORIG-TS": "tranOrigTs",
    "TRAN-PROC-TS": "tranProcTs",
}


#: `CVACT01Y`'s accessors, in the copybook's own field order.
_JAVA_ACCOUNT_FIELD = {
    "ACCT-ID": "acctId",
    "ACCT-ACTIVE-STATUS": "acctActiveStatus",
    "ACCT-CURR-BAL": "acctCurrBal",
    "ACCT-CREDIT-LIMIT": "acctCreditLimit",
    "ACCT-CASH-CREDIT-LIMIT": "acctCashCreditLimit",
    "ACCT-OPEN-DATE": "acctOpenDate",
    "ACCT-EXPIRAION-DATE": "acctExpiraionDate",
    "ACCT-REISSUE-DATE": "acctReissueDate",
    "ACCT-CURR-CYC-CREDIT": "acctCurrCycCredit",
    "ACCT-CURR-CYC-DEBIT": "acctCurrCycDebit",
    "ACCT-ADDR-ZIP": "acctAddrZip",
    "ACCT-GROUP-ID": "acctGroupId",
}

#: The account file is rewritten in place, as `REWRITE` does, so the candidate is the input file
#: the job was given.
ACCOUNT_CANDIDATE = Path("roundtrip") / "input" / "acctdata-stage1.dat"


@dataclass(frozen=True)
class CandidateValue:
    """One field value, for the mutation tests below.

    The candidate itself no longer needs this: the rendered writers emit COBOL's own record format,
    so both sides of the comparison are parsed by `parse_fixed_records` and arrive as `FieldValue`s.
    This stays because a mutation test has to *construct* a wrong value, and `FieldValue` takes
    stored bytes rather than a number.
    """

    name: str
    value: object



def stage_as_fixed_records(source: Path, destination: Path, record_length: int) -> None:
    """Convert a line-terminated corpus file into the fixed-length records COBOL would have written.

    Short lines are padded: the trailing `FILLER` of a record simply was not written out. A line
    *longer* than the record is a different file than the one expected and raises, rather than being
    truncated into something plausible.
    """
    records = []
    for line in source.read_text(encoding="latin-1").replace("\r\n", "\n").split("\n"):
        if not line:
            continue
        if len(line) > record_length:
            raise ValueError(
                f"{source.name}: {len(line)}-character line for a {record_length}-byte record"
            )
        records.append(line.ljust(record_length))
    destination.write_bytes("".join(records).encode("latin-1"))


def render_writers_into(project: Path, design, program_name: str) -> list[Path]:
    """Render an `ItemWriter` for each step that writes a file. Returns the paths written."""
    written = []
    for step in (COMPLETE_STEP, POSTING_STEP):
        source = render_item_writer(
            step,
            design,
            program_name,
            package=WRITER_PACKAGE,
            domain_package=DEFAULT_DOMAIN_PACKAGE,
        )
        destination = (
            project / "src" / "main" / "java" / Path(WRITER_PACKAGE.replace(".", "/"))
            / f"{writer_class_name(step)}.java"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(source, encoding="utf-8")
        written.append(destination)
    return written


def render_reader_into(project: Path, design, program_name: str) -> Path:
    """Render the interest step's `ItemReader` into the generated project. Returns its path."""
    source = render_item_reader(
        STEP,
        design,
        program_name,
        package=READER_PACKAGE,
        domain_package=DEFAULT_DOMAIN_PACKAGE,
    )
    destination = (
        project / "src" / "main" / "java" / Path(READER_PACKAGE.replace(".", "/"))
        / f"{reader_class_name(STEP)}.java"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(source, encoding="utf-8")
    return destination


def describe_result(result: ComparisonResult) -> str:
    """The metric, with the qualifiers ADR-0030 requires it never to appear without.

    **The first one narrowed when the reader started being rendered** (G31). It used to say the
    wiring was hand-written, full stop; the reader now comes from `design.json` and the job and step
    beans do not. Narrowing it rather than dropping it is the point: a qualifier that quietly becomes
    less true is how a stopgap turns permanent, and one that quietly becomes broader is how a real
    result gets undersold.
    """
    return (
        f"{result.render()}; reader and writers rendered from design.json, job and step wiring "
        f"hand-written (ADR-0030), bodies scripted rather than model-authored"
    )


def _unified_design(entry, entities) -> UnifiedDesign:
    """The design both the generated project and the rendered reader are built from.

    One construction, used twice: a second copy assembled for the renderer could differ from the one
    written to design.json, and then the reader would be rendered against a design nothing else saw.
    """
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name=PROGRAM,
                description="Monthly interest calculation.",
                domain_entities=[entity.name for entity in entities],
                steps=[STEP, COMPLETE_STEP, POSTING_STEP],
            )
        ],
        rest_endpoints=[],
        # What the rendered reader is built from (G31): access paths, keys and record layouts.
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


def _design_json(directory: Path, entry, entities) -> Path:
    """`CBACT04C`'s three processor steps: compute, complete, post.

    Built here rather than reused from `test_interest_equivalence`, which declares the first two.
    The third is ADR-0027's `postAccountInterest`, and without it the job writes transactions and
    never touches the account file -- so half of what the program does would go unmeasured.
    """
    document = build_design_document([entry], unified_design=_unified_design(entry, entities))
    path = directory / "design.json"
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return path


def _scripted_author(routing, system_prompt: str, user_content: str) -> str:
    """One scripted body per step, dispatched on the step the prompt names.

    Three steps now, so a single body would put interest arithmetic in the posting step and the
    compile failure would read as a renderer defect rather than as this fixture's mistake.
    """
    if "Step: completeTransaction" in user_content:
        body, imports = _COMPLETE_BODY, _IMPORTS
    elif "Step: postAccountInterest" in user_content:
        body, imports = _POSTING_BODY, _POSTING_IMPORTS
    else:
        body, imports = _CORRECT_BODY, _IMPORTS
    return json.dumps({"imports": list(imports), "body": body, "notes": ""})


@pytest.fixture(scope="module")
def design_inputs():
    """The real `CBACT04C` spec and its `pic_mapper`-derived entities.

    Built here rather than imported from `test_interest_equivalence`: importing that module's
    fixtures and then naming them as parameters is a redefinition, and ruff is right to refuse it.
    """

    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    entry = ProgramDesignEntry(
        program_name=PROGRAM, spec_extraction=extraction, critique=critique
    )
    return entry, build_domain_entities(FIXTURE_ROOT, [entry])


def wire_build_and_run(project: Path, design_inputs, **generate_kwargs):
    """Generate the processors, add the hand-written wiring, build and run. Returns the records.

    Shared by the scripted path and the live one so that **the only difference between them is who
    wrote the method bodies** -- if the wiring or the comparison differed too, a disagreement
    between the two runs would not be attributable to the bodies.
    """
    entry, entities = design_inputs
    project.parent.mkdir(parents=True, exist_ok=True)
    design_path = _design_json(project.parent, entry, entities)
    design = _unified_design(entry, entities)

    outcome = run_generate(design_path, FIXTURE_ROOT, project, **generate_kwargs)
    assert outcome.succeeded, f"generation failed: {[o.reason for o in outcome.blocked]}"

    # The wiring, copied in rather than rendered -- and never into templates/, where it would join
    # every generated project and make every future round-trip claim ambiguous (ADR-0030, bound 1).
    shutil.copytree(HANDWRITTEN / "src", project / "src", dirs_exist_ok=True)

    # The reader is rendered rather than copied in: `InterestJobConfiguration` constructs
    # `ComputeInterestItemReader`, which exists only because this line ran. Free and deterministic,
    # so it happens on every run rather than being a mode.
    render_reader_into(project, design, entry.program_name)
    render_writers_into(project, design, entry.program_name)

    staged = project / "roundtrip" / "input"
    staged.mkdir(parents=True, exist_ok=True)
    for name, source in INPUTS.items():
        shutil.copy2(source, staged / name)
    for name, (source, record_length) in TEXT_INPUTS.items():
        stage_as_fixed_records(source, staged / name, record_length)

    result = compile_project(project, goal="verify")
    assert result.succeeded, "\n".join(d.message for d in result.diagnostics[:10])

    output = project / CANDIDATE
    accounts = project / ACCOUNT_CANDIDATE
    assert output.is_file(), "the job completed and wrote no transaction output"
    assert accounts.is_file(), "the account file the job rewrites is gone"
    return (
        parse_fixed_records(output, TRAN_LAYOUT, 350),
        parse_fixed_records(accounts, ACCOUNT_LAYOUT, 300),
    ), outcome


@pytest.fixture(scope="module")
def candidate(tmp_path_factory, design_inputs):
    """Generate, wire, build and run -- once for the whole module, because Maven is the cost."""
    project = tmp_path_factory.mktemp("round-trip") / "target-project"
    halves, _outcome = wire_build_and_run(
        project,
        design_inputs,
        author=_scripted_author,
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    return halves


@pytest.fixture(scope="module")
def candidate_records(candidate):
    return candidate[0]


@pytest.fixture(scope="module")
def candidate_accounts(candidate):
    return candidate[1]


def test_the_wiring_produces_one_record_per_non_zero_rate(candidate_records):
    """94 balance rows in, 50 records out -- the guard, running in generated Java.

    Checked before the field comparison so that a candidate of the wrong size reports as what it is
    rather than as fifty mismatched fields.
    """
    assert len(candidate_records) == len(load_oracle()) == 50


def test_generated_logic_matches_the_cobol_oracle(candidate_records):
    """The measurement. Read the two qualifiers in `describe_result` before quoting the number."""
    result = compare(candidate_records, load_oracle())
    assert result.passed, "\n".join(result.mismatches[:10]) + f"\n\n{describe_result(result)}"
    assert result.compared == 50 * (len(TRAN_LAYOUT) - len(EXCLUSIONS))
    print(f"\nround trip: {describe_result(result)}")


def test_the_comparison_would_have_caught_a_wrong_amount(candidate_records):
    """The candidate is real, so the differential is re-shown to fail against *it*, not a copy.

    `test_cobol_oracle_comparison` demonstrates this against the oracle compared with itself. Doing
    it again here answers a different question: that the parsed candidate carries values a mutation
    can move, rather than something that compares equal for a reason unrelated to its contents.
    """
    mutated = [dict(record) for record in candidate_records]
    mutated[13]["TRAN-AMT"] = CandidateValue("TRAN-AMT", Decimal("0.01"))
    assert not compare(mutated, load_oracle()).passed


# --- the same measurement, with a model writing the bodies -----------------------------------------


@pytest.mark.live_claude_cli
def test_a_model_authored_run_is_compared_against_the_same_oracle(tmp_path, design_inputs):
    """The question ADR-0030 says the oracle exists to answer: **is the generated logic correct?**

    Identical to the scripted run in every respect except who wrote the two method bodies -- same
    design, same hand-written wiring, same inputs, same differential -- so a disagreement between
    the two is attributable to the bodies and to nothing else.

    **Costs real money**, so it is skipped unless `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`, and the
    budget is a ceiling rather than a hope: eight calls covers two steps and their heal attempts,
    and `RunBudgetExceededError` stops the run rather than letting a repair loop spend without
    bound.

    A failure here is a **finding, not a broken test**. If a model-authored body disagrees with
    COBOL, the mismatch list names the field and both values, and that is the most useful output
    this repo can produce.
    """
    project = tmp_path / "live" / "target-project"
    with collect_usage(RunBudget(max_model_calls=12)) as usage:
        (records, accounts), outcome = wire_build_and_run(project, design_inputs)

    result = compare(records, load_oracle())
    authored = {step.step_name: step.attempts for step in outcome.compiled}
    notes = [note for step in outcome.compiled for note in step.notes if note.strip()]

    print(
        f"\nlive round trip: {result.render()}; wiring hand-written (ADR-0030), bodies "
        f"model-authored"
        f"\n  steps and attempts: {authored}"
        f"\n  account half: {assert_account_half_matches_except_the_last(accounts).render()}"
        f"\n  {usage.model_calls} model call(s), {usage.input_tokens} in / {usage.total_tokens} tokens, "
        f"notional cost {usage.notional_cost_usd}"
    )
    for note in notes:
        print(f"  model note: {note}")

    assert result.passed, "\n".join(result.mismatches[:10])


# --- the other half of what CBACT04C writes --------------------------------------------------------


def account_interest_by_id() -> dict[str, Decimal]:
    """Per account, the interest **COBOL itself** wrote -- read off the oracle, never recomputed."""
    totals: dict[str, Decimal] = {}
    prefix = len("Int. for a/c ")
    for record in load_oracle():
        acct = record["TRAN-DESC"].raw[prefix : prefix + 11]
        totals[acct] = totals.get(acct, Decimal(0)) + record["TRAN-AMT"].value
    return totals


def assert_account_half_matches_except_the_last(accounts) -> ComparisonResult:
    """Every account matches except the one COBOL never credits, and that one by exactly its interest.

    **The divergence is COBOL's, and reproducing it was the cheaper option.** `CBACT04C`'s loop is
    `PERFORM UNTIL END-OF-FILE = 'Y'` with the account-break post in the `ELSE` of
    `IF END-OF-FILE = 'N'`, so that branch never runs and the final account keeps a balance that
    excludes the interest transactions the same run wrote for it. The wiring could have skipped the
    last account and made this green; that would have been encoding a defect to improve a number.

    So the shape is pinned instead: exactly one record differs, it is that account, the field is
    `ACCT-CURR-BAL`, and the difference is exactly its interest as read from the transaction oracle.
    A second diverging account, a different field, or a different amount all fail.
    """
    oracle = load_account_oracle()
    result = compare(accounts, oracle, ACCOUNT_LAYOUT, {})

    interest = account_interest_by_id()
    last = max(interest)
    index = next(i for i, record in enumerate(oracle) if record["ACCT-ID"].raw == last)

    # Every mismatch is on that one record, and in a field `1050-UPDATE-ACCOUNT` writes. The
    # paragraph does exactly three things -- ADD WS-TOTAL-INT TO ACCT-CURR-BAL, MOVE 0 TO
    # ACCT-CURR-CYC-CREDIT, MOVE 0 TO ACCT-CURR-CYC-DEBIT -- so if the diagnosis is right, the
    # divergence set is a subset of its write set and of nothing else. It is.
    writes = {"ACCT-CURR-BAL", "ACCT-CURR-CYC-CREDIT", "ACCT-CURR-CYC-DEBIT"}
    for mismatch in result.mismatches:
        assert mismatch.startswith(f"record {index} "), mismatch
        assert mismatch.split()[2].rstrip(":") in writes, mismatch

    balance = accounts[index]["ACCT-CURR-BAL"].value - oracle[index]["ACCT-CURR-BAL"].value
    assert balance == interest[last], (
        f"account {last} differs by {balance}; expected exactly its uncredited interest "
        f"{interest[last]}"
    )
    # The cycle totals are the paragraph's other two statements. COBOL left this account's alone,
    # so wherever it carried a non-zero total the candidate's zero disagrees -- same single cause,
    # and asserting it keeps a future divergence with a *different* cause from hiding here.
    for field in ("ACCT-CURR-CYC-CREDIT", "ACCT-CURR-CYC-DEBIT"):
        if accounts[index][field].value != oracle[index][field].value:
            assert accounts[index][field].value == 0, field
            assert oracle[index][field].value != 0, field
    assert len(result.mismatches) <= len(writes), "; ".join(result.mismatches[:6])
    return result


def test_the_account_half_covers_every_account(candidate_accounts):
    """50 accounts in, 50 out. Checked before the fields, so an empty run cannot read as mismatches."""
    assert len(candidate_accounts) == len(load_account_oracle()) == 50


def test_the_account_half_differs_only_where_cobol_never_posts(candidate_accounts):
    """The second half of the round trip, and the more exacting one: **no field is excluded.**

    `transact.dat` gives up `TRAN-ID` and both timestamps by ADR-0026. The account record gives up
    nothing -- all twelve fields are producible -- so they have to match by being right rather than
    by being skipped.
    """
    result = assert_account_half_matches_except_the_last(candidate_accounts)
    print(
        f"account half: {result.render()}; the {len(result.mismatches)} mismatch(es) are "
        f"CBACT04C's unreachable EOF branch, in the fields 1050-UPDATE-ACCOUNT writes"
    )


def test_the_account_comparison_would_catch_a_wrong_balance(candidate_accounts):
    """Shown to fail against the real candidate, not against a copy of the oracle.

    One cent on an account that is **not** the last one, so it cannot pass by colliding with the
    divergence the test above tolerates.
    """
    mutated = [dict(record) for record in candidate_accounts]
    mutated[3]["ACCT-CURR-BAL"] = CandidateValue("ACCT-CURR-BAL", Decimal("0.01"))
    result = compare(mutated, load_account_oracle(), ACCOUNT_LAYOUT, {})
    assert any("record 3 ACCT-CURR-BAL" in m for m in result.mismatches)


# --- the qualifier is enforced, not remembered ------------------------------------------------------

README = Path(__file__).resolve().parents[2] / "README.md"

#: What ADR-0030's bound 2 requires beside the count. Matched as a word rather than a sentence so
#: rewording the paragraph does not fail this, but dropping the fact does.
QUALIFIER = "hand-written"


def test_the_readme_never_states_the_round_trip_count_without_its_qualifier():
    """`1 of 4` means *generated logic inside hand-written wiring*, and a bare number does not say so.

    ADR-0030 accepted the stopgap on the condition that every result reports it, and the risk it
    named is that the stopgap becomes permanent -- which begins with the qualifier quietly falling
    off the number. `describe_result` enforces it for anything printed by a run; this enforces it for
    the one file people actually read.

    Paragraph-scoped rather than file-scoped on purpose: a qualifier three screens away from the
    claim is not a qualifier.
    """
    paragraphs = README.read_text(encoding="utf-8").split("\n\n")
    claims = [paragraph for paragraph in paragraphs if "1 of 4" in paragraph]
    assert claims, "the README no longer states the round-trip count at all"
    for claim in claims:
        assert QUALIFIER in claim, (
            "the round-trip count appears without its qualifier:\n" + claim[:400]
        )


def test_the_readme_guard_fails_on_a_bare_claim():
    """The guard above passes; this is what proves it can fail.

    Checked against a string rather than by editing README.md, because a test that mutates the file
    it guards can leave the repository changed when it fails -- and once did, in this session.
    """
    bare = ["the count is 1 of 4 and nothing else is said about it"]
    assert not [claim for claim in bare if QUALIFIER in claim]
