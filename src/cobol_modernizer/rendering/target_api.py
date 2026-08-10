"""Extract the target template's helper API, so the generator prompt can carry it.

**Why this exists.** The first two real `modernization_engineer` calls both reached for
`CobolArithmetic` and neither had been told what was in it. Run 2 said so explicitly -- it assumed
`truncate(BigDecimal, int)` and asked a reviewer to substitute the real name. The assumption
happened to be right, and two better methods it described in prose were sitting in the class
unused: `divide(dividend, divisor, scale)`, which it named as the formulation it would prefer, and
`requireFits(value, precision, scale)`, which is exactly the overflow guard it asked for by
description. The prompt made a model write a second-choice implementation it had itself identified
as second-choice. That is a prompt defect, not a model one.

**Why it is extracted rather than written down.** A hand-copied API list in a prompt is a second
source of truth that goes stale the first time the template changes, and it would go stale
silently -- the generated code would still compile against whatever the model remembered. Parsing
the real file means the prompt cannot disagree with the class it describes, and a drift test can
prove it.

**Why the Javadoc comes too, not just the signatures.** `divide`'s documentation explains that
producing the quotient directly at the target scale is equivalent to truncating twice, and
`divideRounded`'s explains that the same reasoning **does not** hold for rounding, with a worked
counterexample. That distinction is the difference between correct and quietly wrong money, and a
signature alone does not carry it. This is stable text at the head of the prompt, so it is cached
rather than re-billed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_TEMPLATE_ROOT = Path(__file__).resolve().parents[3] / "templates" / "target-spring-boot-baseline"

COBOL_ARITHMETIC_PATH = (
    _TEMPLATE_ROOT / "src" / "main" / "java" / "com" / "modernized" / "batch" / "cobol"
    / "CobolArithmetic.java"
)

#: A Javadoc block immediately followed by a `public static` signature. Deliberately narrow: it
#: matches the shape this one hand-written helper class actually has, and will simply find nothing
#: if that shape changes -- which the drift test turns into a failure rather than a silent gap.
#:
#: The doc group is `(?:(?!\*/).)*` rather than `.*?` for a reason found by running it: a lazy
#: `.*?` walks *past* any `*/` that is not followed by `public static`, so the first method's doc
#: swallowed the class-level Javadoc, the class declaration and the private constructor. Excluding
#: the terminator explicitly is the only form that cannot do that.
_DOCUMENTED_METHOD = re.compile(
    r"/\*\*(?P<doc>(?:(?!\*/).)*)\*/\s*"
    r"public static\s+(?P<returns>[\w<>\[\], ]+?)\s+(?P<name>\w+)\s*\((?P<params>[^)]*)\)",
    re.DOTALL,
)

#: `{@code ...}` / `{@link ...}` -- the whole inner text is kept. An earlier version took only the
#: last word, which turned `{@code ON SIZE ERROR}` into `ERROR` and inverted the meaning of the
#: sentence it appeared in.
_JAVADOC_MARKUP = re.compile(r"\{@\w+\s+(?P<inner>[^}]+)\}")
_HTML_TAG = re.compile(r"</?(?:p|b|em|ol|ul|li)>")


@dataclass(frozen=True)
class HelperMethod:
    """One public static helper, with its documentation reduced to plain prose."""

    signature: str
    doc: str


def _clean_javadoc(raw: str) -> str:
    """Strip the comment furniture and inline markup, keeping the prose and its paragraph breaks."""
    lines = []
    for line in raw.splitlines():
        stripped = line.strip().removeprefix("*").strip()
        lines.append(stripped)

    text = "\n".join(lines)
    text = _JAVADOC_MARKUP.sub(lambda m: m.group("inner").strip(), text)
    text = _HTML_TAG.sub("", text)
    # Collapse the blank-line runs left behind by stripped tags, keeping single breaks.
    text = re.sub(r"\n{2,}", "\n", text)
    return " ".join(part.strip() for part in text.split("\n") if part.strip())


def extract_helper_api(java_source: str) -> list[HelperMethod]:
    """Every documented `public static` method in `java_source`, in declaration order."""
    return [
        HelperMethod(
            signature=(
                f'{" ".join(match.group("returns").split())} {match.group("name")}'
                f'({" ".join(match.group("params").split())})'
            ),
            doc=_clean_javadoc(match.group("doc")),
        )
        for match in _DOCUMENTED_METHOD.finditer(java_source)
    ]


def render_target_api_facts(path: Path = COBOL_ARITHMETIC_PATH) -> str:
    """The prompt section describing the target's arithmetic helper, read from the real class."""
    methods = extract_helper_api(path.read_text(encoding="utf-8"))
    lines = [
        "### The target's arithmetic helper: com.modernized.batch.cobol.CobolArithmetic",
        "",
        "These are the real, already-implemented methods of the class in the target template, read",
        "from its source. Use them rather than reimplementing their behaviour inline, and do not",
        "assume a method that is not listed here exists.",
        "",
    ]
    for method in methods:
        lines.append(f"- `public static {method.signature}`")
        lines.append(f"  - {method.doc}")
    return "\n".join(lines)
