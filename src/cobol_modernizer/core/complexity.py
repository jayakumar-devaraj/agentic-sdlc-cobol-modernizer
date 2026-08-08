"""How hard is this program, measured before any model is called (ADR-0014).

Every signal here is already computed by the deterministic pipeline -- paragraph count from
`parsing/cobol_parser.py`, field counts from `tools/pic_mapper.py`, and the exact prompt text
`nodes/spec_extractor.build_prompt` is about to send. Classifying a program therefore costs
nothing: no extra parse, no probe call, and above all **no model call to decide which model to
call**, which would defeat the purpose.

Two properties this module deliberately keeps:

1. **The dominant signal is the real prompt size, not a proxy for it.** `build_prompt` is
   deterministic and runs before the model call, so the number of characters about to be sent is
   knowable exactly. Estimating it from line counts or file sizes would be guessing at something
   already in hand.
2. **Thresholds, not a weighted score.** A weighted sum of paragraphs/fields/copybooks would need
   coefficients nobody can defend, and would move every program's tier whenever one coefficient
   was re-tuned. Threshold bands on two independent signals are explainable ("this program is in
   `complex` because its prompt is 74k characters") and change one program at a time.

The bands were set against real measurements of all four Track C programs, not chosen first and
justified after -- see ADR-0014 for the table and for why `CBCUS01C` is the only program whose
routing changes today.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

#: Below this prompt size (and under the paragraph ceiling) a program is `SIMPLE`. `CBCUS01C`, the
#: smallest real Track C program, measures 11,346 characters; the next smallest is 74,230. The
#: band sits well clear of both so a small edit to either cannot flip a tier.
SIMPLE_MAX_PROMPT_CHARS = 25_000
SIMPLE_MAX_PARAGRAPHS = 10

#: At or above either of these, a program is `COMPLEX`. Set so all three large Track C programs
#: (74k/78k/81k characters) land here and keep the model they were verified on -- see ADR-0014 on
#: why the initial calibration deliberately does not downgrade them.
COMPLEX_MIN_PROMPT_CHARS = 60_000
COMPLEX_MIN_PARAGRAPHS = 20


class ComplexityTier(str, Enum):
    """Which routing band a unit of work falls into.

    Ordered cheapest to most capable. `COMPLEX` is the safe default for any caller that cannot
    compute signals (see `core.model_routing.resolve_routing`) -- being wrong toward *more*
    capability costs money, being wrong toward less costs correctness, and this repo's whole
    posture is that a plausible-but-wrong answer is the worst outcome available.
    """

    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class ProgramComplexity(BaseModel):
    """The measured signals and the tier they produce, kept together so a decision is explainable.

    Carried rather than discarded because `tier` alone cannot answer "why was this program routed
    to the cheap model?" at a review gate -- the signals are the answer, and they are free to keep.
    """

    program_name: str
    prompt_chars: int
    paragraph_count: int
    mapped_field_count: int
    unsupported_field_count: int
    copybook_count: int
    tier: ComplexityTier
    rationale: str


def classify_prompt(
    program_name: str,
    prompt_chars: int,
    paragraph_count: int,
    mapped_field_count: int = 0,
    unsupported_field_count: int = 0,
    copybook_count: int = 0,
) -> ProgramComplexity:
    """Classify one program from already-computed signals.

    Args:
        prompt_chars: Length of the real user-turn prompt (`build_prompt`'s output), not an
            estimate of it.
        paragraph_count: `PROCEDURE DIVISION` paragraphs -- the control flow a narration has to
            follow, and the signal that catches a program that is structurally involved despite a
            small source file.

    The remaining counts are recorded for explainability and do not currently move the tier. That
    is deliberate: adding them to the decision would mean inventing weights (see module docstring),
    and measurement shows prompt size and paragraph count already separate Track C cleanly.
    """
    if prompt_chars >= COMPLEX_MIN_PROMPT_CHARS or paragraph_count >= COMPLEX_MIN_PARAGRAPHS:
        tier = ComplexityTier.COMPLEX
        reason = (
            f"prompt {prompt_chars:,} chars >= {COMPLEX_MIN_PROMPT_CHARS:,}"
            if prompt_chars >= COMPLEX_MIN_PROMPT_CHARS
            else f"{paragraph_count} paragraphs >= {COMPLEX_MIN_PARAGRAPHS}"
        )
    elif prompt_chars < SIMPLE_MAX_PROMPT_CHARS and paragraph_count <= SIMPLE_MAX_PARAGRAPHS:
        tier = ComplexityTier.SIMPLE
        reason = (
            f"prompt {prompt_chars:,} chars < {SIMPLE_MAX_PROMPT_CHARS:,} "
            f"and {paragraph_count} paragraphs <= {SIMPLE_MAX_PARAGRAPHS}"
        )
    else:
        tier = ComplexityTier.MODERATE
        reason = (
            f"prompt {prompt_chars:,} chars and {paragraph_count} paragraphs fall between the "
            f"simple and complex bands"
        )

    return ProgramComplexity(
        program_name=program_name,
        prompt_chars=prompt_chars,
        paragraph_count=paragraph_count,
        mapped_field_count=mapped_field_count,
        unsupported_field_count=unsupported_field_count,
        copybook_count=copybook_count,
        tier=tier,
        rationale=f"{tier.value}: {reason}",
    )


def critic_tier(program_tier: ComplexityTier, *, has_deterministic_fidelity_issues: bool) -> ComplexityTier:
    """Which tier `spec_critic` should run at for a program of `program_tier`.

    **The cheap path here is not a heuristic -- it falls out of ADR-0007.** When
    `compute_fidelity_issues` has already found a mechanically-proven defect, `overall_confidence`
    is forced to `0.0` *regardless of what the critic model scores*. The critic's per-rule scores
    still appear in `design.json` for a reviewer to read, but no model, at any price, can change
    the number the gate actually keys on. Paying for the strongest tier to produce scores that
    cannot affect the outcome is waste with no capability argument behind it, so this routes to
    `SIMPLE`.

    Otherwise the critic matches the program's own tier: checking a narration is bounded by the
    same source the narration was made from, so a complex program needs a capable critic.
    """
    if has_deterministic_fidelity_issues:
        return ComplexityTier.SIMPLE
    return program_tier
