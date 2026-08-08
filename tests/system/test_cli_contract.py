"""The CLI's argument and output contracts, independent of what any subcommand actually does.

Was `test_cli_skeleton.py`. The name stopped being true once `design` became a real pipeline
(ADR-0012) -- what is left here is the part that is genuinely about the CLI surface: argument
parsing, the two-subcommand split (ADR-0003), the stdout/stderr separation, and `generate`'s
still-honest not-implemented report. `design`'s real end-to-end behavior lives in
`test_cli_design.py`.

Two subcommands, not one -- see
docs/adr/0003-two-phase-invocation-split-at-the-human-gate.md for why a single `run` command would
be wrong for a repo with no durable state (ADR-0001).
"""

from __future__ import annotations

import json

import pytest

from cobol_modernizer.cli import build_parser, main

# A path that does not exist, used deliberately: these tests are about the CLI's contract, and the
# contract has to hold on the failure path too -- arguably especially there. See
# test_logging_never_pollutes_the_json_stdout_contract.
MISSING_TENANT_REPO = "/nonexistent/tenant-repo"


def design_argv(*extra: str) -> list[str]:
    return [
        "design",
        "--programs", "CBACT04C",
        "--tenant-repo", MISSING_TENANT_REPO,
        "--output", "/nonexistent/out",
        *extra,
    ]


# --- Argument parsing ------------------------------------------------------------------------


def test_design_requires_all_arguments():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["design", "--programs", "CBACT04C"])


def test_design_accepts_multiple_programs():
    parser = build_parser()
    args = parser.parse_args(
        [
            "design",
            "--programs", "CBCUS01C", "CBACT01C", "CBTRN02C", "CBACT04C",
            "--tenant-repo", "/tmp/tenant",
            "--output", "/tmp/out",
            "--json",
        ]
    )
    assert args.programs == ["CBCUS01C", "CBACT01C", "CBTRN02C", "CBACT04C"]


def test_run_id_is_optional_and_defaults_to_none():
    parser = build_parser()
    args = parser.parse_args(
        ["design", "--programs", "CBACT04C", "--tenant-repo", "/t", "--output", "/o"]
    )
    assert args.run_id is None


def test_generate_requires_a_design_file():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["generate", "--tenant-repo", "/tmp/tenant", "--output", "/tmp/out"])


# --- generate is still honest about not existing ----------------------------------------------


def test_generate_reports_not_implemented_honestly(capsys):
    exit_code = main(
        [
            "generate",
            "--design", "/tmp/design.json",
            "--tenant-repo", "/tmp/tenant",
            "--output", "/tmp/out",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["phase"] == "generate"
    assert "Milestone C4" in payload["detail"]


# --- Logging goes to stderr, never corrupts the --json stdout contract ------------------------


def test_logging_never_pollutes_the_json_stdout_contract(capsys):
    # Real assertion: stdout, byte for byte, is valid JSON and nothing else -- a log line leaking
    # onto stdout would break any caller (chiefly control-plane) parsing this CLI's output, per
    # cli.py's own docstring contract. Run against a missing tenant repo on purpose: this is the
    # path that logs an exception traceback, so it is the one most likely to corrupt stdout.
    assert main(design_argv("--json")) == 1
    captured = capsys.readouterr()
    json.loads(captured.out)  # raises if stdout is not exactly one clean JSON value
    assert "Traceback" in captured.err  # the traceback really was emitted, just not to stdout


def test_logging_writes_invocation_lifecycle_to_stderr(capsys):
    main(design_argv("--json"))
    captured = capsys.readouterr()
    assert "design: start" in captured.err
    assert "run_id=" in captured.err
    assert "design: failed" in captured.err
