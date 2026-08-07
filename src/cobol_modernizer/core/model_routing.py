"""Static per-node model routing, per ADR-0004.

Per `docs/adr/0004-a-static-per-node-model-tier-not-a-routing-engine.md`, this repo's five node
types (`spec_extractor`, `spec_critic`, `solution_architect`, `modernization_engineer`,
`build_validator`) each read a fixed model identifier from `config/model_routing.yaml`, once per
invocation -- not a dynamic routing engine that scores anything at runtime. This module is
exactly that lookup and nothing more: it does not call a model, does not know what a node does
with the identifier it returns, and does not cache across invocations (each CLI invocation is its
own process per ADR-0001, so there is no cross-invocation cache to maintain).
"""

from __future__ import annotations

from pathlib import Path

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "model_routing.yaml"

#: The five node types ADR-0004 covers. A config file naming any other key, or missing one of
#: these, is a real configuration defect -- not something to silently ignore or default around.
KNOWN_NODES = frozenset(
    {
        "spec_extractor",
        "spec_critic",
        "solution_architect",
        "modernization_engineer",
        "build_validator",
    }
)


class ModelRoutingConfigError(Exception):
    """`config/model_routing.yaml` is missing, malformed, or missing a required node's entry.

    Per this repo's established "fail loudly rather than guess" pattern (see
    `tools/pic_mapper.UnsupportedPicConstructError`), a node with no resolvable model identifier
    must not silently fall back to a hardcoded default -- that would defeat ADR-0004's entire
    point of making model choice a visible, editable config rather than baked into node code.
    """


def load_model_routing(config_path: Path = _DEFAULT_CONFIG_PATH) -> dict[str, str]:
    """Load and validate the full node-name-to-model-identifier mapping.

    Raises:
        ModelRoutingConfigError: the file is missing, is not a YAML mapping, contains a key that
            isn't one of `KNOWN_NODES`, is missing an entry for one of `KNOWN_NODES`, or maps a
            node to something other than a non-empty string.
    """
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelRoutingConfigError(
            f"Could not read model routing config at {config_path}: {exc}"
        ) from None

    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ModelRoutingConfigError(
            f"Model routing config at {config_path} is not valid YAML: {exc}"
        ) from None

    if not isinstance(parsed, dict):
        raise ModelRoutingConfigError(
            f"Model routing config at {config_path} must be a mapping of node name to model "
            f"identifier; got {type(parsed).__name__}"
        )

    unknown_keys = set(parsed) - KNOWN_NODES
    if unknown_keys:
        raise ModelRoutingConfigError(
            f"Model routing config at {config_path} has unknown node key(s) "
            f"{sorted(unknown_keys)}; known nodes are {sorted(KNOWN_NODES)}"
        )

    missing_keys = KNOWN_NODES - set(parsed)
    if missing_keys:
        raise ModelRoutingConfigError(
            f"Model routing config at {config_path} is missing entries for node(s) "
            f"{sorted(missing_keys)}"
        )

    for node_name, model_identifier in parsed.items():
        if not isinstance(model_identifier, str) or not model_identifier.strip():
            raise ModelRoutingConfigError(
                f"Model routing config at {config_path} maps {node_name!r} to a non-string or "
                f"empty value: {model_identifier!r}"
            )

    return parsed


def resolve_model(node_name: str, *, config_path: Path = _DEFAULT_CONFIG_PATH) -> str:
    """Return the configured model identifier for `node_name`.

    Args:
        node_name: One of `KNOWN_NODES`, e.g. `"spec_extractor"`.
        config_path: Defaults to this repo's own `config/model_routing.yaml`. Overridable for
            tests, which exercise this against fixture config files rather than mutating the
            repo's real one.

    Raises:
        ModelRoutingConfigError: `node_name` isn't one of `KNOWN_NODES`, or the config file
            itself is missing/malformed/incomplete (see `load_model_routing`).
    """
    if node_name not in KNOWN_NODES:
        raise ModelRoutingConfigError(
            f"{node_name!r} is not a known node; known nodes are {sorted(KNOWN_NODES)}"
        )
    routing = load_model_routing(config_path)
    return routing[node_name]
