"""Where this package's own data files live, and one loud failure when they do not.

**This module exists because a built wheel did not work at all** (ADR-0055). `prompts/registry/`,
`config/` and `templates/` sat beside `src/` at the repository root, and six modules reached them
with `Path(__file__).resolve().parents[3]`. In a checkout that is the repo root and the expression
is correct. In an installed package it is `<venv>/Lib/`, and the data is not there in any case:
`setuptools` packages what is under `src/`, so the wheel contained **zero non-Python files**.

The defect survived because CI installs with `pip install -e ".[dev]"` — an editable install, where
the package is still in the source tree and `parents[3]` still lands on the repo root. Every test
passed against a layout no consumer would ever have.

`schemas/` deliberately stays at the repository root and is **not** package data. Its only readers
are `scripts/generate_schemas.py` and `tests/contract/test_schemas.py`; nothing in the CLI's runtime
path opens it. Moving it would have been motion rather than a fix.
"""

from __future__ import annotations

from pathlib import Path

#: Root of the package's own data, resolved relative to this module rather than to a repository
#: layout. `core/package_data.py` -> `core/` -> `cobol_modernizer/` -> `cobol_modernizer/data`,
#: which is the same path in a source checkout and in an installed wheel. That equivalence is the
#: whole point: there is no "installed or not" branch to get wrong, and no environment variable to
#: forget to set.
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"

PROMPTS_ROOT = DATA_ROOT / "prompts" / "registry"
CONFIG_ROOT = DATA_ROOT / "config"
TEMPLATES_ROOT = DATA_ROOT / "templates"


class PackageDataMissingError(Exception):
    """Packaged data is absent — the install is broken, not the invocation.

    Separate from every `*ParseError` and from `UnsupportedPicConstructError` because it says
    something different: not "this COBOL is out of scope" and not "the model answered badly", but
    "this build of the specialist cannot run at all." A caller cannot fix it by choosing a
    different program or retrying.
    """


def require(path: Path, *, what: str) -> Path:
    """Return `path`, or fail with a message that names the install rather than the symptom.

    The pattern is `control-plane`'s `_require_claude_cli`, and for its stated reason: it "fails
    with that explanation rather than letting a bare 'No such file or directory' surface from
    subprocess." Before this, a wheel-installed run died with

        FileNotFoundError: ...\\probe-venv\\Lib\\prompts\\registry\\spec_extractor\\v1_0_0.md

    — a path that names a directory nobody ever created, in a tree nobody would think to look at,
    for a reason ("the wheel carries no data files") that the message does not contain. Anyone
    debugging that starts by looking for a missing prompt file. The real answer is that the
    package is built wrong.
    """
    if not path.exists():
        raise PackageDataMissingError(
            f"{what} is missing from this installation of cobol-modernizer: {path}\n"
            f"Expected it under {DATA_ROOT}, which is packaged data rather than a repository path. "
            "A wheel built without the `cobol_modernizer.data` package data, or an incomplete "
            "install, produces exactly this. See ADR-0055."
        )
    return path
