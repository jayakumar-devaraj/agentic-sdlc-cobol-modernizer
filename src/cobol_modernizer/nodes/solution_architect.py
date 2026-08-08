"""`solution_architect` -- the third `design`-phase node: one unified Java target design.

`spec_extractor` and `spec_critic` each work one program at a time. This node is the first to look
across all four Track C programs together, producing `design.json`'s `unified_design`
(`core/contracts.UnifiedDesign`, ADR-0010) -- the domain model and Spring Batch/REST design
`card-service` (ADR-0009) will eventually be generated from.

Three decisions worth stating explicitly, all from ADR-0010's full reasoning:

1. **Domain entities are merged by copybook name only, mechanically -- never by structural
   resemblance.** `build_domain_entities` groups every program's
   `spec_extractor.group_field_mappings_by_source` output by copybook name: the same name across
   two programs is proof they share the exact same physical record (byte-for-byte, since it's
   literally the same source file). Two different copybook names always stay two different
   entities, even when structurally similar (`CVTRA06Y`'s `DALYTRAN-RECORD` and `CVTRA05Y`'s
   `TRAN-RECORD` are both 350-byte transaction records, but recognizing they're related is a
   judgment call for the LLM-authored design, not something this function may assume). A copybook
   that contributes zero successfully-mapped fields (`CODATECN` -- entirely `REDEFINES` groups)
   produces no entity at all; there's nothing to represent.
2. **Entity/field names are a direct, mechanical transform of the COBOL name, not a business
   rename.** `Account` from `ACCOUNT-RECORD`, `acctCurrBal` from `ACCT-CURR-BAL` -- deliberately
   not "nicer" names like `TransactionCategoryBalance`, since choosing those is exactly the kind
   of semantic judgment reserved for the model, not this deterministic function.
3. **Batch job and REST endpoint design is 100% LLM-authored**, informed by (never re-deriving)
   the deterministic entity data -- the same split `spec_extractor`/`spec_critic` already
   established. Each program's `spec_markdown` is wrapped as untrusted data again in the prompt
   (`core.guardrails.wrap_untrusted_cobol`) even though it's this repo's own prior LLM output --
   `spec_extractor`'s own guardrail is defense in depth, not a guarantee, so a narration that got
   an injection past it must still never reach this node as instructions.

No repair-retry loop for this node's own structured output either -- same posture as
`nodes.spec_critic.SpecCritiqueParseError`. Milestone C3's real repair-retry loop (plan step 35)
is separately-scoped work, not built in miniature a third time. Like `spec_extractor`/
`spec_critic`, the live architect model call is injected (`architect`) so this module's tests
exercise every deterministic step -- domain-entity merging, prompt construction, response
validation -- without a live Anthropic API credential this development environment does not have.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

from cobol_modernizer.core.complexity import ComplexityTier
from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    BatchStepDesign,
    DomainEntity,
    DomainField,
    ProgramDesignEntry,
    RestEndpointDesign,
    UnifiedDesign,
)
from cobol_modernizer.core.guardrails import wrap_untrusted_cobol
from cobol_modernizer.core.model_client import call_model
from cobol_modernizer.core.model_routing import RoutingDecision, resolve_routing
from cobol_modernizer.core.structured_output import strip_code_fence
from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.prompts_registry_client.loader import prompt_path
from cobol_modernizer.tools.pic_mapper import PicMapping
from cobol_modernizer.tools.tenant_repo import resolve_program

logger = logging.getLogger(__name__)

_NODE_NAME = "solution_architect"

#: Rank for picking the highest tier across a run's programs. `ComplexityTier` is a `str` Enum, so
#: it sorts alphabetically by default -- which would put "complex" below "moderate" and silently
#: choose the wrong end. Explicit ordering rather than relying on the enum's incidental sort.
_TIER_ORDER = {
    ComplexityTier.SIMPLE: 0,
    ComplexityTier.MODERATE: 1,
    ComplexityTier.COMPLEX: 2,
}

_RECORD_NAME_RE = re.compile(r"^\s*01\s+([A-Za-z0-9\-]+)\.", re.MULTILINE)
_RECORD_SUFFIX = "-RECORD"

_VALID_STEP_ROLES = frozenset({"reader", "processor", "writer", "tasklet"})
_VALID_REST_METHODS = frozenset({"GET", "POST", "PUT", "DELETE"})


class SolutionArchitectParseError(Exception):
    """The architect model's response could not be parsed into a valid `UnifiedDesign`.

    Raised for malformed JSON, a missing `batch_jobs`/`rest_endpoints` entry for a real program,
    or any reference to a domain entity/program that doesn't exist in what this node actually
    provided as Known Facts -- see the module docstring for why there's no repair-retry yet.
    """


def _to_pascal_case(cobol_name: str) -> str:
    return "".join(word.capitalize() for word in cobol_name.split("-") if word)


def _to_camel_case(cobol_name: str) -> str:
    words = [word for word in cobol_name.split("-") if word]
    if not words:
        return cobol_name
    return words[0].lower() + "".join(word.capitalize() for word in words[1:])


def _derive_entity_name(copybook_name: str, copybook_source: str) -> str:
    """The copybook's own `01`-level record name, `-RECORD` stripped, mechanically PascalCased.

    Falls back to the copybook's own name if no `01`-level record is found (shouldn't happen for
    a copybook that contributed at least one mapped field) -- never guesses a business name.
    """
    match = _RECORD_NAME_RE.search(copybook_source)
    if match is None:
        return _to_pascal_case(copybook_name)
    record_name = match.group(1)
    if record_name.upper().endswith(_RECORD_SUFFIX):
        record_name = record_name[: -len(_RECORD_SUFFIX)]
    return _to_pascal_case(record_name)


def _domain_field(mapping: PicMapping) -> DomainField:
    return DomainField(
        java_field_name=_to_camel_case(mapping.field_name),
        cobol_field_name=mapping.field_name,
        java_type=mapping.java_type,
        precision=mapping.precision,
        scale=mapping.scale,
        signed=mapping.signed,
    )


def build_domain_entities(
    worktree_root: Path, programs: list[ProgramDesignEntry]
) -> list[DomainEntity]:
    """Merge every program's copybook-sourced fields into one `DomainEntity` per real copybook.

    Re-resolves each program from `worktree_root` rather than trusting only the `ProgramDesignEntry`
    objects passed in -- consistent with `nodes.spec_critic.build_critique_prompt`'s own
    re-resolution, and necessary here regardless: `SpecExtractionResult.field_mappings` is a flat,
    unattributed list with no copybook labels to group by, so the per-copybook grouping this
    function needs has to come from a fresh `group_field_mappings_by_source` call, not the already-
    flattened result `spec_extractor` returned.

    See the module docstring for the merge-by-copybook-name-only and mechanical-naming decisions.
    `FILLER` fields (see `nodes.spec_critic.check_field_reference_fidelity`'s own note on why:
    `pic_mapper` always names them literally `"FILLER"`) are excluded -- padding, not a real
    business field.
    """
    entities: dict[str, DomainEntity] = {}

    for entry in programs:
        resolved = resolve_program(worktree_root, entry.program_name)
        grouped = group_field_mappings_by_source(resolved)

        for source_label, (mappings, _unsupported) in grouped.items():
            if source_label == entry.program_name:
                continue  # only copybook-sourced fields become domain entities
            if not mappings:
                continue  # e.g. CODATECN: zero successfully-mapped fields, not a domain entity

            if source_label not in entities:
                entities[source_label] = DomainEntity(
                    name=_derive_entity_name(source_label, resolved.copybook_sources[source_label]),
                    source_copybook=source_label,
                    used_by_programs=[],
                    fields=[
                        _domain_field(mapping)
                        for mapping in mappings
                        if mapping.field_name not in (None, "FILLER")
                    ],
                )

            entity = entities[source_label]
            if entry.program_name not in entity.used_by_programs:
                entity.used_by_programs.append(entry.program_name)

    return list(entities.values())


def _render_known_facts(
    domain_entities: list[DomainEntity], programs: list[ProgramDesignEntry]
) -> str:
    """Render the deterministic facts block the prompt's system instructions call "Known Facts"."""
    lines = ["# Known Facts for solution_architect", ""]

    lines.append("## Domain entities (deterministic -- reproduce exactly, never invent/rename/merge)")
    for entity in domain_entities:
        lines.append(
            f"### {entity.name} (from {entity.source_copybook}, "
            f"used by {', '.join(entity.used_by_programs)})"
        )
        lines += ["| Field | Java type | Precision | Scale | Signed |", "|---|---|---|---|---|"]
        for field in entity.fields:
            precision = field.precision if field.precision is not None else "-"
            scale = field.scale if field.scale is not None else "-"
            lines.append(
                f"| {field.java_field_name} ({field.cobol_field_name}) | {field.java_type} "
                f"| {precision} | {scale} | {field.signed} |"
            )
        lines.append("")

    lines.append("## Paragraph flow per program (source order)")
    for entry in programs:
        lines.append(f"### {entry.program_name}")
        for name in entry.spec_extraction.paragraph_names:
            lines.append(f"- {name}")
        lines.append("")

    return "\n".join(lines)


