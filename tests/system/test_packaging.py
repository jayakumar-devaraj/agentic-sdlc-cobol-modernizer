"""The package carries its own data, proven against a real wheel rather than the source tree.

**Every other test in this repo runs against `pip install -e .`, and that is exactly why this file
exists.** An editable install leaves the package inside the checkout, so a module reaching data by
`Path(__file__).resolve().parents[3]` lands on the repository root and works. A wheel install puts
it in `site-packages`, where the same expression is `<venv>/Lib/` and the data is not there at all
— `setuptools` packages what is under `src/`, and the four data directories were not.

That was the state of the repository before ADR-0055: a built wheel contained **zero non-Python
files**, and `cobol-modernizer design` on a clean install died with

    FileNotFoundError: ...\\Lib\\prompts\\registry\\spec_extractor\\v1_0_0.md

The green suite could not see it. So the check here is not "the constants point somewhere" — that
passes in a checkout no matter how wrong the packaging is. It is: **build a wheel, install it into a
throwaway environment with no source tree in sight, and ask that installation for its own data.**
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Runtime data the CLI cannot start without. `schemas/` is deliberately absent -- its only readers
#: are `scripts/generate_schemas.py` and `tests/system/test_schemas.py`, neither on the runtime path.
REQUIRED_DATA = (
    "cobol_modernizer/data/prompts/registry/spec_extractor/v1_0_0.md",
    "cobol_modernizer/data/config/model_routing.yaml",
    "cobol_modernizer/data/config/model_catalog.yaml",
    "cobol_modernizer/data/templates/target-spring-boot-baseline/pom.xml",
)


def _run(*command: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=600, check=False,
        # A build must not inherit a parent's build settings, and an install must never reach the
        # network for this project itself.
        env={**os.environ, "PIP_DISABLE_PIP_VERSION_CHECK": "1"},
    )


@pytest.fixture(scope="module")
def wheel(tmp_path_factory) -> Path:
    """A real wheel, built from this checkout."""
    outdir = tmp_path_factory.mktemp("dist")
    result = _run(sys.executable, "-m", "build", "--wheel", "--outdir", str(outdir), cwd=REPO_ROOT)
    if result.returncode != 0:
        pytest.skip(f"`python -m build` unavailable or failed: {result.stderr[-400:]}")
    wheels = list(outdir.glob("*.whl"))
    assert len(wheels) == 1, f"expected exactly one wheel, got {wheels}"
    return wheels[0]


def test_the_wheel_contains_every_runtime_data_file(wheel: Path) -> None:
    """Read straight out of the archive -- the cheap half of the check, and the one that localises
    a failure to packaging rather than to installation."""
    names = set(zipfile.ZipFile(wheel).namelist())

    missing = [required for required in REQUIRED_DATA if required not in names]
    assert not missing, (
        f"the wheel is missing runtime data: {missing}. "
        "Check [tool.setuptools.package-data] in pyproject.toml -- see ADR-0055."
    )


def test_the_wheel_carries_the_whole_java_baseline(wheel: Path) -> None:
    """Not just the pom. The baseline is copied wholesale into every generated project, so a
    partial copy produces a project that fails to compile for a reason nothing in the loop
    attributes correctly."""
    names = zipfile.ZipFile(wheel).namelist()
    prefix = "cobol_modernizer/data/templates/target-spring-boot-baseline/"

    java = [n for n in names if n.startswith(prefix) and n.endswith(".java")]

    source_java = list(
        (REPO_ROOT / "src/cobol_modernizer/data/templates/target-spring-boot-baseline").rglob("*.java")
    )
    assert len(java) == len(source_java), (
        f"wheel has {len(java)} baseline .java files, the source tree has {len(source_java)}"
    )


@pytest.mark.slow
def test_an_installed_wheel_can_read_its_own_data(wheel: Path, tmp_path: Path) -> None:
    """The decisive check: a throwaway environment, no checkout on any path it consults.

    Deliberately does **not** invoke `design` or `generate`. Those reach a live model on the first
    node, which costs real money -- and the packaging question is answered entirely by whether the
    installed package can find its own files.
    """
    env_dir = tmp_path / "probe-venv"
    venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    install = _run(str(python), "-m", "pip", "install", "--quiet", str(wheel))
    assert install.returncode == 0, f"install failed: {install.stderr[-600:]}"

    probe = _run(str(python), "-c", (
        "from cobol_modernizer.core.package_data import DATA_ROOT;"
        "from cobol_modernizer.prompts_registry_client.loader import read_prompt;"
        "from cobol_modernizer.core.model_catalog import load_catalog;"
        "from cobol_modernizer.graph.generate_pipeline import TEMPLATE_DIR;"
        "assert DATA_ROOT.is_dir(), DATA_ROOT;"
        "assert len(read_prompt('spec_extractor')) > 500;"
        "assert len(read_prompt('spec_critic', 'v1_1_0')) > 500;"
        "assert load_catalog();"
        "assert (TEMPLATE_DIR / 'pom.xml').is_file();"
        "print('ok')"
    ))

    assert probe.returncode == 0, (
        "an installed wheel could not read its own packaged data:\n" + probe.stderr[-1200:]
    )
    assert "ok" in probe.stdout


def test_package_data_missing_names_the_install_not_the_file() -> None:
    """A missing data file must not surface as a bare `FileNotFoundError` on a path nobody
    recognises. `control-plane`'s `_require_claude_cli` is the precedent it follows."""
    from cobol_modernizer.core.package_data import PackageDataMissingError, require

    with pytest.raises(PackageDataMissingError) as caught:
        require(Path("/nonexistent/prompts/spec_extractor/v9_9_9.md"), what="the spec_extractor prompt")

    message = str(caught.value)
    assert "installation of cobol-modernizer" in message
    assert "ADR-0055" in message
