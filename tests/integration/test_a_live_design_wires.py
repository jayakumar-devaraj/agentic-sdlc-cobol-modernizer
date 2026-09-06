"""The wiring renders for a design a *model* produced, and what it renders reaches javac (ADR-0069).

**The gap this closes.** Verification 16 proved `generate` wires a project that builds and runs, and
every number in it was measured against the hand-written round trip's three-step design --
`computeInterest`, `TranCatBalWithRate`, the composite this repository's own fixtures declare. No
design a live `solution_architect` produced had ever reached `render_job_wiring`. Six defects were
sitting behind that, and every one was found by pointing this pipeline at a real design:

1. `aggregation_source` could not see a value carried as a `computed_fields` entry, so a control
   break on a design obeying ADR-0063 resolved to no source and the step fell through to a file
   reader that correctly refused an in-memory aggregate.
2. `plan_steps` never consulted `role`, so a `tasklet` of five file OPENs and a `reader` of
   `1000-TCATBALF-GET-NEXT` were planned as chunk steps and collided on `ItemReader<TranCatBal>`.
3. `render_item_reader` built its constructor from `components` alone, so a composite of three
   records and one computed field came out as a three-argument call to a four-component record --
   uncompilable Java, emitted with no diagnostic.
4. That same renderer wrapped a plain entity in its own constructor -- `new TranCatBal(toTranCatBal(
   record))` -- because every step of the fixture design takes a composite.
5. The design typed two transforms `writer`, and a body is rendered for a processor and nothing
   else, so the job was wired to classes that would never exist (ADR-0070 refuses this now).
6. `STEP_NAMES` went on naming every declared step after `plan_steps` stopped planning some of them,
   so the rendered job required beans for steps nothing rendered and threw at startup (ADR-0071).

None of them is reachable from the fixture design, because it declares no computed field, no
tasklet, no reader step, and no step taking a plain entity. **A fixture the repository wrote cannot
exercise the shapes a model writes**, which is why this module pins one that a model actually wrote.
Defects 4 and 6 were found *by this module*, after the first three had been fixed.

**What it measures, and what it deliberately does not.** The wiring: which steps are planned, that
the classes it renders are consistent, that the job names only steps it has beans for, and that the
whole project compiles. Not correctness -- the processor bodies here are scripted `return null;`,
so nothing about the generated logic is claimed and the differential stays where it belongs, in
`test_generate_renders_the_wiring.py`. A body that compiles is all a wiring test needs, and
scripting it is what keeps this module free of a model call.

**This project compiles, and its job still cannot start.** `computeCategoryFees` is ordered after
`writeInterestTransaction`, and by the time the chain reaches it the item is a `Tran`. It is named
in `STEP_NAMES` with no bean behind it, which is ADR-0032 working exactly as written: business logic
the design gives nowhere to come from fails loudly rather than leaving a shorter job that looks like
it ran. Its COBOL happens to be `* To be implemented` / `EXIT.`, so nothing is really lost -- but the
pipeline cannot know that, and inferring it would be the kind of guess this repository refuses
everywhere else.

**ADR-0072 corrects one word of that, and the correction is why the gap closed.** This module and
ADR-0071 both said *nothing* supplies the step's `AccruedCategoryInterest`. `computeMonthlyInterest`
supplies it three steps back; `writeInterestTransaction` consumes it and returns a `Tran`. It is a
fan-out, not a missing producer -- so a valid order exists one move away, and `solution_architect`
now refuses the design and names that move (`v1_5_0`). What this module renders is unchanged: the
design stays pinned exactly as the model wrote it, because a fixture corrected to the rule it tests
would test nothing.

That distinction is why `test_the_rendered_project_compiles` is not the last word here: compilation
is necessary and not sufficient, and a green build gave exactly that false comfort once already.

Costs a Maven build. See `docs/development-environment.md` for `JAVA_HOME`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.tools.local_compiler import compile_project
from tests.support.interest_design import FIXTURE_ROOT

#: A design `solution_architect` produced for `CBACT04C` under prompt `v1_4_0`, saved verbatim from
#: run `step55-cbact04c-20260906-090845`. Pinned rather than regenerated: the point is that it is
#: *not* this repository's own idea of what a design looks like, and re-deriving it from a model
#: would make this test's subject change under it.
#:
#: **It replaced `step54b`'s design, and the reason is worth stating.** ADR-0069 said that fixture
#: would go stale once ADR-0070's refusal changed what an architect produces, and that it should stay
#: anyway. That was wrong in a way only the replacement showed: `step54b` types two transforms
#: `writer`, and once a `writer` is correctly excluded from being a chunk step (ADR-0071) its
#: aggregation, its control break and its computed fields all stop being reachable. Keeping it would
#: have left a test that passes while exercising none of what it names. A regression fixture has to
#: still reach the code it regresses.
LIVE_DESIGN = Path(__file__).resolve().parents[1] / "fixtures" / "live_designs" / "cbact04c-design.json"

#: The steps this design decomposes `CBACT04C` into that carry an item, in job order.
CHUNK_STEPS = [
    "resolveAccountAndCardXref",
    "resolveInterestRate",
    "computeMonthlyInterest",
    "writeInterestTransaction",
    "postAccountInterest",
]

#: The file lifecycle it also declares, which is real COBOL and not a chunk step.
LIFECYCLE_STEPS = [
    "openInterestCalculationFiles",
    "readTranCatBalance",
    "closeInterestCalculationFiles",
]

#: The one step whose *business logic* this design gives nowhere to come from: it is ordered after
#: `writeInterestTransaction`, so the item reaching it is a `Tran` rather than the
#: `AccruedCategoryInterest` it consumes. Its COBOL is `* To be implemented` / `EXIT.`, so nothing is
#: lost -- but the pipeline cannot know that, and ADR-0032 requires it to be named rather than
#: dropped. Refused at design time since ADR-0072; what the renderer does with one that arrives
#: anyway is this module's subject and has not changed.
UNWIRABLE_STEP = "computeCategoryFees"


def _null_author(routing, system_prompt: str, user_content: str) -> str:
    """A body that compiles for any `ItemProcessor<In, Out>`, whatever the types are.

    `return null;` rather than a per-step body: this module asserts nothing about what a processor
    computes, and writing five real ones would be hand-writing the code under test.
    """
    return json.dumps({"imports": [], "body": "return null;", "notes": ""})


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    """Render the whole project from the live design, once -- Maven is the cost."""
    project = tmp_path_factory.mktemp("live-design") / "target-project"
    project.parent.mkdir(parents=True, exist_ok=True)

    outcome = run_generate(
        LIVE_DESIGN,
        FIXTURE_ROOT,
        project,
        author=_null_author,
        advise=lambda routing, s, u: json.dumps(
            {"repairable": False, "reason": "scripted", "instruction": ""}
        ),
    )
    build = compile_project(project, goal="compile")
    return outcome, project, build


def test_the_wiring_renders_for_a_design_a_model_wrote(generated):
    """`status == "rendered"`, which no live design reached before v0.4.2."""
    outcome, _project, _build = generated
    assert outcome.wiring.status == "rendered", outcome.wiring.reason
    assert "driving stream" not in outcome.wiring.reason
    assert "ambiguous" not in outcome.wiring.reason


def test_every_step_that_carries_an_item_is_planned(generated):
    """The five that transform an item, against the three that are the reader's own lifecycle.

    Asserted as a pair. A test naming only the planned steps would keep passing if the lifecycle
    steps came back as chunk steps, which is the defect that refused this job's wiring.
    """
    outcome, _project, _build = generated
    rendered = {Path(p).name for p in outcome.wiring.files_rendered}

    for step in CHUNK_STEPS:
        expected = f"{step[:1].upper()}{step[1:]}"
        assert any(name.startswith(expected) for name in rendered), (
            f"{step} contributed no rendered class"
        )
    for step in LIFECYCLE_STEPS:
        assert not any(step in name for name in rendered), f"{step} should render no class"


def test_only_a_step_whose_logic_is_missing_is_reported_as_skipped(generated):
    """`skipped_steps` is business logic absent from the project, and a file open is not that.

    Reporting the lifecycle here is what told a live run's gate the differential could not run, and
    what would have told a reviewer three file-handling paragraphs had gone missing. `role` already
    has a field of its own: `steps_not_generated` counts a step this pipeline never renders by role,
    with the role and the paragraphs in its reason.
    """
    outcome, _project, _build = generated

    assert [s.split(":")[0] for s in outcome.wiring.skipped_steps] == [UNWIRABLE_STEP]
    assert "neither readable from a declared file" in outcome.wiring.skipped_steps[0]


def test_the_job_names_the_steps_it_has_beans_for(generated):
    """A job naming a step nothing renders throws at startup, naming it (ADR-0032).

    That is right for `computeCategoryFees`, whose COBOL really is absent. It is wrong for a file
    open, which is not a step at all -- and a live run's job named nine, had beans for five, and
    threw on the first tasklet. So the lifecycle is absent from `STEP_NAMES` and the unwirable step
    is present: the loud failure still covers exactly what it was written to cover.
    """
    _outcome, project, _build = generated
    source = (
        project / "src" / "main" / "java" / "com" / "modernized" / "batch" / "job"
        / "InterestCalculationJobConfiguration.java"
    ).read_text(encoding="utf-8")
    names = source.split("STEP_NAMES = List.of(")[1].split(");")[0]

    for step in CHUNK_STEPS + [UNWIRABLE_STEP]:
        assert f'"{step}"' in names, f"{step} should be named in STEP_NAMES"
    for step in LIFECYCLE_STEPS:
        assert f'"{step}"' not in names, f"{step} is not a step and must not be named"


def test_the_control_break_renders_an_aggregating_reader_over_the_computed_value(generated):
    """ADR-0063's shape, generated: group on the record field, sum the computed one.

    `AccruedCategoryInterest` carries `WS-MONTHLY-INT` as a computed field and nothing called
    `TRAN-AMT`, so the reader that refused to exist is the one this asserts on.
    """
    _outcome, project, _build = generated
    reader = (
        project / "src" / "main" / "java" / "com" / "modernized" / "batch" / "reader"
        / "PostAccountInterestItemReader.java"
    )
    assert reader.exists(), "the control-break step rendered no aggregating reader"

    source = reader.read_text(encoding="utf-8")
    assert "BigDecimal key = item.categoryBalance().trancatAcctId();" in source
    assert "totals.merge(key, item.monthlyInterest(), BigDecimal::add);" in source
    assert "first.account()" in source
    # The group item's accumulator is filled by summing, not copied from a row.
    assert "tranAmt" not in source


def test_no_file_reader_is_rendered_for_an_item_carrying_a_computed_value(generated):
    """`writeInterestTransaction` reads what `computeMonthlyInterest` produced, not a file.

    Its input carries `WS-MONTHLY-INT`, which is in no file. A reader rendered for it compiled
    nowhere -- it built a four-component record from three arguments.
    """
    _outcome, project, _build = generated
    readers = project / "src" / "main" / "java" / "com" / "modernized" / "batch" / "reader"
    assert not (readers / "WriteInterestTransactionItemReader.java").exists()
    assert (project / "src" / "main" / "java" / "com" / "modernized" / "batch" / "job"
            / "AccruedCategoryInterestStaging.java").exists()


def test_the_rendered_project_compiles(generated):
    """Held to javac rather than to review.

    Compilation is necessary and not sufficient: this project compiles and its job still cannot
    start, because `computeCategoryFees` has no bean and `STEP_NAMES` names it. That is
    ADR-0032 working, and it is why `test_the_job_names_the_steps_it_has_beans_for` asserts on
    the list rather than trusting a green build here.
    """
    _outcome, _project, build = generated
    assert build.succeeded, (
        f"the rendered project did not compile: exit {build.exit_code}; "
        + "; ".join(str(d) for d in build.diagnostics)
    )
