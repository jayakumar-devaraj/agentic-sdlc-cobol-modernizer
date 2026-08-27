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
import shutil
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
    """A real wheel, built from this checkout into a fresh `build/` tree.

    `build/` is removed first for determinism: `setuptools` stages into `build/lib/` and does not
    clean it between builds, so in principle a file from an earlier build could survive into a later
    one. **Stated as a precaution rather than as a diagnosis** — a damage probe found no difference
    with and without the removal, so this is not the explanation for anything observed here.

    **What that probe did establish** is worth knowing before trusting this module: deleting
    `data/templates/**/*` from `[tool.setuptools.package-data]` does *not* make these tests fail,
    because setuptools defaults `include_package_data` to true for pyproject-configured projects and
    the data now lives inside the package. Only disabling that default *and* removing the glob
    produces an empty wheel. So these tests cannot be damage-probed by editing one line of
    `package-data`; what they do guard is the property that matters — the files are in the built
    artifact, and an installed copy can read them — which holds regardless of which mechanism put
    them there.
    """
    stale = REPO_ROOT / "build"
    if stale.exists():
        shutil.rmtree(stale)

    # `build/` was the precaution this fixture documented, and it is not the directory that
    # carried stale state. `src/*.egg-info/SOURCES.txt` is: a manifest left from an earlier build
    # reintroduces **26 files of `target/` output** -- compiled `.class` files, a `.jar`,
    # `maven-status/*.lst` -- none of them tracked by git, and it does so *past*
    # `[tool.setuptools.exclude-package-data]`, which the manifest path does not consult.
    #
    # **Both levers are required, and that came from running the 2x2 rather than reasoning about
    # it.** The first attempt at this comment asserted the opposite on a control that varied the
    # wrong variable:
    #
    #   exclusion + stale egg-info -> 26   |   exclusion + fresh egg-info -> 0
    #   no exclusion + fresh egg-info -> 26
    #
    # A `MANIFEST.in prune` was the third candidate and is genuinely redundant once the other two
    # hold, so it is not in this repository. See ADR-0058.
    for egg_info in (REPO_ROOT / "src").glob("*.egg-info"):
        shutil.rmtree(egg_info)

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


def _tracked_data_files() -> set[str]:
    """Every file under the package's data directory that git actually tracks, as wheel paths.

    Deliberately not a hand-written list. `REQUIRED_DATA` above names four files, so the fifth one
    to go missing passes green -- and one did: `.mvn/wrapper/maven-wrapper.properties` was tracked,
    was absent from every wheel this repo has ever built, and both existing checks passed anyway
    because one lists four paths and the other counts only `.java`. Deriving the expectation from
    git is what makes the *next* omission loud instead of the next-but-one.
    """
    result = _run("git", "-C", str(REPO_ROOT), "ls-files", "-z", "src/cobol_modernizer/data")
    assert result.returncode == 0, (
        "cannot derive the expected data set: `git ls-files` failed in "
        f"{REPO_ROOT}. This check compares the built wheel against what the repository "
        f"tracks, so it needs a checkout rather than an unpacked sdist. {result.stderr[-300:]}"
    )
    tracked = {path.removeprefix("src/") for path in result.stdout.split("\0") if path}
    assert tracked, "git tracks no files under src/cobol_modernizer/data -- that cannot be right"
    return tracked


def _wheel_data_files(wheel: Path) -> set[str]:
    return {
        name
        for name in zipfile.ZipFile(wheel).namelist()
        if name.startswith("cobol_modernizer/data/") and not name.endswith("/")
    }


def test_the_wheel_carries_every_data_file_the_repository_tracks(wheel: Path) -> None:
    """The general form of ADR-0055's defect, which its own fix did not fully close.

    A tracked file can be absent from the wheel because `[tool.setuptools.package-data]`'s globs
    do not reach it -- `**/*` does not descend into a dot-directory, which is how the Maven
    wrapper's `.mvn/wrapper/` went missing. The consequence was not cosmetic: `local_compiler`
    prefers `./mvnw`, and an installed baseline without that file exits 1 with
    "Cannot start maven from wrapper" before Maven ever starts.
    """
    missing = sorted(_tracked_data_files() - _wheel_data_files(wheel))
    assert not missing, (
        f"the wheel is missing {len(missing)} file(s) the repository tracks: {missing}. "
        "Check [tool.setuptools.package-data] in pyproject.toml -- note that `**/*` does not "
        "match inside dot-directories. See ADR-0055 and ADR-0058."
    )


def test_the_wheel_carries_nothing_the_repository_does_not(wheel: Path) -> None:
    """The other direction, which matters because a release can be cut from a working tree.

    `include_package_data` collects what is *on disk*, not what is committed, so a developer who
    has ever run the Java baseline's build has 26 files of `target/` output -- compiled `.class`
    files and a `.jar` -- sitting inside the package data directory. Nothing stopped those from
    being packaged into a wheel and shipped to every consumer as if they were baseline sources.
    `.gitignore` does not protect against this; `[tool.setuptools.exclude-package-data]` does.
    """
    extra = sorted(_wheel_data_files(wheel) - _tracked_data_files())
    assert not extra, (
        f"the wheel carries {len(extra)} file(s) the repository does not track, so this build is "
        f"not reproducible from a clean checkout: {extra[:10]}"
        + (" ..." if len(extra) > 10 else "")
        + ". Check [tool.setuptools.exclude-package-data] in pyproject.toml -- see ADR-0058."
    )
