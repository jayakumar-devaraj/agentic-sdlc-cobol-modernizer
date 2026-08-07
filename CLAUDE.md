# CLAUDE.md

Standing instructions for AI agents (and human contributors) working in this repository. Claude
Code auto-loads this file every session; it is committed so the process that produced this
repository is reproducible by anyone who clones it, not just on the machine that ran it.

The three roles this file refers to are defined as executable agents in
[`.claude/agents/`](.claude/agents/).

## Commit discipline

- Build one small piece at a time: write it, test it against something real (not just "looks
  right"), and only once it passes, commit it. Never batch a large, untested pile of files into
  one commit at the end — that's exactly the failure mode this rule exists to prevent.
- Each commit should be small enough to describe honestly in its own message — if the message
  needs "and" three times, it's probably more than one commit.
- Author: Jayakumar Devaraj <jayakumar.d10@gmail.com>. Never add Co-Authored-By or "Generated
  with" trailers/footers of any kind.
- Fresh `git init` per repo, no monolith history preserved.
- **Push after every commit.** These repositories are reviewed via GitHub only, so an unpushed
  commit is invisible work. Do not wait to be asked, and do not accumulate a backlog because a
  brief said "ask before pushing" — ask once, early, then keep the remote current.

## Branching and pull-request workflow

**Every task gets its own branch off `main`, goes through a PR, and gets merged before the next
task starts — never commit directly to `main`.** Verified against this platform's actual practice,
not assumed: `agentic-sdlc-control-plane`'s real history is 8 merged PRs, zero direct-to-main
commits. This repo's first several tasks (through the guardrails module) went straight to `main`
before this was caught and corrected on 2026-08-07 — not retroactively rewritten, since rewriting
already-pushed history is its own risk, but every task from that point on follows this.

- Branch name: `<type>/<short-kebab-description>` — `feature/`, `fix/`, or `chore/`, matching the
  exact prefixes control-plane's real branches use.
- Implement and test on the branch, following the commit discipline above unchanged (small,
  real-tested, honestly-described commits; push the branch after every commit — the "push after
  every commit" rule applies to the branch, not just to `main`).
- Open a PR once the branch is ready. Verify CI is green **on the PR**, not just on a local run.
- Merge via a real merge commit (not squash, not rebase — matches control-plane's actual merge
  history), then delete the branch.
- Only then start the next task.

## Self-check before continuing

Periodically ask: **"Would someone looking only at GitHub right now see what I have actually
done?"** If not — uncommitted work, or committed-but-unpushed work — stop and fix that before
writing anything new. Committing locally is necessary but not sufficient; the audit trail is the
remote one. This applies to every repo in this platform, checked regularly, not just at the end
of a session.

## Keep documentation in sync

Whenever a plan or implementation changes — a design decision gets revised, a bug fix changes
behavior, a dependency pin changes — update the relevant documentation (README, this file, the
platform's planning document) in the same change, not as a follow-up. Stale docs are a bug, not
a TODO.

## Documentation standard

- README.md section order, fixed: Tech stack -> Architecture -> Quickstart -> Local development
  -> Testing -> Deployment/CI. Nothing else. README explains how to run/use this repo, never why
  a decision was made (that's ADRs, `docs/adr/`) or how this repo relates to the other repos in
  this platform split (tracked in a private planning document, not committed anywhere).
- Never reference local machine paths (`C:\Users\...`, `C:\srcCode\...`) in committed files.
- Every significant design decision gets a lightweight ADR: `docs/adr/NNNN-title.md` (Context /
  Decision / Consequences). Write one when a decision has a cost someone could reasonably
  dispute, or a defect had a design cause rather than a coding cause — not for every change.
- A doc claim not backed by a command actually run against real containers/code is a bug, not
  documentation — verify before writing, not after.

## AI-assisted engineering practice

This platform demonstrates AI-assisted engineering across three roles per repo, scoped to what
actually applies:

1. **Design**: a design document and architecture diagram for this repo (README + this file).
2. **Development**: error handling and logging, meaningful Git commit history (see above). This
   repo's own auditing concern is provenance, not a ledger: every generated artifact (`spec.md`,
   `design.json`, a compile diagnosis) must trace back to the exact COBOL source line it was
   derived from. The hash-chained audit ledger itself belongs to `agentic-sdlc-control-plane` —
   duplicating it here would be a second, independently-maintained log of the same events, which
   is a defect class control-plane's own `development` agent explicitly forbids.
3. **QA**: unit tests + coverage report, and/or a functional verification report for anything a
   unit test can't reach (e.g. the self-healing compile loop recovering from a real injected
   error, not a mocked one).

## This repo's place in the platform

A **specialist**, not an orchestrator. `agentic-sdlc-control-plane` is the platform's
domain-agnostic orchestration engine — by its own ADR 0001, it must carry no tenant-specific
vocabulary. This repo is the inverse: it exists specifically *because* COBOL/mainframe-modernization
logic (COBOL parsing, PIC-to-BigDecimal mapping, CardDemo-specific reasoning) cannot live in
control-plane without violating that constraint. It ships as a CLI control-plane invokes the same
way its `coder` node already invokes the `claude` CLI — this repo has no ingress API, no Postgres
checkpointer, and no human-in-the-loop gates of its own. Durability and approval gating are
control-plane's concern at the coarse granularity of one specialist invocation; this repo's own
internal sub-pipeline (spec extraction, critique, design, code generation, self-healing compile) is
a bounded, in-process LangGraph run, not a second durable execution engine.
