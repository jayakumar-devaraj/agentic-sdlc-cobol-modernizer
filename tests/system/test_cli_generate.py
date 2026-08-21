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
    CompositeComponent,
    CompositeType,
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
        guard_condition=None)
READER = BatchStepDesign(
    step_name="readBalances",
    source_paragraphs=["1000-TCATBALF-GET-NEXT"],
    input_type="TranCatBal",
    output_type="TranCatBal",
    role="reader",
    description="Reads balances.",
        guard_condition=None)


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
        guard_condition=None)
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


def test_non_processor_steps_are_reported_rather_than_failed_or_dropped(tmp_path, entry):
    """A reader must not fail the run -- and must not vanish from it either (G27).

    This test used to assert `len(outcome.outcomes) == 1`, which was the defect written down as an
    expectation: the reader produced no outcome at all, so a design of one processor plus one
    non-processor was indistinguishable from a design with nothing else in it. What it was really
    protecting -- that wiring does not fail a run -- is asserted below and still holds.
    """
    design = _design_json(tmp_path, entry, PROCESSOR, READER)
    outcome = run_generate(
        design, FIXTURE_ROOT, tmp_path / "target", author=_author(), advise=_advise()
    )

    assert [o.step_name for o in outcome.generable] == [PROCESSOR.step_name]
    assert [o.step_name for o in outcome.not_generated] == [READER.step_name]
    # The original point: a reader is not a failure.
    assert outcome.succeeded


def test_a_run_that_generated_nothing_is_not_a_success(tmp_path, entry):
    # Reporting `ok` here would tell control-plane's gate that a migration happened when none did.
    design = _design_json(tmp_path, entry, READER)
    outcome = run_generate(design, FIXTURE_ROOT, tmp_path / "target")

    assert outcome.generable == ()
    assert not outcome.succeeded
    # Strictly better than the old `outcomes == ()`: the run still fails, *and* the reader that
    # accounts for the emptiness is now named rather than being an absence a reader has to infer.
    assert [o.step_name for o in outcome.not_generated] == [READER.step_name]


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
        guard_condition=None)
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


# --- Composites, the case ADR-0020 exists for ------------------------------------------------------


def _design_with_composite(tmp_path: Path, entry: ProgramDesignEntry) -> Path:
    """A step consuming a composite -- the chained-step shape a real architect run produced."""
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    composite = CompositeType(
        name="TranCatBalWithAccount",
        components=[
            CompositeComponent(field_name="balance", entity_name="TranCatBal"),
            CompositeComponent(field_name="account", entity_name="Account"),
        ],
    )
    step = BatchStepDesign(
        step_name="computeInterest",
        source_paragraphs=["1300-COMPUTE-INTEREST"],
        role="processor",
        description="Computes interest from a balance and its resolved account.",
        input_type="TranCatBalWithAccount",
        output_type="TranCatBalWithAccount",
        guard_condition=None)
    document = build_design_document(
        [entry],
        unified_design=UnifiedDesign(
            domain_entities=entities,
            batch_jobs=[
                BatchJobDesign(
                    program_name=PROGRAM, job_name="interestJob",
                    domain_entities=[e.name for e in entities], steps=[step],
                )
            ],
            rest_endpoints=[],
            composite_types=[composite],
        ),
    )
    path = tmp_path / "design.json"
    path.write_text(document.model_dump_json(indent=2), encoding="utf-8")
    return path


def test_a_composite_is_rendered_into_the_target(tmp_path, entry):
    design = _design_with_composite(tmp_path, entry)
    run_generate(design, FIXTURE_ROOT, tmp_path / "target", author=_author(), advise=_advise())

    composite_file = (
        tmp_path / "target/src/main/java/com/modernized/batch/domain/TranCatBalWithAccount.java"
    )
    assert composite_file.is_file()
    source = composite_file.read_text(encoding="utf-8")
    assert "TranCatBal balance," in source
    assert "Account account" in source


def test_a_step_consuming_a_composite_generates_and_compiles(tmp_path, entry):
    """The chained-step case ADR-0020 was written for, end to end and actually built.

    Every other round-trip test here uses a plain entity, which would pass just as well if
    composites were never rendered at all.
    """
    design = _design_with_composite(tmp_path, entry)
    outcome = run_generate(
        design, FIXTURE_ROOT, tmp_path / "target", author=_author(), advise=_advise()
    )

    assert outcome.succeeded, [o.reason for o in outcome.outcomes]
    (compiled,) = outcome.compiled
    processor = (tmp_path / "target" / compiled.relative_path).read_text(encoding="utf-8")
    assert "com.modernized.batch.domain.TranCatBalWithAccount" in processor


# --- G27: a step whose logic is real and is not a processor ---------------------------------------


