"""End-to-end tests for `cobol-modernizer design`, through the real CLI, graph, and nodes.

The one thing replaced here is the Anthropic SDK client itself -- `anthropic.Anthropic` is patched
so the nodes' own `_default_narrate`/`_default_critique`/`_default_architect` really run, really
resolve their model from `config/model_routing.yaml`, really pass through
`core/model_client.call_model`'s retry and usage-capture path, and really load their registry
system prompts, but the HTTP call at the bottom returns canned content. Everything above that
boundary is production code: argument parsing, run-id handling, the LangGraph run, guardrail
wrapping, fidelity checks, gate-item aggregation, file writing, and the `--json` stdout contract.

These tests run against the `anthropic_sdk` backend, pinned by `tests/conftest.py` -- not because
it is the default (it is not; `claude_cli` is, per ADR-0013) but because it is the backend this
file's fake actually intercepts. See that conftest for the real accident that made pinning
mandatory rather than incidental.

That boundary is deliberately as low as it can go. Patching the nodes' injected callables instead
would have been easier, but `extract_spec(..., narrate=_default_narrate)` binds its default at
definition time, so patching the module attribute would not take effect anyway -- and, more to the
point, it would leave the default call path (the one real callers use, and the only one control-
plane will ever exercise) untested.

The fake dispatches on **system-prompt identity**, comparing against the real registry files
rather than sniffing for substrings. That makes each assertion exact and, as a side effect, proves
every node loaded its own correct prompt -- a mix-up would raise here rather than silently produce
a plausible-looking design.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from cobol_modernizer import cli
from cobol_modernizer.core.contracts import DesignCliResult, DesignDocument
from cobol_modernizer.prompts_registry_client.loader import prompt_path

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
PROGRAMS = ["CBCUS01C", "CBACT01C"]


class _FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeUsage:
    # core/model_client.py records token usage on every call (ADR-0013), so a stand-in response
    # has to carry it. Values are arbitrary; the assertions here are about wiring, not counts.
    input_tokens = 1234
    output_tokens = 56
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeTextBlock(text)]
        self.usage = _FakeUsage()


class _FakeMessages:
    def __init__(self, prompts: dict[str, str], calls: list[tuple[str, str]]) -> None:
        self._prompts = prompts
        self._calls = calls

    def create(self, *, model, max_tokens, system, messages, output_config=None):
        node = next((name for name, text in self._prompts.items() if text == system), None)
        assert node is not None, "a node called the API with a system prompt from no known registry entry"
        user_content = messages[0]["content"]
        effort = (output_config or {}).get("effort")
        self._calls.append((node, model, effort, max_tokens))

        if node == "spec_extractor":
            # The faithful-narration technique: restate the real Known Facts block verbatim, so
            # spec_critic's deterministic fidelity checks pass for the right reason.
            return _FakeResponse(user_content.split("\n\n<untrusted-cobol-source")[0])
        if node == "spec_critic":
            return _FakeResponse(
                json.dumps([{"rule": "representative rule", "confidence": 0.9, "rationale": "ok"}])
            )
        return _FakeResponse(
            json.dumps(
                {
                    "batch_jobs": [
                        {
                            "program_name": name,
                            "job_name": f"{name.lower()}Job",
                            "domain_entities": [],
                            "steps": [],
                        }
                        for name in PROGRAMS
                    ],
                    "rest_endpoints": [],
                }
            )
        )


class _FakeAnthropic:
    prompts: ClassVar[dict[str, str]] = {}
    #: (node, model, effort, max_output_tokens) per call -- effort and the ceiling are recorded
    #: because ADR-0014 makes them part of the routing decision, not incidental request detail.
    calls: ClassVar[list[tuple[str, str, str | None, int]]] = []

    def __init__(self, *args, **kwargs) -> None:
        self.messages = _FakeMessages(self.prompts, self.calls)


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Replace only the SDK client. Returns the (node, model, effort, ceiling) calls actually made."""
    import anthropic

    _FakeAnthropic.prompts = {
        name: prompt_path(name).read_text(encoding="utf-8")
        for name in ("spec_extractor", "spec_critic", "solution_architect")
    }
    _FakeAnthropic.calls = []
    monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
    return _FakeAnthropic.calls


def design_argv(output: Path, *extra: str) -> list[str]:
    return [
        "design",
        "--programs", *PROGRAMS,
        "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(output),
        *extra,
    ]


