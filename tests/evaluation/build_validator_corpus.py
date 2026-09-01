"""The cases `build_validator` is measured against, and how each expected verdict is known.

**Why this corpus exists.** ADR-0056 wrapped this node's prompt and could not say whether the change
moved its judgment, because **nothing has ever measured that judgment**. Its own tests are scripted
on both sides (`_advise(True)` returns a fixed verdict), and step 43's four-error-class harness says
in its docstring that what it tests is *the loop* -- compile, judge, re-prompt, recompile -- "not
whether a model can repair them". So the node that decides how the heal budget is spent has shipped
a prompt change with no instrument pointed at it.

**The expensive direction is `blocked` judged `repairable`.** The system prompt says it: a failure
called repairable when it is not spends every attempt rewriting statements that were never the
problem, and hands a human three worse versions of the same code. The other direction stops a
fixable build early, which is cheaper -- a person gets a legible problem.

**Nothing here is graded against my reading of anything.** `tests/evaluation/corpus.py` had to
report its source-grounded cases rather than assert on them, because turning a reading of COBOL into
a pass/fail bar promotes an interpretation to ground truth. This corpus does not have that problem,
and the reason is worth stating: **both of its verdicts are mechanically checkable.**

- A case is provably **repairable** when the loop has actually repaired it. The four injected error
  classes are exactly that -- `test_the_loop_heals_every_injected_error_class` compiles, re-prompts
  and recompiles each one under real Maven. A rewrite demonstrably fixes them.
- A case is provably **blocked** when the symbol it needs **does not exist anywhere in the target
  project**. No rewrite of a method body conjures a class the project does not contain, and no
  import reaches one either. `test_build_validator_corpus.py` asserts that absence against a real
  rendered project rather than taking this file's word for it.

That is why every case below carries a `ground`, and why the benchmark applies hard bars only to the
two mechanical grounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# The injected error classes, imported rather than restated -- the same rule `corpus.py` follows for
# its bodies. These four are the ones step 43's harness drives through a real Maven build, so a copy
# that drifted would leave `COMPILER_PROVEN` claiming something no compiler ever saw.
from tests.integration.test_generate_pipeline import _INJECTED_ERRORS


class Verdict(str, Enum):
    REPAIRABLE = "repairable"
    BLOCKED = "blocked"


class Ground(str, Enum):
    """How a case's expected verdict is known. Only the first two carry a hard bar."""

    #: The heal loop has repaired this exact body under real Maven. A rewrite fixes it, demonstrably.
    COMPILER_PROVEN = "compiler_proven"
    #: The symbol the body needs is absent from the whole rendered project, asserted by a test.
    SYMBOL_ABSENT = "symbol_absent"
    #: A defect this repo actually produced, whose classification is this repo's reading of it.
    #: **Reported, never asserted on** -- the same posture `corpus.py` takes with its source-grounded
    #: cases, and for the same reason.
    REPO_HISTORY = "repo_history"


@dataclass(frozen=True)
class ValidatorCase:
    """One failing build, and the verdict a correct `build_validator` returns for it."""

    name: str
    body: str
    imports: tuple[str, ...]
    expected: Verdict
    ground: Ground
    #: How the expected verdict is known. Prose, but prose that names a check or an event.
    grounding: str
    #: For `SYMBOL_ABSENT`: the identifier that must not exist in the rendered project. The test
    #: that enforces this ground reads it from here rather than re-deriving it from `body`.
    absent_symbol: str = ""


#: Cases whose fix is unambiguous once the diagnostic is read: correct the method name, correct the
#: import, correct the return type. `COMPILER_PROVEN` means the heal loop repairs the exact body
#: under real Maven.
#:
#: **`missing_import` is deliberately not in this set** -- see `_AMBIGUOUS` below. The label was
#: wrong from the start and a run found it.
_UNAMBIGUOUS = {"unknown_method", "unresolved_import", "wrong_return"}

_REPAIRABLE: list[ValidatorCase] = [
    ValidatorCase(
        name=name,
        body=body,
        imports=tuple(imports),
        expected=Verdict.REPAIRABLE,
        ground=Ground.COMPILER_PROVEN,
        grounding=(
            "step 43's injected error class of the same name; "
            "`test_the_loop_heals_every_injected_error_class` repairs it under real Maven, and the "
            "fix the diagnostic implies is unambiguous -- a name, an import or a return type is "
            "corrected, not a statement deleted on a guess about intent"
        ),
    )
    for name, body, imports in _INJECTED_ERRORS
    if name in _UNAMBIGUOUS
]

