"""One shared iteration order over a resolved program's source, used by every prompt-building node.

Both `nodes/spec_extractor.py` and `nodes/spec_critic.py` need the same thing: the program's own
source, followed by every copybook it `COPY`s, each labeled and in a stable order -- so that
prompt sections, provenance labels (`UnsupportedField.source_label`), and guardrail-wrapped blocks
all agree on what "the program itself" vs. "a specific named copybook" means. This was originally
private to `spec_extractor.py`; pulled out here once a second real caller needed the identical
behavior, not preemptively.
"""

from __future__ import annotations

from cobol_modernizer.tools.tenant_repo import ResolvedProgram


def iter_source_units(resolved: ResolvedProgram) -> list[tuple[str, str]]:
    """`(source_label, source_text)` for the program itself, then every copybook it `COPY`s.

    Order matches the program's own `COPY` order (as `tenant_repo.resolve_program` preserves it),
    so every caller iterating this list gets the same stable, deterministic, source-order sequence
    -- the same order used for field extraction, prompt construction, and re-verification, which
    is what lets provenance stay traceable to "the program itself" vs. a specific named copybook.
    """
    units = [(resolved.program_name, resolved.source_text)]
    for statement in resolved.copy_statements:
        units.append((statement.copybook_name, resolved.copybook_sources[statement.copybook_name]))
    return units
