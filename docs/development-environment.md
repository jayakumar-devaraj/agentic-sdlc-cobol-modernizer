# Development environment, and the traps that cost time

Committed rather than kept in a session note, because `CLAUDE.md` requires the process that produced
this repository to be **reproducible by anyone who clones it, not just on the machine that ran it**.
Every fact below was previously held only in an uncommitted plan file on one laptop; a clone anywhere
else lost all of it, and each entry here has already cost at least one wasted round trip.

Read this before the first command of a session. It is a spoke rather than part of `CLAUDE.md` on
purpose: `CLAUDE.md` loads unconditionally every session, and this is needed only when something is
being run.

## Environment

*(Verified 2026-08-24. Re-verify anything here before trusting it — the local suite in particular has
changed behaviour twice.)*

- **JDK**: Temurin **25.0.4** at `C:\Program Files\Eclipse Adoptium\jdk-25.0.4.7-hotspot`.

  ```bash
  export JAVA_HOME="C:/Program Files/Eclipse Adoptium/jdk-25.0.4.7-hotspot"
  export PATH="$JAVA_HOME/bin:$PATH"
  ```

  **A shell that does not do this fails the Java tests as "build failed, zero diagnostics"** — which
  is indistinguishable from broken generated code, and the self-healing loop reports it as having
  nothing to repair.

- **Python**: use `.venv/Scripts/python.exe`. Bare `python` resolves to the Windows Store app-execution
  alias and exits 49 with *"Python was not found"* — a message that points at a missing install
  rather than at a `PATH` shim, and invites a pointless reinstall. `py` works but resolves to a bare
  interpreter without this project's dependencies.

- **Docker** is required for three separate things, and the third is the one people forget:
  1. the oracle pipeline (`tools/cobol-oracle/`), image `cobol-oracle:gnucobol3`, pinned by digest —
     `gnucobol4` cannot compile `CBACT04C` at all;
  2. Testcontainers in this repo's own suite;
  3. **`mvn verify` inside every generated project**, because the baseline template's
     `BaselineStackTest` uses Testcontainers.

  Docker stopped mid-session twice during PRs #85–#88. Every round-trip test then fails as *"build
  failed, zero diagnostics"* — the same signature as the missing `JAVA_HOME`. **Run
  `docker version` before trusting a local red.**

- **Postgres**: `cobol-modernizer` on port **5434**. Without it roughly 14 tests skip with a stated
  reason rather than failing.

- **Model backend**: the `claude` CLI on `PATH`; no `ANTHROPIC_API_KEY`. **Live tests spend real
  money** and are skipped unless `COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1`.

- **The local suite is not a reliable gate.** A normal run is 8–13 minutes. One run during PR #88 took
  **15 hours** and reported a failure that passed in 43 seconds when run alone. **CI is the authority
  on a PR** — its test job takes 7–9 minutes.

## Traps

Ordered by how often they bite, not by severity.

0. **Run `ruff check src/ tests/` before every push.** CI lints *before* it tests, so a stray import
   order fails the PR **without running a single test** and reports as a bare red `test: fail`. Cost a
   full round trip on PR #86.

1. **PowerShell `Get-Content`/`Set-Content` destroys UTF-8.** Use the Edit/Write tools for file
   changes. `sed` under Git Bash is byte-safe; PowerShell is not.

2. **Bash heredocs eat backslashes, even quoted ones.** Bit twice during PR #85: a `tr -d '\n'` inside
   a `<<'EOF'` heredoc reached disk as a literal newline inside quotes. Use Write for anything
   containing a backslash, or an escape-free form — `paste -s -d ''` instead of `tr -d '\n'`.

3. **Lifting a behaviour leaves its test asserting the old one.** Verify the constraints around a
   change, not only the change.

4. **`templates/` is guarded against tenant vocabulary**, and a Javadoc naming the corpus trips it.

5. **Assert counts, not exit codes.** `CBTRN02C` exited 4 while completing normally until ADR-0047,
   after which it exits 0. Both were true at the time; neither was ever the evidence.