#: **Demoted from `COMPILER_PROVEN` at the second billed run (2026-08-26), and the reasoning matters
#: more than the reclassification.**
#:
#: `missing_import`'s body is `Tran t = null; return item;`. Step 43's loop does repair it -- but it
#: repairs it with a **scripted** author that returns `return item;` on the second attempt. That
#: proves a rewrite *exists*; it does not prove the error is unambiguously located in the
#: statements, which is what `COMPILER_PROVEN` was defined to mean. The other three injected classes
#: name a fix the diagnostic itself implies. This one requires **deleting a statement whose intent
#: is unknowable**.
#:
#: The model said so, in both samples, without being asked: *"while the variable is never used (dead
#: code), it is unclear whether this represents incomplete or incorrect translation requiring a
#: design-level fix ... or whether safe removal is correct."* That is this node's system prompt being
#: followed to the letter -- *"When you are not sure, answer `false` and say what you would need."*
#:
#: **This is a bar being moved after a failure, which is the shape that deserves suspicion**, so the
#: distinction is stated rather than implied: the ground was over-claimed when it was written, and
#: the run is what exposed it. What is *not* being done is tuning the prompt until the corpus
#: passes -- the prompt is untouched, and the case stays in the corpus with its expected verdict
#: intact. Only its claim to mechanical certainty is withdrawn.
_AMBIGUOUS: list[ValidatorCase] = [
    ValidatorCase(
        name=name,
        body=body,
        imports=tuple(imports),
        expected=Verdict.REPAIRABLE,
        ground=Ground.REPO_HISTORY,
        grounding=(
            "step 43's `missing_import` class. A rewrite fixes it -- deleting the dead reference "
            "compiles -- so `repairable` is defensible. But the loop's own proof used a scripted "
            "author, and deciding that removal is *correct* needs the COBOL's intent, which this "
            "node is not given. Reported, not asserted: both readings are honest"
        ),
    )
    for name, body, imports in _INJECTED_ERRORS
    if name not in _UNAMBIGUOUS
]


#: The blocked cases. Three are mechanically grounded on an absent symbol; one is repo history.
#:
#: **`missing_composite_type` is the case this node was built for.** `build_validator`'s own module
#: docstring cites it: *"cannot find symbol: class TranCatBalWithRate -- a type the design named and
#: nothing generates, which no rewrite of this method will conjure"*, and it records that the first
#: real `generate` call in this repo failed for exactly that reason, and that a loop lacking this
#: check would have retried it three times.
_BLOCKED: list[ValidatorCase] = [
    ValidatorCase(
        name="missing_composite_type",
        body="TranCatBalWithRate joined = item; return joined;",
        imports=(),
        expected=Verdict.BLOCKED,
        ground=Ground.SYMBOL_ABSENT,
        absent_symbol="TranCatBalWithRate",
        grounding=(
            "the design named a composite the renderer does not emit for this step; the class is "
            "absent from the rendered project, so no rewrite and no import reaches it. This is the "
            "failure `build_validator` exists for -- see its module docstring"
        ),
    ),
    ValidatorCase(
        name="missing_record_component",
        body="return item.tranCatBalWithNoSuchComponent();",
        imports=(),
        expected=Verdict.BLOCKED,
        ground=Ground.SYMBOL_ABSENT,
        absent_symbol="tranCatBalWithNoSuchComponent",
        grounding=(
            "an accessor that is not on the record. G24 is the real instance: shown a composite as "
            "a bare type name, a model guessed accessors twice -- `item.tranCatBal()` and then "
            "`item.getTranCatBal()` -- and said in its notes it was guessing. A component the "
            "record does not declare cannot be rewritten into existence"
        ),
    ),
    ValidatorCase(
        name="unreachable_design_type",
        body="AccountWithDisclosureGroup a = item; return a;",
        imports=("com.modernized.batch.domain.AccountWithDisclosureGroup",),
        expected=Verdict.BLOCKED,
        ground=Ground.SYMBOL_ABSENT,
        absent_symbol="AccountWithDisclosureGroup",
        grounding=(
            "the same shape as `missing_composite_type` but reached through an import rather than a "
            "bare reference, so a model cannot resolve it by 'adding the missing import' -- the "
            "import is already there and resolves to nothing"
        ),
    ),
    ValidatorCase(
        name="job_level_fact_not_on_the_item",
        body="return item.withProcessedAt(jobRunTimestamp);",
        imports=(),
        expected=Verdict.BLOCKED,
        ground=Ground.REPO_HISTORY,
        grounding=(
            "a value that is not reachable from the method's inputs at all. The engineer prompt "
            "records the real case: a timestamp from `FUNCTION CURRENT-DATE` is a job-level fact "
            "belonging to the invocation, and a stateless processor has no access to it. The honest "
            "fix is that the design must supply a job parameter (ADR-0026), which is outside a "
            "method body. **Reported, not asserted**: a validator might reasonably call deleting "
            "the call the repairable part, and grading that reading would promote it to ground "
            "truth"
        ),
    ),
]


CASES: tuple[ValidatorCase, ...] = tuple(_REPAIRABLE + _AMBIGUOUS + _BLOCKED)

#: The cases a bar may be applied to. `REPO_HISTORY` is deliberately excluded -- see `Ground`.
GRADED: tuple[ValidatorCase, ...] = tuple(
    case for case in CASES if case.ground is not Ground.REPO_HISTORY
)


def case_by_name(name: str) -> ValidatorCase:
    for case in CASES:
        if case.name == name:
            return case
    raise KeyError(f"no case named {name!r}; have {[c.name for c in CASES]}")
