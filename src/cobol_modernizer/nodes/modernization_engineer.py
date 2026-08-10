"""`modernization_engineer` -- the first `generate`-phase node: one batch step's logic, in Java.

The node's shape is the decision worth reading. `solution_architect` produced a `UnifiedDesign`
whose `domain_entities` are deterministic and whose `batch_jobs` are LLM-authored. This node takes
one `BatchStepDesign` at a time and asks a model for exactly one thing: the statements inside a
`process(...)` method. Everything structural around those statements -- the class, the annotations,
the implements clause, the signature, the imports block, the provenance -- is rendered by
`rendering/java_processor.py` from the same design.

**Why not ask for the file.** Three reasons, in increasing order of importance:

1. A file's worth of scaffolding is a mechanical transform of structured data, and asking a model
   to perform a mechanical transform costs tokens for an answer that can vary between runs.
2. Rendered output is a pure function of `design.json`, so it is reviewable once by reading the
   renderer rather than per-run by reading every generated file. At any real estate size the
   dominant cost of a migration is human review, not inference.
3. A model that writes the whole file decides what the class implements and what it is called.
   Those follow from the design, and a generator free to disagree with the design it was given is
   a generator whose output nobody can check against anything.

**Untrusted input, twice over.** The COBOL paragraph source is untrusted by the rule this repo has
applied since `guardrails.py`: COBOL is data, never instructions, comments included. The `spec.md`
narration is wrapped too, exactly as `solution_architect` wraps it -- prior LLM output that got
something past one guardrail must not arrive at the next one as instructions.

**This node accepts a repair, but does not loop.** `RepairContext` lets a caller say "that did not
compile, here is what the compiler said, try again", and the prompt grows a section for it. The
*deciding* -- whether to retry at all, and how many times -- stays in step 42's loop, because it is
the thing that has to weigh a `build_validator` verdict and a budget. A malformed response still
raises, exactly as in `spec_critic` and `solution_architect`: a response that failed to parse and
code that failed to compile are different failures, and only the second is worth another attempt.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.contracts import BatchStepDesign, DomainEntity, ProgramDesignEntry
from cobol_modernizer.core.guardrails import wrap_untrusted_cobol
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.model_routing import RoutingDecision, resolve_routing
from cobol_modernizer.core.structured_output import strip_code_fence
from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.prompts_registry_client.loader import prompt_path
from cobol_modernizer.rendering.java_processor import render_processor
from cobol_modernizer.rendering.target_api import render_target_api_facts
from cobol_modernizer.tools.local_compiler import CompileDiagnostic
from cobol_modernizer.tools.tenant_repo import ResolvedProgram, resolve_program

logger = logging.getLogger(__name__)

_NODE_NAME = "modernization_engineer"

#: The model returns these three keys and no others are required. `notes` is mandatory rather than
#: optional on purpose: a model that has nothing to flag must say so explicitly, so that an empty
#: caveat is a statement rather than an omission.
_REQUIRED_KEYS = frozenset({"imports", "body", "notes"})

AuthorFn = Callable[[RoutingDecision, str, str], str]


class ModernizationEngineerParseError(Exception):
    """The engineer model's response could not be parsed into a body and an import list.

    Raised for malformed JSON, missing keys, or a non-string body -- never for a body whose *Java*
    is wrong, which is not something this node can judge and is exactly what `build_validator` and
    the compile loop exist for. Keeping those two failure modes distinct matters: one means the
    model broke its contract, the other means it wrote code that does not compile, and they have
    completely different fixes.
    """


@dataclass(frozen=True)
class RepairContext:
    """A failed previous attempt, and what `build_validator` said to do about it.

    Carried rather than reconstructed: the loop already holds the body it wrote and the verdict it
    got, and re-deriving either here would be a second place for them to disagree.
    """

    previous_body: str
    diagnostics: tuple[CompileDiagnostic, ...]
    instruction: str
    attempt: int


@dataclass(frozen=True)
class GeneratedProcessor:
    """One rendered `ItemProcessor`, with the provenance needed to review and audit it."""

    program_name: str
    step_name: str
    class_name: str
    java_source: str
    model: str
    #: Whatever the model could not translate faithfully. Empty string when it flagged nothing.
    #: Surfaced to the caller rather than buried in a code comment so it can become a gate item.
    notes: str


def _to_pascal_case(name: str) -> str:
    """Mechanical, not semantic -- the same posture as `solution_architect`'s own name transforms."""
    parts = [part for chunk in name.split("-") for part in chunk.split("_") if part]
    if len(parts) == 1 and not parts[0].isupper():
        # Already camelCase (`computeMonthlyInterest`); only the first letter needs lifting.
        return parts[0][:1].upper() + parts[0][1:]
    return "".join(part[:1].upper() + part[1:].lower() for part in parts)


