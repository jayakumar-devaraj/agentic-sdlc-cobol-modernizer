---
name: verify-self-healing-loop
description: Run the real functional verification for the self-healing compile loop — real injected errors against a real Maven sandbox container, not mocked — and produce a dated report.
---

Run `tests/system/test_self_healing_loop.py` against the real sandboxed compiler container (not
a mocked `mvn` call — see `.claude/agents/qa.md` on why a mock doesn't prove this). Confirm at
least two distinct injected error classes (e.g. a missing import, a type mismatch) are each
diagnosed by `build_validator` and recovered without human intervention, within the three-attempt
cap.

Capture, for each error class: the injected error, the diagnosis `build_validator` produced, the
patch applied, and the final `mvn` output proving success. Write the result to
`docs/qa/verification-report-self-healing-YYYY-MM-DD.md` with the actual commands run — a
description of what should happen is not a substitute for a real run's output.

If either error class fails to recover within the cap, do not adjust the cap to make the test
pass — that's a latency/cost decision requiring its own ADR (see `.claude/agents/development.md`).
Report the failure as a failure.
