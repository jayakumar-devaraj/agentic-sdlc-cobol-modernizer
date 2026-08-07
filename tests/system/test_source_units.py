"""Tests for core/source_units.py against the real CBACT04C fixture and its real copybooks.

Pulled out of `nodes/spec_extractor.py` once `nodes/spec_critic.py` needed the identical
(source_label, source_text) iteration order -- see both modules' docstrings.
"""

from __future__ import annotations

from pathlib import Path

from cobol_modernizer.core.source_units import iter_source_units
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"


def test_program_comes_first_then_copybooks_in_copy_order():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    units = iter_source_units(resolved)
    labels = [label for label, _ in units]
    assert labels == ["CBACT04C", "CVTRA01Y", "CVACT03Y", "CVTRA02Y", "CVACT01Y", "CVTRA05Y"]


def test_each_unit_pairs_the_label_with_its_own_real_source_text():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    units = dict(iter_source_units(resolved))
    assert units["CBACT04C"] == resolved.source_text
    assert units["CVACT01Y"] == resolved.copybook_sources["CVACT01Y"]
    assert "ACCT-CURR-BAL" in units["CVACT01Y"]
