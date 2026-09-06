"""The wiring renders for a design a *model* produced, and what it renders reaches javac (ADR-0069).

**The gap this closes.** Verification 16 proved `generate` wires a project that builds and runs, and
every number in it was measured against the hand-written round trip's three-step design --
`computeInterest`, `TranCatBalWithRate`, the composite this repository's own fixtures declare. No
design a live `solution_architect` produced had ever reached `render_job_wiring`. Three defects were
sitting behind that, and each one was found by pointing this pipeline at a real design for the first
time:

1. `aggregation_source` could not see a value carried as a `computed_fields` entry, so a control
   break on a design obeying ADR-0063 resolved to no source and the step fell through to a file
   reader that correctly refused an in-memory aggregate.
2. `plan_steps` never consulted `role`, so a `tasklet` of five file OPENs and a `reader` of
   `1000-TCATBALF-GET-NEXT` were planned as chunk steps and collided on `ItemReader<TranCatBal>`.
3. `render_item_reader` built its constructor from `components` alone, so a composite of three
   records and one computed field came out as a three-argument call to a four-component record --
   uncompilable Java, emitted with no diagnostic.

None of them is reachable from the fixture design, because it declares no computed field, no
tasklet and no reader step. **A fixture the repository wrote cannot exercise the shapes a model
writes**, which is why this module pins one that a model actually wrote.

**What it measures, and what it deliberately does not.** The wiring: that every step is planned,
that the classes it renders are consistent, and that what reaches the compiler fails for exactly one
named reason and no other. Not correctness -- the processor bodies here are scripted `return null;`,
so nothing about the generated logic is claimed and the differential stays where it belongs, in
`test_generate_renders_the_wiring.py`. A body that compiles is all a wiring test needs, and
scripting it is what keeps this module free of a model call.

**This project does not yet compile, and the reason is the design's.** It types
`writeInterestTransaction` and `postAccountInterest` as `writer`, and `generate` renders a body only
for `role == "processor"` (ADR-0023, G27), so the job configuration injects two classes the pipeline
will never produce. ADR-0027 settled that a pre-aggregated posting step is an ordinary per-item
transform, and this repository's fixture types it `processor` for that reason -- so the durable fix
is a refusal where the design is produced, not another renderer change. Until it lands,
`test_what_remains_is_exactly_the_two_steps_typed_writer` pins that this is the *only* thing left,
so a fourth defect cannot hide behind a known one.

Costs a Maven build. See `docs/development-environment.md` for `JAVA_HOME`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.tools.local_compiler import compile_project
from tests.support.interest_design import FIXTURE_ROOT

#: A design `solution_architect` produced for `CBACT04C` under prompt `v1_3_0`, saved verbatim from
#: run `step54b-cbact04c-20260905-211254`. Pinned as a fixture rather than regenerated: the point is
#: that it is *not* this repository's own idea of what a design looks like, and re-deriving it from a
#: model would make this test's subject change under it.
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
LIFECYCLE_STEPS = ["openInterestFiles", "readTranCatBalance", "closeInterestFiles"]


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


def test_every_class_the_wiring_needs_is_rendered_or_named(generated):
    """The wiring reaches the compiler at all, which it did not for any live design before this.

    Not `status == "rendered"`: this design does not get there, and for a reason that belongs to the
    design rather than to these renderers -- see
    `test_what_remains_is_exactly_the_two_steps_typed_writer`. What is asserted here is that every
    refusal `plan_steps` and `render_job_wiring` used to raise is gone: the control break resolves,
    the file lifecycle is skipped with a reason, and no two steps collide on one bean type.
    """
    outcome, _project, _build = generated
    assert outcome.wiring.status != "not_rendered", outcome.wiring.reason
    assert "driving stream" not in outcome.wiring.reason
    assert "ambiguous" not in outcome.wiring.reason
    assert outcome.wiring.files_rendered, "nothing was rendered at all"


def test_every_step_that_carries_an_item_is_planned(generated):
    """The five that transform or store an item, and the three that are the reader's lifecycle.

    Asserted as a pair. A test naming only the planned steps would keep passing if the lifecycle
    steps came back as chunk steps, which is exactly the defect that refused this job's wiring.
    """
    outcome, _project, _build = generated
    rendered = {Path(p).name for p in outcome.wiring.files_rendered}

    for step in CHUNK_STEPS:
        expected = f"{step[:1].upper()}{step[1:]}"
        assert any(name.startswith(expected) for name in rendered), (
            f"{step} contributed no rendered class"
        )
    for step in LIFECYCLE_STEPS:
        assert any(step in reason for reason in outcome.wiring.skipped_steps), (
            f"{step} should be reported as skipped with a reason, not silently rendered"
        )


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


#: The two steps this design types `writer` and ADR-0027 types `processor`.
MISTYPED_AS_WRITER = ["WriteInterestTransactionProcessor", "PostAccountInterestProcessor"]


def test_what_remains_is_exactly_the_two_steps_typed_writer(generated):
    """The one thing still between this design and a project that compiles, pinned by its cause.

    `generate` renders a body only for `role == "processor"` (ADR-0023, G27), and this design types
    `writeInterestTransaction` and `postAccountInterest` as `writer`. So the job configuration
    injects two processor classes the pipeline will never produce. **That is a fact about the
    design, not about these renderers**: ADR-0027 settled that once the item is pre-aggregated the
    posting step is an ordinary per-item transform, and this repository's own fixture types it
    `processor` for that reason. The durable fix is a refusal where the design is produced, the same
    shape as ADR-0059, ADR-0062 and ADR-0063.

    Held here rather than deferred to a note, and held *exactly*: the assertion is that these are the
    only errors left. A fourth defect appearing in this project fails this test rather than hiding
    behind a known one -- the same discipline as
    `assert_account_half_matches_except_the_last`, which pins a divergence to its cause so a
    different cause cannot pass.
    """
    _outcome, _project, build = generated
    assert not build.succeeded, (
        "the two writer-typed steps now compile -- if the design-time refusal landed, this test "
        "should become `assert build.succeeded` and the module docstring should lose its caveat"
    )
    unexplained = [
        d
        for d in build.diagnostics
        if not any(name in " ".join(d.details) for name in MISTYPED_AS_WRITER)
    ]
    assert unexplained == [], (
        "something other than the two writer-typed steps stops this project compiling:\n"
        + "\n".join(str(d) for d in unexplained)
    )
