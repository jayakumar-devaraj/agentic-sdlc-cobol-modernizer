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
