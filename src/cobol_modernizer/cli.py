"""Specialist CLI entrypoint, invoked by agentic-sdlc-control-plane's specialist router.

Two subcommands, not one, because this repo has no durable state (ADR-0001) and cannot pause
mid-invocation for a human gate: `design` runs spec extraction through solution design and exits;
`generate` starts fresh from an approved design.json and runs codegen through self-healing
compile. Control-plane's own durable gate sits between two separate, independently-bounded
process invocations, not inside one continuous flow — see
docs/adr/0003-two-phase-invocation-split-at-the-human-gate.md.

Not yet implemented — this is the Milestone C1 skeleton; the sub-pipeline lands in Milestones
C2-C4.
"""

from __future__ import annotations

import argparse
import json
import sys


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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "design":
        result = {
            "status": "not_implemented",
            "phase": "design",
            "programs": args.programs,
            "detail": "Sub-pipeline lands in Milestones C2-C3; this is the Milestone C1 skeleton.",
        }
    elif args.command == "generate":
        result = {
            "status": "not_implemented",
            "phase": "generate",
            "detail": "Sub-pipeline lands in Milestone C4; this is the Milestone C1 skeleton.",
        }
    else:
        return 0

    if args.json:
        print(json.dumps(result))
    else:
        print(result["detail"])
    return 1


if __name__ == "__main__":
    sys.exit(main())
