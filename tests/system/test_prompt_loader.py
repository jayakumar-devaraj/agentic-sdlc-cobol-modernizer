from __future__ import annotations

from cobol_modernizer.prompts_registry_client.loader import (
    DEFAULT_VERSION,
    node_prompt_version,
    prompt_path,
)


def test_prompt_path_resolves_into_registry():
    path = prompt_path("spec_extractor")
    assert path.name == "v1_0_0.md"
    assert path.parent.name == "spec_extractor"
    assert path.exists(), f"expected a real stub file to exist at {path}"


def test_prompt_path_respects_explicit_version():
    path = prompt_path("spec_extractor", version="v2_0_0")
    assert path.name == "v2_0_0.md"


def test_every_node_sends_a_prompt_version_that_exists():
    """The failure mode this closes is silent, not loud.

    A node naming a version whose file was never added does not fail at import or at prompt-build
    time -- it fails at the first live call, which is the one place in this repo that costs money
    to reach. Checked for every node with a registry entry, so the second one to move versions is
    covered by construction rather than by someone remembering.
    """
    for persona in ("spec_extractor", "spec_critic", "solution_architect", "modernization_engineer",
                    "build_validator"):
        version = node_prompt_version(persona)
        path = prompt_path(persona, version)
        assert path.is_file(), f"{persona} names {version}, which is not in the registry"


def test_node_prompt_version_reports_the_version_spec_critic_moved_to():
    """`spec_critic` is the first node to leave `v1_0_0` (ADR-0053), and both files exist.

    Named rather than inferred: a helper that returns the default for everything would satisfy the
    test above completely, and this is what says it reads the node.
    """
    assert node_prompt_version("spec_critic") == "v1_1_0"
    assert prompt_path("spec_critic", "v1_0_0").is_file(), "the superseded version stays readable"
    assert node_prompt_version("spec_extractor") == DEFAULT_VERSION
