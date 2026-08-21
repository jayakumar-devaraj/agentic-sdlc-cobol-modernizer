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

## Declaring a capability complete

**A capability is complete when a second, independent instance exercises it — not when the first
one passes.** This rule exists because the repository violated it and paid for it, and the price is
recorded rather than paraphrased.

Gap **G31** ("nothing renders readers, writers or job configuration") was closed on a grep: did
`rendering/` contain `JobBuilder`, `StepBuilder`, `ItemReader`, `@Bean`. It did — for `CBACT04C`,
the only program the renderer had ever been run against. The audit, the README and this file all
inherited *"wiring is rendered"* when the defensible claim was *"wiring is rendered for one
program"*. The second program with real business logic, `CBTRN02C`, then needed **four new declared
contract facts and three schema versions** before it would build at all (ADR-0037, ADR-0040,
ADR-0041, ADR-0042). None of them was exotic; each was visible in its COBOL from the first day.

So, concretely:

- **Do not close a gap, or mark a construct supported, on one instance.** State the scope in the
  closure itself — *"closed for X"* is an honest closure; *"closed"* is a claim about instances
  nobody has tried.
- **Reconnaissance precedes implementation, per instance.** Before generating a program the
  pipeline has not seen, parse it and list every fact its COBOL needs that the contract does not
  carry. This is cheap — the four facts above were all findable in one pass — and it converts a
  sequence of build failures into one planned change.
- **A recurring defect class gets a mechanism, not another instance fix.** This repo's register
  names the same shape at least five times — *a fact the deterministic layer already held that was
  dropped one step before its consumer* (G21, G24, G26, G28, G30) — and counted them without
  stopping them. When a fix is the *n*-th of a kind, the change worth making is the one that makes
  the *n+1*-th impossible or loud.

## An unverified caveat needs a probe or an owner

**A caveat recorded in prose and left there is a defect with a delay on it.** The oracle's own
`PROVENANCE.md` listed *"the zoned-decimal sign representation"* as not corroborated from the day it
was generated. Everything downstream then treated that oracle as ground truth, and the caveat came
due much later, as seven wrong decisions in a round trip (ADR-0043, audit G33).

Every entry on a known-unverified list is therefore one of two things, and says which:

1. **Probed** — an executable check exists and is named. `tools/cobol-oracle/OPTEST.cbl` is the
   pattern: eleven bytes, one runtime, an answer that settles the question in seconds.
2. **Accepted, untested** — with the consequence written out: what would be wrong, and how anyone
   would notice.

`docs/qa/oracle-caveats.md` holds that register, and a test asserts every caveat the provenance
names appears there with a status. A list nothing checks is how this one aged into a defect.

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

## Refactoring an oversized document: hub-and-spoke

A markdown file that has grown so large that reading one section costs the whole file is a defect,
not a filing preference. It burns context windows and bleeds tokens through every downstream LLM
pipeline that follows a pointer into it. Refactor it into a **hub-and-spoke** layout, split by
logical scope — milestones, features, phases of work, whatever boundary the document itself
already uses.

1. **The hub keeps the original path.** Count the inbound references first
   (`grep -rn "<filename>"`) — they live in source docstrings, config comments, ADRs, tests and
   CI, and a renamed hub breaks every one. The hub carries the framing and an index: **no logs, no
   commands, no metrics of its own.** Spokes go in a sibling directory named for the hub.
2. **Split on contiguous boundaries, in document order.** Never regroup a *running record* into
   thematic buckets: its entries refer to "the entry above", and some exist only to correct an
   earlier one. Reordering silently detaches each correction from what it corrects.
3. **Bodies are byte-verbatim slices — only the wrapper header is new.** Slice the source's line
   list; do not retype. **Prove it**: reassemble the spokes in order and diff against a copy of the
   pre-split file. "It looks complete" is not verification, and a lost metric is invisible.
4. **Name spokes so they sort in document order**, carrying the source's own section number where
   it has one (`05-s3b-…` is § 3b), so a cross-reference like *"see § 3.3"* still resolves.
5. **Where the document tracks work in flight, the index carries a Status column** — Complete /
   In Progress / To Do — so a new session opens the live files and skips the settled ones. Derive
   each status from the repository, never from the document being indexed, and date the
   derivation. `docs/qa/verification-report.md` has no such column on purpose: every entry in it is
   a completed verification, so the column would carry no information.
6. **Update whatever states the maintenance rules** in the same change, naming the spoke each rule
   now applies to. A rule that says "edit § 6" is wrong the moment § 6 is a separate file.

**Do not apply this to:** an **ADR** (one decision per record — if it holds several, the fix is
superseding records, one per decision, with the original left in place and marked `Superseded`;
see ADR-0019 → ADR-0034/0035/0036), **`README.md`** (its section order above is fixed at six
sections, "nothing else"), or a **test fixture** such as
`tests/fixtures/golden/CBACT04C/spec.md`, which tests compare against byte-for-byte.

One cost to state rather than hide: prose cross-references of the form "the entry below" may now
span files. Leave them as written — rewriting verified prose in bulk for navigational convenience
is its own risk — and convert one to a real link when you next touch that entry for another
reason. ADR-0033 records this decision and its costs.

## AI-assisted engineering practice

This platform demonstrates AI-assisted engineering across three roles per repo. The objective is
not only a working solution but showing how these practices apply throughout the SDLC — every PR
doing real work is expected to carry evidence of this, not just the code itself, and not caught up
retroactively once it's noticed missing (as logging and the QA report below were, on 2026-08-07):

1. **Design**: a design document and architecture diagram for this repo (README + this file), kept
   current — updated in the same change as whatever it describes, not after.
2. **Development**: real, tested error handling (the `UnsupportedPicConstructError` family is the
   established pattern — fail loudly on an unambiguous case, never guess) and structured logging
   (`telemetry/logging_config.py`, wired into every entrypoint with an invocation lifecycle worth
   diagnosing — stderr only, never stdout, where a CLI's `--json` contract depends on stdout
   staying clean), plus meaningful Git commit history (see Commit discipline). This repo's own
   auditing concern is provenance, not a ledger: every generated artifact (`spec.md`, `design.json`,
   a compile diagnosis) must trace back to the exact COBOL source line it was derived from, once a
   node exists to produce one. The hash-chained audit ledger itself belongs to
   `agentic-sdlc-control-plane` — duplicating it here would be a second, independently-maintained
   log of the same events, which is a defect class control-plane's own `development` agent
   explicitly forbids.
3. **QA**: unit tests + a coverage report, **and** a functional verification report for anything a
   unit test can't reach on its own (a real database, real fetched source, a real external CLI,
   the self-healing compile loop recovering from a real injected error rather than a mocked one).
   `docs/qa/verification-report.md` is the standing home for both. It is a hub: it carries the
   framing and an index, and every entry lives in exactly one spoke under `docs/qa/verification/`
   (ADR-0033). A new entry goes in the spoke that owns its scope, updated in the same PR as
   whatever it reports on, never accumulated as a backlog. "The tests pass" is not itself a
   functional-verification entry: state what was verified, the exact command run, and the real
   result, the same way every entry already there does.

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