def processor_class_name(step: BatchStepDesign) -> str:
    """`computeMonthlyInterest` -> `ComputeMonthlyInterestProcessor`. Deterministic and reversible."""
    base = _to_pascal_case(step.step_name)
    return base if base.endswith("Processor") else f"{base}Processor"


def render_domain_facts(entities: list[DomainEntity]) -> str:
    """The domain records -- identical for every step of a run, so this heads the prompt.

    Field precision and scale are stated because `pic_mapper` computed them. The system prompt
    forbids recomputing them; this is what it is pointing at.
    """
    lines = ["## Known Facts (deterministic, do not recompute)", "", "### Domain records", ""]
    for entity in entities:
        lines.append(f"#### {entity.name} (from copybook {entity.source_copybook})")
        for field in entity.fields:
            shape = f"{field.java_type} {field.java_field_name}()"
            if field.precision is not None:
                sign = "signed" if field.signed else "unsigned"
                shape += f"  // precision {field.precision}, scale {field.scale}, {sign}"
            lines.append(f"- {shape}")
        lines.append("")
    return "\n".join(lines)


def render_program_field_facts(resolved: ResolvedProgram) -> str:
    """The program's own declared fields, with `pic_mapper`'s computed precision and scale.

    **Why this exists** (gap G21). `build_domain_entities` merges **copybook-sourced fields only**
    (ADR-0010), so a program's `WORKING-STORAGE` never reached this prompt -- including
    `WS-MONTHLY-INT`, which is the *receiving* field of `CBACT04C`'s interest `COMPUTE` and
    therefore what sets the result's target precision and scale. A real model call inferred it as
    `precision 11, scale 2`, was right, and said plainly that it was inferring and would rather be
    told. It is right: a target scale is exactly the kind of number this repo does not let a model
    decide, for the same reason `pic_mapper` may not call one -- a wrong scale on a currency field
    looks exactly like a right one.

    **Rendered as COBOL declarations, not as Java accessors, on purpose.** These are program-local
    variables, not components of any domain record. Rendering them in the accessor shape the domain
    records use would invite calls to methods that do not exist -- trading a narrated scale for a
    hallucinated getter, which is a worse bargain than the one being fixed.

    Resolves ADR-0010's invariant rather than weakening it: entities are still merged by copybook
    name only, and this is a separate, per-program section beside them.
    """
    by_source = group_field_mappings_by_source(resolved)
    mapped, _unsupported = by_source.get(resolved.program_name, ([], []))
    if not mapped:
        return ""

    lines = [
        f"### Program-local fields declared in {resolved.program_name}",
        "",
        "Computed by pic_mapper from the real PIC clauses, exactly like the domain records above.",
        "**These are the program's own variables -- they are not components of any record and have",
        "no accessor.** They are listed so that a COMPUTE's receiving field has a stated precision",
        "and scale rather than one read off a PIC clause in the untrusted narration.",
        "",
    ]
    for field_mapping in mapped:
        shape = f"- {field_mapping.field_name} -- {field_mapping.java_type}"
        if field_mapping.precision is not None:
            sign = "signed" if field_mapping.signed else "unsigned"
            shape += f", precision {field_mapping.precision}, scale {field_mapping.scale}, {sign}"
        lines.append(shape)
    return "\n".join(lines)


def render_step_facts(
    step: BatchStepDesign, *, input_type: str, output_type: str
) -> str:
    """The one part of the prompt that changes per step -- so it goes last, not first."""
    return "\n".join(
        [
            "## The step you are implementing",
            "",
            f"- Step: {step.step_name} ({step.role})",
            f"- Description: {step.description}",
            f"- Source COBOL paragraph(s): {', '.join(step.source_paragraphs) or '(none recorded)'}",
            f"- Write the body of: {output_type} process({input_type} item)",
        ]
    )


