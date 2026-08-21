# ADR-0044: A capability closes on a second instance, and a caveat carries a status

## Status

**Accepted** (2026-08-21). A process decision rather than a code one, written up because it has a
real cost and because the alternative — leaving both rules as habits — is what produced the defects
below.

## Context

Two failures this session were not bugs. Both were the *absence of a mechanism* around something
this repository already knew.

### One instance was allowed to close a capability

Gap **G31** ("nothing renders readers, writers or job configuration") closed on a grep: did
`rendering/` contain `JobBuilder`, `StepBuilder`, `ItemReader`, `@Bean`. It did — for `CBACT04C`,
the only program the renderer had ever run against. The audit, the README and `CLAUDE.md` all
inherited *"wiring is rendered"* where the defensible claim was *"wiring is rendered for one
program"*.

`CBTRN02C`, the second Track C program with real business logic, then needed **four new declared
contract facts across three schema versions** before it would build:

| forced fact | what the program does | record |
|---|---|---|
| `write_mode` with `upsert` | writes `TCATBAL` by both `WRITE` and `REWRITE` | ADR-0037 |
| `reads_own_writes` | decides acceptance from state its own posting rewrites | ADR-0039/0040 |
| a shared working set at chunk 1 | so item *n* sees items *1..n-1* | ADR-0041 |
| `optional_lookups` | its `TCATBAL` read *creates* a row on `INVALID KEY` | ADR-0042 |

None was exotic. Each was visible in that program's COBOL from the first day, and a single
reconnaissance pass found two of them in minutes. What made them four separate emergencies was that
nothing required looking before building.

**This is the ninth instance of one defect class**, and the register has been counting without
stopping it: *a fact the deterministic layer already held that was dropped one step before its
consumer* — G21, G24, G26, G28, G30, its own entries reading *"third instance this session"* and
*"fifth instance of G21's shape"*.

### A caveat was recorded and then relied upon

The oracle's `PROVENANCE.md` has listed *"the zoned-decimal sign representation"* as not
corroborated against IBM Enterprise COBOL since the day it was generated (ADR-0028). Everything
downstream treated that oracle as ground truth anyway. The caveat came due four revisions later, as
seven wrong decisions in `CBTRN02C`'s round trip: GnuCOBOL reads the corpus's sign overpunches as
digit `0`, so the oracle's own credit-limit comparisons ran on amounts missing a digit (ADR-0043,
audit **G33**).

A probe would have taken minutes — `OPTEST.cbl` is eleven bytes and one `DISPLAY`. Nothing asked
for one, because a prose caveat asks nothing of anybody.

## Decision

**1. A capability is complete when a second, independent instance exercises it.** A gap closes
against an instance, and the closure names it: *"closed for X"* is honest, *"closed"* is a claim
about instances nobody has tried. A construct marked supported, a renderer called done, and a gap
marked `CLOSED` are all the same kind of claim and take the same rule.

**2. Reconnaissance precedes implementation, per instance.** Before generating a program the
pipeline has not seen, parse it and list every fact its COBOL needs that the contract does not
carry. The output is a list, not a build failure.

**3. Every entry on a known-unverified list is *probed* or *accepted, untested* — and says which.**
Probed names an executable check. Accepted names the consequence: what would be wrong, and how
anyone would notice. `docs/qa/oracle-caveats.md` is that register, and
`tests/system/test_oracle_caveats.py` fails if a caveat the provenance names has no row, or a row
has no status.

**4. When a fix is the *n*-th of a kind, the change worth making is the one that makes the
*n+1*-th loud.** Instance fixes are not wrong; they are just not the deliverable once a class is
named.

## Consequences

- **Closure slows down, deliberately.** A capability that would have been "done" now waits for a
  second instance. The cost is real and it is smaller than four unplanned schema versions.
- **The register test is enforcement, not documentation.** It is why rule 3 will survive a session
  that is in a hurry — which is the only kind of session that skips it. Its own checks are shown to
  fail first, on a copy with a row removed and a status replaced by reassurance.
- **Rule 1 does not apply retroactively.** G31 stays closed and `CBACT04C`'s `500 of 500` stands;
  what changes is what the *next* closure has to clear. Reopening settled records to satisfy a new
  rule would be its own kind of churn.
- **Three of the four caveats remain deferred, and that is allowed.** The register asserts the shape
  of the record, never the verdict — requiring every caveat to be probed would make the honest
  answer *"not yet worth it"* unrecordable, and an unrecordable answer is how the first one got
  lost.
- **The probe that ran found the oracle wrong**, and the register says so. A register that only
  recorded comfortable answers would be worse than none, so that row is pinned by a test of its own.
