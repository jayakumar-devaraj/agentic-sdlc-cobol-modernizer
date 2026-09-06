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

import re
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    DesignDocument,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    attach_control_breaks,
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_file_bindings import render_file_bindings
from cobol_modernizer.rendering.java_job import (
    DEFAULT_CHUNK_SIZE,
    UnrenderableJobError,
    _has_file_source,
    configuration_class_name,
    plan_steps,
    reads_a_file,
    render_job_configuration,
    render_staging,
    staging_class_name,
)
from tests.support.interest_design import (
    COMPLETE_STEP,
    COMPOSITE,
    OUTPUT_COMPOSITE,
    STEP,
)
from tests.support.posting_design import POSTING
from tests.support.posting_design import STEP as POSTING_STEP

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
    job = BatchJobDesign(
        job_name="interestJob",
        program_name="CBACT04C",
        domain_entities=[entity.name for entity in entities],
        steps=[STEP, COMPLETE_STEP, POSTING_STEP],
    )
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        # Attached as the real pipeline does. Without it no step carries a control break, the
        # aggregation has no source, and every assertion here would be about a design the `design`
        # phase never produces.
        batch_jobs=attach_control_breaks(FIXTURE_ROOT, [job], [entry]),
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
        reader_package="com.modernized.batch.reader",
        **kwargs,
    )


# --- the plan ------------------------------------------------------------------------------------


def test_the_plan_renders_all_three_steps_once_the_break_key_is_reachable(design):
    """The whole decision in one assertion, and it changed when the composite was widened.

    `computeInterest` reads files and hands off to `completeTransaction`, which writes a file.
    `postAccountInterest` aggregates -- and its source is not the step before it but the nearest
    earlier step whose output carries both the break key and the summed column, which is
    `computeInterest`.
    """
    renderable, skipped, staged = plan_steps(design.batch_jobs[0], design, "CBACT04C")

    assert [step.step_name for step in renderable] == [
        "computeInterest",
        "completeTransaction",
        "postAccountInterest",
    ]
    assert skipped == []
    # By producing step, not by type (ADR-0073): the store belongs to the edge, and `computeInterest`
    # is the step that fills this one.
    assert [step.step_name for step in staged] == ["computeInterest"], (
        "the chain's intermediate needs a handoff and no file"
    )


def test_the_refusal_still_says_what_is_missing_when_the_break_key_is_out_of_reach(design):
    """Narrowing the composite puts the step back out of reach, and the message has to say why.

    This is the state the design was in before it was widened: the break key exists, the parse found
    it, and no stream the step can read carries it.
    """
    narrowed = design.model_copy(
        update={
            "composite_types": [
                composite.model_copy(
                    update={"components": composite.components[:3]}
                )
                if composite.name == "TranWithContext"
                else composite
                for composite in design.composite_types
            ]
        }
    )
    _renderable, skipped, _staged = plan_steps(narrowed.batch_jobs[0], narrowed, "CBACT04C")
    _step, reason = skipped[0]

    assert "control break on TRANCAT-ACCT-ID" in reason
    assert "TRANCAT-ACCT-ID is not" in reason
    assert "widen that type" in reason


def test_the_file_lifecycle_steps_a_live_design_declares_are_not_chunk_steps(design):
    """A live `CBACT04C` design decomposes the program the way the COBOL is written.

    Five file OPENs as a `tasklet` and `1000-TCATBALF-GET-NEXT` as a `reader`, both typed
    `TranCatBal -> TranCatBal`, wrapped around the steps that do the work. Planned as chunk steps
    each demanded its own `ItemReader<TranCatBal>` bean beside the step actually driving the file,
    and `render_file_bindings` refused the whole job's wiring for the collision.

    They are skipped with the role in the reason, not refused: the design is recording real COBOL,
    and in Spring Batch both are the item reader's own lifecycle.
    """
    opens = STEP.model_copy(
        update={
            "step_name": "openInterestFiles",
            "role": "tasklet",
            "source_paragraphs": ["0000-TCATBALF-OPEN"],
            "input_type": "TranCatBal",
            "output_type": "TranCatBal",
        }
    )
    reads = STEP.model_copy(
        update={
            "step_name": "readTranCatBalance",
            "role": "reader",
            "source_paragraphs": ["1000-TCATBALF-GET-NEXT"],
            "input_type": "TranCatBal",
            "output_type": "TranCatBal",
        }
    )
    job = design.batch_jobs[0]
    wrapped = job.model_copy(update={"steps": [opens, reads, *job.steps]})

    renderable, skipped, staged = plan_steps(wrapped, design, "CBACT04C")

    assert [step.step_name for step in renderable] == [
        "computeInterest",
        "completeTransaction",
        "postAccountInterest",
    ], "the work steps plan exactly as they do without the lifecycle steps around them"
    # **Not reported as skipped.** `skipped_steps` means business logic absent from the generated
    # project, and `steps_not_generated` already counts a step excluded by role. Reporting a file
    # open in the first is the misreading that field's own docstring warns about, and it is what
    # told a live run's gate that the differential could not run.
    assert skipped == []
    # Dropped from the chain as well as from the plan. If `previous` were still the raw preceding
    # step, `computeInterest` would read `TranCatBal` from a reader that is not being rendered.
    assert [step.step_name for step in staged] == ["computeInterest"]


