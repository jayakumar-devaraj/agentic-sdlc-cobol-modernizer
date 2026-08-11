"""Step 45: the interest equivalence test, rendered against generated code and run by real Maven.

**What makes this an equivalence test rather than a unit test.** The thing under test is the body of
a generated `process(...)` method. Everything else in the chain -- `CobolArithmetic`, the records,
the composite -- is deterministic, already covered, and would pass regardless of what the body does.
So the test is rendered into the generated project and compiled against the generated processor, and
the expected values come from `interest-oracle.json`, where they are literals derived by hand from
the COBOL (ADR-0021).

**The two bodies below are the point of this module.** `_CORRECT_BODY` is the faithful translation;
`_ROUNDING_BODY` differs from it by one token -- `rounded` where `divide` truncates -- which is the
exact defect ADR-0015's benchmark caught a real model making. Running both through real Maven is how
this harness demonstrates that it can fail, rather than asserting that it could.

Neither body is model-authored, and this module does not claim otherwise: they are injected through
`run_generate`'s `author=` parameter, the same seam every other test here uses. What that proves is
that **the harness discriminates**. Whether a *model* writes a correct body is a separate question
and needs a real call.
"""

from __future__ import annotations

import json
import re
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
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_equivalence_test import (
    UnrenderableOracleError,
    render_equivalence_test,
)
from cobol_modernizer.tools.local_compiler import compile_project

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
STEP = BatchStepDesign(
    step_name="computeInterest",
    source_paragraphs=["1300-COMPUTE-INTEREST"],
    role="processor",
    description="Computes monthly interest from a balance and its disclosure-group rate.",
    input_type="TranCatBalWithRate",
    output_type="Tran",
    # ADR-0022, closing G25. Verbatim from `CBACT04C.cbl:214` -- and note it is *not* in
    # `1300-COMPUTE-INTEREST`, which is why `source_paragraphs` could never have carried it.
    guard_condition="IF DIS-INT-RATE NOT = 0",
)

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
    f"return {_TRAN.format(amount='monthlyInterest')};"
)

#: One token different, and wrong. `divideRounded` is `HALF_UP`, so it disagrees with COBOL on every
#: row whose exact quotient is not already a whole number of cents.
_ROUNDING_BODY = _PRELUDE + (
    "java.math.BigDecimal monthlyInterest = CobolArithmetic.divideRounded("
    'balance.multiply(rate), new java.math.BigDecimal("1200"), 2);\n'
    f"return {_TRAN.format(amount='monthlyInterest')};"
)

#: Emits a zero-amount transaction for a zero rate instead of none. Every arithmetic row still
#: passes; only the zero-rate case catches it.
_ALWAYS_WRITES_BODY = (
    "java.math.BigDecimal balance = item.balance().tranCatBal();\n"
    "java.math.BigDecimal rate = item.disclosureGroup().disIntRate();\n"
    "java.math.BigDecimal monthlyInterest = CobolArithmetic.divide("
    'balance.multiply(rate), new java.math.BigDecimal("1200"), 2);\n'
    f"return {_TRAN.format(amount='monthlyInterest')};"
)

#: The imports the body needs. Supplied with the body because that is the real contract: the
#: renderer never reads the body, so it cannot derive them (`java_processor`'s docstring). The
#: `Tran` import is here because the processor's *signature* is rendered fully-qualified while the
#: body constructs the type by simple name -- the first compile failed on exactly that.
_IMPORTS = [
    "java.math.BigDecimal",
    "com.modernized.batch.cobol.CobolArithmetic",
    f"{DEFAULT_DOMAIN_PACKAGE}.Tran",
]


