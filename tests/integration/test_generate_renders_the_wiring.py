"""A project `generate` wired by itself runs, and its output is compared to COBOL's (ADR-0066).

**This is the round trip with the stopgap removed.** `test_hand_written_round_trip.py` measures
generated logic inside wiring a person wrote, and every result it produces carries that qualifier —
ADR-0030 required it, because the wiring was hand-written for one program and generalised to
nothing. Here nothing is copied in: `run_generate` renders the readers, the writers, the staging,
the aggregation, the job configuration and the beans that bind them to files, and the job that runs
is the one the pipeline produced.

**What this module supplies and why it is not the stopgap returning.** Two things, both test-side:

1. **Property values.** The rendered defaults resolve against `cobol.file.base`, and this harness
   points them at the staged corpus instead — one property per file, which is exactly the override
   path ADR-0067 designed. Supplying a *value* for a declared property is deployment; supplying the
   *bean* was the gap.
2. **A runner.** A Spring Batch job runs when something starts it. The generated project's own
   entry point is `BatchApplication`, whose Boot autoconfiguration wants a `DataSource` and a
   container to put it in — neither of which is being measured. The same
   `AnnotationConfigApplicationContext` the fixture uses starts the job directly.

Neither supplies a bean the pipeline should have rendered, which is the line that matters.

Costs a Maven build and a real job run. See `docs/development-environment.md` for `JAVA_HOME`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cobol_modernizer.equivalence import (
    ACCOUNT_LAYOUT,
    TRAN_LAYOUT,
    compare,
    parse_fixed_records,
)
from cobol_modernizer.equivalence.harness import compare_project_output
from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.tools.local_compiler import compile_project
from tests.integration.test_hand_written_round_trip import (
    ACCOUNT_CANDIDATE,
    CANDIDATE,
    CORPUS,
    INPUTS,
    ORACLE_DIR,
    TEXT_INPUTS,
    _design_json,
    _scripted_author,
    assert_account_half_matches_except_the_last,
    design_inputs,  # noqa: F401 -- a fixture, used by name
    load_oracle,
    stage_as_fixed_records,
)
from tests.support.interest_design import FIXTURE_ROOT

#: Where this harness stages the corpus, and what it therefore sets each `cobol.file.*` property to.
#: The two paths `equivalence.harness` already looks in, so `compare_project_output` needs no
#: argument it did not have before — the account file is both an input and the file rewritten in
#: place, which the rendered bindings get right on their own because both come from `ACCTFILE`.
FILE_PROPERTIES = {
    "cobol.file.tcatbalf": "roundtrip/input/tcatbal-posted.dat",
    "cobol.file.acctfile": "roundtrip/input/acctdata-stage1.dat",
    "cobol.file.xreffile": "roundtrip/input/cardxref.dat",
    "cobol.file.discgrp": "roundtrip/input/discgrp.dat",
    "cobol.file.transact": "roundtrip/output/transact.dat",
}

_RUNNER = """\
package com.modernized.batch.harness;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.util.Map;
import org.junit.jupiter.api.Test;
import org.springframework.batch.core.job.Job;
import org.springframework.batch.core.job.JobExecution;
import org.springframework.batch.core.job.parameters.JobParametersBuilder;
import org.springframework.batch.core.launch.JobOperator;
import org.springframework.context.annotation.AnnotationConfigApplicationContext;
import org.springframework.context.support.PropertySourcesPlaceholderConfigurer;
import org.springframework.core.env.MapPropertySource;

/** Starts the job {{@code generate}} rendered. Registers no bean of its own. */
class RenderedWiringRunTest {{