def test_the_job_names_the_chunk_steps_and_not_the_lifecycle_around_them(design):
    """ADR-0032 names an unrendered step so a missing bean fails loudly. A tasklet is not that.

    A live run's rendered job named nine steps, had beans for five, and threw at startup on the
    first file-open tasklet. Naming a step whose *business logic* could not be wired is what makes
    it impossible to forget; naming one that is not a step at all makes the job impossible to start.
    """
    opens = STEP.model_copy(
        update={
            "step_name": "openInterestFiles",
            "role": "tasklet",
            "source_paragraphs": ["0000-TCATBALF-OPEN"],
            "input_type": "TranCatBal",
            "output_type": "TranCatBal",
        }
    )
    job = design.batch_jobs[0]
    wrapped = design.model_copy(
        update={"batch_jobs": [job.model_copy(update={"steps": [opens, *job.steps]})]}
    )
    rendered = render(wrapped)

    assert (
        'STEP_NAMES = List.of("computeInterest", "completeTransaction", "postAccountInterest")'
        in rendered
    )
    assert "openInterestFiles" not in rendered


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
    narrowed = design.model_copy(
        update={
            "composite_types": [
                composite.model_copy(update={"components": composite.components[:3]})
                if composite.name == "TranWithContext"
                else composite
                for composite in design.composite_types
            ]
        }
    )
    header = render(narrowed).split("public class")[0]
    assert "postAccountInterest" in header
    assert "control break" in header


def test_the_chain_is_wired_through_the_staging_bean(design):
    """Step one writes to it, step two reads from it -- the handoff the design declares no store for."""
    rendered = render(design)
    assert ".writer(computeInterestStaging)" in rendered
    assert ".reader(computeInterestStaging)" in rendered


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


def _produces(design, type_name: str):
    """The step whose output the store carries -- what a staging bean is named for (ADR-0073)."""
    return next(
        step for step in design.batch_jobs[0].steps if step.output_type == type_name
    )


def test_the_staging_class_is_both_ends_of_the_handoff(design):
    producer = _produces(design, "TranWithContext")
    rendered = render_staging(producer, package=JOB_PACKAGE, domain_package=DOMAIN)
    assert staging_class_name(producer) == "ComputeInterestStaging"
    assert "implements ItemWriter<com.modernized.batch.domain.TranWithContext>" in rendered
    assert "ItemReader<com.modernized.batch.domain.TranWithContext>" in rendered


def test_the_staging_class_states_its_own_limitation(design):
    """ADR-0032's cost, written where it applies rather than only in the ADR.

    A generated class that is not restartable and does not say so is how a known compromise becomes
    an unknown one.
    """
    rendered = render_staging(
        _produces(design, "TranWithContext"), package=JOB_PACKAGE, domain_package=DOMAIN
    )
    assert "not restartable" in rendered
    assert "staging table" in rendered


# --- ADR-0074: a mid-chain step reads its predecessor, not a file --------------------------------
#
# Nothing in this module caught the precedence flip when it was made. These exist because a change
# that reroutes every mid-chain reader in every generated job was invisible to 906 tests.

STEP56_DESIGN = (
    Path(__file__).parent.parent / "fixtures" / "live_designs" / "cbact04c-design-step56.json"
)


