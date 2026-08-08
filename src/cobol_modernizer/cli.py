"""Specialist CLI entrypoint, invoked by agentic-sdlc-control-plane's specialist router.

Two subcommands, not one, because this repo has no durable state (ADR-0001) and cannot pause
mid-invocation for a human gate: `design` runs spec extraction through solution design and exits;
`generate` starts fresh from an approved design.json and runs codegen through self-healing
compile. Control-plane's own durable gate sits between two separate, independently-bounded
process invocations, not inside one continuous flow -- see
docs/adr/0003-two-phase-invocation-split-at-the-human-gate.md.

`design` is fully wired (ADR-0012): it runs the real LangGraph pipeline over the requested
programs and writes real artifacts. `generate` is still the Milestone C1 skeleton; it lands in
Milestone C4, once `modernization_engineer` and `build_validator` exist.

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

from cobol_modernizer.core.contracts import DesignCliResult, GenerateCliResult
from cobol_modernizer.core.design_outputs import write_design_outputs
from cobol_modernizer.graph.design_graph import run_design
from cobol_modernizer.telemetry.logging_config import configure_logging

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
    generate.add_argument("--json", action="store_true", help="Emit structured JSON on stdout")

    return parser


def _run_design_command(args: argparse.Namespace) -> tuple[DesignCliResult, int]:
    """Execute the `design` subcommand. Returns the stdout contract object and the exit code."""
    run_id = args.run_id or uuid.uuid4().hex
    output_dir = Path(args.output)

    logger.info(
        "design: start run_id=%s programs=%s tenant_repo=%s output=%s",
        run_id,
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
        logger.exception("design: failed run_id=%s", run_id)
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
        "design: done run_id=%s programs=%d gate_items=%d output=%s",
        run_id,
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


def _run_generate_command(args: argparse.Namespace) -> tuple[GenerateCliResult, int]:
    """The Milestone C1 skeleton, unchanged -- `generate` lands in Milestone C4."""
    logger.info("generate phase: design_file=%s", args.design)
    return (
        GenerateCliResult(
            status="error",
            output_path=args.output,
            detail="Not implemented: the generate sub-pipeline lands in Milestone C4.",
        ),
        1,
    )


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "design":
        result, exit_code = _run_design_command(args)
    else:
        result, exit_code = _run_generate_command(args)

    if args.json:
        print(result.model_dump_json())
    else:
        print(result.detail)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