@pytest.fixture(scope="module")
def oracle() -> dict:
    return json.loads(ORACLE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def entry() -> ProgramDesignEntry:
    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return ProgramDesignEntry(program_name=PROGRAM, spec_extraction=extraction, critique=critique)


@pytest.fixture(scope="module")
def entities(entry) -> list:
    return build_domain_entities(FIXTURE_ROOT, [entry])


def _design_json(tmp_path: Path, entry: ProgramDesignEntry, entities: list) -> Path:
    document = build_design_document(
        [entry],
        unified_design=UnifiedDesign(
            domain_entities=entities,
            composite_types=[COMPOSITE],
            batch_jobs=[
                BatchJobDesign(
                    job_name="interestJob",
                    program_name=PROGRAM,
                    description="Monthly interest calculation.",
                    domain_entities=[e.name for e in entities],
                    steps=[STEP],
                )
            ],
            rest_endpoints=[],
        ),
    )
    path = tmp_path / "design.json"
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return path


def _author(body: str):
    def author(routing, system_prompt: str, user_content: str) -> str:
        return json.dumps({"imports": _IMPORTS, "body": body, "notes": ""})

    return author


def _generate_and_render(tmp_path: Path, entry, entities, oracle, body: str) -> Path:
    """Generate the project with `body`, render the equivalence test into it, return the project."""
    design_path = _design_json(tmp_path, entry, entities)
    output_dir = tmp_path / "target-project"
    outcome = run_generate(
        design_path,
        FIXTURE_ROOT,
        output_dir,
        author=_author(body),
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    assert outcome.succeeded, f"generation itself failed: {[o.reason for o in outcome.blocked]}"

    rendered = render_equivalence_test(
        oracle,
        package=DEFAULT_PACKAGE,
        test_class_name="ComputeInterestEquivalenceTest",
        processor_class="ComputeInterestProcessor",
        composite=COMPOSITE,
        entities=entities,
        output_entity="Tran",
        domain_package=DEFAULT_DOMAIN_PACKAGE,
    )
    destination = (
        output_dir / "src" / "test" / "java" / Path(DEFAULT_PACKAGE.replace(".", "/"))
    ) / "ComputeInterestEquivalenceTest.java"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8")
    return output_dir


# --- Rendering, which needs no toolchain ------------------------------------------------------------


def test_the_rendered_test_carries_every_oracle_row_as_a_literal(oracle, entities):
    rendered = render_equivalence_test(
        oracle,
        package=DEFAULT_PACKAGE,
        test_class_name="ComputeInterestEquivalenceTest",
        processor_class="ComputeInterestProcessor",
        composite=COMPOSITE,
        entities=entities,
        output_entity="Tran",
        domain_package=DEFAULT_DOMAIN_PACKAGE,
    )
    for row in oracle["rows"]:
        assert f'"{row["id"]}, {row["balance"]}, {row["rate"]}, {row["expected"]}"' in rendered
    # The zero-rate case must be a separate assertion, never a parameterised row with a 0.00.
    assert "assertNull" in rendered
    assert oracle["not_computed"][0]["id"] in rendered


def test_every_type_the_rendered_test_constructs_is_imported(oracle, entities):
    """The defect widening the composite actually caused, caught by compiling rather than reading.

    The import set was built from the *bound* entities -- balance, rate, output, composite. Adding
    `Account` and `CardXref` (G26) left the file constructing two types it had not imported, and
    javac said `cannot find symbol` twice. A renderer that emits a `new X(...)` must emit the
    import for `X`; asserting it here means the next component added cannot reintroduce this.
    """
    rendered = render_equivalence_test(
        oracle,
        package=DEFAULT_PACKAGE,
        test_class_name="ComputeInterestEquivalenceTest",
        processor_class="ComputeInterestProcessor",
        composite=COMPOSITE,
        entities=entities,
        output_entity="Tran",
        domain_package=DEFAULT_DOMAIN_PACKAGE,
    )
    # Every `new X(` in the file, not the first per line: the composite is constructed on one line
    # with its components nested inside it, so a per-line split sees only the outermost type.
    # `BigDecimal` is imported from java.math, and the processor shares this file's package, so
    # neither needs a domain import. Everything else the file constructs is a domain type.
    constructed = set(re.findall(r"new ([A-Z]\w*)\(", rendered)) - {
        "BigDecimal",
        "ComputeInterestProcessor",
    }
    assert {"TranCatBalWithRate", "Account", "CardXref"} <= constructed
    for name in constructed:
        assert f"import {DEFAULT_DOMAIN_PACKAGE}.{name};" in rendered, name


def test_rendering_refuses_a_binding_the_design_does_not_declare(oracle, entities):
    broken = json.loads(json.dumps(oracle))
    broken["java_binding"]["result_field"]["field"] = "tranTotalAmount"
    with pytest.raises(UnrenderableOracleError, match="no such component"):
        render_equivalence_test(
            broken,
            package=DEFAULT_PACKAGE,
            test_class_name="ComputeInterestEquivalenceTest",
            processor_class="ComputeInterestProcessor",
            composite=COMPOSITE,
            entities=entities,
            output_entity="Tran",
            domain_package=DEFAULT_DOMAIN_PACKAGE,
        )


def test_rendering_refuses_a_composite_that_cannot_reach_the_rate(oracle, entities):
    """PR #28's refusal, now enforced at render time rather than discovered by a model.

    A composite of balance-plus-account has no `DIS-INT-RATE` in it. The model asked to compute
    interest from one threw and said so; rendering a test against it would instead produce Java
    that does not compile, much later and with a worse diagnostic.
    """
    without_rate = CompositeType(
        name="TranCatBalWithAccount",
        components=[
            CompositeComponent(field_name="balance", entity_name="TranCatBal"),
            CompositeComponent(field_name="account", entity_name="Account"),
        ],
    )
    with pytest.raises(UnrenderableOracleError, match="no 'DisGroup' component"):
        render_equivalence_test(
            oracle,
            package=DEFAULT_PACKAGE,
            test_class_name="ComputeInterestEquivalenceTest",
            processor_class="ComputeInterestProcessor",
            composite=without_rate,
            entities=entities,
            output_entity="Tran",
            domain_package=DEFAULT_DOMAIN_PACKAGE,
        )


def test_rendering_refuses_an_oracle_with_no_declared_binding(oracle, entities):
    without_binding = {k: v for k, v in oracle.items() if k != "java_binding"}
    with pytest.raises(UnrenderableOracleError, match="will not guess"):
        render_equivalence_test(
            without_binding,
            package=DEFAULT_PACKAGE,
            test_class_name="ComputeInterestEquivalenceTest",
            processor_class="ComputeInterestProcessor",
            composite=COMPOSITE,
            entities=entities,
            output_entity="Tran",
            domain_package=DEFAULT_DOMAIN_PACKAGE,
        )


# --- The equivalence run itself, against real Maven -------------------------------------------------


def test_a_faithful_body_passes_the_equivalence_test(tmp_path, entry, entities, oracle):
    project = _generate_and_render(tmp_path, entry, entities, oracle, _CORRECT_BODY)
    result = compile_project(project, goal="verify")
    assert result.succeeded, "\n".join(d.message for d in result.diagnostics[:10])


def test_rounding_instead_of_truncating_fails_it(tmp_path, entry, entities, oracle):
    """The demonstration. One token different -- `divideRounded` for `divide` -- and it is caught.

    This is the defect ADR-0015's four-model benchmark saw a real model make while narrating this
    very calculation, so it is the failure mode this harness most needs to catch.
    """
    project = _generate_and_render(tmp_path, entry, entities, oracle, _ROUNDING_BODY)
    result = compile_project(project, goal="verify")
    assert not result.succeeded, "a HALF_UP body must not pass an equivalence test against COBOL"

    # The rows that fail must be exactly the rows the oracle says `HALF_UP` gets wrong -- no more,
    # no fewer. This is what closes the loop on the `rejects` metadata: `test_interest_oracle.py`
    # checks it against Python's Decimal, and this checks the same claim against real Java in a
    # real JVM. A row predicted to discriminate that does not, or one that fails unexpectedly,
    # means the table describes something other than what the target actually computes.
    failed = {
        row["id"] for row in oracle["rows"] if f'{row["id"]}: expected' in result.raw_output
    }
    predicted = {row["id"] for row in oracle["rows"] if "HALF_UP" in row["rejects"]}
    assert failed == predicted, f"failed {sorted(failed)}, oracle predicted {sorted(predicted)}"


def test_emitting_a_transaction_for_a_zero_rate_fails_it(tmp_path, entry, entities, oracle):
    """The row that is not a number, earning its place.

    This body's arithmetic is correct on every parameterised case. It is wrong only in writing a
    transaction COBOL never writes, and only the zero-rate assertion sees it.
    """
    project = _generate_and_render(tmp_path, entry, entities, oracle, _ALWAYS_WRITES_BODY)
    result = compile_project(project, goal="verify")
    assert not result.succeeded, "a zero rate must produce no transaction record"


def test_the_composite_reaches_every_field_1300_b_write_tx_takes_from_a_record(entities):
    """G26. The two fields the model left `null` because it could not reach them.

    `1300-B-WRITE-TX` builds the `TRAN-RECORD` this step returns. Two of its moves read records the
    composite did not carry: `ACCT-ID` (into `TRAN-DESC`) and `XREF-CARD-NUM` (into
    `TRAN-CARD-NUM`). The model named both, left them `null`, and said which paragraph produced
    them rather than inventing values.

    Asserted against the real COBOL and the real entities rather than against the composite alone,
    so this fails if either the copybooks or the paragraph change out from under it.
    """
    source = (FIXTURE_ROOT / "app" / "cbl" / "CBACT04C.cbl").read_text()
    assert "MOVE XREF-CARD-NUM        TO TRAN-CARD-NUM" in source
    assert "'Int. for a/c '" in source  # STRING ... ACCT-ID INTO TRAN-DESC

    carried = {c.entity_name: c.field_name for c in COMPOSITE.components}
    assert {"TranCatBal", "DisGroup", "Account", "CardXref"} <= set(carried)

    by_name = {e.name: e for e in entities}
    reachable = {
        f"item.{carried['Account']}().{f.java_field_name}()"
        for f in by_name["Account"].fields
    } | {
        f"item.{carried['CardXref']}().{f.java_field_name}()"
        for f in by_name["CardXref"].fields
    }
    assert "item.account().acctId()" in reachable
    assert "item.cardXref().xrefCardNum()" in reachable


def test_two_of_the_four_unreachable_fields_stay_unreachable_and_it_is_not_the_composites_fault():
    """The honest residual of G26, pinned so widening the composite is not mistaken for closing it.

    The model named four fields it could not populate. A composite fixes exactly the two that are
    **record data**. The other two are not, and no composite can carry them:

    - `TRAN-ID` is `STRING PARM-DATE, WS-TRANID-SUFFIX` -- a job parameter and a per-run counter.
      ADR-0020 says a composite's components are *existing domain entities*, and neither of these
      is one. The model also noted a stateless processor cannot produce a monotonic suffix
      correctly under restart or partitioning, which is a stronger objection than reachability.
    - `TRAN-ORIG-TS`/`TRAN-PROC-TS` come from `Z-GET-DB2-FORMAT-TIMESTAMP`, whose target fields are
      `REDEFINES` -- a construct the support matrix routes to a human gate rather than translating.

    So the real remainder is a design decision this test deliberately does not make: either
    `1300-B-WRITE-TX` becomes its own step with access to job parameters and a clock, or the target
    populates those fields outside the translated logic.
    """
    source = (FIXTURE_ROOT / "app" / "cbl" / "CBACT04C.cbl").read_text()
    assert "STRING PARM-DATE," in source
    assert "WS-TRANID-SUFFIX" in source
    assert "PERFORM Z-GET-DB2-FORMAT-TIMESTAMP" in source
    # Neither is a domain entity, so neither can become a composite component under ADR-0020.
    assert not any(c.entity_name in {"ParmDate", "Timestamp"} for c in COMPOSITE.components)


def test_the_zero_rate_guard_is_not_in_the_paragraph_the_step_names():
    """Why the first real model-authored body failed R10, pinned so it reads as design, not error.

    On 2026-08-10 a real Opus 5 call wrote a body that passed **all nine arithmetic rows** --
    truncation, both signs, the sub-cent cases, the negative zero, and both of `dailytran`'s real
    extremes -- and failed only R10, returning a `Tran` with `tranAmt=0.00` where COBOL writes no
    record at all.

    That is not the model translating badly. `STEP.source_paragraphs` is `1300-COMPUTE-INTEREST`,
    and **the guard is not in it**: `IF DIS-INT-RATE NOT = 0` sits in the main `PROCEDURE DIVISION`
    loop that *calls* the paragraph. The model was shown one paragraph and translated exactly that
    paragraph, faithfully.

    So the finding is about scope: the oracle's R10 describes behaviour spanning the caller, while
    the step design hands the generator only the callee. Either the step must name the guard's
    paragraph or R10 belongs to whichever step owns the loop.

    **Closed by ADR-0022, and not the way "add the guard paragraph to the step" assumed** -- that
    turned out to be impossible, because there is no such paragraph. `CBACT04C`'s first *named*
    paragraph is `0000-TCATBALF-OPEN.` at line 234; the guard is at 214, in the unnamed main body
    under `PROCEDURE DIVISION`. Naming `PROCEDURE DIVISION` would have scoped the step to the file
    opens and the account update as well.

    The fix is a declared `guard_condition`, which is where a condition belongs anyway:
    `source_paragraphs` answers *what code this came from*, a guard answers *when it runs*. This
    test keeps asserting the source relationship that made the point, so the reason the field
    exists cannot quietly stop being true.
    """
    source = (FIXTURE_ROOT / "app" / "cbl" / "CBACT04C.cbl").read_text().splitlines()
    guard = next(i for i, line in enumerate(source) if "IF DIS-INT-RATE NOT = 0" in line)
    paragraph = next(i for i, line in enumerate(source) if line.strip().startswith("1300-COMPUTE-INTEREST."))

    assert guard < paragraph, "the guard precedes the paragraph; it is in the caller"
    assert STEP.source_paragraphs == ["1300-COMPUTE-INTEREST"]
    # The step names the callee only, so nothing the generator was given mentions the guard.
    assert not any("DIS-INT-RATE NOT" in line for line in source[paragraph : paragraph + 12])
