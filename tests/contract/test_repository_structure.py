"""The repository's shape, asserted rather than described.

Without this, the next contributor inherits a tree that is *mostly* the documented one with no way
to tell which parts were deliberate. Every assertion here is about **shape, never content**: where
things live, that a declared set is complete, that a path someone wrote down still resolves. None
of them reads what a file says.

**Why this is in the contract tier and does no subprocess I/O.** The tier means "would a change
here break a consumer, and no real external dependency" -- `test_schemas.py` next door reads files
too. This module deliberately uses `pathlib` rather than `git ls-files`: shelling out would put it
in the integration tier, and the properties it checks are all answerable from the working tree.

**Three of these exist because the defects were real, on this repository, and nothing noticed.**
A structure audit found, in one pass:

* `.gitattributes` guarding `templates/target-spring-boot-baseline/mvnw`, a path that had not
  existed since the template moved inside the package. `git check-attr` reported `unspecified` for
  the real file, so the CRLF protection had been off for months while looking present.
* `.claude/skills/verify-self-healing-loop.md` naming `tests/system/test_self_healing_loop.py`,
  which has never existed in any branch.
* `.claude/skills/run-demo.md` naming two files that were never written.

All three are one defect class: **a committed file names a path, and nothing checks the path
resolves.** `test_every_gitattributes_pattern_matches_something` and
`test_every_repo_path_named_in_a_skill_exists` are that check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.conftest import TIERS

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"

#: Directories under `tests/` that hold no tests. Everything else must be a tier.
NON_TIER_DIRS = {"fixtures", "__pycache__"}

#: Tiers that may contain test modules. `support` is a tier for import purposes -- `conftest.py`
#: accepts it so a fixture module is not a collection error -- but it holds no tests.
TEST_TIERS = {name for name in TIERS if name != "support"}

#: Ordered outermost-last. A test may import from its own tier or from any tier *below* it; the
#: reverse is an inversion, and the whole point of extracting `tests/support/` was to remove the
#: seven that existed. `support` is below everything because it is shared fixtures.
TIER_RANK = {"support": 0, "unit": 1, "contract": 1, "integration": 2, "evaluation": 3}

#: Substrings that mark a path in a skill as a template placeholder rather than a real file.
PLACEHOLDERS = ("NNNN", "YYYY", "<", ">", "*")


def _test_modules() -> list[Path]:
    return sorted(p for p in TESTS_ROOT.rglob("test_*.py") if "__pycache__" not in p.parts)


# --- Where things live --------------------------------------------------------------------------


def test_the_package_lives_under_src_and_nowhere_else():
    """`src/` earns its keep here because something installs this (ADR-0055).

    A second copy at the repository root is the failure this prevents: tests would import the
    working tree while a consumer got the wheel, and the two could drift without anything failing.
    """
    assert (REPO_ROOT / "src" / "cobol_modernizer" / "__init__.py").is_file()
    assert not (REPO_ROOT / "cobol_modernizer").exists(), (
        "a second copy of the package at the repository root shadows the installed one"
    )


def test_the_package_ships_the_py_typed_marker():
    """Without it a consumer's type checker infers `Any` for every import (ADR-0061)."""
    assert (REPO_ROOT / "src" / "cobol_modernizer" / "py.typed").is_file()


@pytest.mark.parametrize(
    "name",
    [
        "README.md",
        "CLAUDE.md",
        "AGENTS.md",
        "CONTRIBUTING.md",
        "SECURITY.md",
        "LICENSE",
        "CODEOWNERS",
        "pyproject.toml",
        "uv.lock",
        ".gitignore",
        ".gitattributes",
        "docker-compose.yml",
    ],
)
def test_the_governance_and_build_files_are_present(name):
    assert (REPO_ROOT / name).is_file(), f"{name} is missing from the repository root"


# --- The test tiers -----------------------------------------------------------------------------


def test_every_test_module_is_in_a_tier_directory():
    """`conftest.py` already refuses to collect an untiered test; this names them all at once.

    The conftest guard fails a *run*. This fails on the shape, so a module added to the wrong place
    is a structural error with a list of what is wrong, not one `UsageError` at a time.
    """
    stray = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in _test_modules()
        if p.relative_to(TESTS_ROOT).parts[0] not in TEST_TIERS
    ]
    assert not stray, f"test modules outside a tier directory: {stray}"


def test_the_tier_directories_are_exactly_the_declared_set():
    """A new directory under `tests/` is a decision, not an accident.

    Pins both directions: a tier declared in `conftest.TIERS` but never created, and a directory
    created without being declared.
    """
    actual = {d.name for d in TESTS_ROOT.iterdir() if d.is_dir()} - NON_TIER_DIRS
    assert actual == set(TIERS), (
        f"directories under tests/ ({sorted(actual)}) do not match conftest.TIERS "
        f"({sorted(TIERS)})"
    )


