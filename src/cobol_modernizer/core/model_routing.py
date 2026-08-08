"""Per-node, per-complexity-tier model routing (ADR-0004, amended by ADR-0014).

ADR-0004 chose a static per-node lookup over "a dynamic routing engine that scores anything at
runtime", and that choice still holds -- this module still resolves a config entry and nothing
more. What ADR-0014 changed is the *key*: `(node, tier)` instead of `(node)`, where `tier` comes
from `core/complexity.py`'s deterministic pre-call measurement. There is still no scoring at
runtime, no probe call, and emphatically no model call to decide which model to call.

The reason for the change is measured, not theoretical: a live four-program run showed `CBCUS01C`
sending an 11,346-character prompt and `CBTRN02C` sending 81,902 -- a 7x spread that one static
per-node model cannot serve without either overpaying on the small program or under-serving the
large one.

This module does not call a model, does not know what a node does with what it returns, and does
not cache across invocations (each CLI invocation is its own process per ADR-0001).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from cobol_modernizer.core.complexity import ComplexityTier

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

#: Every node must declare every tier. Uniformity is the point: a node missing a tier would either
#: need a silent fallback (which hides a config defect) or would fail only for the one program
#: unlucky enough to land in that band.
REQUIRED_TIERS = frozenset(tier.value for tier in ComplexityTier)

_VALID_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class ModelRoutingConfigError(Exception):
    """`config/model_routing.yaml` is missing, malformed, or missing a required entry.

    Per this repo's established "fail loudly rather than guess" pattern (see
    `tools/pic_mapper.UnsupportedPicConstructError`), a node with no resolvable routing must not
    silently fall back to a hardcoded default -- that would defeat ADR-0004's entire point of
    making model choice a visible, editable config rather than baked into node code.
    """


class RoutingDecision(BaseModel):
    """What one (node, tier) lookup resolves to.

    `max_output_tokens` is a safety ceiling rather than a cost lever -- see the config file's own
    comment. It exists here at all because the previous hardcoded 4096 would have truncated every
    real response measured so far (5.5k-23.4k output tokens), silently on one backend and as a
    parse error on the other.
    """

    node_name: str
    tier: ComplexityTier
    model: str
    effort: str
    max_output_tokens: int


def _validate_entry(node_name: str, tier_name: str, entry: object, config_path: Path) -> None:
    where = f"{node_name}.{tier_name} in {config_path}"
    if not isinstance(entry, dict):
        raise ModelRoutingConfigError(f"{where} must be a mapping; got {type(entry).__name__}")

    model = entry.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ModelRoutingConfigError(f"{where} has a missing or empty 'model': {model!r}")

    effort = entry.get("effort")
    if effort not in _VALID_EFFORTS:
        raise ModelRoutingConfigError(
            f"{where} has an invalid 'effort' {effort!r}; valid values are "
            f"{sorted(_VALID_EFFORTS)}"
        )

    max_output_tokens = entry.get("max_output_tokens")
    if not isinstance(max_output_tokens, int) or isinstance(max_output_tokens, bool):
        raise ModelRoutingConfigError(
            f"{where} has a non-integer 'max_output_tokens': {max_output_tokens!r}"
        )
    if max_output_tokens <= 0:
        raise ModelRoutingConfigError(
            f"{where} has a non-positive 'max_output_tokens': {max_output_tokens}"
        )


def load_model_routing(config_path: Path = _DEFAULT_CONFIG_PATH) -> dict[str, dict[str, dict]]:
    """Load and validate the full `{node: {tier: entry}}` mapping.

    Validates the whole file rather than only the entry being asked for, so a typo in a tier
    nothing happens to hit today still fails on the next invocation instead of lying in wait for
    whichever program first lands in that band.

    Raises:
        ModelRoutingConfigError: the file is missing, is not a YAML mapping, names an unknown
            node, omits a known node, omits a tier, or has an entry with a missing/empty `model`,
            an `effort` outside the valid set, or a non-positive integer `max_output_tokens`.
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
            f"Model routing config at {config_path} must be a mapping of node name to tiered "
            f"routing entries; got {type(parsed).__name__}"
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

    for node_name, tiers in parsed.items():
        if not isinstance(tiers, dict):
            raise ModelRoutingConfigError(
                f"Model routing config at {config_path} maps {node_name!r} to a "
                f"{type(tiers).__name__}, not a mapping of tier name to entry"
            )
        missing_tiers = REQUIRED_TIERS - set(tiers)
        if missing_tiers:
            raise ModelRoutingConfigError(
                f"{node_name!r} in {config_path} is missing tier(s) {sorted(missing_tiers)}"
            )
        unknown_tiers = set(tiers) - REQUIRED_TIERS
        if unknown_tiers:
            raise ModelRoutingConfigError(
                f"{node_name!r} in {config_path} has unknown tier(s) {sorted(unknown_tiers)}; "
                f"valid tiers are {sorted(REQUIRED_TIERS)}"
            )
        for tier_name, entry in tiers.items():
            _validate_entry(node_name, tier_name, entry, config_path)

    return parsed


def resolve_routing(
    node_name: str,
    tier: ComplexityTier = ComplexityTier.COMPLEX,
    *,
    config_path: Path = _DEFAULT_CONFIG_PATH,
) -> RoutingDecision:
    """Resolve the model, effort, and output ceiling for `node_name` at `tier`.

    Args:
        node_name: One of `KNOWN_NODES`.
        tier: Defaults to `COMPLEX` -- the safe end. A caller that cannot measure complexity gets
            the most capable configuration rather than the cheapest, because being wrong toward
            more capability costs money while being wrong toward less costs correctness.
        config_path: Defaults to this repo's real `config/model_routing.yaml`. Overridable for
            tests, which exercise failure paths against fixture configs so a bad fixture can never
            be mistaken for a real-config regression.

    Raises:
        ModelRoutingConfigError: `node_name` isn't known, or the config is
            missing/malformed/incomplete (see `load_model_routing`).
    """
    if node_name not in KNOWN_NODES:
        raise ModelRoutingConfigError(
            f"{node_name!r} is not a known node; known nodes are {sorted(KNOWN_NODES)}"
        )
    routing = load_model_routing(config_path)
    entry = routing[node_name][tier.value]
    return RoutingDecision(
        node_name=node_name,
        tier=tier,
        model=entry["model"],
        effort=entry["effort"],
        max_output_tokens=entry["max_output_tokens"],
    )
