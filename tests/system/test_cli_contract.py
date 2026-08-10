"""The CLI's argument and output contracts, independent of what any subcommand actually does.

Was `test_cli_skeleton.py`. The name stopped being true once `design` became a real pipeline
(ADR-0012) -- what is left here is the part that is genuinely about the CLI surface: argument
parsing, the two-subcommand split (ADR-0003), and the stdout/stderr separation. Each subcommand's
real end-to-end behavior lives in `test_cli_design.py` and `test_cli_generate.py`.

Two subcommands, not one -- see
docs/adr/0003-two-phase-invocation-split-at-the-human-gate.md for why a single `run` command would
be wrong for a repo with no durable state (ADR-0001).
"""

from __future__ import annotations

import argparse
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


# --- The failure path still yields a parseable envelope ---------------------------------------


def test_generate_fails_cleanly_when_the_design_file_is_missing(capsys):
    """Was `test_generate_reports_not_implemented_honestly`, until step 42 implemented it.

    The invocation is unchanged and so is the property worth holding: whatever goes wrong, a caller
    parsing stdout gets one structured object rather than a traceback.
    """
    exit_code = main(
        [
            "generate",
            "--design", "/tmp/does-not-exist.json",
            "--tenant-repo", "/tmp/tenant",
            "--output", "/tmp/out",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["status"] == "error"
    assert payload["phase"] == "generate"
    assert "FileNotFoundError" in payload["detail"]
    assert "Not implemented" not in payload["detail"]


# --- Both phases join the audit chain, not just `design` --------------------------------------
#
# `--run-id` was accepted by `design` and silently absent from `generate` for 27 PRs. Nothing
# failed, because nothing asserted the two subcommands offered the same correlation surface --
# the gap was invisible to a suite that tested each subcommand on its own terms. The parity test
# below is the one that would have caught it, and is deliberately written against the parser
# rather than against a hardcoded flag list so it keeps holding as arguments are added.


def _optional_flags(parser, subcommand: str) -> set[str]:
    """Every long option the given subcommand accepts, `--help` aside."""
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    sub = action.choices[subcommand]
    return {opt for a in sub._actions for opt in a.option_strings if opt != "--help"}


def test_correlation_and_transport_flags_are_offered_by_both_phases():
    parser = build_parser()
    shared = {"--run-id", "--json", "--tenant-repo", "--output"}

    missing_from_design = shared - _optional_flags(parser, "design")
    missing_from_generate = shared - _optional_flags(parser, "generate")

    assert not missing_from_design, f"design is missing {sorted(missing_from_design)}"
    assert not missing_from_generate, f"generate is missing {sorted(missing_from_generate)}"


def test_generate_uses_a_supplied_run_id_verbatim_and_echoes_it(capsys):
    exit_code = main(
        [
            "generate",
            "--design", "/tmp/design.json",
            "--tenant-repo", "/tmp/tenant",
            "--output", "/tmp/out",
            "--run-id", "cp-run-77",
            "--json",
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == 1
    assert json.loads(captured.out)["run_id"] == "cp-run-77"
    # The point of accepting it: control-plane's audit entry and this phase's stderr share an id.
    assert "run_id=cp-run-77" in captured.err


def test_generate_mints_a_run_id_when_none_is_supplied(capsys):
    exit_code = main(
        [
            "generate",
            "--design", "/tmp/design.json",
            "--tenant-repo", "/tmp/tenant",
            "--output", "/tmp/out",
            "--json",
        ]
    )
    captured = capsys.readouterr()
    run_id = json.loads(captured.out)["run_id"]

    assert exit_code == 1
    assert run_id
    assert f"run_id={run_id}" in captured.err


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
