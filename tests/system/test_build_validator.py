"""`nodes/build_validator.py` — attribution against real compiles, triage against an injected model.

The deterministic half is exercised against **real `CompileResult`s from real Maven builds**,
because its whole job is reading javac's output and one of its checks (which region an error falls
in) depends on line numbers a renderer produced. The model half is injected, as everywhere else in
this repo.

The end-to-end test here found the defect that mattered most in this step: `java_processor` was
rendering `org.springframework.batch.item.ItemProcessor`, the pre-6 package, so **every processor
ever generated carried an import that does not resolve.** Nothing short of compiling one would have
caught it.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import BatchStepDesign
from cobol_modernizer.nodes.build_validator import (
    BuildValidatorParseError,
    ValidationVerdict,
    attribute_errors,
    build_validator_prompt,
    classify,
    validate_build,
)
from cobol_modernizer.rendering.java_processor import model_authored_line_range, render_processor
from cobol_modernizer.tools.local_compiler import CompileDiagnostic, CompileResult, compile_project

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "target-spring-boot-baseline"
REL = "src/main/java/com/modernized/batch/processor/PassThroughProcessor.java"

STEP = BatchStepDesign(
    step_name="passThrough",
    source_paragraphs=["1300-COMPUTE-INTEREST"],
    input_type="TranCatBal",
    output_type="TranCatBal",
    role="processor",
    description="Returns the input unchanged.",
        guard_condition=None)


def _render(body: str, imports: tuple[str, ...] = ()) -> str:
    return render_processor(
        STEP,
        package="com.modernized.batch.processor",
        class_name="PassThroughProcessor",
        input_type="java.math.BigDecimal",
        output_type="java.math.BigDecimal",
        body=body,
        body_imports=imports,
        authored_by="test-model",
    )


def _result(*errors: CompileDiagnostic, succeeded: bool = False, raw: str = "x") -> CompileResult:
    return CompileResult(
        succeeded=succeeded,
        exit_code=0 if succeeded else 1,
        diagnostics=errors,
        duration_ms=1,
        raw_output=raw,
    )


def _error(line: int, message: str = "cannot find symbol") -> CompileDiagnostic:
    return CompileDiagnostic(
        file=REL, line=line, column=1, severity="error", message=message
    )


# --- Region attribution -------------------------------------------------------------------------


def test_an_error_in_the_model_region_is_attributed_to_the_model():
    source = _render("return item;")
    span = model_authored_line_range(source)
    assert span is not None
    model_errors, rendered_errors = attribute_errors(_result(_error(span[0])), {REL: source})
    assert len(model_errors) == 1
    assert rendered_errors == ()


def test_an_error_in_rendered_scaffolding_is_not_the_models():
    # Line 1 is the package declaration -- rendered, never authored.
    source = _render("return item;")
    model_errors, rendered_errors = attribute_errors(_result(_error(1)), {REL: source})
    assert model_errors == ()
    assert len(rendered_errors) == 1


def test_the_marker_lines_themselves_are_rendered_not_authored():
    # An off-by-one here would let a model be asked to rewrite its own region markers, which is the
    # one edit that could hide every future attribution.
    source = _render("return item;")
    begin_line = next(
        n for n, line in enumerate(source.splitlines(), 1) if "BEGIN model-authored" in line
    )
    model_errors, rendered_errors = attribute_errors(_result(_error(begin_line)), {REL: source})
    assert model_errors == ()
    assert len(rendered_errors) == 1


def test_an_error_in_a_file_this_run_did_not_generate_counts_as_rendered():
    # The conservative reading: whatever it is, the model did not write it in this attempt.
    other = CompileDiagnostic(
        file="src/main/java/com/modernized/batch/BatchApplication.java",
        line=9, column=1, severity="error", message="cannot find symbol",
    )
    model_errors, rendered_errors = attribute_errors(_result(other), {REL: _render("return item;")})
    assert model_errors == ()
    assert len(rendered_errors) == 1


def test_a_file_with_no_markers_yields_no_range():
    assert model_authored_line_range("class A {}\n") is None


# --- Deterministic classification -----------------------------------------------------------------


def test_a_successful_build_is_accepted_without_a_model_call():
    verdict = classify(_result(succeeded=True), {})
    assert verdict is not None
    assert verdict.outcome == "accepted"
    assert not verdict.should_retry


def test_an_unparsed_failure_is_blocked_and_says_it_needs_a_human():
    verdict = classify(_result(raw="Something Maven said that no parser matched"), {})
    assert verdict is not None
    assert verdict.outcome == "blocked"
    assert "human" in verdict.reason


def test_an_error_in_rendered_scaffolding_blocks_rather_than_asking_for_a_rewrite():
    # The check the markers exist for. Asking a model to fix rendered code lets it rewrite
    # deterministic output to make a symptom go away.
    source = _render("return item;")
    verdict = classify(_result(_error(1)), {REL: source})
    assert verdict is not None
    assert verdict.outcome == "blocked"
    assert "renderer" in verdict.reason
    assert verdict.rendered_region_errors


def test_a_deterministic_verdict_short_circuits_before_any_model_call():
    """The *wiring*, not the helper — and a coverage delta is what found it missing.

    `classify` has a direct test for every deterministic branch above. Nothing tested that
    `validate_build` actually short-circuits on one, and until this change a single integration test
    happened to cover that path: G30's case, which drove a rendered-region error through the real
    entrypoint. Closing G30 made that case *heal* instead, so the early return silently lost its only
    exercise — CI's coverage fell 98.84% → 98.73% and pointed straight at these lines.

    G21's shape again: a helper with its own tests, and no test of the path to production. Worth
    pinning through the real entrypoint rather than by calling `classify` once more, because the
    property is about `validate_build` and it is economic as well as behavioural — a verdict this
    node can reach on its own must never pay for a model call.
    """

    def advise(routing, system_prompt, user_content):
        raise AssertionError("a deterministic verdict must not reach the model")

    accepted = validate_build(_result(succeeded=True), {}, advise=advise)
    assert accepted.outcome == "accepted"

    # And the blocked half, which is the branch G30's test used to be the only cover for.
    source = _render("return item;")
    blocked = validate_build(_result(_error(1)), {REL: source}, advise=advise)
    assert blocked.outcome == "blocked"
    assert blocked.rendered_region_errors


def test_errors_only_in_the_model_region_are_passed_to_the_model():
    # `None` means "deterministic checks could not decide" -- the one case worth a call.
    source = _render("return item;")
    span = model_authored_line_range(source)
    assert classify(_result(_error(span[0])), {REL: source}) is None


# --- The model's triage ---------------------------------------------------------------------------


def _advise(payload: dict):
    def advise(routing, system_prompt: str, user_content: str) -> str:
        return json.dumps(payload)

    return advise


def _validate(source: str, line: int, payload: dict) -> ValidationVerdict:
    return validate_build(_result(_error(line)), {REL: source}, advise=_advise(payload))


def test_a_repairable_verdict_carries_the_instruction_forward():
    source = _render("return item;")
    span = model_authored_line_range(source)
    verdict = _validate(
        source, span[0],
        {"repairable": True, "reason": "the method name is misspelled",
         "instruction": "call truncate, not truncateTypo"},
    )
    assert verdict.outcome == "repairable"
    assert verdict.should_retry
    assert verdict.instruction == "call truncate, not truncateTypo"


def test_a_blocked_verdict_carries_no_instruction():
    # An instruction on a blocked verdict is an invitation for a loop to use it anyway.
    source = _render("return item;")
    span = model_authored_line_range(source)
    verdict = _validate(
        source, span[0],
        {"repairable": False, "reason": "TranCatBalWithRate does not exist",
         "instruction": "this should be ignored"},
    )
    assert verdict.outcome == "blocked"
    assert not verdict.should_retry
    assert verdict.instruction == ""


def test_claiming_repairable_without_an_instruction_is_a_contract_violation():
    source = _render("return item;")
    span = model_authored_line_range(source)
    with pytest.raises(BuildValidatorParseError, match="no instruction"):
        _validate(source, span[0], {"repairable": True, "reason": "r", "instruction": "   "})


@pytest.mark.parametrize("payload", [
    {"reason": "r", "instruction": "i"},
    {"repairable": True, "instruction": "i"},
    {"repairable": True, "reason": "r"},
])
def test_a_missing_required_key_raises(payload):
    source = _render("return item;")
    span = model_authored_line_range(source)
    with pytest.raises(BuildValidatorParseError, match="missing required key"):
        _validate(source, span[0], payload)


def test_a_non_boolean_repairable_raises():
    # "true" as a string is the classic near-miss, and it is truthy in Python.
    source = _render("return item;")
    span = model_authored_line_range(source)
    with pytest.raises(BuildValidatorParseError, match="non-boolean"):
        _validate(source, span[0], {"repairable": "true", "reason": "r", "instruction": "i"})


def test_malformed_json_raises():
    """Still raises -- but only after the repair attempt, since this fake never complies."""
    source = _render("return item;")
    span = model_authored_line_range(source)
    with pytest.raises(BuildValidatorParseError, match="not valid JSON"):
        validate_build(_result(_error(span[0])), {REL: source}, advise=lambda r, s, u: "nope")


def test_a_prose_preamble_is_repaired():
    """The wiring to `parse_with_repair`, pinned (ADR-0054). A damage probe with
    `max_attempts=1` left this file's 30 tests green, so the path was wholly uncovered."""
    source = _render("return item;")
    span = model_authored_line_range(source)
    payload = json.dumps({"repairable": True, "reason": "missing import", "instruction": "add it"})
    responses = [f"Looking at the diagnostics, here is my verdict:\n\n{payload}", payload]
    sent: list[str] = []

    def preamble_then_comply(routing, system_prompt, user_content):
        sent.append(user_content)
        return responses.pop(0)

    verdict = validate_build(
        _result(_error(span[0])), {REL: source}, advise=preamble_then_comply
    )

    assert verdict.outcome == "repairable"
    assert len(sent) == 2, "the node re-asked exactly once"
    assert "could not be parsed" in sent[1]


