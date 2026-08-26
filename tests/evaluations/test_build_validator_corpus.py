"""The corpus's own grounds, checked. Free -- no model is called anywhere in this module.

**A corpus is an instrument, and an instrument nobody calibrated measures nothing.** `corpus.py`'s
`ORACLE` cases are trustworthy because a real JVM already failed those bodies. This corpus makes two
claims of the same kind, and this module is where they stop being claims:

- `COMPILER_PROVEN` -- the four repairable cases really are step 43's, not a drifted copy.
- `SYMBOL_ABSENT` -- the symbol each blocked case needs really is absent from a **rendered project**,
  so "no rewrite reaches it" is a fact about the target rather than an opinion about the code.

The second is the load-bearing one. If a symbol this file calls absent turned out to exist, the
benchmark would be marking a correct `repairable` verdict wrong and reporting a model as worse than
it is.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cobol_modernizer.core.package_data import TEMPLATES_ROOT
from tests.evaluations.build_validator_corpus import (
    CASES,
    GRADED,
    Ground,
    Verdict,
    case_by_name,
)
from tests.system.test_generate_pipeline import _INJECTED_ERRORS

TEMPLATE = TEMPLATES_ROOT / "target-spring-boot-baseline"


@pytest.fixture(scope="module")
def rendered_project(tmp_path_factory) -> Path:
    """A real copy of the baseline every generated project starts from.

    The blocked cases' ground is a statement about what a generated project contains, so it is
    checked against a project rather than against this repo's source tree.
    """
    destination = tmp_path_factory.mktemp("corpus-proj") / "proj"
    shutil.copytree(TEMPLATE, destination, ignore=shutil.ignore_patterns("target"))
    return destination


def _project_text(project: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(project.rglob("*.java"))
    )


# --- The two mechanical grounds -------------------------------------------------------------


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.ground is Ground.SYMBOL_ABSENT], ids=lambda c: c.name
)
def test_a_symbol_absent_case_names_a_symbol_the_project_really_lacks(case, rendered_project):
    """The ground itself, enforced. Without this the blocked half is an assertion about nothing.

    Checked against every `.java` file a generated project starts with -- the baseline the renderer
    copies in, which is where `CobolArithmetic`, `CobolRecord` and the rest live. A symbol found
    here would be one a rewrite could legitimately reach, and the case would be mislabelled.
    """
    assert case.absent_symbol, f"{case.name} claims SYMBOL_ABSENT but names no symbol"
    assert case.absent_symbol not in _project_text(rendered_project), (
        f"{case.name} is graded `blocked` because {case.absent_symbol!r} does not exist, "
        "but it is present in the rendered project -- the case is mislabelled, not the model"
    )


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.ground is Ground.SYMBOL_ABSENT], ids=lambda c: c.name
)
def test_a_symbol_absent_case_actually_references_the_symbol_it_names(case):
    """A case whose body no longer mentions its own absent symbol would pass the test above by
    saying nothing -- the check-that-cannot-fail shape this package exists to avoid."""
    haystack = case.body + " " + " ".join(case.imports)
    assert case.absent_symbol in haystack, (
        f"{case.name} names {case.absent_symbol!r} as its absent symbol, but neither its body nor "
        "its imports reference it"
    )


def test_the_repairable_cases_are_step_43s_and_have_not_drifted():
    """`COMPILER_PROVEN` means a real Maven run repaired *this* body. Imported, so it cannot drift
    -- and asserted anyway, because an import that silently started resolving elsewhere would leave
    the ground claiming a run that never happened."""
    injected = {name: (body, tuple(imports)) for name, body, imports in _INJECTED_ERRORS}

    proven = [c for c in CASES if c.ground is Ground.COMPILER_PROVEN]
    assert {c.name for c in proven} == set(injected), (
        "the compiler-proven cases and step 43's injected classes have diverged"
    )
    for case in proven:
        assert (case.body, case.imports) == injected[case.name]
        assert case.expected is Verdict.REPAIRABLE


# --- The corpus is a corpus ------------------------------------------------------------------


def test_both_verdicts_are_represented_and_neither_dominates():
    """A corpus of one verdict measures nothing: a validator that always answers `blocked` would
    score perfectly on an all-blocked corpus, and it is a validator that stops every heal."""
    repairable = [c for c in CASES if c.expected is Verdict.REPAIRABLE]
    blocked = [c for c in CASES if c.expected is Verdict.BLOCKED]

    assert len(repairable) >= 3 and len(blocked) >= 3
    ratio = len(repairable) / len(blocked)
    assert 0.5 <= ratio <= 2.0, f"the corpus is lopsided ({len(repairable)}:{len(blocked)})"


def test_every_case_states_how_its_verdict_is_known():
    for case in CASES:
        assert case.grounding.strip(), f"{case.name} has no grounding"
        assert len(case.grounding) > 60, (
            f"{case.name}'s grounding is too thin to check: {case.grounding!r}"
        )


def test_case_names_are_unique():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names))


def test_repo_history_cases_are_excluded_from_the_graded_set():
    """The bar applies to mechanical grounds only. This is the line `corpus.py` drew between its
    oracle-grounded and source-grounded cases, drawn again here rather than re-argued."""
    assert all(c.ground is not Ground.REPO_HISTORY for c in GRADED)
    assert len(GRADED) < len(CASES), "nothing is being reported-but-not-asserted; is that right?"


def test_case_by_name_raises_on_an_unknown_case():
    with pytest.raises(KeyError):
        case_by_name("no_such_case")