def render_repair_facts(repair: RepairContext) -> str:
    """The previous attempt and why it failed -- appended last, after the step it belongs to.

    Position matters for the same reason the step facts go last: this is the *most* variable part
    of the prompt, changing on every attempt of every step. Putting it after the step keeps the
    cached prefix intact across a heal loop, so three attempts share everything up to the tail.

    The previous body is included verbatim. Asking for a repair without showing what is being
    repaired invites a rewrite from scratch, which throws away whatever the first attempt got right
    and makes each attempt independent rather than cumulative.
    """
    lines = [
        "",
        f"## Repair attempt {repair.attempt}",
        "",
        "Your previous statements did not compile. Rewrite them.",
        "",
        "### What you wrote",
        "",
        "```java",
        repair.previous_body.strip(),
        "```",
        "",
        "### What the compiler said",
        "",
    ]
    lines += [diagnostic.render() for diagnostic in repair.diagnostics]
    lines += [
        "",
        "### What to change",
        "",
        repair.instruction,
        "",
        (
            "Return the same JSON object as before, with the corrected statements. Change only "
            "what the diagnostics require -- a rewrite that also alters working code makes the "
            "next failure harder to attribute."
        ),
    ]
    return "\n".join(lines)


def build_engineer_prompt(
    step: BatchStepDesign,
    entities: list[DomainEntity],
    program_entry: ProgramDesignEntry,
    resolved: ResolvedProgram,
    *,
    input_type: str,
    output_type: str,
    repair: RepairContext | None = None,
) -> str:
    """Stable content first, the per-step instruction last.

    **The ordering is the whole point.** One program yields several steps, and every one of them
    re-sends the same domain records, the same narration, and the same COBOL source -- for
    `CBACT04C` that shared span is ~68k characters against ~400 characters of step-specific text.
    Putting the step first would make the variable part the prefix and the enormous identical part
    the suffix, which is precisely the shape ADR-0017 corrected in `spec_critic` after G13 measured
    it costing ~26% of a run. Stable-prefix-first is what lets a cache see the same prefix N times.

    Instruction-following points the same way: the concrete task reads better immediately before
    the response than buried above 68k characters of source.

    `DelimiterForgeryError` is deliberately not caught here -- an unambiguous hard failure that
    must propagate, exactly as in every other prompt builder in this repo.
    """
    # The target's helper API leads even the domain records: it is identical for every step of
    # every program, so it is the outermost stable layer of the prefix. Both real calls so far
    # reached for `CobolArithmetic` without having been told what was in it, and run 2 wrote a
    # second-choice implementation it had itself named as second-choice for exactly that reason.
    target_api = render_target_api_facts()
    domain_facts = render_domain_facts(entities)
    narration = wrap_untrusted_cobol(
        program_entry.spec_extraction.spec_markdown,
        source_label=f"{program_entry.program_name}-spec",
    )
    program_facts = render_program_field_facts(resolved)
    source = wrap_untrusted_cobol(resolved.source_text, source_label=program_entry.program_name)
    step_facts = render_step_facts(step, input_type=input_type, output_type=output_type)
    prompt = (
        f"{target_api}\n\n{domain_facts}\n\n{program_facts}\n\n"
        f"{narration}\n\n{source}\n\n{step_facts}"
    )
    if repair is not None:
        prompt += "\n" + render_repair_facts(repair)
    return prompt


def _parse_body_response(raw_response: str) -> tuple[str, list[str], str]:
    """Parse the model's JSON into `(body, imports, notes)`, or raise."""
    try:
        payload = json.loads(strip_code_fence(raw_response))
    except json.JSONDecodeError as exc:
        raise ModernizationEngineerParseError(
            f"{_NODE_NAME} response is not valid JSON: {exc}"
        ) from exc

    if not isinstance(payload, dict) or not _REQUIRED_KEYS <= set(payload):
        missing = sorted(_REQUIRED_KEYS - set(payload)) if isinstance(payload, dict) else ["all"]
        raise ModernizationEngineerParseError(
            f"{_NODE_NAME} response missing required key(s): {missing}"
        )

    body, imports, notes = payload["body"], payload["imports"], payload["notes"]
    if not isinstance(body, str) or not body.strip():
        raise ModernizationEngineerParseError(f"{_NODE_NAME} returned an empty or non-string body")
    if not isinstance(imports, list) or not all(isinstance(name, str) for name in imports):
        raise ModernizationEngineerParseError(
            f"{_NODE_NAME} returned a non-list or non-string `imports`: {imports!r}"
        )
    if not isinstance(notes, str):
        raise ModernizationEngineerParseError(
            f"{_NODE_NAME} returned a non-string `notes`: {notes!r}"
        )
    return body, imports, notes


