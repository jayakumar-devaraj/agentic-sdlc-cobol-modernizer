---
name: verify-self-healing-loop
description: Run the real functional verification for the self-healing compile loop — real injected errors against a real Maven build, not mocked — and record it in the verification report's compile-loop spoke.
---

Run `tests/system/test_generate_pipeline.py` against a real Maven build (not a mocked `mvn` call —
see `.claude/agents/qa.md` on why a mock doesn't prove this). **Export `JAVA_HOME` first**: without
it the build fails as *"build failed, zero diagnostics"*, which arrives downstream looking like a
loop defect rather than a missing toolchain — see `docs/development-environment.md`.

**There is no compiler container, and that is deliberate.**
`src/cobol_modernizer/tools/local_compiler.py` runs `./mvnw`
as a subprocess with an explicit timeout, and its module docstring records why: giving the
specialist's own container a Docker socket so it could launch build containers would hand
root-equivalent host access to the component that compiles model-authored code. Do not go looking
for a sandbox image; "sandboxed" here means the subprocess timeout and the container boundary at
step 46, not an inner container.

The module already declares four injected error classes in `_INJECTED_ERRORS` — `unknown_method`,
`missing_import`, `unresolved_import`, `wrong_return`. Confirm each produces a diagnostic
`build_validator` can act on, and that a recoverable one heals without human intervention within
`MAX_HEAL_ATTEMPTS` (3, in `src/cobol_modernizer/graph/generate_pipeline.py`). A class the compiler
reports but the parser cannot locate must *block*, not retry blindly.

Capture, for each class: the injected error, the diagnosis `build_validator` produced, the patch
applied, and the final Maven output proving success. Record the result in
`docs/qa/verification/06-the-compile-loop-real-data-and-the-interest-oracle.md`, the spoke that owns
this scope — ADR-0033 makes the verification report a hub with one spoke per phase, so a new dated
file at the root of `docs/qa/` is the wrong shape. State the exact commands run: a description of
what should happen is not a substitute for a real run's output.

If a class that should recover fails to within the cap, do not adjust the cap to make the test
pass — that's a latency/cost decision requiring its own ADR (see `.claude/agents/development.md`).
Report the failure as a failure.
