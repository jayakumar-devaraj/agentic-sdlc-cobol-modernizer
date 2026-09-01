# AGENTS.md

**The standing instructions for this repository are [`CLAUDE.md`](CLAUDE.md). Read it first.**

This file exists because `AGENTS.md` is the filename several coding agents look for, and this
repository's instructions predate that convention under a different name. It is a pointer, not a
second copy: every rule lives in `CLAUDE.md`, so there is exactly one place to change when one
changes. Duplicating them here would produce two independently-maintained statements of the same
rules — the defect class `CLAUDE.md` itself forbids when it refuses to mirror control-plane's
audit ledger.

Three things are worth knowing before the first command, each covered in full elsewhere:

1. **Read [`docs/development-environment.md`](docs/development-environment.md) before running
   anything.** Without `JAVA_HOME` exported, every Java test fails as *"build failed, zero
   diagnostics"* — indistinguishable from broken generated code. Bare `python` is a Windows Store
   shim that exits 49; use `.venv/Scripts/python.exe`. Docker is needed for three separate things.
   That file also lists every environment variable and what each live test costs to run.

2. **Every task gets its own branch off `main` and goes through a PR.** Never commit directly to
   `main`. Merge with a real merge commit, not a squash or rebase. `CLAUDE.md` has the full
   workflow and why it is written down.

3. **The three roles are executable agents** in [`.claude/agents/`](.claude/agents/) — `design`,
   `development`, `qa`. They are definitions, not documentation.

One rule is repeated here rather than referenced, because violating it is silent and expensive: **a
capability is complete when a second, independent instance exercises it, not when the first one
passes.** `CLAUDE.md` records what closing a gap on a single instance cost this repository.
