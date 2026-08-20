"""The rendered job, its steps and its staging (G31, ADR-0032) -- and the step it refuses to write.

**The end-to-end proof is the round trip**, which now runs on a rendered job: infrastructure beans,
staging, two of three step beans and the `Job` itself all come from `design.json`, and the numbers
are unchanged at 500 of 500 and 598 of 600.

**The refusal is the interesting part.** `postAccountInterest` consumes an aggregate of earlier
output, and the design carries no grouping key, summed field or ordering -- those are a control break
in the COBOL. A renderer that picked them would be choosing business semantics, so it declines, names
the step in the job anyway, and lets a missing bean be a startup failure rather than a step that
silently does not run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_job import (
    DEFAULT_CHUNK_SIZE,
    UnrenderableJobError,
    configuration_class_name,
    plan_steps,
    render_job_configuration,
    render_staging,
    staging_class_name,
)
from tests.system.test_account_break_posting import POSTING
from tests.system.test_account_break_posting import STEP as POSTING_STEP
from tests.system.test_interest_equivalence import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
JOB_PACKAGE = "com.modernized.batch.job"
DOMAIN = "com.modernized.batch.domain"
PROCESSORS = "com.modernized.batch.processor"


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        batch_jobs=[
            BatchJobDesign(
                job_name="interestJob",
                program_name="CBACT04C",
                domain_entities=[entity.name for entity in entities],
                steps=[STEP, COMPLETE_STEP, POSTING_STEP],
            )
        ],
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


def render(design: UnifiedDesign, **kwargs) -> str:
    return render_job_configuration(
        design.batch_jobs[0],
        design,
        "CBACT04C",
        package=JOB_PACKAGE,
        domain_package=DOMAIN,
        processor_package=PROCESSORS,
        **kwargs,
    )


# --- the plan ------------------------------------------------------------------------------------


def test_the_plan_renders_the_chain_and_refuses_the_aggregate(design):
    """The whole decision in one assertion.

    `computeInterest` reads files and hands off to `completeTransaction`, which writes a file -- both
    renderable. `postAccountInterest` consumes an aggregate that no file yields and no step outputs,
    so it is not.
    """
    renderable, skipped, staged = plan_steps(design.batch_jobs[0], design, "CBACT04C")

    assert [step.step_name for step in renderable] == ["computeInterest", "completeTransaction"]
    assert [step.step_name for step, _reason in skipped] == ["postAccountInterest"]
    assert staged == ["TranWithContext"], "the chain's intermediate needs a handoff and no file"


def test_the_refusal_says_what_is_missing_rather_than_that_it_failed(design):
    """A reader of this message should learn which fact the design lacks, not that something broke."""
    _renderable, skipped, _staged = plan_steps(design.batch_jobs[0], design, "CBACT04C")
    _step, reason = skipped[0]

    assert "AccountInterestPosting" in reason
    assert "grouping key" in reason and "control break" in reason
    assert "ADR-0032" in reason


def test_a_job_with_no_steps_is_refused(design):
    empty = design.batch_jobs[0].model_copy(update={"steps": []})
    with pytest.raises(UnrenderableJobError, match="declares no steps"):
        plan_steps(empty, design, "CBACT04C")


def test_a_step_whose_output_goes_nowhere_is_refused(design):
    """A step writing to no file and feeding no successor would be wired to nothing."""
    orphan = COMPLETE_STEP.model_copy(update={"output_type": "CardXref"})
    job = design.batch_jobs[0].model_copy(update={"steps": [STEP, orphan]})
    _renderable, skipped, _staged = plan_steps(job, design, "CBACT04C")
    assert [step.step_name for step, _ in skipped] == ["completeTransaction"]
    assert "nowhere to put it" in skipped[0][1]


# --- what it emits -------------------------------------------------------------------------------


def test_the_job_names_every_declared_step_including_the_one_it_did_not_render(design):
    """The mechanism that stops an unrendered step from silently not running."""
    rendered = render(design)
    assert (
        'STEP_NAMES = List.of("computeInterest", "completeTransaction", "postAccountInterest")'
        in rendered
    )
    assert "and no bean named" in rendered, "a missing step must fail loudly, naming itself"


def test_the_unrendered_step_is_documented_in_the_configuration_itself(design):
    """A reviewer opening the generated file learns what it left out and why, without the ADR."""
    rendered = render(design)
    assert "postAccountInterest" in rendered.split("public class")[0]
    assert "control break" in rendered.split("public class")[0]


def test_the_chain_is_wired_through_the_staging_bean(design):
    """Step one writes to it, step two reads from it -- the handoff the design declares no store for."""
    rendered = render(design)
    assert ".writer(tranWithContextStaging)" in rendered
    assert ".reader(tranWithContextStaging)" in rendered


def test_a_file_backed_step_takes_its_reader_and_writer_as_beans(design):
    """Paths are deployment, not design: `ASSIGN TO TCATBALF` names an environment, not a location.

    So the rendered step declares what it needs and lets whoever runs the job bind it.
    """
    rendered = render(design)
    assert "ItemReader<com.modernized.batch.domain.TranCatBalWithRate> reader" in rendered
    assert "ItemWriter<com.modernized.batch.domain.Tran> writer" in rendered
    assert "new ComputeInterestItemReader(" not in rendered


def test_the_chunk_size_is_named_and_says_it_is_not_a_cobol_fact(design):
    rendered = render(design)
    assert f"CHUNK_SIZE = {DEFAULT_CHUNK_SIZE}" in rendered
    assert "Not a COBOL fact" in rendered


def test_the_configuration_is_ungated_unless_a_profile_is_asked_for(design):
    """In a generated service the job *is* the application; gating it by default would be strange."""
    assert "@Profile" not in render(design)
    assert '@Profile("handwritten-wiring")' in render(design, profile="handwritten-wiring")


def test_the_class_name_is_mechanical(design):
    assert configuration_class_name(design.batch_jobs[0]) == "InterestJobConfiguration"
    assert "public class InterestJobConfiguration" in render(design)


# --- the staging class ---------------------------------------------------------------------------


def test_the_staging_class_is_both_ends_of_the_handoff():
    rendered = render_staging("TranWithContext", package=JOB_PACKAGE, domain_package=DOMAIN)
    assert staging_class_name("TranWithContext") == "TranWithContextStaging"
    assert "implements ItemWriter<com.modernized.batch.domain.TranWithContext>" in rendered
    assert "ItemReader<com.modernized.batch.domain.TranWithContext>" in rendered


def test_the_staging_class_states_its_own_limitation():
    """ADR-0032's cost, written where it applies rather than only in the ADR.

    A generated class that is not restartable and does not say so is how a known compromise becomes
    an unknown one.
    """
    rendered = render_staging("TranWithContext", package=JOB_PACKAGE, domain_package=DOMAIN)
    assert "not restartable" in rendered
    assert "staging table" in rendered
