"""Tests for core/model_routing.py against the repo's real config/model_routing.yaml.

Per ADR-0004, this is a static lookup, not a routing engine -- these tests exercise both the
real, checked-in config (the config every node actually reads) and fixture configs (for the
malformed/incomplete failure paths, which must never be exercised against the real file).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.model_routing import (
    KNOWN_NODES,
    ModelRoutingConfigError,
    load_model_routing,
    resolve_model,
)

_REAL_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "model_routing.yaml"


# --- Against the real, checked-in config -----------------------------------------------------


def test_real_config_maps_every_known_node():
    routing = load_model_routing(_REAL_CONFIG_PATH)
    assert set(routing) == KNOWN_NODES


def test_resolve_model_spec_extractor_against_real_config():
    model = resolve_model("spec_extractor", config_path=_REAL_CONFIG_PATH)
    assert isinstance(model, str)
    assert model.strip() == model
    assert model


def test_resolve_model_unknown_node_raises():
    with pytest.raises(ModelRoutingConfigError, match="not a known node"):
        resolve_model("not_a_real_node", config_path=_REAL_CONFIG_PATH)


# --- Failure paths, against fixture configs only ---------------------------------------------


def test_missing_config_file_raises(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ModelRoutingConfigError, match="Could not read"):
        load_model_routing(missing)


def test_non_mapping_config_raises(tmp_path):
    bad_config = tmp_path / "model_routing.yaml"
    bad_config.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ModelRoutingConfigError, match="must be a mapping"):
        load_model_routing(bad_config)


def test_invalid_yaml_raises(tmp_path):
    bad_config = tmp_path / "model_routing.yaml"
    bad_config.write_text("spec_extractor: [unclosed\n", encoding="utf-8")
    with pytest.raises(ModelRoutingConfigError, match="not valid YAML"):
        load_model_routing(bad_config)


def test_unknown_node_key_raises(tmp_path):
    bad_config = tmp_path / "model_routing.yaml"
    bad_config.write_text(
        "\n".join(f"{node}: claude-haiku-4-5-20251001" for node in KNOWN_NODES)
        + "\nsome_made_up_node: claude-haiku-4-5-20251001\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelRoutingConfigError, match="unknown node key"):
        load_model_routing(bad_config)


def test_missing_node_entry_raises(tmp_path):
    bad_config = tmp_path / "model_routing.yaml"
    incomplete_nodes = sorted(KNOWN_NODES)[:-1]
    bad_config.write_text(
        "\n".join(f"{node}: claude-haiku-4-5-20251001" for node in incomplete_nodes) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ModelRoutingConfigError, match="missing entries"):
        load_model_routing(bad_config)


def test_empty_model_identifier_raises(tmp_path):
    bad_config = tmp_path / "model_routing.yaml"
    ordered_nodes = sorted(KNOWN_NODES)
    lines = [f"{node}: claude-haiku-4-5-20251001" for node in ordered_nodes]
    lines[0] = f"{ordered_nodes[0]}: ''"
    bad_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ModelRoutingConfigError, match="non-string or empty"):
        load_model_routing(bad_config)


def test_non_string_model_identifier_raises(tmp_path):
    bad_config = tmp_path / "model_routing.yaml"
    ordered_nodes = sorted(KNOWN_NODES)
    lines = [f"{node}: claude-haiku-4-5-20251001" for node in ordered_nodes]
    lines[0] = f"{ordered_nodes[0]}: 42"
    bad_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ModelRoutingConfigError, match="non-string or empty"):
        load_model_routing(bad_config)
