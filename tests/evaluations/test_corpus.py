"""What has to be true before a judge score means anything.

These run always and cost nothing. They exist because every way this harness could quietly stop
measuring anything is a property of the *corpus*, not of the judge: a corruption that no longer
applies, a criterion nobody exercises, a defect the renderer already refuses. A judge benchmark
sitting on top of any of those would still produce a number, and the number would be fiction.
"""

from __future__ import annotations

import pytest

from cobol_modernizer.rendering.java_processor import (
    model_authored_line_range,
    render_processor,
)
from tests.evaluations.corpus import (
    _COMPLETE_BODY,
    CASES,
    CRITERIA,
    CRITERIA_BY_ID,
    FAITHFUL_CASES,
    PROGRAM,
    UNFAITHFUL_CASES,
    Ground,
)


def _render(case) -> str:
    return render_processor(
        case.step,
        package="com.modernized.batch.processor",
        class_name="EvalProcessor",
        input_type=case.step.input_type,
        output_type=case.step.output_type,
        body=case.body,
        body_imports=case.imports,
        authored_by="eval-corpus",
    )


@pytest.mark.parametrize("case", UNFAITHFUL_CASES, ids=[c.name for c in UNFAITHFUL_CASES])
def test_the_deterministic_guards_do_not_catch_any_unfaithful_case(case):
    """The precondition this whole package rests on.

    `render_processor` already refuses a body that forges the review markers, supplies a malformed
    import, or reads ambient state -- and those refusals are cheap, total, and free. A judge is worth
    paying for only where they cannot reach.

    So if this ever fails, the reading is not "the corpus broke". It is that a defect this package
    was built to measure has been promoted to a deterministic refusal, which is a *better* outcome
    than a judge catching it -- and the case must be retired from the corpus rather than the guard
    weakened. Stated here because the opposite reflex is the natural one.
    """
    rendered = _render(case)
    assert model_authored_line_range(rendered) is not None, (
        f"{case.name} rendered without a model-authored region, so nothing about it is attributable"
    )


@pytest.mark.parametrize("case", FAITHFUL_CASES, ids=[c.name for c in FAITHFUL_CASES])
def test_every_faithful_case_renders_too(case):
    # Rules out the opposite reading of the test above -- that `render_processor` accepts anything
    # at all and the precondition is inert.
    assert model_authored_line_range(_render(case)) is not None


def test_the_guards_the_corpus_sits_outside_of_are_actually_live():
    """The other half of the precondition, and the one that makes it say something.

    "`render_processor` accepted every case" is only evidence that the corpus is outside the guards
    if the guards reject *anything*. Shown here against a corpus body rather than a synthetic one:
    take the real completion body, put a clock in it, and it is refused. So the boundary this package
    draws -- deterministic refusal on one side, judge on the other -- is a real boundary and the
    corpus is genuinely on the far side of it.
    """
    from cobol_modernizer.rendering.java_processor import NonDeterministicBodyError
    from tests.evaluations.corpus import CASES_BY_NAME

    case = CASES_BY_NAME["completion_faithful"]
    with_clock = _mutate_for_test(case.body, "null,\n    null);", "LocalDateTime.now(),\n    null);")

    with pytest.raises(NonDeterministicBodyError):
        render_processor(
            case.step,
            package="com.modernized.batch.processor",
            class_name="EvalProcessor",
            input_type=case.step.input_type,
            output_type=case.step.output_type,
            body=with_clock,
            body_imports=case.imports,
            authored_by="eval-corpus",
        )


def _mutate_for_test(body: str, anchor: str, replacement: str) -> str:
    assert anchor in body, anchor
    mutated = body.replace(anchor, replacement)
    assert mutated != body
    return mutated


@pytest.mark.parametrize("case", UNFAITHFUL_CASES, ids=[c.name for c in UNFAITHFUL_CASES])
def test_every_unfaithful_body_differs_from_the_faithful_one_it_came_from(case):
    """A corruption that silently did not apply would label a correct body defective.

    `corpus._mutate` already raises at import time, so this is the same guard expressed as a test --
    worth having twice, because an import-time assertion in a module a future edit stops importing
    fails silently, and a test does not.
    """
    faithful = {c.body for c in FAITHFUL_CASES}
    assert case.body not in faithful, (
        f"{case.name} is byte-identical to a body this corpus calls faithful"
    )


