"""Asserts schemas/*.schema.json exactly match what the live Pydantic models would produce.

Per ADR-0008 decision 4, JSON Schema is generated from core/contracts.py's models, never
hand-maintained separately -- this is what makes that claim checkable rather than aspirational.
A model change without a matching `python scripts/generate_schemas.py` run fails here, in CI, not
by silently drifting until an external consumer (chiefly control-plane, which may not even be
Python) notices its copy of the contract no longer matches reality.
"""

from __future__ import annotations

from cobol_modernizer.core.schema_export import SCHEMA_EXPORTS, SCHEMAS_DIR, render_schema


def test_every_schema_export_has_a_committed_file():
    for filename in SCHEMA_EXPORTS:
        assert (SCHEMAS_DIR / filename).exists(), f"schemas/{filename} is missing -- run scripts/generate_schemas.py"


def test_committed_schemas_match_the_live_pydantic_models():
    stale = []
    for filename, model in SCHEMA_EXPORTS.items():
        committed = (SCHEMAS_DIR / filename).read_text(encoding="utf-8")
        fresh = render_schema(model)
        if committed != fresh:
            stale.append(filename)
    assert stale == [], (
        f"{stale} are out of date relative to core/contracts.py -- run "
        f"`./.venv/Scripts/python scripts/generate_schemas.py` and commit the result"
    )


def test_design_document_schema_documents_the_gate_items_field():
    # A spot check that the committed schema is actually the real DesignDocument schema, not an
    # empty/placeholder file that happens to pass the byte-for-byte check above.
    schema_text = (SCHEMAS_DIR / "design_document.schema.json").read_text(encoding="utf-8")
    assert '"gate_items"' in schema_text
    assert '"unified_design"' in schema_text
    assert "unsupported_construct" in schema_text
