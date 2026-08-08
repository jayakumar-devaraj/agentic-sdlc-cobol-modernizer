"""One shared helper for parsing structured JSON out of a model's raw text response.

Originally private to `nodes/spec_critic.py`; pulled out here once `nodes/solution_architect.py`
needed the identical behavior, not preemptively -- the same "second real caller" threshold
`core/source_units.py` and `core/schema_export.py` were already extracted at.
"""

from __future__ import annotations

import re


def strip_code_fence(text: str) -> str:
    """Strip a leading/trailing ``` (optionally ```json) fence, if the model added one anyway.

    Every structured-output prompt in this repo explicitly forbids this, but stripping a
    syntactic wrapper the model added despite that instruction is not "guessing at data" -- the
    JSON payload itself is still parsed and validated as-is afterward; this only removes
    formatting around it.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()