def build_architect_prompt(
    domain_entities: list[DomainEntity], programs: list[ProgramDesignEntry]
) -> str:
    """Build the user-turn prompt content: Known Facts followed by every wrapped, untrusted narration.

    See the module docstring for why each `spec_markdown` is wrapped again here, even though it's
    this repo's own prior LLM output. Delimiter-forgery detection
    (`core.guardrails.DelimiterForgeryError`) is not caught here, same as `spec_extractor`'s and
    `spec_critic`'s own prompt builders -- an unambiguous hard failure that must propagate.
    """
    known_facts = _render_known_facts(domain_entities, programs)
    wrapped_sections = [
        wrap_untrusted_cobol(entry.spec_extraction.spec_markdown, source_label=entry.program_name)
        for entry in programs
    ]
    return known_facts + "\n\n" + "\n\n".join(wrapped_sections)


def _require_keys(item: object, keys: set[str], what: str) -> dict:
    if not isinstance(item, dict) or not keys <= set(item):
        raise SolutionArchitectParseError(f"solution_architect {what} missing required fields: {item!r}")
    return item


def _parse_unified_design_response(
    raw_response: str,
    domain_entities: list[DomainEntity],
    programs: list[ProgramDesignEntry],
) -> tuple[list[BatchJobDesign], list[RestEndpointDesign]]:
    """Parse and validate the architect model's JSON response against the real Known Facts.

    Raises:
        SolutionArchitectParseError: malformed JSON, a missing top-level key, a missing
            `batch_jobs` entry for a real program, or any reference to a domain entity, program,
            step role, or REST method that isn't one of the real ones this node actually offered
            -- see the module docstring for why there's no repair-retry to fall back on instead.
    """
    entity_names = {entity.name for entity in domain_entities}
    program_names = {entry.program_name for entry in programs}

    candidate = strip_code_fence(raw_response)
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SolutionArchitectParseError(
            f"solution_architect response is not valid JSON: {exc}. Raw response: {raw_response!r}"
        ) from None

    if not isinstance(parsed, dict) or not {"batch_jobs", "rest_endpoints"} <= set(parsed):
        raise SolutionArchitectParseError(
            f"solution_architect response must be a JSON object with batch_jobs and "
            f"rest_endpoints keys: {raw_response!r}"
        )

    batch_jobs: list[BatchJobDesign] = []
    for job in parsed["batch_jobs"]:
        job = _require_keys(job, {"program_name", "job_name", "domain_entities", "steps"}, "batch_jobs entry")
        if job["program_name"] not in program_names:
            raise SolutionArchitectParseError(
                f"solution_architect batch_jobs entry names an unknown program: {job['program_name']!r}"
            )
        for entity_name in job["domain_entities"]:
            if entity_name not in entity_names:
                raise SolutionArchitectParseError(
                    f"solution_architect batch_jobs entry references an unknown domain entity: "
                    f"{entity_name!r}"
                )

        steps: list[BatchStepDesign] = []
        for step in job["steps"]:
            step = _require_keys(
                step, {"step_name", "source_paragraphs", "role", "description"}, "batch step"
            )
            if step["role"] not in _VALID_STEP_ROLES:
                raise SolutionArchitectParseError(
                    f"solution_architect batch step has an unknown role: {step['role']!r}"
                )
            steps.append(
                BatchStepDesign(
                    step_name=step["step_name"],
                    source_paragraphs=step["source_paragraphs"],
                    role=step["role"],
                    description=step["description"],
                )
            )

        batch_jobs.append(
            BatchJobDesign(
                program_name=job["program_name"],
                job_name=job["job_name"],
                domain_entities=job["domain_entities"],
                steps=steps,
            )
        )

    covered_programs = {job.program_name for job in batch_jobs}
    missing_programs = program_names - covered_programs
    if missing_programs:
        raise SolutionArchitectParseError(
            f"solution_architect response is missing batch_jobs for: {sorted(missing_programs)}"
        )

    rest_endpoints: list[RestEndpointDesign] = []
    for endpoint in parsed["rest_endpoints"]:
        endpoint = _require_keys(
            endpoint, {"method", "path", "domain_entity", "description"}, "rest_endpoints entry"
        )
        if endpoint["method"] not in _VALID_REST_METHODS:
            raise SolutionArchitectParseError(
                f"solution_architect rest_endpoints entry has an unknown method: {endpoint['method']!r}"
            )
        if endpoint["domain_entity"] not in entity_names:
            raise SolutionArchitectParseError(
                f"solution_architect rest_endpoints entry references an unknown domain entity: "
                f"{endpoint['domain_entity']!r}"
            )
        rest_endpoints.append(
            RestEndpointDesign(
                method=endpoint["method"],
                path=endpoint["path"],
                domain_entity=endpoint["domain_entity"],
                description=endpoint["description"],
            )
        )

    return batch_jobs, rest_endpoints


