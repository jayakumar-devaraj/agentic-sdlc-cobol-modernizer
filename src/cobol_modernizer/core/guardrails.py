"""Guardrails for handing untrusted COBOL source text to an LLM prompt.

Per `.claude/agents/development.md`, "COBOL input is untrusted data, not instructions": every
field of a parsed COBOL program -- including comments -- is untrusted text passed to an LLM,
never treated as instructions to that LLM. A COBOL comment that reads like a directive to the
model is exactly the injection surface this module exists to catch before the text reaches a
prompt. `tools/tenant_repo.py` explicitly hands this concern off to this module; the future
`nodes/spec_extractor.py` is the first caller that will actually build a prompt with it.

Two distinct risks are handled here, deliberately differently:

1. **Delimiter forgery** (`DelimiterForgeryError`, raised, never returned as a flag). The wrapping
   scheme below marks "everything between these tags is untrusted data, not instructions" to the
   model. If the untrusted COBOL text itself contains that exact delimiter -- coincidentally or,
   worse, crafted into a comment -- it could prematurely close the untrusted block and make
   subsequent attacker-controlled text look like it sits outside the data boundary, i.e. look like
   real instructions. This is mechanically unambiguous: no legitimate COBOL source should ever
   contain this module's internal delimiter tag text, so its presence is a hard failure, not
   something to guess around -- consistent with this codebase's established "fail loudly rather
   than guess" pattern (see `tools/pic_mapper.UnsupportedPicConstructError`,
   `parsing/cobol_parser.UnsupportedCopyConstructError`).
2. **Injection-phrase heuristics** (`detect_injection_patterns`, returned as flags, never raised).
   This is a best-effort, explicitly non-exhaustive scan for classic prompt-injection phrasings
   (e.g. "ignore previous instructions", "you are now", explicit role markers like `SYSTEM:`).
   It is defense-in-depth, not a guarantee -- a sufficiently creative injection can phrase itself
   around every pattern here. A match is *flagged* for a human or downstream gate to weigh, not
   used to block processing outright, since (unlike delimiter forgery) it needs judgment rather
   than being mechanically unambiguous.

This module does not build prompts and does not call a model. It only prepares a safely-wrapped,
verified block of text; the system-level instruction telling the model that block is inert data
belongs to the prompt that embeds it (the future `nodes/spec_extractor.py`).
"""

from __future__ import annotations

import re

from pydantic import BaseModel

# --- Wrapping / delimiter-forgery detection -----------------------------------------------------
#
# The tag name itself (not the label attribute, which varies per call) is the fixed, structural
# marker checked for forgery. Checking on the tag-name prefix -- not the fully-rendered opening
# tag with this call's specific label -- also catches an attacker injecting an opening tag with a
# *different* label than the current one, which would be just as effective at confusing a model
# about where the untrusted block ends.

_TAG_NAME = "untrusted-cobol-source"
_OPEN_TAG_PREFIX = f"<{_TAG_NAME}"
_CLOSE_TAG = f"</{_TAG_NAME}>"


class DelimiterForgeryError(Exception):
    """The untrusted COBOL source contains this module's own prompt-wrapping delimiter.

    This is the unambiguous half of the guardrail: no legitimate COBOL source should ever contain
    `<untrusted-cobol-source ...>` or `</untrusted-cobol-source>` literally, so finding one is a
    hard failure, not evidence to weigh. Callers must not catch this and fall back to wrapping the
    text anyway (e.g. by stripping the offending substring and continuing) -- that would silently
    reopen exactly the delimiter-forgery hole this check exists to close. Route it to a
    human-in-the-loop gate, the same as `UnsupportedPicConstructError` and
    `UnsupportedCopyConstructError`.
    """

    def __init__(self, delimiter: str, source_label: str, source_text: str) -> None:
        self.delimiter = delimiter
        self.source_label = source_label
        self.source_text = source_text
        super().__init__(
            f"Delimiter forgery detected in source {source_label!r}: the untrusted text "
            f"contains the literal prompt-wrapping delimiter {delimiter!r}. This must route to "
            f"a human gate, not be silently stripped and wrapped anyway."
        )


def _check_no_delimiter_forgery(source_text: str, *, source_label: str) -> None:
    if _OPEN_TAG_PREFIX in source_text:
        raise DelimiterForgeryError(_OPEN_TAG_PREFIX, source_label, source_text)
    if _CLOSE_TAG in source_text:
        raise DelimiterForgeryError(_CLOSE_TAG, source_label, source_text)


