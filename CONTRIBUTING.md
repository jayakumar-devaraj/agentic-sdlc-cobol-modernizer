# Contributing

This repo follows the commit and documentation discipline in [`CLAUDE.md`](CLAUDE.md) — read it
first. In short:

- One small, tested change per commit. Push after every commit.
- A design decision with a disputable cost gets an ADR (`docs/adr/NNNN-*.md`), not just a commit
  message — see `.claude/skills/new-adr.md` for the format.
- Keep `docs/cobol-construct-support-matrix.md` in sync with what the parser actually accepts —
  a construct's scope status is a claim that must be backed by reading real source, not assumed.
- Unit tests plus, for anything a unit test can't reach (a real sandboxed compile, an end-to-end
  run through `agentic-sdlc-control-plane`), a functional verification report with the actual
  command that produced it — see `.claude/agents/qa.md`.

Pull requests are reviewed against these same rules, not a separate checklist.
