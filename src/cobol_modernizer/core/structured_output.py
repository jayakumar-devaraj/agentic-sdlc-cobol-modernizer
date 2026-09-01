"""One shared helper for parsing structured JSON out of a model's raw text response.

Originally private to `nodes/spec_critic.py`; pulled out here once `nodes/solution_architect.py`
needed the identical behavior, not preemptively -- the same "second real caller" threshold
`core/source_units.py` and `core/schema_export.py` were already extracted at.

`parse_with_repair` is plan step 35's repair-retry loop, and it lives here rather than in any one
node for the reason ADR-0007 and ADR-0010 both gave when they deferred it: four
similar-but-not-identical retry mechanisms are harder to reason about than one shared one built
once, when its contract is known. It is now known -- see ADR-0054.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

#: How many times a model is asked for the *same* structured answer when the answer it gave could
#: not be parsed. Named for what it bounds, following the rule `model_client` states and
#: `graph/generate_pipeline` repeats: this repo now has three unrelated attempt caps, and they
#: multiply if anything ever confuses them.
#:
#: - `model_client.MAX_TRANSPORT_ATTEMPTS` (5) -- one HTTP call against a 429 or a 5xx.
#: - `graph.generate_pipeline.MAX_HEAL_ATTEMPTS` (3) -- a model asked to rewrite code that did not
#:   compile. The content is different each time; the question is new each time.
#: - `MAX_CONTENT_ATTEMPTS` (this) -- well-formed transport, malformed *content*. The question is
#:   identical each time; only the instruction is stronger.
#:
#: **2, not 3 or 5, and the number is measured rather than picked.** ADR-0049 sampled 21 judge
#: calls per candidate over the same corpus: `claude-opus-5` held the response contract 21 of 21,
#: `claude-haiku-4-5-20251001` 16 of 21, and every one of the five failures was the same shape --
#: a prose preamble ahead of otherwise-valid JSON, against a prompt saying "Respond with a JSON
#: array and nothing else." That is a model ignoring an instruction, not a model unable to follow
#: it, so one more attempt carrying a stronger instruction is the whole of the remedy. A third
#: attempt would spend a second full prompt to distinguish "ignored it twice" from "cannot do it",
#: and this repo's answer to "cannot do it" is to fail loudly rather than keep paying.
MAX_CONTENT_ATTEMPTS = 2


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


#: Stands in for the model's own words wherever an error message quoted them back.
REDACTED = "<the unparseable response, omitted here on purpose>"


def redact_response(message: str, raw_response: str) -> str:
    """Remove the model's own output from a parse error message.

    **This is not a precaution, it is a fix for a defect this repo actually had.** Three of the
    four parse errors embed what the model said -- `spec_critic`'s reads
    `f"... is not valid JSON: {exc}. Raw response: {raw_response!r}"` -- which are excellent
    messages for a human reading a log and exactly the wrong thing to paste into the next prompt.
    Writing `render_repair_instruction` to "carry the error and nothing else" was therefore not
    sufficient on its own: the error was not free of model output, and the boundary test in
    `tests/unit/test_spec_critic.py` caught it (ADR-0054).

    The redaction is exact rather than heuristic: the caller knows precisely what the model
    returned, so both the plain text and its `repr()` form -- the one an f-string's `!r` produces
    -- are excised by literal match. Nothing is guessed at, and a message that never quoted the
    response is returned unchanged.
    """
    stripped = raw_response.strip()
    for form in (repr(raw_response), repr(stripped), raw_response, stripped):
        if form and form in message:
            message = message.replace(form, REDACTED)
    return message


def render_repair_instruction(error: Exception, raw_response: str) -> str:
    """The correction appended to the original prompt when a response could not be parsed.

    **It carries this repo's own parse error and nothing else** -- with the model's own words
    redacted out of that error by `redact_response`. The obvious alternative -- quote the
    malformed response back and ask the model to fix *that* -- is deliberately not taken, for two
    reasons that point the same way:

    1. **It would put model-authored text inside a prompt, unwrapped.** That is the exact question
       ADR-0053 left open for `build_validator` rather than answering in passing, and a repair loop
       spanning four nodes is the worst place to pre-empt it. The redacted error message is this
       repo's own computed fact -- the same category as the Known Facts every node sends unwrapped.
    2. **A malformed response is untrusted content.** `spec_critic` and `spec_extractor` read
       tenant COBOL, so a response echoing an injected instruction back into the next prompt is a
       laundering path straight through the boundary `core/guardrails` exists to hold.

    Re-asking without the prior text costs one more full prompt rather than a diff. That is the
    price, it is stated rather than hidden, and ADR-0054 records why it is worth paying.
    """
    reason = redact_response(str(error), raw_response)
    return (
        "Your previous response to this exact request could not be parsed, and the request is "
        f"unchanged. The parser reported: {reason}\n\n"
        "Respond again with the required JSON value and nothing else -- no explanation before it, "
        "no commentary after it, no code fence around it. Do not apologise or acknowledge this "
        "correction; return only the JSON."
    )


def parse_with_repair[T](
    node: str,
    raw_response: str,
    parse: Callable[[str], T],
    reask: Callable[[str], str],
    *,
    on: type[Exception] | tuple[type[Exception], ...],
    max_attempts: int = MAX_CONTENT_ATTEMPTS,
) -> T:
    """Parse `raw_response`, re-asking the model with a stronger instruction if that fails.

    Args:
        node: node name, for the log line only.
        raw_response: what the model already returned. Parsed first, so a well-formed answer --
            the overwhelmingly common case -- costs nothing at all.
        parse: the caller's existing parse-and-validate function. Unchanged by this loop, which is
            the point: every node keeps its own validation and its own error type.
        reask: sends the repair instruction and returns a fresh raw response. The node owns how it
            reaches the model, so this loop needs no prompt, no routing and no budget of its own.
        on: the parse failure(s) worth another attempt -- always the caller's own `*ParseError`.
            **Deliberately not a bare `except Exception`**: a `TypeError` raised inside `parse` is
            a defect in this repo, and re-asking a model to fix it would spend real money hiding a
            bug behind a retry that cannot possibly work.
        max_attempts: total parse attempts, including the first. `1` disables repair entirely.

    Returns:
        Whatever `parse` returns, from the first attempt that succeeds.

    Raises:
        The caller's own error, from the final attempt, if every attempt fails. The loop never
        substitutes an error type of its own -- a caller that already handles
        `SolutionArchitectParseError` keeps handling exactly that, and the traceback still names
        the real reason the last response was unusable rather than "repair exhausted".
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be at least 1, got {max_attempts}")

    # `while True` rather than `for attempt in range(...)`: the loop always leaves through a
    # `return` or a `raise`, so a bounded loop needs an unreachable statement after it to satisfy
    # the return type -- a line no test can reach, in a repo that uses no `pragma: no cover`
    # anywhere and would therefore carry it as a permanent uncovered line.
    attempt = 1
    current = raw_response
    while True:
        try:
            return parse(current)
        except on as error:
            if attempt >= max_attempts:
                logger.warning(
                    "structured output unparseable after repair node=%s attempts=%d error=%s",
                    node, attempt, type(error).__name__,
                )
                raise
            logger.warning(
                "structured output repair node=%s attempt=%d/%d error=%s",
                node, attempt, max_attempts, type(error).__name__,
            )
            current = reask(render_repair_instruction(error, current))
            attempt += 1
