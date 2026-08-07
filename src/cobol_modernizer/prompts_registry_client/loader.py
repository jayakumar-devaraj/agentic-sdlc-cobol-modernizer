"""Loads versioned prompt text from prompts/registry/.

Skeleton only (Milestone C1) — real hierarchical resolution (system > role > task) and semver
lookup land alongside the first node that actually needs it, in Milestone C2. For now this
resolves the exact path a persona + version maps to, nothing more.
"""

from __future__ import annotations

from pathlib import Path

_REGISTRY_ROOT = Path(__file__).resolve().parents[3] / "prompts" / "registry"


def prompt_path(persona: str, version: str = "v1_0_0") -> Path:
    """Return the path to a persona's prompt file for the given version.

    Does not read or parse the file — callers that need the text should read the returned path
    themselves until the real loader (with caching and hierarchical composition) lands in C2.
    """
    return _REGISTRY_ROOT / persona / f"{version}.md"
