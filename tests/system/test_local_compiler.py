"""`tools/local_compiler.py` — parsing against captured Maven output, and real builds.

Split deliberately. The parser is a pure function and is tested against **real captured Maven
output**, including the shapes that are easy to get wrong: the duplicated error list, javac's
indented follow-on lines, and a build that fails without producing a located diagnostic at all.

The rest runs **real Maven against the real template**. That is slower and needs a JDK, and it is
not optional: the first version of this module resolved the wrapper path relative to the caller's
working directory while running the child with `cwd=project_dir`, so Maven never started and the
failure arrived as "build failed, zero diagnostics" in 186ms. No amount of mocked `subprocess.run`
would have found that — only running it did. CI gives this job a JDK for the same reason
`template-build` insists on Docker: a test that skips in CI is decoration.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from cobol_modernizer.core.package_data import TEMPLATES_ROOT
from cobol_modernizer.tools.local_compiler import (
    CompileDiagnostic,
    CompilerNotFoundError,
    CompileTimeoutError,
    JdkNotFoundError,
    ToolchainNotFoundError,
    compile_project,
    parse_diagnostics,
    require_jdk,
    resolve_build_command,
)

TEMPLATE = TEMPLATES_ROOT / "target-spring-boot-baseline"

# Real output from `mvnw -B -ntp compile` against the template with `setScale` typo'd, trimmed to
# the shape that matters. Note the error appears twice -- Maven's summary and its detail listing --
# and only the second copy carries javac's `symbol:`/`location:` lines.
REAL_FAILURE_OUTPUT = """\
[INFO] Compiling 4 source files with javac [debug parameters release 25]
[INFO] -------------------------------------------------------------
[ERROR] COMPILATION ERROR :
[INFO] -------------------------------------------------------------
[ERROR] /tmp/proj/src/main/java/com/modernized/batch/cobol/CobolArithmetic.java:[46,21] cannot find symbol
[INFO] 1 error
[INFO] -------------------------------------------------------------
[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.14.0:compile
[ERROR] /tmp/proj/src/main/java/com/modernized/batch/cobol/CobolArithmetic.java:[46,21] cannot find symbol
[ERROR]   symbol:   method setScaleTypo(int,java.math.RoundingMode)
[ERROR]   location: variable value of type java.math.BigDecimal
[ERROR] -> [Help 1]
"""


# --- Parsing: pure, no toolchain ----------------------------------------------------------------


def test_a_located_error_is_parsed_into_its_parts():
    (diagnostic,) = [d for d in parse_diagnostics(REAL_FAILURE_OUTPUT) if d.severity == "error"]
    assert diagnostic.file.endswith("CobolArithmetic.java")
    assert diagnostic.line == 46
    assert diagnostic.column == 21
    assert diagnostic.message == "cannot find symbol"


def test_mavens_duplicated_error_list_collapses_to_one():
    # Maven prints each compile error twice. Two copies invite a repair loop to believe there are
    # two problems, and cost tokens saying so.
    errors = [d for d in parse_diagnostics(REAL_FAILURE_OUTPUT) if d.severity == "error"]
    assert len(errors) == 1


def test_the_surviving_copy_is_the_one_carrying_javacs_detail_lines():
    # Dropping the wrong duplicate throws away the part that says *which* symbol is missing --
    # which is the only part a repair can act on.
    (diagnostic,) = [d for d in parse_diagnostics(REAL_FAILURE_OUTPUT) if d.severity == "error"]
    assert diagnostic.details == (
        "symbol:   method setScaleTypo(int,java.math.RoundingMode)",
        "location: variable value of type java.math.BigDecimal",
    )


def test_unlocated_error_lines_are_not_invented_into_diagnostics():
    # `[ERROR] Failed to execute goal ...` and `-> [Help 1]` have no file or line. Emitting them as
    # diagnostics would send a repair loop somewhere no code exists.
    files = {d.file for d in parse_diagnostics(REAL_FAILURE_OUTPUT)}
    assert all(f.endswith(".java") for f in files)


def test_a_column_less_diagnostic_still_parses():
    output = "[ERROR] /tmp/p/src/main/java/A.java:[7] something a non-javac plugin said"
    (diagnostic,) = parse_diagnostics(output)
    assert diagnostic.line == 7
    assert diagnostic.column is None


def test_warnings_are_kept_and_distinguished_from_errors():
    output = "[WARNING] /tmp/p/src/main/java/A.java:[3,9] deprecated API"
    (diagnostic,) = parse_diagnostics(output)
    assert diagnostic.severity == "warning"


def test_clean_output_yields_nothing():
    assert parse_diagnostics("[INFO] BUILD SUCCESS\n[INFO] Total time: 9.9 s\n") == ()


def test_render_is_one_actionable_line_plus_its_details():
    rendered = CompileDiagnostic(
        file="src/main/java/A.java", line=4, column=2, severity="error",
        message="cannot find symbol", details=("symbol: method x()",),
    ).render()
    assert rendered.splitlines()[0] == "error: src/main/java/A.java:4:2: cannot find symbol"
    assert rendered.splitlines()[1].strip() == "symbol: method x()"


# --- Command resolution -------------------------------------------------------------------------


def test_the_projects_pinned_wrapper_is_preferred_over_path_maven():
    (command,) = resolve_build_command(TEMPLATE)
    assert Path(command).name in {"mvnw", "mvnw.cmd"}


def test_the_wrapper_is_chosen_by_platform_not_by_which_file_exists():
    # Both scripts are committed, so an existence-ordered check picks `mvnw.cmd` on Linux -- a
    # Windows batch file with no execute bit, which fails as `PermissionError: [Errno 13]`. Caught
    # by CI and not by any local run on Windows, where the .cmd is the correct choice.
    (command,) = resolve_build_command(TEMPLATE)
    expected = "mvnw.cmd" if os.name == "nt" else "mvnw"
    assert Path(command).name == expected


@pytest.mark.skipif(os.name == "nt", reason="execute bits are not meaningful on Windows")
def test_the_posix_wrapper_is_executable():
    assert os.access(TEMPLATE / "mvnw", os.X_OK), (
        "mvnw lost its execute bit; ./mvnw fails as Permission denied on any POSIX runner"
    )


def test_the_wrapper_path_is_absolute():
    # The regression guard for the real defect: compile_project runs the child with
    # cwd=project_dir, so a relative command resolves against the wrong base and Maven never
    # starts -- surfacing as a build failure with zero diagnostics rather than as a missing file.
    (command,) = resolve_build_command(TEMPLATE)
    assert Path(command).is_absolute()


def test_no_wrapper_and_no_mvn_raises_rather_than_reporting_an_empty_failure(tmp_path, monkeypatch):
    # "no diagnostics" and "no build tool" are different facts. A heal loop told the first when the
    # second is true will spend every attempt it has rewriting correct code.
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(CompilerNotFoundError, match="no Maven wrapper"):
        resolve_build_command(tmp_path)


# --- The toolchain precondition -----------------------------------------------------------------


def test_a_missing_jdk_raises_instead_of_looking_like_broken_code(monkeypatch):
    # The failure this check exists for, found by running the suite in a shell without a JDK: the
    # wrapper exits non-zero with no located diagnostic, which is byte-for-byte the shape of code
    # that does not compile. A heal loop cannot tell them apart, so the module must.
    monkeypatch.delenv("JAVA_HOME", raising=False)
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(JdkNotFoundError, match="cannot start a JVM"):
        compile_project(TEMPLATE)


def test_a_java_home_pointing_nowhere_is_rejected_rather_than_ignored(monkeypatch, tmp_path):
    # Silently falling back to PATH would make a misconfigured JAVA_HOME invisible until some later
    # build picked up a different JDK than the one the operator thought they had selected.
    monkeypatch.setenv("JAVA_HOME", str(tmp_path))
    with pytest.raises(JdkNotFoundError, match="has no bin/java"):
        require_jdk()


def test_both_toolchain_faults_share_a_base_so_step_42_can_catch_one_thing():
    assert issubclass(JdkNotFoundError, ToolchainNotFoundError)
    assert issubclass(CompilerNotFoundError, ToolchainNotFoundError)


# --- Real builds --------------------------------------------------------------------------------


def test_a_missing_pom_is_a_caller_error_not_a_build_failure(tmp_path):
    with pytest.raises(FileNotFoundError, match="no pom.xml"):
        compile_project(tmp_path)


def test_a_timeout_is_raised_not_reported_as_a_compile_failure(template_copy):
    # The distinction step 42 depends on. A timeout says nothing about whether the source compiles,
    # so folding it into a failed CompileResult would have a heal loop rewriting code that may be
    # perfectly valid. One second is far below a real build and reliably trips it.
    with pytest.raises(CompileTimeoutError, match="exceeded 1s"):
        compile_project(template_copy, goal="compile", timeout_seconds=1)


def test_an_unparsed_failure_is_flagged_for_a_human(tmp_path):
    # A pom that Maven itself rejects: the build fails with no javac diagnostic at all, which is
    # the one case a repair loop must be handed to a human instead.
    #
    # The wrapper is copied in rather than relying on `mvn` being on PATH -- without it this test
    # raises CompilerNotFoundError and proves nothing, which is exactly what it did when first
    # written in a shell that had a JDK but no Maven.
    for name in ("mvnw", "mvnw.cmd"):
        shutil.copy2(TEMPLATE / name, tmp_path / name)
    shutil.copytree(TEMPLATE / ".mvn", tmp_path / ".mvn")
    (tmp_path / "pom.xml").write_text("<project>not a real pom</project>", encoding="utf-8")
    result = compile_project(tmp_path, goal="compile")
    assert not result.succeeded
    assert result.has_unparsed_failure
    assert result.raw_output, "raw_output is the only thing a human has here; it must not be empty"


def test_a_path_outside_the_project_is_left_exactly_as_maven_reported_it(tmp_path):
    # Relativising only makes sense inside the project. Inventing a relationship for a path that
    # has none would produce a location no repair could act on.
    from cobol_modernizer.tools.local_compiler import _relativize

    diagnostic = CompileDiagnostic(
        file="/somewhere/else/Other.java", line=1, column=1, severity="error", message="x"
    )
    assert _relativize(diagnostic, tmp_path).file == "/somewhere/else/Other.java"


def test_a_project_without_a_wrapper_falls_back_to_path_maven(tmp_path, monkeypatch):
    # Supported, but the caller loses the version pin -- so it warns rather than doing it quietly.
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/mvn")
    assert resolve_build_command(tmp_path) == ["/usr/bin/mvn"]


@pytest.fixture(scope="module")
def template_copy(tmp_path_factory) -> Path:
    """A throwaway copy, so an injected error cannot touch the real template."""
    destination = tmp_path_factory.mktemp("target-project") / "proj"
    shutil.copytree(TEMPLATE, destination, ignore=shutil.ignore_patterns("target"))
    return destination


def test_the_real_template_compiles(template_copy):
    result = compile_project(template_copy, goal="compile")
    assert result.succeeded, result.raw_output[-2000:]
    assert result.exit_code == 0
    assert result.errors == ()
    assert not result.has_unparsed_failure


def test_a_real_injected_error_produces_one_actionable_diagnostic(template_copy):
    # The end-to-end property step 42 depends on: a real broken build in, one located, deduplicated,
    # project-relative diagnostic out.
    source = template_copy / "src/main/java/com/modernized/batch/cobol/CobolArithmetic.java"
    original = source.read_text(encoding="utf-8")
    source.write_text(
        original.replace(
            "return value.setScale(scale, RoundingMode.DOWN);",
            "return value.setScaleTypo(scale, RoundingMode.DOWN);",
        ),
        encoding="utf-8",
    )
    try:
        result = compile_project(template_copy, goal="compile")
    finally:
        source.write_text(original, encoding="utf-8")

    assert not result.succeeded
    assert not result.has_unparsed_failure
    (error,) = result.errors
    assert error.message == "cannot find symbol"
    assert error.line == 46
    # Project-relative and POSIX-separated: an absolute path is noise a model pays for by the
    # token, and Maven's Windows form (`/C:/...`) is not a path anything else here would recognise.
    assert error.file == "src/main/java/com/modernized/batch/cobol/CobolArithmetic.java"
    assert any("setScaleTypo" in detail for detail in error.details)
