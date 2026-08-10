"""Run the target project's Maven build and return structured compile diagnostics.

**This module's output is an input to a model, and that shapes every decision in it.**
`build_validator` (step 41) reads these diagnostics, and the self-healing loop (step 42) feeds them
back to `modernization_engineer` so it can repair its own output. So the parsing is the load-bearing
part, not the subprocess call: a diagnostic reduced to "the build failed" gives the loop nothing to
act on, and one that invents a line number sends it somewhere real code is not.

**Why `mvn` on `PATH` and not a container**, with Docker available. In production the specialist is
itself a container (step 46, gap G5). Giving *that* container a Docker socket so it could launch
build containers would hand root-equivalent host access to the component that compiles
model-authored code derived from untrusted COBOL -- the wrong trust boundary for a repo whose
standing rule is that COBOL is data, never instructions. The specialist's own image carries the JDK
and Maven instead. It is also much faster: the heal loop compiles repeatedly, and a cold container
with a cold `~/.m2` on every attempt would dominate its wall time.

**`./mvnw` is preferred over `mvn`.** The wrapper pins the Maven version in the target project, so
the loop parses output from the same build tool everywhere. Falling back to `mvn` is deliberate but
noisy in the logs: an unpinned Maven is a diagnostic format nobody chose.

**What "sandboxed" means here, precisely.** The build runs as a subprocess with an explicit timeout,
no shell (so a path with a space or a quote in it cannot become an argument split or an injection),
its working directory pinned to the project root, and its output captured rather than inherited.
It is *not* a security sandbox: Maven resolves dependencies over the network and runs plugin code,
and pretending otherwise would be a claim this module cannot back. Isolating a hostile build is the
container boundary's job at step 46, not this function's.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

#: Generous on purpose: a first build resolves the whole dependency tree, and a timeout that fires
#: on a cold `~/.m2` would look exactly like a compile failure to everything downstream.
DEFAULT_TIMEOUT_SECONDS = 900

#: `-B` batch mode and `-ntp` no-transfer-progress, matching what CI runs. Both exist to keep the
#: output parseable: interactive prompts and download progress bars are noise in a diagnostic feed.
_BASE_ARGS = ("-B", "-ntp")

#: `[ERROR] /abs/path/Foo.java:[12,34] cannot find symbol`
#: The `maven-compiler-plugin` format. Column is present for javac and absent for some other
#: plugins, so it is optional rather than assumed.
_DIAGNOSTIC = re.compile(
    r"^\[(?P<severity>ERROR|WARNING)\]\s+"
    r"(?P<file>[^\[\]]+?\.java)"
    r":\[(?P<line>\d+)(?:,(?P<column>\d+))?\]\s+"
    r"(?P<message>.*)$"
)

#: `[ERROR]   symbol:   class Foo` -- javac's follow-on detail lines. They belong to the diagnostic
#: above them and carry the part a repair actually needs (which symbol, which location), so they are
#: attached rather than dropped.
_CONTINUATION = re.compile(r"^\[(?:ERROR|WARNING)\]\s{2,}(?P<detail>\S.*)$")

Severity = Literal["error", "warning"]


class ToolchainNotFoundError(Exception):
    """The build environment is incomplete -- distinct from the build failing.

    **Step 42 must treat this family as an environment fault and stop, never as a compile failure
    to repair.** A missing toolchain and broken code produce the same surface symptom -- a non-zero
    exit with no located diagnostics -- and a heal loop that cannot tell them apart will spend
    every attempt it has rewriting source that was correct all along. Raising here is what makes
    the two distinguishable at all.
    """


class CompilerNotFoundError(ToolchainNotFoundError):
    """No Maven wrapper in the project and no `mvn` on `PATH`."""


class JdkNotFoundError(ToolchainNotFoundError):
    """No `JAVA_HOME` and no `java` on `PATH`, so the Maven wrapper cannot start a JVM.

    Found the honest way: the first run of this module's own tests in a shell without a JDK failed
    as `succeeded=False, diagnostics=0` -- indistinguishable from code that does not compile, and
    exactly the confusion this check exists to prevent.
    """


class CompileTimeoutError(Exception):
    """The build exceeded its timeout and was killed.

    Deliberately not folded into a failed `CompileResult`. A timeout is not a compile error, and a
    repair loop that treats it as one would ask a model to fix source that may be perfectly valid.
    """


@dataclass(frozen=True)
class CompileDiagnostic:
    """One javac message, located precisely enough to act on."""

    file: str
    line: int | None
    column: int | None
    severity: Severity
    message: str
    #: javac's indented follow-on lines (`symbol:`, `location:`), in order. Empty for most.
    details: tuple[str, ...] = ()

    def render(self) -> str:
        """One human- and model-readable line, with the details folded in."""
        where = self.file
        if self.line is not None:
            where += f":{self.line}"
            if self.column is not None:
                where += f":{self.column}"
        rendered = f"{self.severity}: {where}: {self.message}"
        for detail in self.details:
            rendered += f"\n    {detail}"
        return rendered


@dataclass(frozen=True)
class CompileResult:
    """The outcome of one build invocation."""

    succeeded: bool
    exit_code: int
    diagnostics: tuple[CompileDiagnostic, ...]
    duration_ms: int
    #: The full captured output. Kept because a failure the parser did not recognise must still be
    #: diagnosable by a human -- a parser that silently drops what it cannot classify is worse than
    #: no parser, and this is the field that makes that verifiable rather than hoped for.
    raw_output: str

    @property
    def errors(self) -> tuple[CompileDiagnostic, ...]:
        return tuple(d for d in self.diagnostics if d.severity == "error")

    @property
    def has_unparsed_failure(self) -> bool:
        """The build failed and produced no structured error.

        The case a repair loop must not be given: something went wrong that this module could not
        turn into an actionable location, so there is nothing for a model to fix and the honest
        move is to surface `raw_output` to a human.
        """
        return not self.succeeded and not self.errors


def resolve_build_command(project_dir: Path) -> list[str]:
    """Prefer the project's pinned wrapper; fall back to `mvn` on `PATH`, loudly.

    Raises `CompilerNotFoundError` when neither exists.
    """
    # `mvnw.cmd` on Windows, `mvnw` elsewhere. Checked by existence rather than by platform so a
    # POSIX shell on Windows (which is how this repo's own tests run) gets the script that works.
    #
    # **Absolute, not relative.** `compile_project` runs the child with `cwd=project_dir`, so a
    # path relative to the *caller's* working directory resolves against the wrong base inside the
    # child and Maven never starts -- the failure is `The system cannot find the path specified.`
    # in 186ms, which arrives downstream as a build failure with zero diagnostics rather than as
    # anything that names the real cause.
    for candidate in ("mvnw.cmd", "mvnw"):
        wrapper = project_dir / candidate
        if wrapper.is_file():
            return [str(wrapper.resolve())]

    from shutil import which

    mvn = which("mvn")
    if mvn is None:
        raise CompilerNotFoundError(
            f"no Maven wrapper in {project_dir} and no `mvn` on PATH; "
            "the specialist's runtime image must carry a JDK and Maven (step 46)"
        )
    logger.warning(
        "no Maven wrapper in %s; falling back to `mvn` on PATH, whose version is not pinned "
        "by the target project",
        project_dir,
    )
    return [mvn]


def require_jdk() -> str:
    """Return where the JDK was found, or raise before a build that could not possibly work.

    Checked as a precondition rather than diagnosed from the failure, because the failure is not
    diagnosable: the wrapper exits non-zero with no `[ERROR] file:[line,col]` line, which is the
    same shape as a genuine compile failure.
    """
    from shutil import which

    java_home = os.environ.get("JAVA_HOME")
    if java_home:
        home = Path(java_home)
        if (home / "bin" / "java.exe").is_file() or (home / "bin" / "java").is_file():
            return f"JAVA_HOME={java_home}"
        raise JdkNotFoundError(f"JAVA_HOME is set to {java_home!r} but has no bin/java")

    java = which("java")
    if java is None:
        raise JdkNotFoundError(
            "no JAVA_HOME and no `java` on PATH; the Maven wrapper cannot start a JVM. "
            "The specialist's runtime image must carry a JDK (step 46)"
        )
    return f"PATH={java}"


def parse_diagnostics(output: str) -> tuple[CompileDiagnostic, ...]:
    """Extract every located javac message from a Maven build log.

    Continuation lines are attached to the diagnostic above them rather than emitted as their own
    entries: `symbol: class Foo` on its own is not a location anything can act on.
    """
    diagnostics: list[CompileDiagnostic] = []
    pending_details: list[str] = []

    def flush() -> None:
        if diagnostics and pending_details:
            last = diagnostics[-1]
            diagnostics[-1] = CompileDiagnostic(
                file=last.file,
                line=last.line,
                column=last.column,
                severity=last.severity,
                message=last.message,
                details=tuple(pending_details),
            )
        pending_details.clear()

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        match = _DIAGNOSTIC.match(line)
        if match:
            flush()
            column = match.group("column")
            diagnostics.append(
                CompileDiagnostic(
                    file=match.group("file").strip(),
                    line=int(match.group("line")),
                    column=int(column) if column else None,
                    severity="error" if match.group("severity") == "ERROR" else "warning",
                    message=match.group("message").strip(),
                )
            )
            continue

        continuation = _CONTINUATION.match(line)
        if continuation and diagnostics:
            pending_details.append(continuation.group("detail").strip())
            continue

        flush()

    flush()
    return _deduplicate(diagnostics)


def _deduplicate(diagnostics: list[CompileDiagnostic]) -> tuple[CompileDiagnostic, ...]:
    """Collapse Maven's repeated error list, keeping the copy that carries javac's detail lines.

    Maven prints each compilation error twice: once in the failure summary and once in the detailed
    listing, and only one of the two has the `symbol:`/`location:` follow-ons. Handing a repair loop
    both copies is noise that costs tokens and invites a model to think there are two problems --
    and dropping the wrong one throws away the part that says *which* symbol is missing.
    """
    best: dict[tuple[Severity, str, int | None, int | None, str], CompileDiagnostic] = {}
    for diagnostic in diagnostics:
        key = (
            diagnostic.severity,
            diagnostic.file,
            diagnostic.line,
            diagnostic.column,
            diagnostic.message,
        )
        existing = best.get(key)
        if existing is None or len(diagnostic.details) > len(existing.details):
            best[key] = diagnostic
    return tuple(best.values())


def _relativize(diagnostic: CompileDiagnostic, project_dir: Path) -> CompileDiagnostic:
    """Rewrite an absolute source path as one relative to the project root, when it is under it.

    Two reasons, both about what a model is handed. An absolute path is noise it pays for by the
    token, and on Windows Maven emits a leading-slash form (`/C:/Users/...`) that is not a path
    anything else in this pipeline would recognise. A path relative to the project root is also the
    only form a repair can act on without knowing where the workspace happens to be mounted.
    """
    raw = diagnostic.file
    # Maven on Windows emits `/C:/...`; strip the leading slash so `Path` sees a real drive path.
    if re.match(r"^/[A-Za-z]:[\\/]", raw):
        raw = raw[1:]
    try:
        relative = Path(raw).resolve().relative_to(project_dir.resolve())
    except (ValueError, OSError):
        # Outside the project (a dependency's source, say) -- leave it exactly as Maven reported it
        # rather than inventing a relationship that does not hold.
        return diagnostic
    return CompileDiagnostic(
        file=relative.as_posix(),
        line=diagnostic.line,
        column=diagnostic.column,
        severity=diagnostic.severity,
        message=diagnostic.message,
        details=diagnostic.details,
    )


def compile_project(
    project_dir: Path,
    *,
    goal: str = "compile",
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    offline: bool = False,
) -> CompileResult:
    """Run one Maven build in `project_dir` and return its structured outcome.

    Args:
        project_dir: The target project root -- the directory holding `pom.xml`.
        goal: The Maven lifecycle phase. `compile` for the heal loop's inner iterations; `verify`
            when the tests should run too.
        timeout_seconds: Killed past this. See `CompileTimeoutError` for why a timeout is not
            reported as a compile failure.
        offline: Pass `-o`. Useful once `~/.m2` is warm, and wrong on a first build.

    Raises:
        FileNotFoundError: `project_dir` has no `pom.xml` -- an unambiguous caller error, and one
            Maven would otherwise report as a confusing build failure.
        CompilerNotFoundError: no wrapper and no `mvn`.
        CompileTimeoutError: the build exceeded `timeout_seconds`.
    """
    pom = project_dir / "pom.xml"
    if not pom.is_file():
        raise FileNotFoundError(f"no pom.xml in {project_dir}")

    toolchain = require_jdk()
    argv = [*resolve_build_command(project_dir), *_BASE_ARGS]
    if offline:
        argv.append("-o")
    argv.append(goal)

    logger.info(
        "local_compiler: %s in %s (timeout %ds, jdk via %s)",
        " ".join(argv), project_dir, timeout_seconds, toolchain,
    )
    started = time.monotonic()
    try:
        completed = subprocess.run(
            argv,
            cwd=project_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            # No shell: a project path containing a space or a quote must never become an argument
            # split, and nothing here should be interpretable as a command.
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CompileTimeoutError(
            f"maven exceeded {timeout_seconds}s in {project_dir}"
        ) from exc

    duration_ms = int((time.monotonic() - started) * 1000)
    # Maven writes diagnostics to stdout; stderr carries JVM-level noise. Both are kept so an
    # unrecognised failure is still diagnosable.
    output = (completed.stdout or "") + (completed.stderr or "")
    diagnostics = tuple(
        _relativize(diagnostic, project_dir) for diagnostic in parse_diagnostics(output)
    )
    result = CompileResult(
        succeeded=completed.returncode == 0,
        exit_code=completed.returncode,
        diagnostics=diagnostics,
        duration_ms=duration_ms,
        raw_output=output,
    )

    logger.info(
        "local_compiler: %s exit=%d errors=%d warnings=%d in %dms",
        "succeeded" if result.succeeded else "failed",
        result.exit_code,
        len(result.errors),
        len(diagnostics) - len(result.errors),
        duration_ms,
    )
    if result.has_unparsed_failure:
        logger.warning(
            "local_compiler: build failed with no located diagnostic; the loop has nothing to "
            "repair and raw_output needs a human"
        )
    return result
