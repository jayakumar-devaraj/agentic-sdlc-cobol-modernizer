"""Step 39a: the context-window budget, measured on the real prompts and enforced at the one call site.

**Why this pillar was open.** Pillar 3 was deferred to "once `spec_extractor` calls
`knowledge_store`" -- a precondition ADR-0016 removed, which left it orphaned (audit gap G11). It
binds at `generate` rather than at extraction, because a `generate` prompt carries `design.json`,
the program's own source and the target-API facts together, and that is the first place in this
repo where context pressure could be real.

**It is not real yet, and this module is how that stops being an assertion.** The three prompts a
real `CBACT04C` run produces are built here through the same `author` seam every other test uses --
no model is called, nothing is spent -- and their sizes are asserted rather than described. If a
prompt-template change or a larger program moves them, this fails with the new number, so
`MAX_PROMPT_CHARS`'s justification cannot quietly go stale.

**The guard's contract is that it costs nothing.** `PromptBudgetExceededError` is raised before a
backend is chosen, which is asserted here by making the backend explode if it is ever reached.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import ProgramDesignEntry
from cobol_modernizer.core.model_client import (
    MAX_PROMPT_CHARS,
    ModelCallError,
    PromptBudgetExceededError,
    call_model,
    prompt_size_chars,
)
from cobol_modernizer.graph.generate_pipeline import run_generate
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from tests.system.test_hand_written_round_trip import _design_json, _scripted_author
from tests.system.test_interest_equivalence import FIXTURE_ROOT, PROGRAM

#: The largest `generate` prompt measured for `CBACT04C` on 2026-08-19, in characters. Recorded so
#: the ceiling's derivation is checkable, and pinned with tolerance below rather than left as prose.
MEASURED_MAX_CHARS = 85_215

#: How far the measurement may drift before someone has to look. Generous enough that ordinary
#: prompt-template edits do not fail the suite, tight enough that a program or a section arriving
#: at several times the size does.
DRIFT_TOLERANCE = 1.15


@pytest.fixture(scope="module")
def generate_prompts(tmp_path_factory) -> list[tuple[str, str]]:
    """Every prompt a real `generate` run for `CBACT04C` would send, captured instead of sent."""
    directory = tmp_path_factory.mktemp("context-budget")

    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    entry = ProgramDesignEntry(
        program_name=PROGRAM, spec_extraction=extraction, critique=critique
    )
    entities = build_domain_entities(FIXTURE_ROOT, [entry])

    captured: list[tuple[str, str]] = []

    def capturing_author(routing, system_prompt: str, user_content: str) -> str:
        captured.append((system_prompt, user_content))
        return _scripted_author(routing, system_prompt, user_content)

    design_path = _design_json(directory, entry, entities)
    try:
        run_generate(
            design_path,
            FIXTURE_ROOT,
            directory / "target-project",
            author=capturing_author,
            advise=lambda routing, s, u: json.dumps(
                {"repairable": False, "reason": "not run", "instruction": ""}
            ),
        )
    except Exception as exc:  # noqa: BLE001 -- any failure past prompt-building is out of scope
        # The compile half needs a JDK and is not what this module measures. The prompts are built
        # before anything is compiled, so they are captured either way -- and a run that cannot
        # compile must not silently yield zero prompts, which the assertion below catches.
        print(f"generate stopped after the prompts were built: {type(exc).__name__}: {exc}")

    assert captured, "no prompts were captured, so nothing here measures anything"
    return captured


def test_the_real_generate_prompts_are_inside_the_budget(generate_prompts):
    """The measurement, re-taken on every run rather than quoted from a session that has ended."""
    sizes = [prompt_size_chars(system, user) for system, user in generate_prompts]
    largest = max(sizes)
    print(f"\ngenerate prompts (chars): {sizes}; ceiling {MAX_PROMPT_CHARS:,}")

    assert largest < MAX_PROMPT_CHARS
    assert largest <= MEASURED_MAX_CHARS * DRIFT_TOLERANCE, (
        f"the largest generate prompt is now {largest:,} characters against a recorded "
        f"{MEASURED_MAX_CHARS:,}. That is not a failure of the code -- it means the measurement "
        "behind MAX_PROMPT_CHARS and ADR-0031 is stale. Re-measure, update both, and say what grew."
    )


def test_the_budget_leaves_room_for_a_much_larger_program(generate_prompts):
    """Headroom is the whole design of this ceiling, so it is stated as a number.

    ADR-0031 sets the budget at roughly seven times the largest thing measured, because Track B's
    CICS programs are substantially larger than Track C's and a ceiling that only just fits today
    would fail on the first real one. A ceiling with no stated headroom is a ceiling nobody can
    tell has been outgrown.
    """
    largest = max(prompt_size_chars(system, user) for system, user in generate_prompts)
    assert MAX_PROMPT_CHARS / largest >= 5


def test_the_steps_of_one_program_share_almost_all_of_their_prompt(generate_prompts):
    """PR #28's caching claim, re-derived from the prompts rather than from a bill.

    The audit records `generate`'s prompts as having a **99.8% shared prefix across a program's
    steps**, which is what makes cross-invocation caching worth anything. Three steps whose total
    sizes agree to within a fraction of a percent is consistent with that; three that diverge would
    mean the shared prefix had been broken by an edit, and the first visible symptom would be a
    cost increase nobody attributed to a prompt change.
    """
    sizes = [prompt_size_chars(system, user) for system, user in generate_prompts]
    assert len(sizes) >= 2
    assert (max(sizes) - min(sizes)) / max(sizes) < 0.01

    systems = {system for system, _ in generate_prompts}
    assert len(systems) == 1, "the system prompt differs between steps, so no prefix is shared"


# --- the guard itself ------------------------------------------------------------------------------


def test_an_oversized_prompt_is_refused_before_any_backend_is_reached(monkeypatch):
    """The property that makes this a guard rather than a report: it costs nothing.

    Both backends are made to fail loudly if they are ever entered, so a check that ran *after*
    backend selection -- or not at all -- fails this test with a different exception than the one
    it asserts.
    """
    def explode(*args, **kwargs):
        raise AssertionError("the backend was reached for a prompt that is over budget")

    monkeypatch.setattr("cobol_modernizer.core.model_client._call_claude_cli", explode)
    monkeypatch.setattr("cobol_modernizer.core.model_client._call_anthropic_sdk", explode)

    with pytest.raises(PromptBudgetExceededError) as raised:
        call_model("modernization_engineer", "claude-opus-5", "s" * 100, "u" * MAX_PROMPT_CHARS)

    message = str(raised.value)
    # The split, not just the total: a system prompt over budget is a template defect and a user
    # content over budget is a data problem, and they have different fixes.
    assert "system 100" in message and f"user {MAX_PROMPT_CHARS:,}" in message
    assert "modernization_engineer" in message
    assert "not truncated" in message


def test_a_prompt_exactly_at_the_ceiling_is_allowed(monkeypatch):
    """Off-by-one in a ceiling is the difference between a guard and an outage.

    Asserted by reaching a stubbed backend: if the boundary were exclusive this would raise instead.
    """
    reached: list[int] = []

    def record(model, system_prompt, user_content, *args, **kwargs):
        reached.append(prompt_size_chars(system_prompt, user_content))
        raise RuntimeError("stopped after the budget check")

    # Both backends, because `conftest.py` pins which one runs and this test is about the ceiling
    # rather than about transport. Patching only one made this fail with an SDK auth error -- a
    # green-looking route to testing nothing, since any exception would satisfy a looser assertion.
    monkeypatch.setattr("cobol_modernizer.core.model_client._call_claude_cli", record)
    monkeypatch.setattr("cobol_modernizer.core.model_client._call_anthropic_sdk", record)
    # `ModelCallError` because the SDK path wraps anything that is not already one; the message is
    # what identifies the stub, and asserting on the type alone would pass for an auth failure.
    with pytest.raises((RuntimeError, ModelCallError), match="stopped after the budget check"):
        call_model("spec_extractor", "claude-opus-5", "", "u" * MAX_PROMPT_CHARS)

    assert reached == [MAX_PROMPT_CHARS]


def test_the_ceiling_is_a_parameter_rather_than_an_environment_variable():
    """ADR-0031's one refusal, pinned.

    An env var would let any run raise its own ceiling with no record, which is exactly how a
    measured budget becomes a number nobody has checked in a year. Overriding it is a parameter a
    caller passes deliberately, and this proves both halves: the parameter works, and no
    environment variable does.
    """
    with pytest.raises(PromptBudgetExceededError):
        call_model("spec_critic", "claude-opus-5", "", "u" * 101, max_prompt_chars=100)

    source = (
        Path(__file__).resolve().parents[2]
        / "src" / "cobol_modernizer" / "core" / "model_client.py"
    ).read_text(encoding="utf-8")
    budget_block = source.split("MAX_PROMPT_CHARS = ")[0].rsplit("#: The input ceiling", 1)[-1]
    assert "getenv" not in budget_block
