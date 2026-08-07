"""Real tests against the actual CLI skeleton — not placeholders.

Two subcommands (design, generate), not one — see
docs/adr/0003-two-phase-invocation-split-at-the-human-gate.md for why a single `run` command
would be wrong for a repo with no durable state (ADR-0001).
"""

from __future__ import annotations

import json

import pytest

from cobol_modernizer.cli import build_parser, main


def test_design_requires_all_arguments():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["design", "--programs", "CBACT04C"])


def test_design_accepts_multiple_programs():
    parser = build_parser()
    args = parser.parse_args(
        [
            "design",
            "--programs",
            "CBCUS01C",
            "CBACT01C",
            "CBTRN02C",
            "CBACT04C",
            "--tenant-repo",
            "/tmp/tenant",
            "--output",
            "/tmp/out",
            "--json",
        ]
    )
    assert args.programs == ["CBCUS01C", "CBACT01C", "CBTRN02C", "CBACT04C"]


def test_design_reports_not_implemented_honestly(capsys):
    exit_code = main(
        [
            "design",
            "--programs",
            "CBACT04C",
            "--tenant-repo",
            "/tmp/tenant",
            "--output",
            "/tmp/out",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "not_implemented"
    assert payload["phase"] == "design"


def test_generate_requires_a_design_file():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["generate", "--tenant-repo", "/tmp/tenant", "--output", "/tmp/out"])


def test_generate_reports_not_implemented_honestly(capsys):
    exit_code = main(
        [
            "generate",
            "--design",
            "/tmp/design.json",
            "--tenant-repo",
            "/tmp/tenant",
            "--output",
            "/tmp/out",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "not_implemented"
    assert payload["phase"] == "generate"


# --- Logging goes to stderr, never corrupts the --json stdout contract ---------------------


def test_logging_never_pollutes_the_json_stdout_contract(capsys):
    # main() configures logging fresh on every call, so this doesn't depend on ordering with
    # other tests. Real assertion: stdout, byte for byte, is valid JSON and nothing else -- a
    # log line leaking onto stdout would break any caller (chiefly control-plane) parsing this
    # CLI's output programmatically, per cli.py's own docstring contract.
    main(
        [
            "design",
            "--programs",
            "CBACT04C",
            "--tenant-repo",
            "/tmp/tenant",
            "--output",
            "/tmp/out",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    json.loads(captured.out)  # raises if stdout is not exactly one clean JSON value


def test_logging_writes_invocation_lifecycle_to_stderr(capsys):
    main(
        [
            "design",
            "--programs",
            "CBACT04C",
            "--tenant-repo",
            "/tmp/tenant",
            "--output",
            "/tmp/out",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert "invocation started" in captured.err
    assert "command=design" in captured.err
    assert "invocation finished" in captured.err
    assert "status=not_implemented" in captured.err
