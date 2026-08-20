# ADR-0033: The verification report is a hub with one spoke per phase, split on chronology

## Status

**Accepted** (2026-08-20). Applies to `docs/qa/verification-report.md` and the new
`docs/qa/verification/` directory. Does not change what is verified, or any claim made about it.

## Context

`docs/qa/verification-report.md` had grown to **3,338 lines** — one preamble, a coverage table, 70
functional-verification entries and a closing gaps section, in a single file. Each entry states the
exact command run and its real output, which is exactly why the file grows monotonically: nothing
in it can be summarised away without breaking the rule that produced it.

Two costs had become real rather than theoretical:

- **Reading one entry means loading all of them.** The file is cited from source docstrings
  (`core/model_catalog.py`, `graph/design_graph.py`, `nodes/spec_critic.py`,
  `nodes/spec_extractor.py`), from config comments (`config/model_catalog.yaml`,
  `config/model_routing.yaml`), from four ADRs, from six test modules, from `README.md` and from
  `CLAUDE.md`. Every one of those pointers is a pointer at a specific fact, and following it costs
  the whole file — for a human and, more expensively, for any agent whose context window it
  displaces.
- **Appending is the only safe edit.** With no scope boundaries, the only place a new entry can go
  without a merge conflict is the end, so chronology became the de facto structure by accident
  rather than by decision.

**Counted, before deciding anything** (`grep -rn "verification-report"`, excluding `.venv/` and the
file itself): **18 inbound references** across 16 files. Any structural change that moves the path
those 18 point at pays for itself 18 times over, in files that have nothing to do with QA.

## Decision

### 1. The hub keeps the existing path

`docs/qa/verification-report.md` stays where it is and becomes the hub: the preamble, the
unit-test-versus-functional-verification framing, and an index table. It carries **no logs, no
commands and no metrics of its own** — verified after the split: 0 code fences, 0 `###` entries,
38 lines.

This is the whole reason the hub is not `docs/qa/README.md` or `docs/qa/master-index.md`, both of
which read better as filenames. All 18 inbound references keep resolving with zero edits outside
`docs/qa/`, and a stale pointer in a docstring is a defect nobody notices until they follow it.

### 2. Spokes split on contiguous chronological boundaries, never by reordering

The 12 spokes under `docs/qa/verification/` are named for the phase of work they cover — `00`
coverage, `01`–`10` the functional-verification phases in the order they happened, `11` the open
gaps. Every spoke is a **contiguous line range** of the original file.

The alternative — grouping by module or by pillar, gathering every `cobol_parser` entry into one
file regardless of when it was written — was rejected. The report is a *running record*: entries
refer to each other as "the entry above" and "the entry below", and several exist specifically to
correct an earlier one (the coverage table's transcription error, the oracle's first record, the
gap that "named the wrong cause"). Reordering those into thematic buckets would silently detach
each correction from what it corrects.

### 3. Only wrapper headers are new; every body is a byte-verbatim slice

Each spoke is a `# <title>`, a four-line pointer back to the hub, an optional
`## Functional verification`, and then the original bytes. No entry was retyped, rewrapped or
paraphrased.

This was **proved, not asserted**: a script reassembles the 12 spoke bodies in order, re-inserting
the one blank line per seam that became each file's terminating newline, and diffs the result
against a copy of the pre-split file — `PARITY OK: 3323 lines reassembled byte-identical`, with
`headings missing from spokes: none` and `broken hub links: none`.

## Consequences

### Twenty-two prose cross-references now sometimes cross a file boundary

Counted, not estimated: **22** phrases of the form "the entry above" / "the entry below" survive in
the spokes. Most still resolve within their own spoke; some now point into a neighbouring file.
They were deliberately left as written rather than rewritten into links, because rewriting them is
an edit to verified prose for a navigational convenience, and this ADR's whole premise is that the
prose is the artifact. Anyone rewriting one should turn it into an explicit link at that point, not
in bulk.

### The largest spoke is 566 lines, and that is the number to watch

Spoke sizes run 55 to 566 lines. `09-the-write-path-and-the-round-trips.md` is the outlier and will
keep growing, since the round trips are still the active work. When a spoke stops being readable in
one sitting the answer is to split *it* on the same rule — a contiguous chronological boundary,
verified by reassembly — not to re-shard the whole report.

### A new entry now requires choosing a spoke

Previously an entry went at the end. Now the author picks the spoke that owns the scope, which is a
small judgement call and occasionally a wrong one. That is the intended trade: it is the same
judgement that makes the index useful, and a misfiled entry is a one-line `git mv` of a block,
whereas an unstructured 3,338-line file is not recoverable by any single edit.

### The README's section order is untouched, and no other document was split

`README.md` (358 lines) is bounded by this repo's own documentation standard — a fixed six-section
order with "nothing else" — so splitting it would break a stated rule to solve a problem it does
not have. ADRs are single-decision records by construction and stay whole, including the longest
(ADR-0019, 290 lines). `tests/fixtures/golden/CBACT04C/spec.md` is a hand-verified regression
baseline that tests compare against; it is a fixture, not documentation, and must not be
restructured.

## Amendment (2026-08-20, same day)

The sentence above — *"ADRs are single-decision records by construction and stay whole, including
the longest (ADR-0019, 290 lines)"* — asserted the premise and the instance in one breath, and only
the premise holds. **ADR-0019 was not a single-decision record.** It bundled the target stack, the
`generate` scope and the persistence choice, which is why it was the longest: three decisions'
worth of context, evidence and consequences in one file, cited roughly sixty times across the repo
in ways that could not say which of the three they meant.

It has since been superseded by [ADR-0034](0034-java-25-on-maven-with-the-framework-version-pinned-in-the-build.md),
[ADR-0035](0035-fixed-occurs-stays-unrepresentable-and-cbact01c-demo-outputs-stay-out-of-generate.md)
and [ADR-0036](0036-the-generated-jobs-persist-to-postgresql-loaded-once-from-carddemo-ascii-files.md),
one per decision, with ADR-0019 left in place and unedited as the historical record.

**The rule this record states did not change, and is what caught the exception**: an ADR stays
whole because it holds one decision. Length was never the criterion — ADR-0019 was long *because*
it held three, and the fix is superseding records, not a hub-and-spoke split. Recorded as an
amendment rather than an edit because "no other document was split" was the claim, and it stopped
being true the same day it was written.
