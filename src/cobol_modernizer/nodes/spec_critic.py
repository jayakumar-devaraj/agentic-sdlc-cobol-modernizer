"""`spec_critic` -- the second `design`-phase node: an independent check on `spec_extractor`'s narration.

Per ADR-0001's consequences, `spec_critic`'s confidence score is "the only independent check on
extraction quality" the human-in-the-loop gate sees before a `design.json` gets approved. That
framing shapes two decisions this module makes, both worth stating explicitly (see
`docs/adr/0007-confidence-score-composition-is-deterministic-first.md` for the full reasoning):

1. **Deterministic fidelity checks run before the model is ever asked for an opinion, and their
   findings cannot be overridden by it.** `check_field_reference_fidelity`, `check_paragraph_coverage`,
   and `check_unsupported_constructs_carried_forward` mechanically re-verify that `spec_extractor`'s
   narration actually restated the same deterministic facts it was given -- the same "reproduce
   exactly, never recompute" instruction `prompts/registry/spec_extractor/v1_0_0.md` gives the
   narrating model, now independently checked rather than trusted on faith. If any of these finds a
   real discrepancy, `overall_confidence` is `0.0` regardless of what the critic model's own
   per-rule scores say -- an independent check that a wrong-but-confident narration could talk its
   way past would not be independent at all.
2. **`overall_confidence` is the minimum of the critic model's per-rule scores, not their average.**
   A single badly-supported rule in an otherwise well-supported spec should not be diluted away by
   nine well-supported ones -- the score exists so a human gate can trust "the weakest claim in
   this spec is at least this trustworthy", not "the typical claim is".

Structured output from the critic model (`_parse_rule_confidence`) has no repair-retry loop --
that's Milestone C3's own, separately-scoped piece of work (plan step 35, a repair-retry loop for
`solution_architect`'s `design.json` too). A malformed critic response is a hard failure here, not
guessed past with an empty rule list that would make `overall_confidence` silently meaningless.

Like `spec_extractor`, the live critic model call is injected (`critique`) so this module's tests
exercise every deterministic step -- fidelity checks, prompt construction -- against real data
without a live Anthropic API credential this development environment does not have. See
`docs/qa/verification-report.md` for how that gap is tracked.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from cobol_modernizer.core.complexity import critic_tier
from cobol_modernizer.core.guardrails import prepare_untrusted_cobol_for_prompt
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.model_routing import RoutingDecision, resolve_routing
from cobol_modernizer.core.source_units import iter_source_units
from cobol_modernizer.core.structured_output import strip_code_fence
from cobol_modernizer.nodes.spec_extractor import (
    PicMapping,
    SpecExtractionResult,
    UnsupportedField,
    render_known_facts,
)
from cobol_modernizer.prompts_registry_client.loader import prompt_path
from cobol_modernizer.tools.tenant_repo import resolve_program

logger = logging.getLogger(__name__)

_NODE_NAME = "spec_critic"

_TABLE_ROW_RE = re.compile(
    r"^\|\s*(?P<field>[^|]*?)\s*\|\s*(?P<pic>[^|]*?)\s*\|\s*(?P<java_type>[^|]*?)\s*\|"
    r"\s*(?P<precision>[^|]*?)\s*\|\s*(?P<scale>[^|]*?)\s*\|\s*(?P<signed>[^|]*?)\s*\|\s*$",
    re.MULTILINE,
)
_SEPARATOR_CELL_RE = re.compile(r"^-+$")


class RuleConfidence(BaseModel):
    """One claim from `spec.md`, independently scored against the real COBOL source.

    `rule` is a self-contained restatement of the claim (not just "see spec.md line N") so it's
    identifiable without cross-referencing the narration again. `confidence` is in `[0.0, 1.0]`.
    """

    rule: str
    confidence: float
    rationale: str


class SpecCritiqueParseError(Exception):
    """The critic model's response could not be parsed into structured `RuleConfidence` entries.

    Per the module docstring, there is no repair-retry loop yet (Milestone C3, plan step 35) --
    callers must not catch this and fall back to an empty rule list or a default confidence value,
    either of which would make `overall_confidence` claim independence it doesn't have.
    """


class SpecCritiqueResult(BaseModel):
    """Everything `spec_critic` produces for one program's `spec_extractor` output.

    `overall_confidence` is `0.0` whenever `fidelity_issues` is non-empty, regardless of
    `rule_confidence` -- see the module docstring's decision (1). Otherwise it is the minimum of
    `rule_confidence`'s scores (decision (2)), or `1.0` if the critic model found no checkable
    rules at all (an empty spec has nothing to be unconfident about, which is itself something
    `fidelity_issues`' paragraph-coverage check would already have caught if it were a defect).
    """

    program_name: str
    fidelity_issues: list[str]
    rule_confidence: list[RuleConfidence]
    overall_confidence: float


def check_paragraph_coverage(spec_markdown: str, paragraph_names: list[str]) -> list[str]:
    """Paragraph names from Known Facts that never appear anywhere in the narration.

    A narration that silently drops a paragraph from its "Paragraph flow" section produces an
    incomplete `spec.md` a downstream reader would have no way to know was incomplete without
    this check -- it isn't the model's place to decide a paragraph wasn't worth mentioning.
    """
    return [name for name in paragraph_names if name not in spec_markdown]


def check_unsupported_constructs_carried_forward(
    spec_markdown: str, unsupported_fields: list[UnsupportedField]
) -> list[str]:
    """Unsupported-field names from Known Facts that never appear anywhere in the narration.

    Per ADR-0002/ADR-0006, every flagged field is a real human-in-the-loop gate item -- a
    narration that drops one from its "Flagged for human review" section would silently defeat
    the reason that field was flagged in the first place.

    This is a plain substring check, same as `check_paragraph_coverage` -- it can under-report:
    `UnsupportedField.reason` embeds `pic_mapper`'s exception text, which for a `REDEFINES` group
    includes every sibling field's own raw declaration line (`cobol_parser`'s `sibling_text`), so
    one flagged field's name can coincidentally appear inside *another* flagged field's own reason
    text even if the narration never mentions it directly. Accepted as a known limitation of a
    cheap, real check, not silently assumed away -- see `tests/system/test_spec_critic.py`.
    """
    missing = []
    for field in unsupported_fields:
        if field.field_name and field.field_name not in spec_markdown:
            missing.append(field.field_name)
    return missing


def _parse_known_facts_field_table(spec_markdown: str) -> dict[str, tuple[str, str, str]]:
    """Parse every real field row out of `spec_markdown`'s Field Reference table.

    Returns `{field_name: (java_type, precision_str, scale_str)}`. Header and separator rows
    (`| Field | ... |`, `|---|---|`) are skipped; a table this permissive regex cannot find at all
    simply yields an empty dict, which `check_field_reference_fidelity` then reports as every real
    field being missing -- a real, honest finding, not a parsing failure to hide.
    """
    table: dict[str, tuple[str, str, str]] = {}
    for match in _TABLE_ROW_RE.finditer(spec_markdown):
        field = match.group("field").strip()
        if not field or field.lower() == "field" or _SEPARATOR_CELL_RE.match(field):
            continue
        table[field.upper()] = (
            match.group("java_type").strip(),
            match.group("precision").strip(),
            match.group("scale").strip(),
        )
    return table


def check_field_reference_fidelity(
    spec_markdown: str, field_mappings: list[PicMapping]
) -> list[str]:
    """Cross-check `spec_markdown`'s Field Reference table against the real deterministic mapping.

    The prompt instructs the model to "restate the Known Facts field table as-is... never
    re-derive it" -- this is the mechanical proof that instruction was followed, not trusted on
    faith. A model that silently altered a precision/scale value while restating the table would
    defeat this repo's zero-data-drift claim in the one place it's supposed to be narration-proof.

    `FILLER` fields are skipped by name (`pic_mapper.map_pic_clause` always names them literally
    `"FILLER"` -- it re-derives a field's name straight from its own declaration text and has no
    notion of `cobol_parser.FieldDeclaration.is_filler`'s `field_name=None` convention, so
    `field_name is None` never actually happens here). Real `CBACT04C` data has five distinct
    `FILLER` fields across its copybooks, all sharing that one name -- comparing them by name
    alone would silently check every one against whichever row happened to be parsed last from
    the narration, which is not a meaningful per-field check.
    """
    narrated = _parse_known_facts_field_table(spec_markdown)
    issues: list[str] = []

    for mapping in field_mappings:
        if mapping.field_name is None or mapping.field_name == "FILLER":
            continue
        expected_precision = str(mapping.precision) if mapping.precision is not None else "-"
        expected_scale = str(mapping.scale) if mapping.scale is not None else "-"

        actual = narrated.get(mapping.field_name)
        if actual is None:
            issues.append(f"field {mapping.field_name!r} missing from spec.md's Field Reference table")
            continue

        actual_java_type, actual_precision, actual_scale = actual
        if (actual_java_type, actual_precision, actual_scale) != (
            mapping.java_type,
            expected_precision,
            expected_scale,
        ):
            issues.append(
                f"field {mapping.field_name!r} narrated as (java_type={actual_java_type!r}, "
                f"precision={actual_precision!r}, scale={actual_scale!r}) but pic_mapper computed "
                f"(java_type={mapping.java_type!r}, precision={expected_precision!r}, "
                f"scale={expected_scale!r})"
            )

    return issues


def compute_fidelity_issues(extraction: SpecExtractionResult) -> list[str]:
    """Run every deterministic fidelity check and combine their findings, in a stable order."""
    issues: list[str] = []
    issues += [
        f"paragraph {name!r} missing from narration"
        for name in check_paragraph_coverage(extraction.spec_markdown, extraction.paragraph_names)
    ]
    issues += check_field_reference_fidelity(extraction.spec_markdown, extraction.field_mappings)
    issues += [
        f"unsupported field {name!r} missing from 'Flagged for human review' section"
        for name in check_unsupported_constructs_carried_forward(
            extraction.spec_markdown, extraction.unsupported_fields
        )
    ]
    return issues


def build_critique_prompt(worktree_root: Path, extraction: SpecExtractionResult) -> str:
    """Build the user-turn prompt content: Known Facts, then source, then the narration to judge.

    **Order is load-bearing and deliberate (ADR-0017), not incidental.** The leading blocks are
    byte-identical to what `spec_extractor` was given for the same program -- measured at 74,230
    of 74,303 characters for `CBACT04C`, 99.9% -- while the trailing narration is the only part
    unique to this call. Stable-prefix-first is what makes that shared span a *prefix* rather than
    a suffix, which is the precondition for ever serving it from cache instead of paying full
    input price twice within seconds. The previous order put the volatile narration first, so no
    amount of cache configuration could have helped.

    Caching is not claimed here and no `cache_control` is set -- see ADR-0017 for why that half was
    measured and dropped. This function only stops the ordering from being the blocker.

    `prompts/registry/spec_critic/v1_0_0.md` states this same order to the model and **must stay in
    step with it**: a prompt that tells the critic to expect the narration first while the payload
    delivers it last is worse than either order chosen consistently.

    Re-resolves the program from `worktree_root` rather than requiring the caller to keep the
    original `ResolvedProgram` around -- `spec_critic` can run against a `SpecExtractionResult`
    that was persisted and reloaded, not just one still held in the same process (consistent with
    ADR-0001: nothing here depends on in-process state surviving between calls).
    """
    resolved = resolve_program(worktree_root, extraction.program_name)

    known_facts = render_known_facts(
        extraction.program_name,
        extraction.paragraph_names,
        extraction.field_mappings,
        extraction.unsupported_fields,
    )

    wrapped_sections = [
        prepare_untrusted_cobol_for_prompt(source_text, source_label=source_label).wrapped_text
        for source_label, source_text in iter_source_units(resolved)
    ]

    return (
        known_facts
        + "\n\n"
        + "\n\n".join(wrapped_sections)
        + f"\n\n# spec.md under review for {extraction.program_name}\n\n{extraction.spec_markdown}"
    )


def _parse_rule_confidence(raw_response: str) -> list[RuleConfidence]:
    """Parse the critic model's JSON response into structured `RuleConfidence` entries.

    Raises:
        SpecCritiqueParseError: the response isn't valid JSON, isn't a JSON array, or an entry is
            missing a required field or has a `confidence` outside `[0.0, 1.0]`. No repair-retry
            -- see the module docstring.
    """
    candidate = strip_code_fence(raw_response)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SpecCritiqueParseError(
            f"spec_critic response is not valid JSON: {exc}. Raw response: {raw_response!r}"
        ) from None

    if not isinstance(parsed, list):
        raise SpecCritiqueParseError(
            f"spec_critic response must be a JSON array; got {type(parsed).__name__}: {raw_response!r}"
        )

    entries: list[RuleConfidence] = []
    for item in parsed:
        if not isinstance(item, dict) or not {"rule", "confidence", "rationale"} <= set(item):
            raise SpecCritiqueParseError(
                f"spec_critic response entry missing rule/confidence/rationale: {item!r}"
            )
        confidence = item["confidence"]
        if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
            raise SpecCritiqueParseError(
                f"spec_critic response entry has an out-of-range confidence: {item!r}"
            )
        entries.append(
            RuleConfidence(rule=item["rule"], confidence=float(confidence), rationale=item["rationale"])
        )

    return entries


#: `(model, system_prompt, user_content) -> raw response text`. Injected so `critique_spec`'s
#: tests exercise every deterministic step above without a live model credential.
CritiqueFn = Callable[[RoutingDecision, str, str], str]


def _default_critique(routing: RoutingDecision, system_prompt: str, user_content: str) -> str:
    """Call a real model through `core/model_client.py` (ADR-0013), which owns backend choice,
    timeout, retry/backoff, and usage capture -- this node does not reimplement any of that."""
    return call_model(
        _NODE_NAME,
        routing.model,
        system_prompt,
        user_content,
        effort=routing.effort,
        max_output_tokens=routing.max_output_tokens,
    ).text


def _load_system_prompt() -> str:
    return prompt_path(_NODE_NAME).read_text(encoding="utf-8")


def critique_spec(
    worktree_root: Path,
    extraction: SpecExtractionResult,
    *,
    model_routing_config: Path | None = None,
    critique: CritiqueFn = _default_critique,
) -> SpecCritiqueResult:
    """Critique one `spec_extractor` output: deterministic fidelity checks, then a model's judgment.

    Args:
        worktree_root: The cloned tenant-repo worktree path -- re-resolves the program's real
            source to build the critique prompt against (see `build_critique_prompt`).
        extraction: `spec_extractor`'s output for the same program.
        model_routing_config: Overrides `core/model_routing.py`'s default config path -- for
            tests only.
        critique: Overrides the default live Anthropic call -- for tests only.

    Raises:
        tenant_repo.TenantRepoFileNotFoundError: the program's source or a copybook is missing.
        parsing.cobol_parser.UnsupportedCopyConstructError: a `COPY ... REPLACING` was found.
        core.guardrails.DelimiterForgeryError: the source contains this repo's prompt delimiter.
        core.model_routing.ModelRoutingConfigError: the config is missing/malformed/incomplete.
        SpecCritiqueParseError: the critic model's response isn't valid structured JSON.

    None of these are caught here -- each is unambiguous enough to fail loudly, consistent with
    `nodes/spec_extractor.py`'s `extract_spec`.
    """
    fidelity_issues = compute_fidelity_issues(extraction)
    user_content = build_critique_prompt(worktree_root, extraction)
    system_prompt = _load_system_prompt()

    # The cheap path here is a consequence of ADR-0007, not a heuristic: when a deterministic
    # fidelity issue has already been proven, `overall_confidence` is forced to 0.0 below
    # regardless of what this call returns, so no model at any price can change the number the
    # gate keys on. See core.complexity.critic_tier.
    tier = critic_tier(
        extraction.complexity.tier, has_deterministic_fidelity_issues=bool(fidelity_issues)
    )
    routing_kwargs = {} if model_routing_config is None else {"config_path": model_routing_config}
    routing = resolve_routing(_NODE_NAME, tier, **routing_kwargs)
    logger.info(
        "spec_critic routing: program=%s tier=%s model=%s effort=%s fidelity_issues=%d",
        extraction.program_name, routing.tier.value, routing.model, routing.effort,
        len(fidelity_issues),
    )
    raw_response = critique(routing, system_prompt, user_content)
    rule_confidence = _parse_rule_confidence(raw_response)

    if fidelity_issues:
        overall_confidence = 0.0
    elif rule_confidence:
        overall_confidence = min(entry.confidence for entry in rule_confidence)
    else:
        overall_confidence = 1.0

    return SpecCritiqueResult(
        program_name=extraction.program_name,
        fidelity_issues=fidelity_issues,
        rule_confidence=rule_confidence,
        overall_confidence=overall_confidence,
    )
