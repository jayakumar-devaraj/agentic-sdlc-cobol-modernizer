"""Tests for core/model_catalog.py and the real config/model_catalog.yaml (ADR-0015).

The catalog's job is to make three things data instead of assertions: what a model costs, whether
a node has *evidence* it is good enough, and therefore which model gets selected. These tests are
mostly about the gate holding -- a selection function that quietly falls back to an unverified
model would be worse than the hardcoded names it replaced, because the fallback would look like a
decision somebody made.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cobol_modernizer.core.model_catalog import (
    CatalogEntry,
    ModelCatalogError,
    TokenProfile,
    estimated_cost_usd,
    load_catalog,
    select_model,
)
from cobol_modernizer.core.package_data import CONFIG_ROOT

_REAL_CATALOG = CONFIG_ROOT / "model_catalog.yaml"

PROFILE = TokenProfile(typical_input_tokens=36_000, typical_output_tokens=13_000)


def _entry(model_id, in_price, out_price, rank, verified) -> CatalogEntry:
    return CatalogEntry(
        model_id=model_id,
        input_usd_per_mtok=in_price,
        output_usd_per_mtok=out_price,
        capability_rank=rank,
        verified_for=verified,
        price_note="test fixture",
    )


def _write(tmp_path: Path, catalog: dict) -> Path:
    path = tmp_path / "model_catalog.yaml"
    path.write_text(yaml.safe_dump(catalog), encoding="utf-8")
    return path


def _valid_raw() -> dict:
    return {
        "cheap-model": {
            "input_usd_per_mtok": 1.0,
            "output_usd_per_mtok": 5.0,
            "capability_rank": 1,
            "verified_for": ["spec_critic"],
            "price_note": "test",
        }
    }


# --- The real catalog ---------------------------------------------------------------------------


def test_the_real_catalog_loads_and_every_entry_is_priced_and_dated():
    catalog = load_catalog(_REAL_CATALOG)
    assert catalog
    for entry in catalog.values():
        assert entry.input_usd_per_mtok > 0 and entry.output_usd_per_mtok > 0
        # A price with no date or source is one nobody can tell is stale.
        assert entry.price_note.strip()


def test_the_benchmarked_verification_evidence_is_recorded_where_it_was_earned():
    """`verified_for` must reflect what was actually measured, not what seems reasonable.

    Both facts here come from real runs recorded in `docs/qa/verification-report.md`:
    Haiku caught 3/3 planted defects as a critic, and Opus was the only model to identify the
    unreachable branch as an extractor.
    """
    catalog = load_catalog(_REAL_CATALOG)
    assert "spec_critic" in catalog["claude-haiku-4-5-20251001"].verified_for
    assert "spec_extractor" in catalog["claude-opus-5"].verified_for


def test_neither_sonnet_is_verified_for_extraction():
    """Both narrated dead code as live on CBACT04C, 2026-08-08.

    Pinned as a test rather than left as a comment because this is the finding most likely to be
    "optimized away" later by someone reading only the price column: Sonnet 5 is 2.5x cheaper than
    Opus for this node and still not eligible.
    """
    catalog = load_catalog(_REAL_CATALOG)
    assert catalog["claude-sonnet-5"].verified_for == []
    assert catalog["claude-sonnet-4-6"].verified_for == []


def test_sonnet_4_6_is_dominated_by_sonnet_5_on_price_today():
    # The user's question, answered as data: 4.6 is not cheaper, and it is not more capable.
    catalog = load_catalog(_REAL_CATALOG)
    older, newer = catalog["claude-sonnet-4-6"], catalog["claude-sonnet-5"]
    assert newer.input_usd_per_mtok <= older.input_usd_per_mtok
    assert newer.output_usd_per_mtok <= older.output_usd_per_mtok
    assert newer.capability_rank > older.capability_rank
    # The intro rate expires, so the note must say so -- otherwise this test silently becomes a
    # false claim on 2026-09-01.
    assert "2026-08-31" in newer.price_note


# --- Cost model ------------------------------------------------------------------------------


def test_cost_uses_both_input_and_output_price():
    entry = _entry("m", 1.0, 5.0, 1, [])
    profile = TokenProfile(typical_input_tokens=1_000_000, typical_output_tokens=1_000_000)
    assert estimated_cost_usd(entry, profile) == pytest.approx(6.0)


def test_a_model_with_cheap_input_and_dear_output_is_ranked_by_real_cost():
    """Ranking on list input price alone would pick the wrong model.

    Output is ~70% of a real extractor call's cost, so a model that looks cheap on input and is
    expensive on output must lose. This is the reason `TokenProfile` exists at all.
    """
    cheap_in_dear_out = _entry("trap", 0.5, 50.0, 5, ["n"])
    balanced = _entry("balanced", 3.0, 15.0, 5, ["n"])
    catalog = {e.model_id: e for e in (cheap_in_dear_out, balanced)}
    assert select_model(catalog, "n", 1, PROFILE).model_id == "balanced"


# --- The verified_for gate ----------------------------------------------------------------------


def test_an_unverified_model_is_never_selected_however_cheap():
    free_but_unproven = _entry("free", 0.01, 0.01, 9, [])
    verified = _entry("proven", 5.0, 25.0, 4, ["spec_extractor"])
    catalog = {e.model_id: e for e in (free_but_unproven, verified)}
    assert select_model(catalog, "spec_extractor", 1, PROFILE).model_id == "proven"


def test_no_eligible_model_raises_rather_than_falling_back():
    # The failure mode this design exists to prevent: silently routing a node to something nothing
    # has evidence for would look exactly like a decision someone made.
    catalog = {"m": _entry("m", 1.0, 5.0, 1, ["other_node"])}
    with pytest.raises(ModelCatalogError, match="No catalogued model is verified"):
        select_model(catalog, "spec_extractor", 1, PROFILE)


def test_the_error_names_what_is_verified_so_it_is_actionable():
    catalog = {"m": _entry("m", 1.0, 5.0, 1, ["spec_extractor"])}
    with pytest.raises(ModelCatalogError, match="capability_rank"):
        select_model(catalog, "spec_extractor", 99, PROFILE)


def test_capability_rank_gates_independently_of_price():
    cheap_low_rank = _entry("cheap", 1.0, 5.0, 1, ["n"])
    dear_high_rank = _entry("dear", 5.0, 25.0, 4, ["n"])
    catalog = {e.model_id: e for e in (cheap_low_rank, dear_high_rank)}
    assert select_model(catalog, "n", 1, PROFILE).model_id == "cheap"
    assert select_model(catalog, "n", 4, PROFILE).model_id == "dear"


def test_ties_break_deterministically_toward_the_lower_rank():
    # Two runs must never disagree about which model produced an artifact.
    a = _entry("a-model", 1.0, 5.0, 3, ["n"])
    b = _entry("b-model", 1.0, 5.0, 1, ["n"])
    catalog = {e.model_id: e for e in (a, b)}
    assert select_model(catalog, "n", 1, PROFILE).model_id == "b-model"


# --- Malformed catalogs -------------------------------------------------------------------------


def test_missing_catalog_raises(tmp_path):
    with pytest.raises(ModelCatalogError, match="Could not read"):
        load_catalog(tmp_path / "nope.yaml")


def test_empty_catalog_raises(tmp_path):
    path = tmp_path / "model_catalog.yaml"
    path.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ModelCatalogError, match="non-empty mapping"):
        load_catalog(path)


@pytest.mark.parametrize("bad_price", [0, -1, "free", None])
def test_non_positive_price_raises(tmp_path, bad_price):
    raw = _valid_raw()
    raw["cheap-model"]["input_usd_per_mtok"] = bad_price
    with pytest.raises(ModelCatalogError, match="non-positive or non-numeric"):
        load_catalog(_write(tmp_path, raw))


def test_missing_price_note_raises(tmp_path):
    # An undated price is the failure mode that made ADR-0015 necessary in the first place.
    raw = _valid_raw()
    del raw["cheap-model"]["price_note"]
    with pytest.raises(ModelCatalogError, match="no price_note"):
        load_catalog(_write(tmp_path, raw))


def test_verified_for_must_be_a_list_of_node_names(tmp_path):
    raw = _valid_raw()
    raw["cheap-model"]["verified_for"] = "spec_critic"
    with pytest.raises(ModelCatalogError, match="verified_for"):
        load_catalog(_write(tmp_path, raw))
