from __future__ import annotations

from cobol_modernizer.prompts_registry_client.loader import prompt_path


def test_prompt_path_resolves_into_registry():
    path = prompt_path("spec_extractor")
    assert path.name == "v1_0_0.md"
    assert path.parent.name == "spec_extractor"
    assert path.exists(), f"expected a real stub file to exist at {path}"


def test_prompt_path_respects_explicit_version():
    path = prompt_path("spec_extractor", version="v2_0_0")
    assert path.name == "v2_0_0.md"