def test_no_tier_imports_from_a_tier_above_it():
    """The inversion this repository actually had, made permanent.

    Before `tests/support/` existed, six unit-tier modules imported design constants from
    `test_interest_equivalence` and `test_account_break_posting` -- both of which drive real Maven
    builds. `tests/unit/` depended on `tests/integration/`, and every one of those imports worked,
    so nothing failed and nothing would have.

    `tests/evaluation/test_corpus.py` importing integration modules is *not* an inversion and must
    keep passing: evaluation is the outermost tier, and it imports those modules deliberately, to
    assert the test functions its corpus cites still exist.
    """
    pattern = re.compile(r"from tests\.(\w+)[. ]|from tests\.(\w+) import")
    inversions = []
    for module in _test_modules() + sorted(TESTS_ROOT.glob("*/[!t]*.py")):
        parts = module.relative_to(TESTS_ROOT).parts
        if len(parts) < 2 or parts[0] not in TIER_RANK:
            continue
        importer = parts[0]
        for match in pattern.finditer(module.read_text(encoding="utf-8")):
            provider = match.group(1) or match.group(2)
            if provider in TIER_RANK and TIER_RANK[provider] > TIER_RANK[importer]:
                inversions.append(
                    f"{module.relative_to(REPO_ROOT).as_posix()} ({importer}) imports {provider}"
                )
    assert not inversions, "a tier imports one above it:\n  " + "\n  ".join(sorted(set(inversions)))


# --- Paths that were written down -----------------------------------------------------------


def test_every_gitattributes_pattern_matches_something():
    """The `mvnw` defect, made impossible to repeat.

    A `.gitattributes` rule whose pattern matches nothing is silently inert: `git check-attr`
    reports `unspecified`, the guard does not apply, and the file looks correct. This repository
    shipped exactly that for months after a path moved.
    """
    dead = []
    for line in (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        pattern = stripped.split()[0]
        if not any(REPO_ROOT.glob(pattern)):
            dead.append(pattern)
    assert not dead, (
        f".gitattributes patterns matching no file, so their rules never apply: {dead}"
    )


def test_every_repo_path_named_in_a_skill_exists():
    """The two dead skill references, made impossible to repeat.

    A skill is instructions an agent follows literally. One naming a test module that never existed
    sends that agent looking for it, and the failure surfaces as confusion rather than as an error.
    """
    pattern = re.compile(r"`((?:docs|src|tests|tools|scripts|\.claude|\.github)/[\w./-]+)`")
    missing = []
    for skill in sorted((REPO_ROOT / ".claude" / "skills").glob("*.md")):
        for match in pattern.finditer(skill.read_text(encoding="utf-8")):
            path = match.group(1)
            # A placeholder in a template is not a stale path. `new-adr.md` tells an author to
            # create `docs/adr/NNNN-descriptive-sentence-slug.md`, and that file is *supposed* not
            # to exist -- naming it is the instruction.
            if any(token in path for token in PLACEHOLDERS):
                continue
            if not (REPO_ROOT / path).exists():
                missing.append(f"{skill.name} names {path!r}")
    assert not missing, "skills naming paths that do not exist:\n  " + "\n  ".join(missing)


# --- Numbered and generated sets ----------------------------------------------------------------


def test_adr_numbers_run_from_one_without_gaps():
    """A gap means a record was deleted or never written, and the next author cannot tell which."""
    numbers = sorted(
        int(m.group(1))
        for p in (REPO_ROOT / "docs" / "adr").glob("*.md")
        if (m := re.match(r"(\d{4})-", p.name))
    )
    assert numbers, "no ADRs found"
    assert numbers == list(range(1, len(numbers) + 1)), (
        f"ADR numbering has gaps or duplicates: {numbers}"
    )


def test_every_exported_schema_has_a_committed_file():
    """`test_schemas.py` checks the *content* matches the models; this checks the set is complete.

    A model added to `schema_export.SCHEMA_EXPORTS` without regenerating leaves a name with no file, which
    is a different failure from a file whose content has drifted.
    """
    from cobol_modernizer.core.schema_export import SCHEMA_EXPORTS

    schemas_dir = REPO_ROOT / "schemas"
    missing = [name for name in SCHEMA_EXPORTS if not (schemas_dir / name).is_file()]
    assert not missing, f"exported schemas with no committed file: {missing}"

    orphans = [p.name for p in schemas_dir.glob("*.schema.json") if p.name not in SCHEMA_EXPORTS]
    assert not orphans, f"committed schemas nothing exports: {orphans}"
