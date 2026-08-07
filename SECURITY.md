# Security

## Reporting a vulnerability

Open a private security advisory via GitHub's "Report a vulnerability" flow on this repository, or
contact jayakumar.d10@gmail.com directly. Do not open a public issue for a suspected vulnerability.

## Scope notes specific to this repo

- This repo has no ingress API and no credentials of its own beyond what it needs to read the
  tenant repository it's pointed at and call an LLM provider — see
  `docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md` for the invocation
  boundary.
- COBOL source text (including comments) parsed from a tenant repository is treated as untrusted
  input, never as instructions — see `.claude/agents/development.md`. A prompt-injection report
  against the extraction/critique prompts is in scope.
- Track C's parser has a defined hard boundary (`REDEFINES`, `OCCURS DEPENDING ON`,
  `COPY REPLACING` are rejected to a human gate, never guessed at —
  `docs/adr/0002-a-hand-rolled-parser-for-a-deliberately-bounded-grammar.md`). A report that this
  boundary can be silently bypassed is a high-priority finding.
