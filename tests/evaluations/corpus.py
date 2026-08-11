"""The rubric and the cases -- and, for every case, how its expected verdict is known.

**Nothing here is invented to be easy.** Every criterion is a defect this platform actually
produced, and every unfaithful body is either one a real JVM has already failed or one whose fault
a reader can check against the COBOL by line number. That is the same posture step 43's injected
compile errors were built with, applied to semantics instead of syntax: a corpus of plausible-looking
mistakes nobody has made would measure the judge against this session's imagination.

**The bodies are imported, not copied.** `interest_faithful`, `interest_rounds` and
`interest_unguarded` are the exact strings `tests/system/test_interest_equivalence.py` compiles and
runs through Maven. Re-typing them here would let the two drift, and the moment they drift the
`ORACLE` ground below becomes a claim about a body no JVM ever saw.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cobol_modernizer.core.contracts import BatchStepDesign

# The real bodies, and the real steps they implement. See the module docstring on why these are
# imported rather than restated.
from tests.system.test_interest_equivalence import (
    _ALWAYS_WRITES_BODY,
    _COMPLETE_BODY,
    _CORRECT_BODY,
    _IMPORTS,
    _ROUNDING_BODY,
    COMPLETE_STEP,
    STEP,
)

PROGRAM = "CBACT04C"


class Verdict(str, Enum):
    """One criterion's answer about one body.

    `NOT_APPLICABLE` exists because the alternative is worse. `arithmetic_mode` has nothing to say
    about a body that performs no arithmetic, and a judge forced to choose `PASS` or `FAIL` there is
    being asked to express "no opinion" as an opinion -- which shows up later as noise in exactly the
    number this harness exists to produce.
    """

    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class Ground(str, Enum):
    """How this case's expected verdict is known -- and the distinction is load-bearing.

    `ORACLE` means a real JVM has already returned this answer: the body was compiled by Maven and
    run against ADR-0021's hand-derived expected values, and it passed or failed named rows. Nothing
    about that depends on anyone's reading.

    `SOURCE` means a human can check it against the COBOL by line number, and no oracle covers it.
    That is weaker and is labelled weaker. It is not worthless -- the two `SOURCE` cases are real
    defects that reached real generated code (G28, G26) -- but a judge agreeing with a `SOURCE` case
    agrees with this repo's reading of the COBOL, whereas a judge agreeing with an `ORACLE` case
    agrees with the machine. The benchmark reports the two separately for that reason.
    """

    ORACLE = "oracle"
    SOURCE = "source"


@dataclass(frozen=True)
class Criterion:
    """One named property of a faithful translation, and the defect that put it on the list."""

    id: str
    #: What the judge is asked, phrased so that `fail` means the body is wrong. Wording matters:
    #: "is this good Java" invites a style review, which is not what any of this is for.
    question: str
    #: Why this is a criterion at all. Rendered into the prompt -- a judge told only *what* to check
    #: and never *why it went wrong before* scores the letter of the rule.
    rationale: str
    #: Where the defect is recorded, for a reviewer who wants to check the criterion is real.
    provenance: str


CRITERIA: tuple[Criterion, ...] = (
    Criterion(
        id="arithmetic_mode",
        question=(
            "Where the COBOL performs arithmetic, does the Java use the same rounding mode COBOL "
            "would? A COMPUTE without ROUNDED truncates toward zero at the receiving field's scale."
        ),
        rationale=(
            "Truncation and rounding agree on most inputs and disagree by one cent on the rest, so "
            "this is invisible to review and to any test whose expected values were derived by the "
            "same reading. It is a currency defect that looks exactly like correct code."
        ),
        provenance="ADR-0021; `CobolArithmetic.divide` vs `divideRounded`",
    ),
    Criterion(
        id="guard_applied",
        question=(
            "If the step declares a guard condition, does the body apply it and return null when it "
            "does not hold? Returning null is how an ItemProcessor emits no record."
        ),
        rationale=(
            "The guard that decides whether a paragraph runs is often not in that paragraph -- "
            "CBACT04C performs 1300-COMPUTE-INTEREST under `IF DIS-INT-RATE NOT = 0` from its "
            "unnamed main body. A body that translates the paragraph faithfully and unconditionally "
            "writes a record the COBOL never writes, and every arithmetic check still passes."
        ),
        provenance="ADR-0022, audit G25; oracle row R10",
    ),
    Criterion(
        id="fixed_width_text",
        question=(
            "Are alphanumeric fields written at their declared PIC X(n) width, rather than as an "
            "empty or short string?"
        ),
        rationale=(
            "`MOVE SPACES` to a PIC X(50) field writes fifty blanks. An empty string compiles, reads "
            "correctly, and is a different record on disk -- which surfaces only at the first "
            "byte-for-byte comparison, long after the code is approved."
        ),
        provenance="audit G28; `CobolText.spaces`/`CobolText.pad`",
    ),
    Criterion(
        id="no_invented_values",
        question=(
            "Every field the body sets -- is its value actually derivable from the inputs the step "
            "was given? A field whose source is not reachable must be left unset and flagged, never "
            "filled with a plausible-looking constant."
        ),
        rationale=(
            "This is the property the whole architecture rests on: the generator refuses what it "
            "cannot justify instead of guessing. A fabricated identifier is the most expensive "
            "possible output, because it is the one a reviewer is least likely to question -- it "
            "looks like exactly what the field is for."
        ),
        provenance="audit G26 and G29; TRAN-ID is PARM-DATE plus a per-run counter",
    ),
)

CRITERIA_BY_ID = {criterion.id: criterion for criterion in CRITERIA}


def _mutate(body: str, anchor: str, replacement: str) -> str:
    """Replace `anchor` in `body`, refusing to produce a body that was not actually corrupted.

    The assertion is the whole reason this helper exists. A corruption whose anchor no longer matches
    is a silent no-op, and the case built from it would be a *faithful* body labelled unfaithful --
    so the judge would be marked wrong for being right, and the benchmark would report a failure that
    is really a stale fixture. `test_critic_discrimination` learned the same lesson about narration
    corruptions; this is that guard, at import time.
    """
    if anchor not in body:
        raise AssertionError(
            f"corruption anchor is not in the body, so the mutation would be a silent no-op and "
            f"the case would label an unmodified body as defective: {anchor!r}"
        )
    mutated = body.replace(anchor, replacement)
    # Checking the anchor matched is not enough, which a mutation test of this very function showed:
    # a replacement equal to its anchor matches everywhere and changes nothing, and only the *result*
    # reveals it.
    if mutated == body:
        raise AssertionError(
            f"the anchor matched but the body is unchanged, so this corruption is a no-op: "
            f"{anchor!r} -> {replacement!r}"
        )
    return mutated


#: G28's defect, reintroduced into the body that fixed it. `TRAN-MERCHANT-NAME` and its neighbour are
#: `PIC X(50)`, so this writes an empty string where the COBOL writes fifty blanks.
_EMPTY_STRING_BODY = _mutate(_COMPLETE_BODY, "CobolText.spaces(50)", '""')

#: G26's defect, in the form the architecture is least able to survive: not a null left behind and
#: flagged, but a fabricated identifier. `TRAN-ID` is `STRING PARM-DATE, WS-TRANID-SUFFIX` -- a job
#: parameter and a per-run counter, neither reachable from a stateless processor -- and the real
#: model left it null and said why. This is what it would look like if the next one did not.
#:
#: **Padded to the right width on purpose**, and that is not a detail. `TRAN-ID` is `PIC X(16)`
#: (`CVTRA05Y:5`), so a bare `"INT0000000001"` would be short and would fail `fixed_width_text`
#: as well -- two defects in one body, which cannot distinguish a judge that found the fabrication
#: from one that flagged the width and stopped. Every case here isolates exactly one criterion, and
#: keeping that true took checking the copybook rather than assuming.
_INVENTED_ID_BODY = _mutate(
    _COMPLETE_BODY,
    'return new Tran(\n    null,\n    "01",',
    'return new Tran(\n    CobolText.pad("INT0000000001", 16),\n    "01",',
)


#: What happens to each step's output after it returns -- keyed by step, so it **cannot** vary with
#: the case and therefore cannot leak which body is defective.
#:
#: **Why this is here at all**, and it is the fifth instance of one defect class. The first billed run
#: flagged `fixed_width_text` on all three `computeInterest` bodies, including the faithful one, and
#: it was *right on the facts*: that step builds a carrier `Tran` with `""` in `PIC X(16)` and
#: `"Int."` in `PIC X(100)`. What makes those placeholders legitimate rather than defective is that
#: `completeTransaction` reads only `tranAmt` off that record and rebuilds every other field -- a fact
#: `design.json` holds in its step chain and the judge was never given. Same shape as G21, G24, G28
#: and G26: *a computed fact this repo holds and never hands over.*
#:
#: **This is not teaching to the test**, and the check is whether a human reviewer would need it. They
#: would: shown only `computeInterest` and its COBOL, a careful reviewer flags `""` in a `PIC X(100)`
#: field too. And it does not blunt the criterion -- `completeTransaction` is terminal, so a short
#: string there is still a defect, which is what `completion_empty_string` pins.
DOWNSTREAM_BY_STEP: dict[str, str] = {
    "computeInterest": (
        "This step's output is consumed by a later step, `completeTransaction`, which reads only "
        "`tran().tranAmt()` from it and constructs the final transaction record itself. Every other "
        "component of the `Tran` this step returns is discarded downstream and never reaches a file, "
        "so a placeholder in one of those fields is carried data rather than written output."
    ),
    "completeTransaction": (
        "This step is terminal: the `Tran` it returns is the transaction record written to the "
        "output file as-is. No later step repopulates any of its fields."
    ),
}


@dataclass(frozen=True)
class EvalCase:
    """One body, the step it implements, and what a competent judge must say about it."""

    name: str
    step: BatchStepDesign
    body: str
    imports: tuple[str, ...]
    #: `None` when the body is faithful. Otherwise the one criterion that must come back `FAIL`.
    #: Exactly one, deliberately: a case that is wrong in two ways cannot distinguish a judge that
    #: found the defect from one that flags everything.
    failing_criterion: str | None
    ground: Ground
    #: The specific evidence for `failing_criterion`, citable by a reviewer. For `ORACLE` cases this
    #: names the rows a real Maven run fails.
    evidence: str


CASES: tuple[EvalCase, ...] = (
    EvalCase(
        name="interest_faithful",
        step=STEP,
        body=_CORRECT_BODY,
        imports=tuple(_IMPORTS),
        failing_criterion=None,
        ground=Ground.ORACLE,
        evidence=(
            "passes all 10 oracle rows under real Maven "
            "(test_a_faithful_body_passes_the_equivalence_test). **The oracle asserts on `tranAmt` "
            "and nothing else**, so this case claims arithmetic and control-flow fidelity, not "
            "byte-fidelity of the whole record -- the first billed run flagged the carrier's "
            "placeholder fields and was right on the facts (see DOWNSTREAM_BY_STEP). Narrowed here "
            "rather than in the bar: calling a body faithful because it passed a one-column check is "
            "a conclusion generalised past its evidence"
        ),
    ),
    EvalCase(
        name="interest_rounds",
        step=STEP,
        body=_ROUNDING_BODY,
        imports=tuple(_IMPORTS),
        failing_criterion="arithmetic_mode",
        ground=Ground.ORACLE,
        evidence=(
            "`divideRounded` is HALF_UP; fails oracle rows R1, R2, R5, R6, R7, R8 under real Maven "
            "(test_rounding_instead_of_truncating_fails_it)"
        ),
    ),
    EvalCase(
        name="interest_unguarded",
        step=STEP,
        body=_ALWAYS_WRITES_BODY,
        imports=tuple(_IMPORTS),
        failing_criterion="guard_applied",
        ground=Ground.ORACLE,
        evidence=(
            "emits a zero-amount transaction where COBOL writes no record; fails oracle row R10 "
            "under real Maven (test_emitting_a_transaction_for_a_zero_rate_fails_it)"
        ),
    ),
    EvalCase(
        name="completion_faithful",
        step=COMPLETE_STEP,
        body=_COMPLETE_BODY,
        imports=tuple(_IMPORTS),
        failing_criterion=None,
        ground=Ground.SOURCE,
        evidence=(
            "the real Opus 5 body from PR #44, which padded every alphanumeric field and left the "
            "two unreachable fields null"
        ),
    ),
    EvalCase(
        name="completion_empty_string",
        step=COMPLETE_STEP,
        body=_EMPTY_STRING_BODY,
        imports=tuple(_IMPORTS),
        failing_criterion="fixed_width_text",
        ground=Ground.SOURCE,
        evidence=(
            "TRAN-MERCHANT-NAME is PIC X(50) in CVTRA05Y, so `\"\"` is 50 bytes short of what "
            "`MOVE SPACES` writes (audit G28)"
        ),
    ),
    EvalCase(
        name="completion_invented_tran_id",
        step=COMPLETE_STEP,
        body=_INVENTED_ID_BODY,
        imports=tuple(_IMPORTS),
        failing_criterion="no_invented_values",
        ground=Ground.SOURCE,
        evidence=(
            "TRAN-ID is `STRING PARM-DATE, WS-TRANID-SUFFIX` -- a job parameter and a per-run "
            "counter, neither reachable from a stateless processor (audit G26)"
        ),
    ),
)

CASES_BY_NAME = {case.name: case for case in CASES}

FAITHFUL_CASES = tuple(case for case in CASES if case.failing_criterion is None)
UNFAITHFUL_CASES = tuple(case for case in CASES if case.failing_criterion is not None)
