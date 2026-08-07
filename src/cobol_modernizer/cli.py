"""Specialist CLI entrypoint, invoked by agentic-sdlc-control-plane's specialist router.

Contract (see docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md): a
single bounded invocation, structured JSON on stdout, no state held between invocations. Not yet
implemented — this is the Milestone C1 skeleton; the sub-pipeline lands in Milestones C2-C4.
"""

from __future__ import annotations

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cobol-modernizer",
        description="Modernize a COBOL program from a tenant repository into Java.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="Run the modernization pipeline for one program.")
    run.add_argument("--program", required=True, help="COBOL program name, e.g. CBACT04C")
    run.add_argument("--tenant-repo", required=True, help="Path to the cloned tenant repo worktree")
    run.add_argument("--output", required=True, help="Directory to write generated artifacts into")
    run.add_argument("--json", action="store_true", help="Emit structured JSON on stdout")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "run":
        result = {
            "status": "not_implemented",
            "program": args.program,
            "detail": "Sub-pipeline lands in Milestones C2-C4; this is the Milestone C1 skeleton.",
        }
        if args.json:
            print(json.dumps(result))
        else:
            print(result["detail"])
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