    @Test
    void runsTheRenderedJob() throws Exception {{
        JobExecution execution;
        try (AnnotationConfigApplicationContext context = new AnnotationConfigApplicationContext()) {{
            context.getEnvironment()
                    .getPropertySources()
                    .addFirst(new MapPropertySource("files", Map.of(
{properties})));
            context.registerBean(PropertySourcesPlaceholderConfigurer.class);
            context.register(
                    com.modernized.batch.job.InterestJobConfiguration.class,
                    com.modernized.batch.job.InterestJobFileBindings.class);
            context.refresh();

            Job job = context.getBean(Job.class);
            JobOperator operator = context.getBean(JobOperator.class);
            execution = operator.start(
                    job, new JobParametersBuilder().addString("source", "rendered").toJobParameters());
        }}
        assertEquals(
                "COMPLETED",
                execution.getExitStatus().getExitCode(),
                execution.getAllFailureExceptions().toString());
    }}
}}
"""


def _write_runner(project: Path) -> None:
    entries = ",\n".join(
        f'                    "{name}", "{value}"' for name, value in FILE_PROPERTIES.items()
    )
    destination = (
        project / "src" / "test" / "java" / "com" / "modernized" / "batch" / "harness"
        / "RenderedWiringRunTest.java"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(_RUNNER.format(properties=entries), encoding="utf-8")


def _stage_inputs(project: Path) -> None:
    staged = project / "roundtrip" / "input"
    staged.mkdir(parents=True, exist_ok=True)
    for name, source in INPUTS.items():
        shutil.copy2(source, staged / name)
    for name, (source, record_length) in TEXT_INPUTS.items():
        stage_as_fixed_records(source, staged / name, record_length)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory, design_inputs):  # noqa: F811 -- the imported fixture
    """Generate everything, stage the corpus, run the job. Once — Maven is the cost."""
    entry, entities = design_inputs
    project = tmp_path_factory.mktemp("rendered-wiring") / "target-project"
    project.parent.mkdir(parents=True, exist_ok=True)
    design_path = _design_json(project.parent, entry, entities)

    outcome = run_generate(
        design_path,
        FIXTURE_ROOT,
        project,
        author=_scripted_author,
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    assert outcome.succeeded, f"generation failed: {[o.reason for o in outcome.blocked]}"

    _stage_inputs(project)
    _write_runner(project)
    build = compile_project(
        project,
        goal="test",
        extra_args=("-Dtest=RenderedWiringRunTest", "-Dsurefire.failIfNoSpecifiedTests=false"),
    )
    return outcome, project, build


def test_generate_renders_the_wiring_and_it_compiles(rendered):
    """`generate` alone produces the readers, writers, staging, job and bindings."""
    outcome, project, _build = rendered

    assert outcome.wiring.status == "rendered", outcome.wiring.reason
    rendered_names = {Path(p).name for p in outcome.wiring.files_rendered}
    # The classes the hand-written remainder used to stand in for, now rendered.
    assert "InterestJobConfiguration.java" in rendered_names
    assert "InterestJobFileBindings.java" in rendered_names
    assert "ComputeInterestItemReader.java" in rendered_names
    assert "application.properties" in rendered_names

    # And nothing hand-written was copied in: the stopgap's own package must be absent.
    assert not (project / "src" / "main" / "java" / "com" / "modernized" / "batch"
                / "handwritten").exists()


def test_the_rendered_properties_name_every_file_the_job_touches(rendered):
    """ADR-0067: the deployment surface is documented, not implied by a base directory."""
    _outcome, project, _build = rendered

    text = (project / "src" / "main" / "resources" / "application.properties").read_text(
        encoding="utf-8"
    )
    for name in FILE_PROPERTIES:
        assert f"{name}=" in text, f"{name} is not in the rendered properties"
    assert "cobol.file.base=data" in text


def test_the_rendered_job_runs_to_completion(rendered):
    """The claim ADR-0030 deferred in August: a generated project that runs."""
    _outcome, _project, build = rendered

    assert build.succeeded, (
        "the job generate rendered did not run: "
        + ("\n".join(d.render() for d in build.errors[:5]) or build.raw_output[-3000:])
    )


def test_the_differential_runs_at_all(rendered):
    """**The measurement ADR-0064 could not take.**

    `compare_project_output` has returned `not_run` for every real design since it was written, with
    the reason naming the missing wiring. This is the first time it has had something to compare:
    a project the pipeline produced end to end, built and run.

    It reports `mismatched`, and the next two tests are what that word means here.
    """
    _outcome, project, _build = rendered

    verdict = compare_project_output(project, ORACLE_DIR)
    assert verdict.status != "not_run", verdict.reason
    assert verdict.records_compared > 0
    print(f"\nrendered wiring: {verdict.reason}")


def test_the_transaction_half_matches_the_oracle_exactly(rendered):
    """Every interest transaction the rendered job wrote, against COBOL's own, field by field."""
    _outcome, project, _build = rendered

    records = parse_fixed_records(project / CANDIDATE, TRAN_LAYOUT, 350)
    assert len(records) == len(load_oracle()) == 50
    result = compare(records, load_oracle())
    assert result.passed, "\n".join(result.mismatches[:10])


def test_the_account_half_diverges_exactly_where_the_hand_written_wiring_does(rendered):
    """The three mismatches are `CBACT04C`'s own unreachable EOF branch, not a rendering defect.

    **Asserted through the hand-written round trip's own helper, deliberately.** That module pins
    the shape of the acceptable divergence -- exactly one record, it is the last account, the fields
    are the three `1050-UPDATE-ACCOUNT` writes, and the balance differs by exactly that account's
    uncredited interest. Reusing it rather than restating it means the rendered wiring is held to
    the identical standard, and a divergence with a *different* cause cannot pass here by being
    described differently.

    Its docstring says the wiring could have skipped the last account and made this green, and that
    doing so would be encoding a defect to improve a number. That applies to rendered wiring exactly
    as it applied to hand-written wiring.
    """
    _outcome, project, _build = rendered

    accounts = parse_fixed_records(project / ACCOUNT_CANDIDATE, ACCOUNT_LAYOUT, 300)
    result = assert_account_half_matches_except_the_last(accounts)
    print(f"account half: {result.render()}; {len(result.mismatches)} expected mismatch(es)")


def test_a_skipped_step_is_named_rather_than_counted(rendered):
    """ADR-0066 decision 2: a step the design cannot wire is COBOL absent from the project.

    This design wires every renderable step, so the list is empty — and the assertion that matters
    is the *shape*: whatever is skipped arrives as a named step with a reason, never as a number a
    reviewer has to go looking behind.
    """
    outcome, _project, _build = rendered

    for entry in outcome.wiring.skipped_steps:
        assert ": " in entry, f"{entry!r} names no reason"
    assert "step(s)" in outcome.wiring.reason or "every renderable step" in outcome.wiring.reason


def test_the_corpus_used_here_is_the_oracle_s_own(rendered):
    """Guards the comparison against being run on inputs the oracle was not produced from."""
    assert (ORACLE_DIR / "tcatbal-posted.dat").is_file()
    assert (CORPUS / "discgrp.txt").is_file()
