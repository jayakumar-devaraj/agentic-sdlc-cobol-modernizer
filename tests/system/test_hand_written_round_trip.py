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

from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.tools.local_compiler import compile_project
from tests.system.test_cobol_oracle_comparison import (
    EXCLUSIONS,
    TRAN_LAYOUT,
    ComparisonResult,
    compare,
    load_oracle,
)
from tests.system.test_interest_equivalence import (
    FIXTURE_ROOT,
    _author,
    _CORRECT_BODY,
    _design_json,
    entities,  # noqa: F401  -- pytest fixture, imported for use
    entry,  # noqa: F401  -- pytest fixture, imported for use
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
    "cardxref.txt": CORPUS / "cardxref.txt",
    "discgrp.txt": CORPUS / "discgrp.txt",
}

CANDIDATE = Path("roundtrip") / "output" / "candidate.jsonl"

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


@dataclass(frozen=True)
class CandidateValue:
    """One field as the generated code produced it.

    Deliberately *not* re-encoded into a zoned-decimal string first. Round-tripping the candidate
    through COBOL's own on-disk representation would mean writing an encoder whose only consumer is
    this comparison -- the serialiser ADR-0029 declined -- and any bug in it would show up as a
    translation defect.
    """

    name: str
    value: object


def parse_candidate(path: Path) -> list[dict[str, CandidateValue]]:
    """The JSON lines the job wrote, keyed by COBOL field name.

    Alphanumerics keep whatever width the generated code emitted, so padding is compared rather
    than normalised. Numerics become `Decimal`s, which compare by value against the oracle's
    decoded overpunch.
    """
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        record = {}
        for name, _offset, _width, scale in TRAN_LAYOUT:
            value = raw[_JAVA_FIELD[name]]
            if scale is None:
                record[name] = CandidateValue(name, value)
            else:
                record[name] = CandidateValue(
                    name, None if value is None else Decimal(str(value))
                )
        records.append(record)
    return records


def describe_result(result: ComparisonResult) -> str:
    """The metric, with the two qualifiers ADR-0030 requires it never to appear without."""
    return (
        f"{result.render()}; wiring hand-written (ADR-0030), bodies scripted rather than "
        f"model-authored"
    )


@pytest.fixture(scope="module")
def candidate_records(tmp_path_factory, entry, entities) -> list[dict[str, CandidateValue]]:
    """Generate, wire, build and run -- once for the whole module, because Maven is the cost."""
    project = tmp_path_factory.mktemp("round-trip") / "target-project"
    design_path = _design_json(project.parent, entry, entities)

    outcome = run_generate(
        design_path,
        FIXTURE_ROOT,
        project,
        author=_author(_CORRECT_BODY),
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    assert outcome.succeeded, f"generation failed: {[o.reason for o in outcome.blocked]}"

    # The wiring, copied in rather than rendered -- and never into templates/, where it would join
    # every generated project and make every future round-trip claim ambiguous (ADR-0030, bound 1).
    shutil.copytree(HANDWRITTEN / "src", project / "src", dirs_exist_ok=True)

    staged = project / "roundtrip" / "input"
    staged.mkdir(parents=True, exist_ok=True)
    for name, source in INPUTS.items():
        shutil.copy2(source, staged / name)

    result = compile_project(project, goal="verify")
    assert result.succeeded, "\n".join(d.message for d in result.diagnostics[:10])

    output = project / CANDIDATE
    assert output.is_file(), "the job completed and wrote no candidate output"
    return parse_candidate(output)


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