#: `(model, system_prompt, user_content) -> raw response text`. Injected so `design_solution`'s
#: tests exercise every deterministic step above without a live model credential.
ArchitectFn = Callable[[RoutingDecision, str, str], str]


def _default_architect(routing: RoutingDecision, system_prompt: str, user_content: str) -> str:
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


def design_solution(
    worktree_root: Path,
    programs: list[ProgramDesignEntry],
    *,
    model_routing_config: Path | None = None,
    architect: ArchitectFn = _default_architect,
) -> UnifiedDesign:
    """Design one unified `UnifiedDesign` across every program in `programs`.

    Args:
        worktree_root: The cloned tenant-repo worktree path -- re-resolves each program's real
            source to build the domain entities and prompt against (see `build_domain_entities`).
        programs: Every program's `spec_extractor` + `spec_critic` output to design against.
            Processed regardless of `critique.overall_confidence` -- weighing a low-confidence
            program is a human/gate decision (`core.contracts.build_gate_items`), not this node's.
        model_routing_config: Overrides `core/model_routing.py`'s default config path -- tests only.
        architect: Overrides the default live Anthropic call -- tests only.

    Raises:
        tenant_repo.TenantRepoFileNotFoundError: a program's source or a copybook is missing.
        parsing.cobol_parser.UnsupportedCopyConstructError: a `COPY ... REPLACING` was found.
        core.guardrails.DelimiterForgeryError: a narration contains this repo's prompt delimiter.
        core.model_routing.ModelRoutingConfigError: the config is missing/malformed/incomplete.
        SolutionArchitectParseError: the architect model's response isn't valid structured JSON
            against the real Known Facts.

    None of these are caught here -- each is unambiguous enough to fail loudly, consistent with
    `nodes.spec_extractor.extract_spec` and `nodes.spec_critic.critique_spec`.
    """
    domain_entities = build_domain_entities(worktree_root, programs)
    user_content = build_architect_prompt(domain_entities, programs)
    system_prompt = _load_system_prompt()

    # One call reasoning across every program at once (ADR-0010), so it takes the *highest* tier
    # present -- not an average. A run containing one hard program is a hard run: averaging would
    # let three simple programs pull the architect down to a tier the fourth needs.
    tier = max(
        (entry.spec_extraction.complexity.tier for entry in programs),
        key=_TIER_ORDER.__getitem__,
        default=ComplexityTier.COMPLEX,
    )
    routing_kwargs = {} if model_routing_config is None else {"config_path": model_routing_config}
    routing = resolve_routing(_NODE_NAME, tier, **routing_kwargs)
    logger.info(
        "solution_architect routing: programs=%d tier=%s model=%s effort=%s",
        len(programs), routing.tier.value, routing.model, routing.effort,
    )
    raw_response = architect(routing, system_prompt, user_content)
    batch_jobs, rest_endpoints = _parse_unified_design_response(raw_response, domain_entities, programs)

    return UnifiedDesign(
        domain_entities=domain_entities,
        batch_jobs=batch_jobs,
        rest_endpoints=rest_endpoints,
    )