# --- The prompt shows only what the model may change ----------------------------------------------


def test_the_prompt_carries_the_statements_but_not_the_scaffolding():
    source = _render("return item;")
    span = model_authored_line_range(source)
    prompt = build_validator_prompt((_error(span[0]),), {REL: source})
    assert "return item;" in prompt
    for structural in ("@Override", "public class", "package ", "import "):
        assert structural not in prompt


# --- Real builds ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def project(tmp_path_factory) -> Path:
    destination = tmp_path_factory.mktemp("validator-project") / "proj"
    shutil.copytree(TEMPLATE, destination, ignore=shutil.ignore_patterns("target"))
    return destination


def _write(project: Path, source: str) -> None:
    target = project / REL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")


def test_a_rendered_processor_actually_compiles(project):
    """The test that caught the package rename.

    `java_processor` rendered `org.springframework.batch.item.ItemProcessor` -- correct before
    Spring Batch 6, and absent from 6.0.4, which ships
    `org.springframework.batch.infrastructure.item.ItemProcessor`. Every processor generated to that
    point carried an unresolvable import. Only compiling one shows it.
    """
    source = _render("return item;")
    _write(project, source)
    result = compile_project(project, goal="compile")
    assert result.succeeded, "\n".join(e.render() for e in result.errors)


