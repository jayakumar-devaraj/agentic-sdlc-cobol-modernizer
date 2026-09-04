"""Specialist CLI entrypoint, invoked by agentic-sdlc-control-plane's specialist router.

Two subcommands, not one, because this repo has no durable state (ADR-0001) and cannot pause
mid-invocation for a human gate: `design` runs spec extraction through solution design and exits;
`generate` starts fresh from an approved design.json and runs codegen through self-healing
compile. Control-plane's own durable gate sits between two separate, independently-bounded
process invocations, not inside one continuous flow -- see
docs/adr/0003-two-phase-invocation-split-at-the-human-gate.md.

Both subcommands are wired. `design` runs the real LangGraph pipeline (ADR-0012) over the
requested programs and writes real artifacts. `generate` runs the self-healing loop: render a
processor, write it into the target project, compile, and -- while `build_validator` says a rewrite
could help -- ask for one, at most three times.

**stdout carries exactly one JSON object and nothing else** when `--json` is passed. Every log
line goes to stderr (`telemetry/logging_config.py`). That contract is the reason this module
catches exceptions at the subcommand boundary rather than letting them escape: an unhandled
traceback would leave a caller parsing stdout with nothing to parse, in precisely the situation
where it most needs a machine-readable reason. The traceback still reaches stderr in full.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from pathlib import Path

from cobol_modernizer.core.contracts import (
    DesignCliResult,
    DesignDocument,
    EquivalenceTestVerdict,
    EquivalenceVerdict,
    GenerateCliResult,
)
from cobol_modernizer.core.design_outputs import write_design_outputs
from cobol_modernizer.core.package_data import ORACLE_ROOT
from cobol_modernizer.equivalence.harness import compare_project_output, unrenderable_reason
from cobol_modernizer.graph.design_graph import run_design
from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.rendering.java_job import plan_steps
from cobol_modernizer.telemetry import tracing
from cobol_modernizer.telemetry.logging_config import (
    bind_run_id,
    configure_logging,
    current_run_id,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cobol-modernizer",
        description="Modernize COBOL programs from a tenant repository into Java, in two phases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    design = subparsers.add_parser(
        "design",
        help="Run spec extraction, critique, and solution design. Exits at design.json.",
    )
    design.add_argument(
        "--programs",
        required=True,
        nargs="+",
        help="COBOL program names to process together, e.g. CBCUS01C CBACT01C CBTRN02C CBACT04C",
    )
    design.add_argument("--tenant-repo", required=True, help="Path to the cloned tenant repo worktree")
    design.add_argument("--output", required=True, help="Directory to write design.json and spec.md into")
    design.add_argument(
        "--run-id",
        default=None,
        help=(
            "Correlation id for this invocation, echoed in --json output and every log line. "
            "Pass control-plane's own audit-log run id to tie the two together; omitted, one is "
            "generated."
        ),
    )
    design.add_argument("--json", action="store_true", help="Emit structured JSON on stdout")

    generate = subparsers.add_parser(
        "generate",
        help="Run codegen and the self-healing compile loop from an approved design.json.",
    )
    generate.add_argument("--design", required=True, help="Path to the approved design.json")
    generate.add_argument("--tenant-repo", required=True, help="Path to the cloned tenant repo worktree")
    generate.add_argument("--output", required=True, help="Directory to write generated Java into")
    generate.add_argument(
        "--run-id",
        default=None,
        help=(
            "Correlation id for this invocation, echoed in --json output and every log line. "
            "Pass control-plane's own audit-log run id to tie the two together; omitted, one is "
            "generated. Identical in meaning to `design --run-id`; a caller running both phases "
            "of one migration should pass the same value to both, since they are separate "
            "processes with no shared state (ADR-0003)."
        ),
    )
    generate.add_argument("--json", action="store_true", help="Emit structured JSON on stdout")

    return parser


def _run_design_command(args: argparse.Namespace) -> tuple[DesignCliResult, int]:
    """Execute the `design` subcommand. Returns the stdout contract object and the exit code."""
    run_id = args.run_id or uuid.uuid4().hex
    output_dir = Path(args.output)

    # Bind before anything else in this subcommand: from here on every record -- including the
    # ones model_client and the nodes emit from concurrent branch threads -- carries the id
    # (ADR-0018), so these three lines no longer interpolate it by hand.
    bind_run_id(run_id)

    logger.info(
        "design: start programs=%s tenant_repo=%s output=%s",
        ",".join(args.programs),
        args.tenant_repo,
        output_dir,
    )

    try:
        document = run_design(Path(args.tenant_repo), args.programs)
        design_json_path = write_design_outputs(document, output_dir)
    except Exception as exc:
        # Deliberately broad: see the module docstring. Any failure below this point must still
        # produce a parseable stdout object for control-plane, and the full traceback still goes
        # to stderr via logger.exception. Narrowing this to the node-specific exception types
        # would mean an unanticipated one silently breaks the --json contract instead.
        logger.exception("design: failed")
        return (
            DesignCliResult(
                status="error",
                run_id=run_id,
                programs=args.programs,
                output_path=str(output_dir),
                gate_item_count=0,
                detail=f"{type(exc).__name__}: {exc}",
            ),
            1,
        )

    gate_item_count = len(document.gate_items)
    logger.info(
        "design: done programs=%d gate_items=%d output=%s",
        len(document.programs),
        gate_item_count,
        design_json_path,
    )
    return (
        DesignCliResult(
            status="ok",
            run_id=run_id,
            programs=args.programs,
            output_path=str(design_json_path),
            gate_item_count=gate_item_count,
            detail=(
                f"Design complete for {len(document.programs)} program(s); "
                f"{gate_item_count} gate item(s) for review."
            ),
        ),
        0,
    )


def _describe_equivalence_test(verdict: EquivalenceTestVerdict) -> str:
    """One line for the rendered unit test, for the same sentence (ADR-0065).

    **`REFUSED` reads as a finding here, not as a tooling failure**, because that is what it is: the
    step computing the interest carries it nowhere the test can observe, which is the shape of the
    defect that shipped as step 49. A gate that rendered it as "could not run" would restore exactly
    the silence this line exists to break.
    """
    if verdict.status == "passed":
        return (
            f"{verdict.test_class} PASSED -- the per-row interest arithmetic matches COBOL's own "
            f"answers. Covers one COMPUTE, not the account accumulator (ADR-0065)."
        )
    if verdict.status == "failed":
        return f"{verdict.test_class} FAILED -- {verdict.reason}"
    if verdict.status == "refused":
        return f"REFUSED to render -- {verdict.reason}"
    return f"not rendered -- {verdict.reason}"


def _describe_equivalence(verdict: EquivalenceVerdict) -> str:
    """One line a reviewer can act on, for the sentence the release gate renders.

    Reports `not run` as plainly as it reports a match. A summary that simply omits the subject is
    what a human approved twice while the generated code posted the wrong money.
    """
    if verdict.status == "matched":
        excluded = (
            f", {len(verdict.excluded_fields)} field(s) excluded by decision"
            if verdict.excluded_fields
            else ""
        )
        return (
            f"MATCHED against the COBOL oracle -- {verdict.records_compared} record(s), "
            f"{verdict.fields_compared} field(s){excluded}."
        )
    if verdict.status == "mismatched":
        shown = "; ".join(verdict.mismatches[:3])
        more = f" (+{len(verdict.mismatches) - 3} more)" if len(verdict.mismatches) > 3 else ""
        return f"MISMATCHED against the COBOL oracle -- {shown}{more}"
    return f"NOT RUN -- {verdict.reason}"


def _equivalence_for(outcome, design_path: Path, output_dir: Path) -> EquivalenceVerdict:
    """The differential's verdict for this run, or `not_run` saying precisely why not (ADR-0064).

    **Today this returns `not_run` for every real design, and the reason is the deliverable.**
    `generate` renders processors (ADR-0019), not readers, writers or job configuration, so it
    produces no project that runs -- for `CBACT04C`'s real design `plan_steps` reports 6 of 9 steps
    renderable. Naming the three that are missing is a materially different thing for a reviewer to
    weigh than a summary that omits correctness entirely, which is what a human approved twice while
    the generated code posted the wrong money.

    It becomes a real verdict with no change here the moment a run produces output: the comparison
    is wired, the oracle ships in the wheel, and `compare_project_output` is what runs.
    """
    verdict = compare_project_output(output_dir, ORACLE_ROOT / "CBACT04C")
    if verdict.status != "not_run":
        return verdict

    # No output. Say whether that is because the job could not be rendered -- which is a fact this
    # phase knows and a reviewer cannot otherwise get -- rather than only that nothing was there.
    try:
        document = DesignDocument.model_validate_json(design_path.read_text(encoding="utf-8"))
        design = document.unified_design
        if design is not None and design.batch_jobs:
            job = design.batch_jobs[0]
            _renderable, skipped, _staged = plan_steps(job, design, job.program_name)
            if skipped:
                return EquivalenceVerdict(status="not_run", reason=unrenderable_reason(skipped))
    except Exception:
        logger.debug("generate: could not explain the missing comparison", exc_info=True)
    return verdict


def _run_generate_command(args: argparse.Namespace) -> tuple[GenerateCliResult, int]:
    """Execute the `generate` subcommand. Returns the stdout contract object and the exit code."""
    run_id = args.run_id or uuid.uuid4().hex
    output_dir = Path(args.output)
    bind_run_id(run_id)

    logger.info(
        "generate: start design=%s tenant_repo=%s output=%s",
        args.design, args.tenant_repo, output_dir,
    )

    try:
        outcome = run_generate(Path(args.design), Path(args.tenant_repo), output_dir)
    except Exception as exc:
        # Deliberately broad, for the reason `_run_design_command` documents: a caller parsing
        # stdout must get a structured reason even when something unanticipated failed. The full
        # traceback still reaches stderr.
        logger.exception("generate: failed")
        return (
            GenerateCliResult(
                status="error",
                run_id=run_id,
                output_path=str(output_dir),
                detail=f"{type(exc).__name__}: {exc}",
            ),
            1,
        )

    unfinished = outcome.blocked + outcome.exhausted
    # ADR-0064: the equivalence verdict travels in `detail` as well as in its own field, because
    # `detail` is the sentence control-plane's release gate renders. "Generated and compiled N
    # processor step(s)." is true, contains no claim about correctness, and reads as success -- and
    # two runs shipped wrong money past a human who saw exactly that line.
    equivalence = _equivalence_for(outcome, Path(args.design), output_dir)
    if outcome.succeeded:
        detail = (
            f"Generated and compiled {len(outcome.compiled)} processor step(s). "
            f"Equivalence: {_describe_equivalence(equivalence)} "
            f"Equivalence test: {_describe_equivalence_test(outcome.equivalence_test)}"
        )
    elif not outcome.outcomes:
        detail = (
            "No processor steps to generate: the design's batch jobs contain no steps with "
            "role='processor'."
        )
    else:
        # The first unfinished reason, in full. A count alone tells a reviewer that something is
        # wrong without telling them what, and the reason is the part that took a model call.
        detail = (
            f"{len(outcome.compiled)} of {len(outcome.outcomes)} processor step(s) compiled. "
            f"First unresolved: {unfinished[0].program_name}/{unfinished[0].step_name} -- "
            f"{unfinished[0].reason}"
        )

    return (
        GenerateCliResult(
            status="ok" if outcome.succeeded else "error",
            run_id=run_id,
            output_path=str(output_dir),
            detail=detail,
            steps_total=len(outcome.generable),
            steps_compiled=len(outcome.compiled),
            steps_blocked=len(outcome.blocked),
            steps_exhausted=len(outcome.exhausted),
            steps_not_generated=len(outcome.not_generated),
            equivalence=equivalence,
            equivalence_test=outcome.equivalence_test,
        ),
        0 if outcome.succeeded else 1,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    # After parsing, so `--help` and a usage error cost nothing, and before the command, so every
    # model call inside it nests under one span. Returns False and logs when no collector is
    # configured, which is the ordinary case (ADR-0046).
    tracing.configure_tracing()
    try:
        # `root=True` joins a TRACEPARENT if control-plane handed one in; without one this is the
        # trace's root, which is the right shape for a direct invocation.
        with tracing.span(f"cobol-modernizer.{args.command}", root=True) as span:
            # Annotated rather than inferred: without this the `design` branch fixes the type and
            # the `generate` branch reads as an error, even though the only thing done with either
            # below is `.model_dump_json()`.
            result: DesignCliResult | GenerateCliResult
            if args.command == "design":
                result, exit_code = _run_design_command(args)
            else:
                result, exit_code = _run_generate_command(args)
            # Read back rather than passed in: the subcommands generate the id when `--run-id` is
            # absent, and `bind_run_id` is the one place it is known for certain.
            span.set(
                {"cobol_modernizer.run_id": current_run_id(), "cobol_modernizer.exit_code": exit_code}
            )

        if args.json:
            print(result.model_dump_json())
        else:
            print(result.detail)
        return exit_code
    finally:
        # This process is a subprocess that exits; spans still batched when it does have, from the
        # collector's side, never happened. A `finally` rather than a trailing call so a failing
        # run - the one most worth having a trace of - still flushes.
        tracing.shutdown_tracing()


if __name__ == "__main__":
    sys.exit(main())