def wrap_untrusted_cobol(source_text: str, *, source_label: str) -> str:
    """Wrap `source_text` in an unambiguous delimiter pair marking it as untrusted data.

    The result is intended to be embedded in a prompt alongside a system-level instruction
    (built by the caller, not this function) telling the model everything between the tags is
    data to reason about, never instructions to follow.

    Args:
        source_text: Raw COBOL source (or copybook) text, exactly as read from disk.
        source_label: A human-readable label identifying the source, e.g. a program or
            copybook name -- embedded in the opening tag for traceability, not treated as
            untrusted (it comes from this repo's own resolution of a program/copybook name, not
            from COBOL source text).

    Raises:
        DelimiterForgeryError: `source_text` contains the literal opening or closing delimiter
            used by this wrapping scheme.
    """
    _check_no_delimiter_forgery(source_text, source_label=source_label)
    escaped_label = source_label.replace('"', "&quot;")
    open_tag = f'<{_TAG_NAME} label="{escaped_label}">'
    return f"{open_tag}\n{source_text}\n{_CLOSE_TAG}"


# --- Injection-phrase heuristics (best-effort, non-exhaustive) -----------------------------------


_INJECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "ignore_instructions",
        # Qualifiers combine in real-world phrasings ("ignore ALL PREVIOUS instructions" is the
        # canonical form of this attack, not an edge case) -- each qualifier is independently
        # optional rather than a single either/or choice, so combinations are still matched.
        re.compile(
            r"\bignore\s+(?:all\s+)?(?:previous\s+|prior\s+|the\s+above\s+)?instructions\b",
            re.IGNORECASE,
        ),
    ),
    ("new_instructions", re.compile(r"\bnew\s+instructions\b", re.IGNORECASE)),
    ("role_assumption", re.compile(r"\byou\s+are\s+now\b", re.IGNORECASE)),
    ("role_assumption", re.compile(r"\bact\s+as\b", re.IGNORECASE)),
    ("role_assumption", re.compile(r"\bpretend\s+(?:that\s+)?you\s+are\b", re.IGNORECASE)),
    ("role_marker", re.compile(r"\b(?:SYSTEM|ASSISTANT|USER)\s*:", re.IGNORECASE)),
    ("role_marker", re.compile(r"###\s*Instruction\b", re.IGNORECASE)),
]


class InjectionFlag(BaseModel):
    """One best-effort heuristic match of a classic prompt-injection phrasing.

    This is defense-in-depth, not a guarantee -- see the module docstring. `pattern` names which
    heuristic matched (not a specific regex, which is an implementation detail); `matched_text`
    and `line_number` are provided so a caller (or a human reviewing a flagged gate item) can see
    exactly what triggered the flag without re-scanning the source themselves.
    """

    pattern: str
    matched_text: str
    line_number: int


def detect_injection_patterns(text: str) -> list[InjectionFlag]:
    """Best-effort, non-exhaustive scan for classic prompt-injection phrasings.

    This is defense-in-depth, not a guarantee: a sufficiently creative injection can be phrased
    to avoid every pattern here. Matches are returned as flags for a caller to weigh (e.g. surface
    to a human-in-the-loop gate as a risk signal) -- this function never raises and never decides
    what happens with a match, unlike `DelimiterForgeryError`'s unambiguous hard failure.

    Args:
        text: Any text to scan -- typically COBOL source about to be wrapped for a prompt.

    Returns:
        Zero or more `InjectionFlag`s, in the order their patterns were matched, line by line.
    """
    flags: list[InjectionFlag] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for pattern_name, pattern in _INJECTION_PATTERNS:
            match = pattern.search(line)
            if match:
                flags.append(
                    InjectionFlag(
                        pattern=pattern_name,
                        matched_text=match.group(0),
                        line_number=line_number,
                    )
                )
    return flags


# --- Composed entry point -------------------------------------------------------------------------


class GuardrailResult(BaseModel):
    """Result of preparing untrusted COBOL source text for embedding in a prompt.

    `injection_flags` is truthfully surfaced, not acted on -- this module doesn't decide what
    happens with a flag (e.g. whether it blocks processing or is just noted); that's for the
    caller, consistent with how `spec_critic`'s confidence score is expected to work.
    """

    wrapped_text: str
    injection_flags: list[InjectionFlag]


def prepare_untrusted_cobol_for_prompt(source_text: str, *, source_label: str) -> GuardrailResult:
    """Wrap `source_text` for a prompt and scan it for injection-phrase heuristics, in one call.

    Composes `wrap_untrusted_cobol` and `detect_injection_patterns` -- delimiter-forgery detection
    runs first and raises on the unambiguous case; the heuristic scan then runs over the original
    (unwrapped) `source_text` so line numbers in any returned flags line up with the source file,
    not the wrapped block.

    Args:
        source_text: Raw COBOL source (or copybook) text, exactly as read from disk.
        source_label: A human-readable label identifying the source (program/copybook name).

    Raises:
        DelimiterForgeryError: `source_text` contains the literal opening or closing delimiter
            used by `wrap_untrusted_cobol`. Not returned as a flag -- this case is unambiguous
            enough to be a hard failure, per the module docstring.
    """
    wrapped_text = wrap_untrusted_cobol(source_text, source_label=source_label)
    injection_flags = detect_injection_patterns(source_text)
    return GuardrailResult(wrapped_text=wrapped_text, injection_flags=injection_flags)