# --- The happy path -------------------------------------------------------------------------------


def test_design_writes_real_artifacts_and_reports_ok(tmp_path, fake_anthropic, capsys):
    exit_code = cli.main(design_argv(tmp_path, "--json"))
    assert exit_code == 0

    result = DesignCliResult.model_validate_json(capsys.readouterr().out.strip())
    assert result.status == "ok"
    assert result.phase == "design"
    assert result.programs == PROGRAMS

    design_json = tmp_path / "design.json"
    assert Path(result.output_path) == design_json
    assert design_json.exists()

    document = DesignDocument.model_validate_json(design_json.read_text(encoding="utf-8"))
    assert [entry.program_name for entry in document.programs] == PROGRAMS
    assert result.gate_item_count == len(document.gate_items)
    # CBCUS01C's 2 real REDEFINES fields plus CBACT01C's 32 (28 REDEFINES + 4 fixed OCCURS).
    assert result.gate_item_count == 34

    for program in PROGRAMS:
        spec_md = tmp_path / program / "spec.md"
        assert spec_md.exists()
        assert spec_md.read_text(encoding="utf-8").startswith(f"# Known Facts for {program}")


def test_every_node_is_called_through_its_own_registry_prompt(tmp_path, fake_anthropic):
    assert cli.main(design_argv(tmp_path)) == 0

    nodes = [node for node, _model, _effort, _max in fake_anthropic]
    # One extraction and one critique per program, one architect call for the whole run.
    assert nodes.count("spec_extractor") == len(PROGRAMS)
    assert nodes.count("spec_critic") == len(PROGRAMS)
    assert nodes.count("solution_architect") == 1


def test_complexity_still_varies_effort_and_ceiling_per_program(tmp_path, fake_anthropic):
    """ADR-0014 tiering, as it stands after ADR-0015's evidence gate.

    `PROGRAMS` spans both bands on purpose -- `CBCUS01C` measures 11,346 prompt characters and 5
    paragraphs (simple), `CBACT01C` measures 78,647 and 16 (complex).

    **This test used to assert the two programs got different *models*, and no longer does.**
    ADR-0014 routed the simple program to Haiku on the reasoning that a small program is an easy
    task -- but Haiku had never been benchmarked on extraction. When it was (2026-08-08), it
    scored 4/6 on CBACT04C, missing both COMPUTE-without-ROUNDED truncation and the unreachable
    branch. So ADR-0015's `verified_for` gate withdrew that saving: no model except Opus 5 has
    evidence for this node at any tier, and the gate refuses to route on a guess.

    Tiering is still real and still measured here -- effort and the output ceiling both vary. The
    model will vary again as soon as a cheaper model is benchmarked for extraction, with no code
    change: that is the point of computing selection from the catalog.
    """
    assert cli.main(design_argv(tmp_path)) == 0

    extractor_calls = [
        (model, effort, max_tokens)
        for node, model, effort, max_tokens in fake_anthropic
        if node == "spec_extractor"
    ]

    # Only Opus is verified for extraction, so every tier resolves to it -- deliberately, and this
    # is the assertion that would fail if someone re-added a cheaper model without a benchmark.
    assert {model for model, _e, _m in extractor_calls} == {"claude-opus-5"}

    # Tiering still varies the levers it has evidence for.
    assert {effort for _m, effort, _t in extractor_calls} == {"low", "high"}
    assert len({max_tokens for _m, _e, max_tokens in extractor_calls}) == 2

    # The ceiling really reaches the request -- the previous hardcoded 4096 would have truncated
    # every real narration measured so far.
    assert all(max_tokens >= 12_000 for _m, _e, max_tokens in extractor_calls)


def test_the_architect_takes_the_highest_tier_present(tmp_path, fake_anthropic):
    # One call across all programs (ADR-0010), so a run containing one complex program is a
    # complex run -- averaging would let the simple program pull the architect down.
    assert cli.main(design_argv(tmp_path)) == 0
    architect = [c for c in fake_anthropic if c[0] == "solution_architect"]
    assert len(architect) == 1
    _node, model, effort, _max_tokens = architect[0]
    assert model == "claude-opus-5"
    assert effort == "high"


