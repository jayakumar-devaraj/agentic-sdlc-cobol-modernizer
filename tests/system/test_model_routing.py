"""Tests for core/model_routing.py against the repo's real config/model_routing.yaml.

Per ADR-0004 as amended by ADR-0014, this is still a static lookup rather than a routing engine --
what changed is the key, `(node, tier)` instead of `(node)`. These tests exercise both the real,
checked-in config (the one every node actually reads) and fixture configs for the
malformed/incomplete failure paths, which must never be exercised against the real file: a bad
fixture must never be mistakable for a real-config regression.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.model_routing import (
    KNOWN_NODES,
    REQUIRED_TIERS,
    ModelRoutingConfigError,
    load_model_routing,
    resolve_routing,
)

_REAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_routing.yaml"


def _valid_config() -> dict:
    return {
        node: {
            tier: {"model": "claude-haiku-4-5", "effort": "low", "max_output_tokens": 8000}
            for tier in REQUIRED_TIERS
        }
        for node in KNOWN_NODES
    }


def _write(tmp_path: Path, config: dict) -> Path:
    path = tmp_path / "model_routing.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


# --- Against the real, checked-in config -----------------------------------------------------


def test_real_config_covers_every_node_and_tier():
    routing = load_model_routing(_REAL_CONFIG_PATH)
    assert set(routing) == KNOWN_NODES
    for node, tiers in routing.items():
        assert set(tiers) == REQUIRED_TIERS, node


@pytest.mark.parametrize("node", sorted(KNOWN_NODES))
@pytest.mark.parametrize("tier", list(ComplexityTier))
def test_every_node_tier_pair_resolves_against_the_real_config(node, tier):
    decision = resolve_routing(node, tier, config_path=_REAL_CONFIG_PATH)
    assert decision.model.strip() == decision.model and decision.model
    assert decision.effort in {"low", "medium", "high", "xhigh", "max"}
    assert decision.max_output_tokens > 0
    assert decision.tier is tier


def test_the_cheap_tier_is_actually_cheaper_than_the_expensive_one():
    # The whole point of ADR-0014. If a future config edit made every tier identical, tiering
    # would still "work" and quietly save nothing -- so assert the spread exists for the node
    # where it matters most.
    simple = resolve_routing("spec_extractor", ComplexityTier.SIMPLE, config_path=_REAL_CONFIG_PATH)
    complex_ = resolve_routing(
        "spec_extractor", ComplexityTier.COMPLEX, config_path=_REAL_CONFIG_PATH
    )
    assert simple.model != complex_.model
    assert simple.effort == "low"
    assert complex_.effort == "high"


def test_real_config_ceilings_clear_every_observed_output_length():
    # Measured output from the live four-program run: extractor 5,485-14,350; critic
    # 16,675-23,366; architect 4,964. A ceiling below these truncates -- silently on the CLI
    # backend and as a hard parse error on the SDK backend, which is how the previous hardcoded
    # 4096 would have failed on every single call.
    observed_max = {"spec_extractor": 14_350, "spec_critic": 23_366, "solution_architect": 4_964}
    for node, observed in observed_max.items():
        for tier in ComplexityTier:
            decision = resolve_routing(node, tier, config_path=_REAL_CONFIG_PATH)
            if tier is ComplexityTier.COMPLEX:
                assert decision.max_output_tokens > observed, f"{node}/{tier.value}"


def test_unknown_node_raises():
    with pytest.raises(ModelRoutingConfigError, match="not a known node"):
        resolve_routing("not_a_real_node", config_path=_REAL_CONFIG_PATH)


def test_tier_defaults_to_complex_when_a_caller_cannot_measure():
    # Being wrong toward more capability costs money; being wrong toward less costs correctness.
    default = resolve_routing("spec_extractor", config_path=_REAL_CONFIG_PATH)
    explicit = resolve_routing(
        "spec_extractor", ComplexityTier.COMPLEX, config_path=_REAL_CONFIG_PATH
    )
    assert default == explicit


# --- Failure paths, against fixture configs only ---------------------------------------------


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ModelRoutingConfigError, match="Could not read"):
        load_model_routing(tmp_path / "does-not-exist.yaml")


def test_non_mapping_config_raises(tmp_path):
    path = tmp_path / "model_routing.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ModelRoutingConfigError, match="must be a mapping"):
        load_model_routing(path)


def test_invalid_yaml_raises(tmp_path):
    path = tmp_path / "model_routing.yaml"
    path.write_text("spec_extractor: [unclosed\n", encoding="utf-8")
    with pytest.raises(ModelRoutingConfigError, match="not valid YAML"):
        load_model_routing(path)


def test_unknown_node_key_raises(tmp_path):
    config = _valid_config()
    config["some_made_up_node"] = config["spec_extractor"]
    with pytest.raises(ModelRoutingConfigError, match="unknown node key"):
        load_model_routing(_write(tmp_path, config))


def test_missing_node_entry_raises(tmp_path):
    config = _valid_config()
    del config[min(KNOWN_NODES)]
    with pytest.raises(ModelRoutingConfigError, match="missing entries"):
        load_model_routing(_write(tmp_path, config))


def test_missing_tier_raises(tmp_path):
    # The failure this validation exists for: a node missing one tier would work fine until the
    # first program that happens to land in that band, then fail in production.
    config = _valid_config()
    del config["spec_extractor"]["moderate"]
    with pytest.raises(ModelRoutingConfigError, match="missing tier"):
        load_model_routing(_write(tmp_path, config))


def test_unknown_tier_raises(tmp_path):
    config = _valid_config()
    config["spec_extractor"]["cheapish"] = config["spec_extractor"]["simple"]
    with pytest.raises(ModelRoutingConfigError, match="unknown tier"):
        load_model_routing(_write(tmp_path, config))


def test_node_mapped_to_non_mapping_raises(tmp_path):
    config = _valid_config()
    config["spec_extractor"] = "claude-opus-5"  # the pre-ADR-0014 flat shape
    with pytest.raises(ModelRoutingConfigError, match="not a mapping of tier name"):
        load_model_routing(_write(tmp_path, config))


@pytest.mark.parametrize("bad_model", ["", "   ", 42, None])
def test_missing_or_empty_model_raises(tmp_path, bad_model):
    config = _valid_config()
    config["spec_extractor"]["simple"]["model"] = bad_model
    with pytest.raises(ModelRoutingConfigError, match="missing or empty 'model'"):
        load_model_routing(_write(tmp_path, config))


@pytest.mark.parametrize("bad_effort", ["ludicrous", "", None, 3])
def test_invalid_effort_raises(tmp_path, bad_effort):
    # A typo here would otherwise reach the provider as an unknown effort level, or be dropped.
    config = _valid_config()
    config["spec_extractor"]["simple"]["effort"] = bad_effort
    with pytest.raises(ModelRoutingConfigError, match="invalid 'effort'"):
        load_model_routing(_write(tmp_path, config))


@pytest.mark.parametrize("bad_ceiling", ["8000", 3.5, None])
def test_non_integer_max_output_tokens_raises(tmp_path, bad_ceiling):
    config = _valid_config()
    config["spec_extractor"]["simple"]["max_output_tokens"] = bad_ceiling
    with pytest.raises(ModelRoutingConfigError, match="non-integer 'max_output_tokens'"):
        load_model_routing(_write(tmp_path, config))


@pytest.mark.parametrize("bad_ceiling", [0, -1])
def test_non_positive_max_output_tokens_raises(tmp_path, bad_ceiling):
    config = _valid_config()
    config["spec_extractor"]["simple"]["max_output_tokens"] = bad_ceiling
    with pytest.raises(ModelRoutingConfigError, match="non-positive 'max_output_tokens'"):
        load_model_routing(_write(tmp_path, config))


def test_a_bad_entry_in_an_unused_tier_still_fails(tmp_path):
    # Validation covers the whole file, not just the entry being asked for -- otherwise a typo in
    # a tier nothing happens to hit today lies in wait for whichever program first lands there.
    config = _valid_config()
    config["build_validator"]["complex"]["effort"] = "ludicrous"
    with pytest.raises(ModelRoutingConfigError, match="invalid 'effort'"):
        resolve_routing("spec_extractor", ComplexityTier.SIMPLE, config_path=_write(tmp_path, config))
