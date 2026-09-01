"""Maps `schemas/*.schema.json` filenames to the Pydantic models they're generated from.

Per ADR-0008 decision 4: JSON Schema is generated from `core/contracts.py`'s Pydantic models,
never hand-maintained separately -- Pydantic stays the single source of truth (Pillar 15).
`scripts/generate_schemas.py` (a thin CLI wrapper) and `tests/contract/test_schemas.py` (the drift
check) both import `SCHEMA_EXPORTS`/`render_schema` from here rather than each defining their own
copy of "which models get a committed schema file," so the two can't silently disagree about that.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from cobol_modernizer.core.contracts import DesignCliResult, DesignDocument, GenerateCliResult

SCHEMAS_DIR = Path(__file__).resolve().parents[3] / "schemas"

#: filename -> model. Every entry here must have a matching committed file under `schemas/`,
#: verified by `tests/contract/test_schemas.py`.
SCHEMA_EXPORTS: dict[str, type[BaseModel]] = {
    "design_document.schema.json": DesignDocument,
    "design_cli_result.schema.json": DesignCliResult,
    "generate_cli_result.schema.json": GenerateCliResult,
}


def render_schema(model: type[BaseModel]) -> str:
    """Render `model`'s JSON Schema exactly as committed: sorted keys, 2-space indent, trailing newline."""
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
