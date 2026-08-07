"""`spec_extractor` -- the first LLM-calling node, parses COBOL structure and narrates `spec.md`.

Per the plan's Milestone C2 gate, this module composes every foundational tool built so far:
`tools/tenant_repo.py` reads a program's real source plus every copybook it `COPY`s;
`parsing/cobol_parser.py` extracts paragraph flow and field declarations; `tools/pic_mapper.py`
maps every field's `PIC` clause to a deterministic Java shape; `core/guardrails.py` wraps the raw
COBOL text as untrusted data before it reaches a prompt; `core/model_routing.py` (ADR-0004)
resolves which model this node calls.

Two design decisions worth stating explicitly, since a reasonable reviewer could dispute either
(see `docs/adr/0006-per-field-construct-isolation-and-provenance-scoping.md`):

1. **A field's `UnsupportedPicConstructError`/`ValueError` is caught per field, not per program.**
   `CBACT04C`'s own `WORKING-STORAGE` genuinely contains two real `REDEFINES` groups
   (`TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY`, `FILLER REDEFINES DB2-FORMAT-TS`) alongside
   dozens of ordinary fields. Per ADR-0002, a `REDEFINES` construct must be detected and routed to
   a human gate, never guessed at -- but that boundary applies to the specific field it appears
   on, not to "give up narrating this program at all". `extract_field_mappings` therefore catches
   the exception per field and records it in `unsupported_fields`, so a genuinely ambiguous field
   never gets a fabricated Java shape while every other, unambiguous field in the same program
   still gets mapped and narrated.
2. **Provenance here is source-label-level (program name vs. copybook name), not line-level.**
   `CLAUDE.md`'s standing provenance goal is that "every generated artifact... must trace back to
   the exact COBOL source line it was derived from" -- true line-level tracing would require
   `parsing/cobol_parser.py` to carry line numbers through field/paragraph extraction, which it
   does not do today. Extending an already-merged, independently-tested module's public contract
   is out of scope for this node; every fact this module emits is attributed to the exact source
   unit (program or named copybook) it came from, which is real, checkable provenance -- just not
   line-precise yet.

The actual model call (`narrate`) is the one part of this module that is not independently
testable against real infrastructure the way `knowledge_store.py`'s Postgres tests are: it needs
a live Anthropic API credential this development environment does not have. Every deterministic
step upstream of that call -- field mapping, paragraph extraction, prompt construction, guardrail
wrapping -- is exercised by `extract_spec`'s own tests against the real `CBACT04C` fixture and its
real copybooks; `narrate` is injected so those tests never depend on network access, and the
default implementation (`_default_narrate`) is a thin, direct `anthropic` SDK call with nothing
left to unit-test once the wiring above it is proven correct. See `docs/qa/verification-report.md`
for how this gap is tracked, not silently assumed away.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import anthropic
from pydantic import BaseModel

from cobol_modernizer.core.guardrails import InjectionFlag, prepare_untrusted_cobol_for_prompt
from cobol_modernizer.core.model_routing import resolve_model
from cobol_modernizer.core.source_units import iter_source_units
from cobol_modernizer.parsing.cobol_parser import (
    Paragraph,
    extract_paragraphs,
    extract_working_storage_fields,
)
from cobol_modernizer.prompts_registry_client.loader import prompt_path
from cobol_modernizer.tools.pic_mapper import (
    PicMapping,
    UnsupportedPicConstructError,
    map_pic_clause,
)
from cobol_modernizer.tools.tenant_repo import ResolvedProgram, resolve_program

_NODE_NAME = "spec_extractor"


class UnsupportedField(BaseModel):
    """One field declaration `pic_mapper` refused to map deterministically.

    Per ADR-0002, `REDEFINES`/`OCCURS ... DEPENDING ON` (`UnsupportedPicConstructError`) and any
    `PIC` clause `pic_mapper` cannot resolve without guessing (`ValueError`) are both caught here,
    one field at a time -- see the module docstring. `reason` is the caught exception's own
    message, verbatim, so a human reviewing this as a gate item sees exactly why without
    re-deriving it themselves.
    """

    source_label: str
    field_name: str | None
    raw_text: str
    reason: str


class SpecExtractionResult(BaseModel):
    """Everything `spec_extractor` produces for one program.

    `spec_markdown` is the model's narration; every other field is deterministic and independent
    of it -- a caller that only needs the field/paragraph facts (e.g. `spec_critic`, checking
    extraction quality) does not need to re-derive them from `spec_markdown` prose.
    """

    program_name: str
    paragraph_names: list[str]
    field_mappings: list[PicMapping]
    unsupported_fields: list[UnsupportedField]
    injection_flags: list[InjectionFlag]
    spec_markdown: str


def extract_field_mappings(
    resolved: ResolvedProgram,
) -> tuple[list[PicMapping], list[UnsupportedField]]:
    """Map every field in the program's own source and every copybook it `COPY`s.

    A field declaration with no `PIC`/`PICTURE` token of its own (a group/record header, e.g.
    `01 ACCOUNT-RECORD.` or a bare `01 FILLER REDEFINES DB2-FORMAT-TS.`) is structural, not a leaf
    field to map -- it is skipped rather than sent to `pic_mapper.map_pic_clause`, which would
    otherwise raise `ValueError` for having no `PIC` clause to find. This is a simple substring
    check, not a duplicate of `pic_mapper`'s own `PIC` regex -- `pic_mapper` remains the single
    source of truth for whether a clause is actually resolvable.

    Returns:
        `(field_mappings, unsupported_fields)` -- every leaf field with a `PIC` clause ends up in
        exactly one of the two lists, never silently dropped.
    """
    mappings: list[PicMapping] = []
    unsupported: list[UnsupportedField] = []

    for source_label, source_text in iter_source_units(resolved):
        for field in extract_working_storage_fields(source_text):
            if "PIC" not in field.raw_text.upper():
                continue
            try:
                mapping = map_pic_clause(field.raw_text, adjacent_text=field.sibling_text)
            except (UnsupportedPicConstructError, ValueError) as exc:
                unsupported.append(
                    UnsupportedField(
                        source_label=source_label,
                        field_name=field.name,
                        raw_text=field.raw_text.strip(),
                        reason=str(exc),
                    )
                )
                continue
            mappings.append(mapping)

    return mappings, unsupported


def render_known_facts(
    program_name: str,
    paragraph_names: list[str],
    field_mappings: list[PicMapping],
    unsupported_fields: list[UnsupportedField],
) -> str:
    """Render the deterministic facts block the prompt's system instructions call "Known Facts".

    Public (not `_`-prefixed) so `nodes/spec_critic.py` can re-derive the exact same Known Facts
    block a narration was built against from `SpecExtractionResult`'s own fields, rather than a
    second, parallel implementation that could silently drift out of sync with what
    `spec_extractor` actually sent the model.
    """
    lines = [f"# Known Facts for {program_name}", "", "## Paragraph flow (source order)"]
    for name in paragraph_names:
        lines.append(f"- {name}")

    lines += [
        "",
        "## Field reference (deterministic, pic_mapper output -- reproduce exactly, never recompute)",
        "| Field | PIC | Java type | Precision | Scale | Signed |",
        "|---|---|---|---|---|---|",
    ]
    for mapping in field_mappings:
        precision = mapping.precision if mapping.precision is not None else "-"
        scale = mapping.scale if mapping.scale is not None else "-"
        lines.append(
            f"| {mapping.field_name or '(unnamed)'} | {mapping.raw_pic} | {mapping.java_type} "
            f"| {precision} | {scale} | {mapping.signed} |"
        )

    if unsupported_fields:
        lines += ["", "## Unsupported constructs (flag for human review, do not guess)"]
        for field in unsupported_fields:
            lines.append(f"- `{field.field_name or '(unnamed)'}` in {field.source_label}: {field.reason}")

    return "\n".join(lines)


def build_prompt(
    resolved: ResolvedProgram,
    paragraphs: list[Paragraph],
    field_mappings: list[PicMapping],
    unsupported_fields: list[UnsupportedField],
) -> tuple[str, list[InjectionFlag]]:
    """Build the user-turn prompt content: Known Facts followed by every wrapped, untrusted source unit.

    Delimiter-forgery detection (`core.guardrails.DelimiterForgeryError`) is not caught here --
    per that module's docstring, it is an unambiguous hard failure that must propagate, not be
    silently worked around. Injection-phrase heuristic flags, in contrast, are collected and
    returned rather than acted on; the caller (or a future human-in-the-loop gate) weighs them.
    """
    known_facts = render_known_facts(
        resolved.program_name,
        [paragraph.name for paragraph in paragraphs],
        field_mappings,
        unsupported_fields,
    )

    wrapped_sections: list[str] = []
    injection_flags: list[InjectionFlag] = []
    for source_label, source_text in iter_source_units(resolved):
        result = prepare_untrusted_cobol_for_prompt(source_text, source_label=source_label)
        wrapped_sections.append(result.wrapped_text)
        injection_flags.extend(result.injection_flags)

    user_content = known_facts + "\n\n" + "\n\n".join(wrapped_sections)
    return user_content, injection_flags


#: `(model, system_prompt, user_content) -> narration text`. Injected so `extract_spec`'s tests
#: exercise every deterministic step above without a live model credential -- see module docstring.
NarrateFn = Callable[[str, str, str], str]


def _default_narrate(model: str, system_prompt: str, user_content: str) -> str:
    """Call the real Anthropic API. Not exercised by this repo's own tests -- see module docstring."""
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_content}],
    )
    return "".join(block.text for block in response.content if block.type == "text")