6. **A test that passes on the artifact that produced it is not evidence.** Show every new check
   failing first, on deliberately damaged input.

7. **Check a slice width against the copybook.** PR #85 found a four-revision-old test reading the
   `FILLER` after an eleven-byte `PIC S9(09)V99` and calling it the sign. It passed, and it was cited
   as evidence for `500 of 500`.

8. **A guard keyed to a literal stops guarding when the literal changes.** The README round-trip guard
   was hardcoded to `"1 of 4"`; moving the metric would have left it passing while enforcing nothing.
   It reads a constant now (`COUNT` in `test_hand_written_round_trip.py`).

9. **Check a document's counts against the code before quoting them.** *"Six cases"* survived in the
   README and four docstrings after the eval corpus grew to seven, and made every cost estimate 17%
   low. Corrected in PR #87, then again in PR #90 when it reached nine.

10. **Read a judge's rationales before changing anything it flagged** (ADR-0050). The harness keeps
    them precisely so a disagreement is diagnosable without paying for another run. Skipping them
    cost a billed run and a wrong fix.

11. **Anything placed in `src/cobol_modernizer/data/templates/target-spring-boot-baseline/` joins
    every generated project.** (Moved under the package at ADR-0055; it was `templates/` at the
    repository root until then, and older records still say so.)

13. **The four runtime data directories live inside the package, not beside `src/`.** Prompts,
    model config and the Java baseline are packaged data (`cobol_modernizer/data/`), reached
    through `core/package_data.py` rather than by counting `.parents[]`. **`pip install -e .` hides
    any mistake here**, because the package is still in the source tree — a real wheel install is
    the only thing that catches it, and `test_packaging.py` is what does. `schemas/` is *not*
    package data and stays at the repository root: nothing in the runtime path reads it.

12. **Emoji printed from Python on Windows crashes on cp1252.**

## Running the things that cost money

Every live test is opt-in and each one spends real subscription quota. **Quote a number before
running one**; the two estimates made so far were wrong by 38% and by the wrong outcome entirely
(ADR-0049, ADR-0050).

```bash
# The judge benchmark: one call per case per run. Nine cases, so 27 at the default n=3 (~$2.40).
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 ./.venv/Scripts/python -m pytest tests/evaluations/test_judge_benchmark.py -q -s

# Cheaper confirmation: n=2, 18 calls (~$1.80). Never below 2 -- one sample is the defect ADR-0045 fixed.
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 COBOL_MODERNIZER_JUDGE_SAMPLES=2 ./.venv/Scripts/python -m pytest tests/evaluations/test_judge_benchmark.py -q -s

# build_validator's discrimination benchmark (ADR-0057). Eight cases, so 24 calls at n=3.
# **~$0.55, and that is an over-estimate**: it is priced from the node's declared token profile,
# which its own routing entry calls "a placeholder, not a measurement". Measuring that profile is
# one of the things the run produces. Compiles each case with real Maven first -- free, but it
# makes the run several minutes longer than the call count suggests.
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 ./.venv/Scripts/python -m pytest tests/evaluations/test_build_validator_benchmark.py -q -s

# Cheaper: n=2, 16 calls (~$0.37). Same floor as above -- never 1.
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 COBOL_MODERNIZER_VALIDATOR_SAMPLES=2 ./.venv/Scripts/python -m pytest tests/evaluations/test_build_validator_benchmark.py -q -s

# A model-authored round trip: one processor step plus heal attempts (~$0.35 measured for CBTRN02C).
COBOL_MODERNIZER_RUN_LIVE_CLI_TESTS=1 ./.venv/Scripts/python -m pytest tests/system/test_cbtrn02c_round_trip.py -q -s
```

Regenerating the oracle costs nothing but needs Docker:

```bash
docker run --rm -v "$PWD/tests/fixtures/tenant_repo_sample/app:/src:ro" \
                -v "$PWD/tools/cobol-oracle:/co:ro" -v "$PWD/out:/out" \
                cobol-oracle:gnucobol3 sh /co/run-oracle.sh
```