def test_the_two_source_grounded_corruptions_really_changed_the_real_model_body():
    # Named separately from the test above because these two are derived from `_COMPLETE_BODY` by
    # string replacement, which is the mechanism that can no-op.
    from tests.evaluations.corpus import _EMPTY_STRING_BODY, _INVENTED_ID_BODY

    assert _EMPTY_STRING_BODY != _COMPLETE_BODY
    assert _INVENTED_ID_BODY != _COMPLETE_BODY
    assert _EMPTY_STRING_BODY != _INVENTED_ID_BODY
    # And each changed the one thing it claims to have changed, nothing else.
    assert "CobolText.spaces(50)" not in _EMPTY_STRING_BODY
    assert "CobolText.spaces(10)" in _EMPTY_STRING_BODY, "only the PIC X(50) fields should change"
    assert "INT0000000001" in _INVENTED_ID_BODY
    assert "CobolText.spaces(50)" in _INVENTED_ID_BODY, "the width defect must not leak in here"


def test_the_invented_identifier_is_the_right_width_so_only_one_criterion_fires():
    """Each case must isolate exactly one criterion, and this is the one that nearly did not.

    `TRAN-ID` is `PIC X(16)` (`CVTRA05Y:5`). A fabricated value written as a bare short string would
    be wrong *twice* -- invented and under-width -- and a judge that flagged only the width would
    score as having caught it. Padding to 16 leaves the fabrication as the single defect.
    """
    from tests.evaluations.corpus import _INVENTED_ID_BODY

    assert 'CobolText.pad("INT0000000001", 16)' in _INVENTED_ID_BODY
    # The literal really is what PIC X(16) asks `CobolText.pad` to produce, so the padding call is
    # not itself hiding a second defect.
    assert len("INT0000000001") <= 16


@pytest.mark.parametrize("case", UNFAITHFUL_CASES, ids=[c.name for c in UNFAITHFUL_CASES])
def test_every_case_names_a_criterion_that_exists(case):
    assert case.failing_criterion in CRITERIA_BY_ID


@pytest.mark.parametrize("criterion", CRITERIA, ids=[c.id for c in CRITERIA])
def test_every_criterion_is_exercised_by_a_case(criterion):
    """A criterion in the rubric and in no case is a question the judge is asked and never scored on.

    That is the shape of a rubric growing faster than its evidence: the prompt gets longer, the
    measured surface does not, and the number stops meaning what its name says.
    """
    assert any(case.failing_criterion == criterion.id for case in UNFAITHFUL_CASES), (
        f"criterion {criterion.id!r} has no case that must fail it"
    )


def test_the_corpus_can_measure_false_positives_as_well_as_misses():
    """Without a faithful case, a judge that fails everything scores perfectly.

    That judge is not merely useless -- it is the expensive failure. Section 4b of the feasibility
    assessment puts human review three to four orders of magnitude above inference cost, so a judge
    that flags correct output routes work to the dominant cost centre and the harness must be able
    to see it doing so.
    """
    assert FAITHFUL_CASES, "no faithful case: nothing here could detect a judge that fails everything"
    assert UNFAITHFUL_CASES, "no unfaithful case: nothing here could detect a judge that passes all"


def test_every_step_in_the_corpus_says_what_becomes_of_its_output():
    """A step with no downstream fact is a step the judge must guess about.

    The first billed run is why this exists: without it the judge flagged placeholder fields in a
    *carried* record as short written output, correctly on the facts and wrongly as a defect. A new
    step added without this entry would reintroduce that silently, and the symptom would look like
    the judge getting worse.
    """
    from tests.evaluations.corpus import DOWNSTREAM_BY_STEP

    for case in CASES:
        assert case.step.step_name in DOWNSTREAM_BY_STEP, case.step.step_name


def test_the_downstream_fact_is_keyed_by_step_so_it_cannot_leak_the_answer():
    # Keyed by step rather than by case, so two cases of one step get identical text by construction
    # rather than by anyone remembering to keep them in sync.
    from tests.evaluations.corpus import DOWNSTREAM_BY_STEP

    for case in CASES:
        assert case.name not in DOWNSTREAM_BY_STEP[case.step.step_name]
        if case.failing_criterion:
            assert case.failing_criterion not in DOWNSTREAM_BY_STEP[case.step.step_name]


