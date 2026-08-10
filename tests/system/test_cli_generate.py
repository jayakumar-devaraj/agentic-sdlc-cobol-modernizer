"""The `generate` subcommand end to end, from a real `design.json` on disk.

The design document is built from real `spec_extractor`/`spec_critic` output over the real
`CBACT04C` fixture, with a hand-written `unified_design` standing in for `solution_architect`'s
LLM-authored half. No model is called: what is under test is the pipeline's own decisions -- what it
scaffolds, what it skips, what it refuses, and what it puts on stdout.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer import cli
from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    GenerateCliResult,
    ProgramDesignEntry,
    UnifiedDesign,
    build_design_document,
)
from cobol_modernizer.graph.generate_pipeline import materialize_target_project, run_generate
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBACT04C"

PROCESSOR = BatchStepDesign(
    step_name="computeMonthlyInterest",
    source_paragraphs=["1300-COMPUTE-INTEREST"],
    input_type="TranCatBal",
    output_type="TranCatBal",
    role="processor",
    description="Computes monthly interest.",
)
READER = BatchStepDesign(
    step_name="readBalances",
    source_paragraphs=["1000-TCATBALF-GET-NEXT"],
    input_type="TranCatBal",
    output_type="TranCatBal",
    role="reader",
    description="Reads balances.",
)


def _author(body: str = "return item;"):
    """A scripted generator: the pass-through body, which compiles against any single type."""

    def author(routing, system_prompt: str, user_content: str) -> str:
        return json.dumps({"imports": [], "body": body, "notes": ""})

    return author


def _advise():
    def advise(routing, system_prompt: str, user_content: str) -> str:
        return json.dumps({"repairable": False, "reason": "scripted", "instruction": ""})

    return advise


@pytest.fixture(scope="module")
def entry() -> ProgramDesignEntry:
    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return ProgramDesignEntry(
        program_name=PROGRAM, spec_extraction=extraction, critique=critique
    )


def _design_json(tmp_path: Path, entry: ProgramDesignEntry, *steps: BatchStepDesign) -> Path:
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    document = build_design_document(
        [entry],
        unified_design=UnifiedDesign(
            domain_entities=entities,
            batch_jobs=[
                BatchJobDesign(
                    program_name=PROGRAM,
                    job_name="interestJob",
                    domain_entities=[e.name for e in entities],
                    steps=list(steps),
                )
            ],
            rest_endpoints=[],
        ),
    )
    path = tmp_path / "design.json"
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return path


# --- Scaffolding the target project ---------------------------------------------------------------


def test_an_empty_target_is_scaffolded_from_the_template(tmp_path):
    assert materialize_target_project(tmp_path / "target") is True
    assert (tmp_path / "target" / "pom.xml").is_file()
    assert (tmp_path / "target" / "mvnw").is_file()


def test_an_existing_project_is_never_overwritten(tmp_path):
    # card-service is a real repository. A second run that clobbered a reviewed scaffold would
    # destroy work between the gate and the merge.
    target = tmp_path / "target"
    target.mkdir()
    (target / "pom.xml").write_text("<project>mine</project>", encoding="utf-8")

    assert materialize_target_project(target) is False
    assert (target / "pom.xml").read_text(encoding="utf-8") == "<project>mine</project>"


# --- What the pipeline refuses, and why ------------------------------------------------------------


def test_a_step_naming_a_type_that_does_not_exist_is_blocked(tmp_path, entry):
    # ADR-0020 made the types required; they still have to *resolve*. A name matching neither a
    # domain entity nor a declared composite is a design that cannot be generated from, and saying
    # so beats rendering Java against a class that will not exist.
    unresolvable = BatchStepDesign(
        step_name="computeMonthlyInterest",
        source_paragraphs=["1300-COMPUTE-INTEREST"],
        role="processor",
        description="Computes monthly interest.",
        input_type="NoSuchType",
        output_type="TranCatBal",
    )
    design = _design_json(tmp_path, entry, unresolvable)
    outcome = run_generate(design, FIXTURE_ROOT, tmp_path / "target")

    assert len(outcome.outcomes) == 1
    (blocked,) = outcome.blocked
    assert blocked.status == "blocked"
    assert blocked.attempts == 0, "a design defect must not spend a generation attempt"
    assert "NoSuchType" in blocked.reason


def test_the_domain_records_a_processor_needs_are_rendered_into_the_target(tmp_path, entry):
    # Processors are generated against these types, so they have to exist before anything compiles.
    design = _design_json(tmp_path, entry, PROCESSOR)
    run_generate(design, FIXTURE_ROOT, tmp_path / "target", author=_author(), advise=_advise())

    domain = tmp_path / "target" / "src/main/java/com/modernized/batch/domain"
    assert (domain / "TranCatBal.java").is_file()
    assert "public record TranCatBal(" in (domain / "TranCatBal.java").read_text(encoding="utf-8")


def test_a_resolvable_step_is_generated_and_compiles(tmp_path, entry):
    """The round trip, end to end: design.json in, compiling Java in the target repo out."""
    design = _design_json(tmp_path, entry, PROCESSOR)
    outcome = run_generate(
        design, FIXTURE_ROOT, tmp_path / "target", author=_author(), advise=_advise()
    )

    assert outcome.succeeded, [o.reason for o in outcome.outcomes]
    (compiled,) = outcome.compiled
    assert compiled.attempts == 1
    assert (tmp_path / "target" / compiled.relative_path).is_file()


def test_non_processor_steps_are_skipped_rather_than_failed(tmp_path, entry):
    # Readers, writers and tasklets are Spring Batch wiring, not translated business logic. Nothing
    # is wrong with them; they are simply not this renderer's to produce.
    design = _design_json(tmp_path, entry, PROCESSOR, READER)
    outcome = run_generate(
        design, FIXTURE_ROOT, tmp_path / "target", author=_author(), advise=_advise()
    )

    assert len(outcome.outcomes) == 1
    assert outcome.outcomes[0].step_name == PROCESSOR.step_name


def test_a_run_that_generated_nothing_is_not_a_success(tmp_path, entry):
    # Reporting `ok` here would tell control-plane's gate that a migration happened when none did.
    design = _design_json(tmp_path, entry, READER)
    outcome = run_generate(design, FIXTURE_ROOT, tmp_path / "target")

    assert outcome.outcomes == ()
    assert not outcome.succeeded


def test_a_design_without_a_unified_design_is_a_clear_error(tmp_path, entry):
    document = build_design_document([entry], unified_design=None)
    design = tmp_path / "design.json"
    design.write_text(document.model_dump_json(), encoding="utf-8")

    with pytest.raises(ValueError, match="no unified_design"):
        run_generate(design, FIXTURE_ROOT, tmp_path / "target")


# --- The CLI contract -------------------------------------------------------------------------------


def test_generate_emits_one_parseable_json_object_with_real_counts(tmp_path, entry, capsys):
    unresolvable = BatchStepDesign(
        step_name="computeMonthlyInterest", source_paragraphs=["1300-COMPUTE-INTEREST"],
        role="processor", description="d", input_type="NoSuchType", output_type="TranCatBal",
    )
    design = _design_json(tmp_path, entry, unresolvable, READER)
    exit_code = cli.main([
        "generate", "--design", str(design), "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(tmp_path / "target"), "--json",
    ])
    captured = capsys.readouterr()
    result = GenerateCliResult.model_validate_json(captured.out.strip())

    assert exit_code == 1
    assert result.status == "error"
    assert result.steps_total == 1
    assert result.steps_compiled == 0
    assert result.steps_blocked == 1
    assert result.steps_exhausted == 0
    # The reason itself, not just a count -- a count tells a reviewer something is wrong without
    # telling them what, and the reason is the part that cost a model call to produce.
    assert "NoSuchType" in result.detail


def test_generate_no_longer_reports_not_implemented(tmp_path, entry, capsys):
    design = _design_json(tmp_path, entry, PROCESSOR)
    cli.main([
        "generate", "--design", str(design), "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(tmp_path / "target"), "--json",
    ])
    assert "Not implemented" not in capsys.readouterr().out


def test_a_missing_design_file_still_produces_parseable_json(tmp_path, capsys):
    exit_code = cli.main([
        "generate", "--design", str(tmp_path / "nope.json"), "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(tmp_path / "target"), "--json",
    ])
    captured = capsys.readouterr()
    result = GenerateCliResult.model_validate_json(captured.out.strip())

    assert exit_code == 1
    assert result.status == "error"
    assert "FileNotFoundError" in result.detail
    assert "Traceback" in captured.err


def test_logging_never_reaches_stdout_on_the_generate_path(tmp_path, entry, capsys):
    design = _design_json(tmp_path, entry, PROCESSOR)
    cli.main([
        "generate", "--design", str(design), "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(tmp_path / "target"), "--json",
    ])
    # Byte for byte, stdout is one JSON object and nothing else.
    json.loads(capsys.readouterr().out)
