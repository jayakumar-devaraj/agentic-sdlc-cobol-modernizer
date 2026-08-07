"""Regenerates schemas/*.schema.json from this repo's Pydantic contract models (ADR-0008).

Run this after any change to core/contracts.py's models:

    ./.venv/Scripts/python scripts/generate_schemas.py

tests/system/test_schemas.py asserts the committed files match what this produces, so CI fails
if a model changes without a matching regeneration -- see core/schema_export.py for the shared
filename-to-model mapping this script and that test both use.
"""

from __future__ import annotations

from cobol_modernizer.core.schema_export import SCHEMA_EXPORTS, SCHEMAS_DIR, render_schema


def main() -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for filename, model in SCHEMA_EXPORTS.items():
        (SCHEMAS_DIR / filename).write_text(render_schema(model), encoding="utf-8", newline="\n")
        print(f"wrote schemas/{filename}")


if __name__ == "__main__":
    main()