def _default_author(routing: RoutingDecision, system_prompt: str, user_content: str) -> str:
    """Call a real model through `core/model_client.py` (ADR-0013), which owns backend choice,
    timeout, retry/backoff, budget enforcement, and usage capture -- not reimplemented here."""
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


def generate_processor(
    worktree_root: Path,
    program_entry: ProgramDesignEntry,
    step: BatchStepDesign,
    entities: list[DomainEntity],
    *,
    package: str,
    input_type: str,
    output_type: str,
    tier: ComplexityTier = ComplexityTier.COMPLEX,
    model_routing_config: Path | None = None,
    author: AuthorFn = _default_author,
    repair: RepairContext | None = None,
) -> GeneratedProcessor:
    """Generate one `ItemProcessor` for `step`, rendered around a model-authored body.

    Args:
        worktree_root: The cloned tenant-repo worktree -- the program's real source is re-resolved
            from it rather than carried in, consistent with every other node that needs it.
        program_entry: The approved `spec_extractor`/`spec_critic` output for this program.
        step: The one batch step being implemented. One call per step, never one per program.
        entities: The deterministic domain entities the body may reference.
        package: The Java package the rendered class is declared in.
        input_type: The `process` method's parameter type.
        output_type: The `process` method's return type.
        tier: Complexity tier for model routing. Defaults to `COMPLEX` -- the safe end, matching
            `resolve_routing`'s own default posture.
        model_routing_config: Overrides the default routing config path -- tests only.
        author: Overrides the live model call -- tests only.

    Raises:
        tenant_repo.TenantRepoFileNotFoundError: the program's source is missing.
        core.guardrails.DelimiterForgeryError: the narration or source forges the prompt delimiter.
        core.model_routing.ModelRoutingConfigError: the routing config is missing or malformed.
        ModernizationEngineerParseError: the model broke its response contract.
        rendering.java_processor.GeneratedBodyForgeryError: the body forged the review markers.
        rendering.java_processor.UnrenderableImportError: an import is not a qualified name.
        rendering.java_names.UnrenderableJavaNameError: the class name is not legal Java.
    """
    resolved = resolve_program(worktree_root, program_entry.program_name)
    routing_kwargs = {"config_path": model_routing_config} if model_routing_config else {}
    routing = resolve_routing(_NODE_NAME, tier, **routing_kwargs)

    user_content = build_engineer_prompt(
        step,
        entities,
        program_entry,
        resolved,
        input_type=input_type,
        output_type=output_type,
        repair=repair,
    )

    logger.info(
        "%s: program=%s step=%s model=%s tier=%s",
        _NODE_NAME,
        program_entry.program_name,
        step.step_name,
        routing.model,
        tier.value,
    )

    body, imports, notes = _parse_body_response(
        author(routing, _load_system_prompt(), user_content)
    )
    class_name = processor_class_name(step)

    java_source = render_processor(
        step,
        package=package,
        class_name=class_name,
        input_type=input_type,
        output_type=output_type,
        body=body,
        body_imports=imports,
        authored_by=routing.model,
    )

    if notes.strip():
        # Surfaced, not swallowed: a caveat the model raised is exactly the kind of fact a human
        # gate exists to see, and a code comment is not a gate item.
        logger.warning(
            "%s: program=%s step=%s reported notes: %s",
            _NODE_NAME,
            program_entry.program_name,
            step.step_name,
            notes.strip(),
        )

    return GeneratedProcessor(
        program_name=program_entry.program_name,
        step_name=step.step_name,
        class_name=class_name,
        java_source=java_source,
        model=routing.model,
        notes=notes.strip(),
    )
