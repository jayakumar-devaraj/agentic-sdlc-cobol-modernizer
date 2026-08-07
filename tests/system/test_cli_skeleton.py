"""Real tests against the actual CLI skeleton — not placeholders.

Milestone C1 has no pipeline logic yet, so these tests assert what genuinely exists today: the
argument contract and the honest "not implemented" response, not a mocked future behaviour.
"""

from __future__ import annotations

import json

import pytest

from cobol_modernizer.cli import build_parser, main


def test_run_requires_all_three_arguments():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--program", "CBACT04C"])


def test_run_parses_valid_arguments():
    parser = build_parser()
    args = parser.parse_args(
        [
            "run",
            "--program",
            "CBACT04C",
            "--tenant-repo",
            "/tmp/tenant",
            "--output",
            "/tmp/out",
            "--json",
        ]
    )
    assert args.program == "CBACT04C"
    assert args.tenant_repo == "/tmp/tenant"
    assert args.output == "/tmp/out"
    assert args.json is True


def test_run_reports_not_implemented_honestly(capsys):
    exit_code = main(
        ["run", "--program", "CBACT04C", "--tenant-repo", "/tmp/tenant", "--output", "/tmp/out", "--json"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "not_implemented"
    assert payload["program"] == "CBACT04C"