def _load_system_prompt() -> str:
    return prompt_path(_NODE_NAME).read_text(encoding="utf-8")


def extract_spec(
    worktree_root: Path,
    program_name: str,
    *,
    model_routing_config: Path | None = None,
    narrate: NarrateFn = _default_narrate,
) -> SpecExtractionResult:
    """Extract one program's `spec.md` content: parse, map, guardrail-wrap, then narrate.

    Args:
        worktree_root: The cloned tenant-repo worktree path (`tools/tenant_repo.py`'s contract).
        program_name: A COBOL program name, e.g. `"CBACT04C"`.
        model_routing_config: Overrides `core/model_routing.py`'s default `config/model_routing.yaml`
            path -- for tests only; real callers should leave this unset.
        narrate: Overrides the default live Anthropic call -- for tests only; real callers should
            leave this unset.

    Raises:
        tenant_repo.TenantRepoFileNotFoundError: the program's source or a copybook it `COPY`s is
            missing from the worktree.
        parsing.cobol_parser.UnsupportedCopyConstructError: a `COPY ... REPLACING` was found.
        core.guardrails.DelimiterForgeryError: the source contains this repo's own prompt-wrapping
            delimiter.
        core.model_routing.ModelRoutingConfigError: `config/model_routing.yaml` is missing,
            malformed, or missing the `spec_extractor` entry.

    None of these are caught here -- per this repo's established pattern, each is unambiguous
    enough to fail loudly and route to a human-in-the-loop gate, not to be swallowed and guessed
    past.
    """
    resolved = resolve_program(worktree_root, program_name)
    paragraphs = extract_paragraphs(resolved.source_text)
    field_mappings, unsupported_fields = extract_field_mappings(resolved)
    user_content, injection_flags = build_prompt(resolved, paragraphs, field_mappings, unsupported_fields)

    system_prompt = _load_system_prompt()
    model = (
        resolve_model(_NODE_NAME)
        if model_routing_config is None
        else resolve_model(_NODE_NAME, config_path=model_routing_config)
    )
    spec_markdown = narrate(model, system_prompt, user_content)

    return SpecExtractionResult(
        program_name=program_name,
        paragraph_names=[paragraph.name for paragraph in paragraphs],
        field_mappings=field_mappings,
        unsupported_fields=unsupported_fields,
        injection_flags=injection_flags,
        spec_markdown=spec_markdown,
    )
