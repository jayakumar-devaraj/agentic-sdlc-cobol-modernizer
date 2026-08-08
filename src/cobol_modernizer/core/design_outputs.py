"""Where a `design` run's artifacts land on disk, and in what shape.

Separate from `cli.py` because the on-disk layout is a real contract with
`agentic-sdlc-control-plane` -- it is what control-plane's HITL gate reads and what a reviewer
opens -- not an incidental detail of argument parsing. Separate from `graph/design_graph.py`
because that module's job ends at producing a `DesignDocument`; a graph that also decided where
files go would be two responsibilities in one place.

Layout, per ADR-0012:

    <output>/design.json          the full DesignDocument (ADR-0008), gate_items included
    <output>/<PROGRAM>/spec.md    one program's narration, one directory per program

`<PROGRAM>/spec.md` deliberately mirrors `tests/fixtures/golden/CBACT04C/spec.md`, so the
hand-verified golden fixture and a real run's output are the same shape and can be diffed against
each other directly once a live model credential exists to produce the latter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cobol_modernizer.core.contracts import DesignDocument

logger = logging.getLogger(__name__)

DESIGN_JSON_NAME = "design.json"
SPEC_MD_NAME = "spec.md"


def write_design_outputs(document: DesignDocument, output_dir: Path) -> Path:
    """Write `design.json` plus one `spec.md` per program under `output_dir`.

    `design.json` is written with `indent=2` and a trailing newline rather than compact JSON. It is
    read by humans at a review gate and, per ADR-0009's provenance convention, committed alongside
    generated code -- a single-line JSON blob would make every review diff useless. This is the
    opposite choice from the CLI's `--json` stdout, which is compact and machine-only.

    Args:
        document: The completed `DesignDocument`.
        output_dir: Directory to write into. Created if absent, including parents.

    Returns:
        The path of the written `design.json`.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    design_json_path = output_dir / DESIGN_JSON_NAME
    design_json_path.write_text(document.model_dump_json(indent=2) + "\n", encoding="utf-8")
    logger.info(
        "wrote %s (%d program(s), %d gate item(s))",
        design_json_path,
        len(document.programs),
        len(document.gate_items),
    )

    for entry in document.programs:
        program_dir = output_dir / entry.program_name
        program_dir.mkdir(parents=True, exist_ok=True)
        spec_path = program_dir / SPEC_MD_NAME
        spec_path.write_text(entry.spec_extraction.spec_markdown, encoding="utf-8")
        logger.info("wrote %s", spec_path)

    return design_json_path