def test_a_processor_taking_job_parameters_actually_compiles(project):
    """The same check as above, for the shape ADR-0026 introduced — and for the same reason.

    `@StepScope` and `@Value` are framework imports this renderer emits unconditionally, exactly like
    `ItemProcessor` was when it silently pointed at a package Spring Batch 6 had moved. A rendered
    package name is a claim about a jar, and the only thing that settles it is `javac`. Written
    before trusting the rendering, not after.
    """
    from cobol_modernizer.core.contracts import JobParameter

    source = render_processor(
        STEP,
        package="com.modernized.batch.processor",
        class_name="PassThroughProcessor",
        input_type="java.math.BigDecimal",
        output_type="java.math.BigDecimal",
        body="return item;",
        authored_by="test",
        job_parameters=[
            JobParameter(
                name="parmDate",
                java_type="String",
                description="The run date, from JCL.",
                source_cobol="PARM-DATE",
            )
        ],
    )
    _write(project, source)
    result = compile_project(project, goal="compile")
    assert result.succeeded, "\n".join(e.render() for e in result.errors)


def test_a_real_body_error_is_attributed_to_the_model_and_reaches_triage(project):
    source = _render(
        "return CobolArithmetic.truncateTypo(item, 2);",
        ("com.modernized.batch.cobol.CobolArithmetic",),
    )
    _write(project, source)
    result = compile_project(project, goal="compile")

    assert not result.succeeded
    model_errors, rendered_errors = attribute_errors(result, {REL: source})
    assert len(model_errors) == 1, [e.render() for e in result.errors]
    assert rendered_errors == (), [e.render() for e in rendered_errors]
    assert "truncateTypo" in " ".join(model_errors[0].details)
    # Deterministic checks defer: this is exactly the case the model is paid to judge.
    assert classify(result, {REL: source}) is None