def test_both_grounds_are_represented_and_labelled():
    grounds = {case.ground for case in CASES}
    assert grounds == {Ground.ORACLE, Ground.SOURCE}


def test_every_oracle_grounded_case_cites_a_test_that_still_exists():
    """The `ORACLE` ground is a claim about another module, so it can go stale there.

    `evidence` names the test whose real Maven run establishes the verdict. If that test is renamed
    or deleted, the citation becomes decoration and the strongest claim in this package -- that two
    of its cases are graded by a JVM rather than by a reading -- quietly stops being checkable.
    """
    import re

    from tests.system import test_cbtrn02c_round_trip, test_interest_equivalence

    # Searched across the modules that own the runs rather than one of them: the corpus spans two
    # programs since ADR-0050, and a check hard-coded to the first program's module would have
    # silently stopped covering the second -- which is the shape of guard that passes while
    # enforcing nothing.
    modules = (test_interest_equivalence, test_cbtrn02c_round_trip)

    for case in CASES:
        if case.ground is not Ground.ORACLE:
            continue
        cited = re.findall(r"\btest_\w+", case.evidence)
        assert cited, f"{case.name} is oracle-grounded but cites no test"
        for name in cited:
            assert any(hasattr(module, name) for module in modules), (
                f"{case.name} cites {name!r}, which no longer exists in "
                f"{[m.__name__.rsplit('.', 1)[-1] for m in modules]}"
            )
# --- The second instance (ADR-0050) ---------------------------------------------------------------


def test_the_corpus_covers_more_than_one_program():
    """**The property ADR-0044's rule is about, asserted rather than assumed.**

    A corpus drawn from one program measures a judge against one program's idioms and reports it as
    a property of the judge. Every case here derived from `CBACT04C` until ADR-0050, which is the
    specific reason ADR-0049 could not close pillar 22 despite measuring the judge reproducible.

    Asserted as a count rather than by naming `CBTRN02C`, so that removing the second program fails
    here rather than leaving a rule about second instances enforced by nobody.
    """
    programs = {case.program for case in CASES}
    assert len(programs) >= 2, f"a corpus of one program is not a second instance: {programs}"


def test_each_program_contributes_a_faithful_case_and_a_defective_one():
    """Neither half alone measures anything.

    A program contributing only defective bodies cannot show a judge invents no false positives on
    its idioms; one contributing only faithful bodies cannot show the judge detects anything there.
    Both directions have to be present per program or the second instance is half an instance.
    """
    for program in sorted({case.program for case in CASES}):
        cases = [case for case in CASES if case.program == program]
        assert any(case.failing_criterion is None for case in cases), f"{program}: no faithful case"
        assert any(
            case.failing_criterion is not None for case in cases
        ), f"{program}: no defective case"


def test_the_second_program_is_graded_by_a_jvm_rather_than_a_reading():
    """The weaker corpus would have been easy, and would have been worth much less.

    `CBTRN02C`'s cases could have been `SOURCE`-grounded -- read the COBOL, write a plausible defect,
    label it. Both are `ORACLE` instead: the faithful body is the one matched against COBOL's own
    output on 4,144 fields, and the defective one is that body minus a single `if`, run under real
    Maven and shown to write 300 records where COBOL writes 262.
    """
    for case in CASES:
        if case.program == PROGRAM:
            continue
        assert case.ground is Ground.ORACLE, (
            f"{case.name} is the second instance and is graded by a reading; the point of adding "
            "it was that its verdicts come from a run"
        )


def test_the_second_programs_bodies_are_the_ones_that_were_actually_run():
    """Imported, never retyped -- the same rule the interest bodies follow and for the same reason.

    The moment a corpus body diverges from the string a JVM compiled, its `ORACLE` ground becomes a
    claim about a body no JVM ever saw.
    """
    from tests.system.test_cbtrn02c_round_trip import _BODY, _UNGUARDED_BODY

    by_name = {case.name: case for case in CASES}
    assert by_name["posting_faithful"].body is _BODY
    assert by_name["posting_unguarded"].body is _UNGUARDED_BODY
