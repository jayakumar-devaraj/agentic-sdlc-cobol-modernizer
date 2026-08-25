"""Loads versioned prompt text from this package's own `data/prompts/registry/`.

Skeleton only (Milestone C1) — real hierarchical resolution (system > role > task) and semver
lookup land alongside the first node that actually needs it, in Milestone C2. For now this
resolves the exact path a persona + version maps to, nothing more.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from cobol_modernizer.core.package_data import PROMPTS_ROOT, require

_REGISTRY_ROOT = PROMPTS_ROOT

#: The version a persona resolves to when its node names none. Every persona started here.
DEFAULT_VERSION = "v1_0_0"


def node_prompt_version(persona: str) -> str:
    """The prompt version `persona`'s node module names, or `DEFAULT_VERSION` if it names none.

    One place answers this so nothing else has to know. A node moving to a new version is a
    two-file change -- the registry entry and the node's own `PROMPT_VERSION` -- and anything
    else that has to send the same system prompt (a test faking the model, a billed benchmark)
    asks here rather than repeating the default. A test that hardcodes `v1_0_0` while the node
    sends something else is measuring a prompt nobody uses.

    Imported lazily: the node modules import this one.
    """
    module = importlib.import_module(f"cobol_modernizer.nodes.{persona}")
    return getattr(module, "PROMPT_VERSION", DEFAULT_VERSION)


def prompt_path(persona: str, version: str = DEFAULT_VERSION) -> Path:
    """Return the path to a persona's prompt file for the given version.

    Does not read or parse the file — callers that need the text should read the returned path
    themselves until the real loader (with caching and hierarchical composition) lands in C2.
    """
    return _REGISTRY_ROOT / persona / f"{version}.md"


def read_prompt(persona: str, version: str = DEFAULT_VERSION) -> str:
    """The persona's prompt text, or `PackageDataMissingError` naming the install.

    Separate from `prompt_path` on purpose. `prompt_path` stays a pure path construction because
    two tests legitimately ask it about files that do not exist -- one probes every node's declared
    version precisely to catch a missing one cheaply, before the first live call that would
    otherwise find it at cost. A constructor that raised would break the question being asked.
    The read is where a missing file means the *install* is wrong rather than the argument.
    """
    return require(
        prompt_path(persona, version), what=f"the {persona} prompt at {version}"
    ).read_text(encoding="utf-8")