def _step56_job():
    """`CBACT04C` as the architect designed it under prompt `v1_5_0`, first attempt, no repair.

    Pinned for ADR-0069's reason and one more: this is the design that *obeys* ADR-0072 and was then
    refused by the renderer, so it is the only fixture that reaches ADR-0074's defect. The `step55`
    fixture beside it cannot: it strands the passthrough before the question arises.
    """
    document = DesignDocument.model_validate_json(STEP56_DESIGN.read_text(encoding="utf-8"))
    return document.unified_design, document.unified_design.batch_jobs[0]


def _readers(job, design):
    """`{step name: the reader parameter its bean declares}` for every rendered step."""
    source = render_job_configuration(
        job, design, "CBACT04C",
        package="j", domain_package="dom", processor_package="p", reader_package="r",
    )
    found = {}
    for match in re.finditer(r"Step (\w+)Step\(([^)]*)\)", source):
        params = [p.strip() for p in " ".join(match.group(2).split()).split(",")]
        # The two infrastructure parameters come first and are the same on every bean.
        found[match.group(1)] = params[2]
    return found


def test_the_live_design_that_obeys_the_ordering_rule_wires(design):
    """The regression: this exact design was refused by `render_file_bindings` as ambiguous.

    `computeCategoryFees` and `computeMonthlyInterest` both consume a `RatedCategoryBalance`, and
    while both were given file readers the context had two `ItemReader<RatedCategoryBalance>` beans
    and could not resolve either.
    """
    step56_design, job = _step56_job()
    renderable, skipped, _staged = plan_steps(job, step56_design, "CBACT04C")

    assert skipped == []
    assert [step.step_name for step in renderable] == [
        "resolveAccountAndXref",
        "resolveInterestRate",
        "computeCategoryFees",
        "computeMonthlyInterest",
        "writeInterestTransaction",
        "postAccountInterest",
    ]
    # The refusal this reproduces lives in `java_file_bindings`, so the bindings are what must render.
    render_file_bindings(
        job, step56_design, "CBACT04C",
        package="j", domain_package="dom", reader_package="r", writer_package="w",
    )


def test_only_the_head_of_the_chain_reads_a_file(design):
    """One file reader per job, at the driving stream, and stores everywhere after it.

    Asserted as the whole mapping rather than as a property of one step. A test naming only
    `computeCategoryFees` would pass again the moment some other mid-chain step was handed a file
    reader, which is the defect this records.
    """
    step56_design, job = _step56_job()
    readers = _readers(job, step56_design)

    assert readers == {
        "resolveAccountAndXref": "ItemReader<dom.TranCatBal> reader",
        "resolveInterestRate": "ResolveAccountAndXrefStaging resolveAccountAndXrefStaging",
        "computeCategoryFees": "ResolveInterestRateStaging resolveInterestRateStaging",
        "computeMonthlyInterest": "ComputeCategoryFeesStaging computeCategoryFeesStaging",
        "writeInterestTransaction": "ComputeMonthlyInterestStaging computeMonthlyInterestStaging",
        "postAccountInterest": "ComputeMonthlyInterestStaging computeMonthlyInterestStaging",
    }


def test_every_store_a_step_fills_is_read_by_the_step_after_it(design):
    """The point of the change, stated as a property rather than as a list of names.

    Before it, the first step's store was written and read by nobody while the second step rebuilt
    the same item from files -- a silent duplication of exactly the lookup the first step performs.
    """
    step56_design, job = _step56_job()
    _renderable, _skipped, staged = plan_steps(job, step56_design, "CBACT04C")
    readers = _readers(job, step56_design)

    filled = {staging_class_name(step) for step in staged}
    read = {parameter.split()[0] for parameter in readers.values() if "ItemReader<" not in parameter}
    assert filled <= read, f"stores nothing reads: {sorted(filled - read)}"


def test_a_file_readable_input_is_still_read_from_a_file_at_the_head(design):
    """The other half. A rule that never chose a file would leave the job with no driving stream.

    `resolveAccountAndXref` consumes a `TranCatBal`, which is as file-assemblable as the composites
    behind it -- what makes it the file reader is that no chunk step precedes it.
    """
    step56_design, job = _step56_job()
    head = next(step for step in job.steps if step.step_name == "resolveAccountAndXref")
    second = next(step for step in job.steps if step.step_name == "resolveInterestRate")

    assert reads_a_file(head, step56_design, "CBACT04C", job)
    assert not reads_a_file(second, step56_design, "CBACT04C", job)
    # And the reason is the chain, not the file: its input is still assemblable from one.
    assert _has_file_source(second, step56_design, "CBACT04C")
