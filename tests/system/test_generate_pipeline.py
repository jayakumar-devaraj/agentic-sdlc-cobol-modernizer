"""The self-healing loop, against **real Maven builds** with the model scripted.

The model is injected, but nothing else is: every attempt renders a real file, writes it into a real
copy of the target template, and runs a real `mvn compile`. That is the only way to test a heal loop
honestly — a mocked compiler would let the loop "recover" from failures a compiler would never have
reported, and would have hidden the Spring Batch 6 package rename that step 41 caught.

The scripted `author` returns a different body per attempt, so a test can say "fail once, then
succeed" and assert the loop really did compile twice and stop.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.contracts import BatchStepDesign, ProgramDesignEntry
from cobol_modernizer.core.model_client import MAX_TRANSPORT_ATTEMPTS
from cobol_modernizer.graph.generate_pipeline import (
    MAX_HEAL_ATTEMPTS,
    heal_step,
    processor_relative_path,
)
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.tools.local_compiler import compile_project

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "target-spring-boot-baseline"
PROGRAM = "CBACT04C"
PACKAGE = "com.modernized.batch.processor"

STEP = BatchStepDesign(
    step_name="passThrough",
    source_paragraphs=["1300-COMPUTE-INTEREST"],
    input_type="TranCatBal",
    output_type="TranCatBal",
    role="processor",
    description="Returns the input unchanged.",
        guard_condition=None)

GOOD = "return item;"
BROKEN = "return CobolArithmetic.truncateTypo(item, 2);"
BROKEN_IMPORTS = ["com.modernized.batch.cobol.CobolArithmetic"]


def _scripted_author(*bodies: tuple[str, list[str]]):
    """Return each (body, imports) in turn, recording the prompts it was given."""
    calls: list[str] = []

    def author(routing, system_prompt: str, user_content: str) -> str:
        body, imports = bodies[min(len(calls), len(bodies) - 1)]
        calls.append(user_content)
        return json.dumps({"imports": imports, "body": body, "notes": ""})

    author.calls = calls  # type: ignore[attr-defined]
    return author


def _advise(repairable: bool, instruction: str = "call truncate, not truncateTypo"):
    def advise(routing, system_prompt: str, user_content: str) -> str:
        return json.dumps({
            "repairable": repairable,
            "reason": "scripted verdict",
            "instruction": instruction if repairable else "",
        })

    return advise


@pytest.fixture(scope="module")
def program_entry() -> ProgramDesignEntry:
    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return ProgramDesignEntry(
        program_name=PROGRAM, spec_extraction=extraction, critique=critique
    )


@pytest.fixture(scope="module")
def entities(program_entry):
    return build_domain_entities(FIXTURE_ROOT, [program_entry])


@pytest.fixture
def project(tmp_path) -> Path:
    destination = tmp_path / "proj"
    shutil.copytree(TEMPLATE, destination, ignore=shutil.ignore_patterns("target"))
    return destination


def _heal(project, program_entry, entities, author, advise, **overrides):
    kwargs = {
        "package": PACKAGE,
        "input_type": "java.math.BigDecimal",
        "output_type": "java.math.BigDecimal",
        "tier": ComplexityTier.SIMPLE,
        "author": author,
        "advise": advise,
    }
    kwargs.update(overrides)
    return heal_step(FIXTURE_ROOT, project, program_entry, STEP, entities, **kwargs)


# --- The two caps are not the same number, and must never become it ------------------------------


def test_the_heal_cap_is_separate_from_the_transport_cap():
    # They bound unrelated things and multiply if confused -- ADR-0013's stacking failure.
    assert MAX_HEAL_ATTEMPTS == 3
    assert MAX_TRANSPORT_ATTEMPTS != MAX_HEAL_ATTEMPTS


# --- Real compiles ------------------------------------------------------------------------------


def test_code_that_compiles_first_time_costs_one_attempt(project, program_entry, entities):
    author = _scripted_author((GOOD, []))
    outcome = _heal(project, program_entry, entities, author, _advise(True))

    assert outcome.succeeded
    assert outcome.status == "compiled"
    assert outcome.attempts == 1
    assert len(author.calls) == 1, "a compiling first attempt must not be regenerated"
    assert (project / outcome.relative_path).is_file()


def test_the_loop_really_heals_a_real_compile_error(project, program_entry, entities):
    """The property step 42 exists for: broken in, compiling out, without a human.

    Attempt 1 calls a method that does not exist; the compiler says so; attempt 2 is correct.
    """
    author = _scripted_author((BROKEN, BROKEN_IMPORTS), (GOOD, []))
    outcome = _heal(project, program_entry, entities, author, _advise(True))

    assert outcome.succeeded, outcome.reason
    assert outcome.attempts == 2
    assert len(author.calls) == 2
    assert (project / outcome.relative_path).read_text(encoding="utf-8").count("return item;") == 1


def test_the_repair_prompt_carries_the_previous_body_and_the_real_diagnostic(
    project, program_entry, entities
):
    # A repair prompt that omits either is asking for a rewrite from scratch, which makes attempts
    # independent instead of cumulative.
    author = _scripted_author((BROKEN, BROKEN_IMPORTS), (GOOD, []))
    _heal(project, program_entry, entities, author, _advise(True))

    second = author.calls[1]
    assert "## Repair attempt 2" in second
    assert "truncateTypo" in second, "the previous body must be shown"
    assert "cannot find symbol" in second, "the real compiler diagnostic must be shown"
    assert "call truncate, not truncateTypo" in second, "the validator's instruction must be shown"


def test_a_blocked_verdict_stops_immediately_rather_than_spending_attempts(
    project, program_entry, entities
):
    # The loop's hard job. Retrying a design defect burns the whole budget and produces three worse
    # versions of the same code.
    author = _scripted_author((BROKEN, BROKEN_IMPORTS))
    outcome = _heal(project, program_entry, entities, author, _advise(False))

    assert outcome.status == "blocked"
    assert outcome.attempts == 1
    assert len(author.calls) == 1, "a blocked verdict must not cost a second generation"


def test_persistent_failure_exhausts_the_cap_and_says_so(project, program_entry, entities):
    author = _scripted_author((BROKEN, BROKEN_IMPORTS))
    outcome = _heal(project, program_entry, entities, author, _advise(True))

    assert outcome.status == "exhausted"
    assert not outcome.succeeded
    assert outcome.attempts == MAX_HEAL_ATTEMPTS
    assert len(author.calls) == MAX_HEAL_ATTEMPTS
    assert str(MAX_HEAL_ATTEMPTS) in outcome.reason


def test_a_failed_run_still_leaves_the_code_that_failed_on_disk(project, program_entry, entities):
    # A human reviewing a failure needs the file, not its absence.
    author = _scripted_author((BROKEN, BROKEN_IMPORTS))
    outcome = _heal(project, program_entry, entities, author, _advise(False))

    written = (project / outcome.relative_path).read_text(encoding="utf-8")
    assert "truncateTypo" in written
    assert outcome.java_source == written


def test_generator_notes_survive_into_the_outcome(project, program_entry, entities):
    def author(routing, system_prompt, user_content):
        return json.dumps({
            "imports": [], "body": GOOD, "notes": "the zero-rate guard lives in the caller",
        })

    outcome = _heal(project, program_entry, entities, author, _advise(True))
    assert outcome.notes == ("the zero-rate guard lives in the caller",)


# --- Path agreement, which the attribution silently depends on -----------------------------------


def test_the_written_path_matches_what_the_compiler_reports(project, program_entry, entities):
    # If these ever diverge, every diagnostic becomes "a file this run did not generate" and the
    # loop stops attributing anything to the model -- silently, and always in the safe direction,
    # which is exactly why it needs an assertion rather than a comment.
    author = _scripted_author((BROKEN, BROKEN_IMPORTS), (GOOD, []))
    outcome = _heal(project, program_entry, entities, author, _advise(True))
    assert outcome.relative_path == processor_relative_path(PACKAGE, outcome.class_name)
    assert outcome.attempts == 2, "attribution worked, so the repair was actually attempted"


# --- Step 43: the injected-error harness ------------------------------------------------------------
#
# Step 42 demonstrated the loop healing *one* compile error. One demonstrated heal is an anecdote:
# it says the machinery works for the error that was tried, and nothing about the next one. This
# turns it into a standing guarantee across error classes that differ in how the compiler reports
# them -- which is what the loop's ability to act on a diagnostic actually depends on.
#
# **Every class below is one this platform really produced.** None is invented to be easy:
#
#   unknown_method   PR #28 -- a model assumed `CobolArithmetic`'s API rather than being told it.
#   missing_import   Hit twice in one session (2026-08-11): a body constructing `Tran` by simple
#                    name while the processor's signature is rendered fully-qualified, and the
#                    rendered equivalence test constructing composite components it had not
#                    imported. `cannot find symbol`, both times, from javac rather than review.
#   unresolved_import  `_validated_imports` checks an import's *shape*, never its existence, so a
#                    model may supply a fully-qualified name for a class that is not there. PR #32
#                    found exactly this shipping in every processor -- the pre-Spring-Batch-6
#                    `org.springframework.batch.item.ItemProcessor` package.
#   wrong_return     The output type churned three times this session (TranCatBal -> Tran ->
#                    TranWithContext); a body returning the previous shape is the natural mistake.

_INJECTED_ERRORS: list[tuple[str, str, list[str]]] = [
    ("unknown_method", "return CobolArithmetic.truncateTypo(item, 2);", BROKEN_IMPORTS),
    ("missing_import", "Tran t = null; return item;", []),
    ("unresolved_import", "return item;", ["com.modernized.batch.nowhere.NoSuchHelper"]),
    ("wrong_return", 'return "not a BigDecimal";', []),
]


@pytest.mark.parametrize(("name", "body", "imports"), _INJECTED_ERRORS, ids=[e[0] for e in _INJECTED_ERRORS])
def test_each_injected_error_class_produces_a_diagnostic_the_loop_can_act_on(
    project, program_entry, entities, name, body, imports
):
    """A class the compiler reports but the parser cannot locate would never heal, silently.

    `build_validator` blocks on a failure with no located diagnostic -- correctly, since there is
    nothing to hand a model. So an error class that produced one would exhaust no attempts, report
    `blocked`, and look identical to a design defect. Asserting *located and attributed* per class
    is what makes the heal results below mean something.
    """
    author = _scripted_author((body, imports))
    outcome = _heal(project, program_entry, entities, author, _advise(False), max_attempts=1)

    assert not outcome.succeeded, f"{name} was supposed to fail to compile"
    result = compile_project(project, goal="compile")
    assert not result.succeeded
    assert result.errors, f"{name} produced no structured diagnostic"
    assert not result.has_unparsed_failure, f"{name} is invisible to the diagnostic parser"
    assert any(
        d.file.endswith(f"{outcome.class_name}.java") for d in result.errors
    ), f"{name} is not attributed to the generated file, so no rewrite would be aimed at it"


#: `unresolved_import` is excluded from the heal cases and has its own test below. Excluded on
#: evidence, not convenience: the harness found that the loop **cannot** heal it, and why.
_HEALABLE = [e for e in _INJECTED_ERRORS if e[0] != "unresolved_import"]


@pytest.mark.parametrize(("name", "body", "imports"), _HEALABLE, ids=[e[0] for e in _HEALABLE])
def test_the_loop_heals_every_injected_error_class(
    project, program_entry, entities, name, body, imports
):
    """The standing guarantee step 42 could not give from a single example.

    Scripted on both sides deliberately: what is under test is the **loop** -- that it compiles,
    judges, re-prompts and recompiles for each class -- not whether a model can repair them. Those
    are different claims, and conflating them is how a passing suite would come to stand for
    something nobody measured.
    """
    author = _scripted_author((body, imports), (GOOD, []))
    outcome = _heal(project, program_entry, entities, author, _advise(True))

    assert outcome.succeeded, f"the loop failed to heal {name}"
    assert outcome.attempts == 2, f"{name} healed in {outcome.attempts} attempts, expected 2"
    assert compile_project(project, goal="compile").succeeded


def test_a_model_supplied_import_that_does_not_resolve_is_refused_rather_than_repaired(
    project, program_entry, entities
):
    """What the harness found on its first run, and the finding is about attribution (gap G30).

    A model supplies the imports its body needs -- the renderer never reads the body, so it cannot
    derive them. But those imports are *rendered* into the import block, outside the
    `BEGIN/END model-authored` markers, and `build_validator` attributes a diagnostic by **line**.
    So a bad import lands on line 3, is attributed to rendered scaffolding, and the loop refuses to
    hand it back -- correctly by its own rule, and wrongly in substance: the model wrote it and a
    rewrite would plainly fix it.

    Two costs, and the second is worse. The step spends one attempt instead of two and stops. And
    the blocked reason tells a reviewer *"That is a defect in this repo's renderer"*, which for this
    class is **false** -- it misattributes a model's mistake to the code generator.

    Pinned as the behaviour that exists rather than the behaviour that should. Changing where the
    attribution line falls is a change to ADR-0020-era reasoning about which lines a model owns, and
    that belongs in its own decision, not smuggled into a test harness.
    """
    author = _scripted_author(
        ("return item;", ["com.modernized.batch.nowhere.NoSuchHelper"]), (GOOD, [])
    )
    outcome = _heal(project, program_entry, entities, author, _advise(True))

    assert outcome.status == "blocked"
    assert outcome.attempts == 1, "a blocked verdict must not spend the whole budget"
    assert "rendered scaffolding" in outcome.reason
    # The misattribution, asserted so the fix has a failing test waiting for it.
    assert "renderer" in outcome.reason, "today's message blames the renderer for a model's import"


def test_the_error_classes_are_genuinely_different_to_the_compiler(project, program_entry, entities):
    """Four cases that produced one message would be one test wearing four names.

    The harness is only worth its runtime if the classes exercise different diagnostics, so this
    asserts the distinctness the parametrisation above quietly assumes.
    """
    messages = set()
    for name, body, imports in _INJECTED_ERRORS:
        shutil.rmtree(project / "target", ignore_errors=True)
        _heal(
            project,
            program_entry,
            entities,
            _scripted_author((body, imports)),
            _advise(False),
            max_attempts=1,
        )
        errors = compile_project(project, goal="compile").errors
        messages.add(errors[0].message)

    assert len(messages) >= 2, f"the injected classes collapse to {len(messages)} diagnostic(s)"
