"""`generate` renders the equivalence test, runs it, and reports what it found (ADR-0065).

**This module exists to prove the gate is not inert**, which is the specific way the last two
mechanisms in this repository failed. ADR-0063 shipped a rule that did nothing while 1251 tests
passed, because every one of them built the state the feature wanted rather than the state
production produces. `rendering/java_equivalence_test.py` had the same shape of problem for longer:
fully written, fully tested, and called by nothing.

So every test here goes through `run_generate` -- the real entry point, with a real Maven build --
and asserts on the verdict a release gate would actually read. None of them calls the renderer
directly; `test_interest_equivalence.py` already does that, and it is exactly the coverage that let
an unwired renderer look finished.

**The load-bearing test is `test_a_wrong_rounding_mode_fails_the_rendered_test`.** A gate that only
ever reports `passed` is indistinguishable from one that reports nothing, so the proof that this
catches anything is a scripted defect reaching it and coming back `failed`.

Costs a Maven build per test. See `docs/development-environment.md` for `JAVA_HOME`, without which
these fail as "build failed, zero diagnostics".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    ProgramDesignEntry,
    UnifiedDesign,
    build_design_document,
)
from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from tests.support.interest_design import (
    _ALWAYS_WRITES_BODY,
    _COMPLETE_BODY,
    _CORRECT_BODY,
    _IMPORTS,
    _ROUNDING_BODY,
    COMPLETE_STEP,
    COMPOSITE,
    FIXTURE_ROOT,
    OUTPUT_COMPOSITE,
    PROGRAM,
    STEP,
)


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


def _author(body: str):
    def author(routing, system_prompt: str, user_content: str) -> str:
        chosen = _COMPLETE_BODY if "Step: completeTransaction" in user_content else body
        return json.dumps({"imports": _IMPORTS, "body": chosen, "notes": ""})

    return author


#: Step 49's body, to the token: the interest is computed and the input returned unchanged. It
#: compiles, and there is no field on the returned composite that could hold the answer.
_DISCARDS_BODY = """\
java.math.BigDecimal balance = item.balance().tranCatBal();
java.math.BigDecimal rate = item.disclosureGroup().disIntRate();
if (rate.signum() == 0) { return null; }
java.math.BigDecimal monthlyInterest = CobolArithmetic.divide(
    balance.multiply(rate), new java.math.BigDecimal("1200"), 2);
return item;"""


def _generate(tmp_path: Path, entry, entities, body: str, *, composites=None, steps=None):
    """Run the real `generate` with `body` scripted for the interest step."""
    document = build_design_document(
        [entry],
        unified_design=UnifiedDesign(
            domain_entities=entities,
            composite_types=composites if composites is not None else [COMPOSITE, OUTPUT_COMPOSITE],
            batch_jobs=[
                BatchJobDesign(
                    job_name="interestJob",
                    program_name=PROGRAM,
                    description="Monthly interest calculation.",
                    domain_entities=[e.name for e in entities],
                    steps=steps if steps is not None else [STEP, COMPLETE_STEP],
                )
            ],
            rest_endpoints=[],
        ),
    )
    design_path = tmp_path / "design.json"
    design_path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return run_generate(
        design_path,
        FIXTURE_ROOT,
        tmp_path / "target-project",
        author=_author(body),
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )


def test_generate_renders_the_test_beside_the_processor(tmp_path, entry, entities):
    """The file lands where Maven will find it, without anyone calling the renderer."""
    outcome = _generate(tmp_path, entry, entities, _CORRECT_BODY)

    rendered = (
        tmp_path
        / "target-project"
        / "src" / "test" / "java" / "com" / "modernized" / "batch" / "processor"
        / "ComputeInterestProcessorEquivalenceTest.java"
    )
    assert rendered.is_file(), (
        "generate rendered no equivalence test; the renderer is wired into nothing again"
    )
    # Rendered from the design, so it names the generated processor rather than CobolArithmetic --
    # the distinction the renderer's own docstring says makes it an equivalence test at all.
    assert "new ComputeInterestProcessor()" in rendered.read_text(encoding="utf-8")
    assert outcome.equivalence_test.status == "passed"


def test_a_correct_body_passes_and_the_verdict_states_its_limit(tmp_path, entry, entities):
    outcome = _generate(tmp_path, entry, entities, _CORRECT_BODY)

    assert outcome.equivalence_test.status == "passed"
    assert outcome.equivalence_test.test_class == "ComputeInterestProcessorEquivalenceTest"
    # A green verdict that does not say what it covers is how a reviewer over-reads one.
    assert "accumulator" in outcome.equivalence_test.reason


def test_a_wrong_rounding_mode_fails_the_rendered_test(tmp_path, entry, entities):
    """The proof the gate catches anything: a one-token defect comes back `failed`.

    `_ROUNDING_BODY` differs from the faithful translation by `divideRounded` where the COBOL
    truncates. Every earlier gate in this pipeline passes it -- it compiles, it returns a
    transaction, and it is wrong by a cent on exactly the rows the oracle was chosen to separate.
    """
    outcome = _generate(tmp_path, entry, entities, _ROUNDING_BODY)

    assert outcome.equivalence_test.status == "failed", (
        "a HALF_UP rounding defect reached the gate reported as passing; the rendered test either "
        "did not run or does not discriminate"
    )
    assert "ComputeInterestProcessorEquivalenceTest" in outcome.equivalence_test.reason


def test_a_zero_rate_transaction_fails_the_rendered_test(tmp_path, entry, entities):
    """The case that cannot be written as a number, and needs its own assertion to catch.

    `_ALWAYS_WRITES_BODY` gets every arithmetic row right and still emits a record COBOL never
    writes. Included because a rendered test that dropped the `assertNull` case would stay green
    here and lose a whole class of defect silently.
    """
    outcome = _generate(tmp_path, entry, entities, _ALWAYS_WRITES_BODY)

    assert outcome.equivalence_test.status == "failed"


def test_a_design_carrying_the_interest_nowhere_is_refused(tmp_path, entry, entities):
    """Step 49's defect, caught at render time (ADR-0065).

    **This is the shape that actually shipped**, reproduced rather than approximated: the interest
    step's output type is its *input* type, so the composite it returns has nowhere to carry
    `WS-MONTHLY-INT`, and the body computes the value and returns the item unchanged. Note what does
    *not* go wrong -- the processor compiles cleanly and every earlier gate in the pipeline passes
    it. The money is simply gone.

    An earlier version of this test removed `Tran` from the output composite instead. That made the
    *body* fail to compile, so the step never reached the renderer and the verdict came back
    `not_rendered` -- a different finding, and a reminder that a test has to build the state
    production actually produces.
    """
    discards = STEP.model_copy(update={"output_type": COMPOSITE.name})
    outcome = _generate(
        tmp_path, entry, entities, _DISCARDS_BODY, steps=[discards, COMPLETE_STEP]
    )

    assert outcome.outcomes[0].succeeded, (
        "the processor must compile for this to be the step-49 case: its defect was invisible to "
        f"the compiler. Got {outcome.outcomes[0].status}: {outcome.outcomes[0].reason}"
    )
    assert outcome.equivalence_test.status == "refused"
    assert "WS-MONTHLY-INT" in outcome.equivalence_test.reason