def test_a_non_processor_step_is_reported_rather_than_silently_dropped(tmp_path, entry):
    """Gap G27. `1050-UPDATE-ACCOUNT` is business logic, and it is not an `ItemProcessor`.

    It does `ADD WS-TOTAL-INT TO ACCT-CURR-BAL` -- a control-break accumulation that mutates a
    balance. Any design giving it an owning step must give it a non-processor role, because a
    stateless per-item processor cannot hold state across items. This pipeline renders processors
    only, which is correct. What was not correct is that it `continue`d past everything else
    without recording anything, so such a step reached no outcome, no count, and no gate.

    The failure that produced: one processor plus this writer reported
    `steps_total: 1, steps_compiled: 1, status: ok` -- indistinguishable from a design containing
    nothing else. A human approving that saw complete success over a job whose account update was
    never generated. The model that flagged it named the cost: the balance is wrong *silently, and
    by the full interest amount*.
    """
    updater = BatchStepDesign(
        step_name="updateAccount",
        source_paragraphs=["1050-UPDATE-ACCOUNT"],
        role="writer",
        description="Adds accumulated interest to the account balance on each account break.",
        input_type="TranCatBal",
        output_type="TranCatBal",
        guard_condition=None,
    )
    design_path = _design_json(tmp_path, entry, PROCESSOR, updater)
    outcome = run_generate(
        design_path, FIXTURE_ROOT, tmp_path / "proj", author=_author(), advise=_advise()
    )

    not_generated = [o for o in outcome.outcomes if o.status == "not_generated"]
    assert [o.step_name for o in not_generated] == ["updateAccount"]
    # The reason must name the paragraphs, or a reviewer cannot tell wiring from lost logic.
    assert "1050-UPDATE-ACCOUNT" in not_generated[0].reason
    assert "writer" in not_generated[0].reason

    # Reported, not failed: the pipeline is right not to render it, and right not to hide it.
    assert outcome.succeeded
    assert len(outcome.compiled) == 1


# --- ADR-0039/0040: a processor whose decision reads its own writes -------------------------------


def test_a_step_that_reads_its_own_writes_is_reported_rather_than_rendered(tmp_path, entry):
    """`reads_own_writes` is refused for the opposite reason to the role check above.

    That one is refused because this pipeline renders `ItemProcessor`s and the step is not one.
    This one *is* a processor by role, and rendering it would **succeed** -- producing a class that
    compiles, runs, and emits individually-correct records in the wrong set.

    Measured on `CBTRN02C` (ADR-0039): its acceptance test compares a credit limit against cycle
    fields its own posting rewrites, so judged per item, 30 of its 43 rejections disappear and it
    writes 287 records where COBOL writes 257. A field-level differential passes on every one of
    them; only the count disagrees. That is why silence here is worse than a refusal, and why the
    refusal has to be mechanical rather than a note in an ADR.
    """
    sequential = PROCESSOR.model_copy(
        update={
            "step_name": "postTransaction",
            "source_paragraphs": ["1500-B-LOOKUP-ACCT", "2800-UPDATE-ACCOUNT-REC"],
            "reads_own_writes": True,
        }
    )
    # Declared beside an ordinary step, the way a real job would be: a run with nothing generable
    # in it is not a success by design, and asserting the refusal on its own would conflate
    # "this step was refused" with "the run produced nothing".
    design_path = _design_json(tmp_path, entry, PROCESSOR, sequential)
    outcome = run_generate(
        design_path, FIXTURE_ROOT, tmp_path / "proj", author=_author(), advise=_advise()
    )

    not_generated = [o for o in outcome.outcomes if o.status == "not_generated"]
    assert [o.step_name for o in not_generated] == ["postTransaction"]
    reason = not_generated[0].reason
    # The reason has to say what is wrong, not merely that something is: a reviewer who reads
    # "not generated" and nothing else cannot tell this from the role case above.
    assert "reads state it writes" in reason
    assert "1500-B-LOOKUP-ACCT" in reason
    assert "ADR-0039" in reason

    # Reported, not failed: the other step compiled, and the refusal is surfaced beside it rather
    # than turning an honest run into an error.
    assert outcome.succeeded
    assert [o.step_name for o in outcome.compiled] == [PROCESSOR.step_name]
    assert not [o for o in outcome.outcomes if o.step_name == "postTransaction" and o.class_name]


def test_the_same_step_without_the_flag_is_rendered(tmp_path, entry):
    """The discrimination case: `reads_own_writes` is what refuses it, not its name or paragraphs.

    Without this, the assertion above would pass for a pipeline that had stopped rendering
    processors altogether.
    """
    ordinary = PROCESSOR.model_copy(
        update={
            "step_name": "postTransaction",
            "source_paragraphs": ["1500-B-LOOKUP-ACCT", "2800-UPDATE-ACCOUNT-REC"],
        }
    )
    design_path = _design_json(tmp_path, entry, ordinary)
    outcome = run_generate(
        design_path, FIXTURE_ROOT, tmp_path / "proj", author=_author(), advise=_advise()
    )

    assert not [o for o in outcome.outcomes if o.status == "not_generated"]
    assert [o.step_name for o in outcome.compiled] == ["postTransaction"]