def test_design_json_is_indented_and_newline_terminated_for_review_diffs(tmp_path, fake_anthropic):
    # ADR-0012: design.json is read by a human at a gate and committed alongside generated code,
    # so it is pretty-printed -- the opposite choice from the compact --json stdout below.
    assert cli.main(design_argv(tmp_path)) == 0
    raw = (tmp_path / "design.json").read_text(encoding="utf-8")
    assert raw.endswith("}\n")
    assert "\n  " in raw


def test_the_run_is_byte_reproducible_apart_from_its_timestamp(tmp_path, fake_anthropic):
    first_dir, second_dir = tmp_path / "one", tmp_path / "two"
    assert cli.main(design_argv(first_dir)) == 0
    assert cli.main(design_argv(second_dir)) == 0

    first = json.loads((first_dir / "design.json").read_text(encoding="utf-8"))
    second = json.loads((second_dir / "design.json").read_text(encoding="utf-8"))
    # generated_at is genuinely per-run; everything else must match exactly, which is what makes
    # a design.json diff at a review gate mean something.
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


# --- The --json stdout contract -------------------------------------------------------------------


def test_stdout_carries_exactly_one_json_object_and_logs_go_to_stderr(tmp_path, fake_anthropic, capsys):
    assert cli.main(design_argv(tmp_path, "--json")) == 0

    captured = capsys.readouterr()
    # Exactly one line, exactly one parseable object -- the contract control-plane depends on.
    assert len(captured.out.strip().splitlines()) == 1
    json.loads(captured.out)

    # The run really did log, and all of it went to stderr.
    assert "design: start" in captured.err
    assert "spec_extractor" in captured.err
    assert "solution_architect" in captured.err


def test_without_json_stdout_is_human_text_not_json(tmp_path, fake_anthropic, capsys):
    assert cli.main(design_argv(tmp_path)) == 0
    out = capsys.readouterr().out.strip()
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)
    assert "gate item(s) for review" in out


# --- Run-id correlation ----------------------------------------------------------------------------


def test_a_supplied_run_id_is_used_verbatim_and_echoed(tmp_path, fake_anthropic, capsys):
    assert cli.main(design_argv(tmp_path, "--json", "--run-id", "cp-run-42")) == 0
    captured = capsys.readouterr()
    assert DesignCliResult.model_validate_json(captured.out.strip()).run_id == "cp-run-42"
    # The same id appears in the logs, which is the entire point of accepting it.
    assert "run_id=cp-run-42" in captured.err


def test_a_run_id_is_generated_and_reported_when_none_is_supplied(tmp_path, fake_anthropic, capsys):
    assert cli.main(design_argv(tmp_path, "--json")) == 0
    captured = capsys.readouterr()
    run_id = DesignCliResult.model_validate_json(captured.out.strip()).run_id
    assert run_id
    assert f"run_id={run_id}" in captured.err


# --- The failure path ------------------------------------------------------------------------------


def test_a_missing_program_exits_nonzero_but_still_emits_parseable_json(tmp_path, fake_anthropic, capsys):
    # The case the broad except in cli.py exists for: control-plane must still get a structured
    # reason on stdout, not an unparseable traceback.
    argv = [
        "design",
        "--programs", "CBACT04C", "NOSUCHPGM",
        "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(tmp_path),
        "--json",
    ]
    exit_code = cli.main(argv)
    assert exit_code == 1

    captured = capsys.readouterr()
    result = DesignCliResult.model_validate_json(captured.out.strip())
    assert result.status == "error"
    assert result.gate_item_count == 0
    assert "TenantRepoFileNotFoundError" in result.detail
    assert "NOSUCHPGM" in result.detail
    # The full traceback is still available to a human, on stderr.
    assert "Traceback" in captured.err


def test_a_failed_run_writes_no_partial_design_json(tmp_path, fake_anthropic):
    argv = [
        "design",
        "--programs", "CBACT04C", "NOSUCHPGM",
        "--tenant-repo", str(FIXTURE_ROOT),
        "--output", str(tmp_path),
    ]
    assert cli.main(argv) == 1
    assert not (tmp_path / "design.json").exists()


def test_generate_still_reports_not_implemented(tmp_path, capsys):
    argv = ["generate", "--design", "x.json", "--tenant-repo", str(FIXTURE_ROOT),
            "--output", str(tmp_path), "--json"]
    assert cli.main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "error"
    assert payload["phase"] == "generate"
    assert "Milestone C4" in payload["detail"]
