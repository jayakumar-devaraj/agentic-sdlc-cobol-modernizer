# 0002 - A hand-rolled parser for a deliberately bounded grammar

> **Amended by [ADR-0011](0011-parse-every-data-division-section-and-reject-fixed-occurs.md)
> (2026-08-07).** Where this ADR says the parser "walks `WORKING-STORAGE`" for field declarations,
> it now walks the whole `DATA DIVISION` — `FILE SECTION` and `LINKAGE SECTION` included. Reading
> only `WORKING-STORAGE` turned out to drop real field declarations silently rather than reporting
> them, which contradicts this ADR's own reasoning below about not failing quietly. ADR-0011 also
> moves a fixed `OCCURS` (no `DEPENDING ON`) into the unsupported set. The decision recorded here
> is otherwise unchanged and still stands.

## Context

Track C's scope (documented in `docs/cobol-construct-support-matrix.md`) is deliberately narrow:
`WORKING-STORAGE` PIC clauses, `PROCEDURE DIVISION` paragraph structure, straight `COPY`,
sequential/VSAM read-only file I/O, and `MOVE`/`COMPUTE`/`IF`/`EVALUATE`/`PERFORM`. It explicitly
excludes CICS, BMS, JCL scheduling semantics, embedded SQL, `COPY REPLACING`, `REDEFINES`, and
`OCCURS DEPENDING ON` — deferred to Track B.

A full COBOL grammar (for example a generated ANTLR parser covering the whole language surface)
would handle both scopes correctly, including the excluded constructs. It would also mean pulling
in a JVM-based parser generator and its runtime as a dependency of a Python specialist, to parse a
grammar surface Track C has explicitly decided not to need yet — most of what a full grammar buys
is unused until Track B exists.

The excluded constructs are not excluded for parser-complexity reasons alone.
`REDEFINES`/`OCCURS DEPENDING ON` specifically requires resolving, for a given point in the
program, which of several overlapping interpretations of the same bytes is active — a real
alias-analysis problem, not a bigger grammar. A hand-rolled parser that attempted this without
that analysis would not fail loudly; it would silently pick a plausible-looking interpretation,
which is exactly the failure mode this platform's `pic_mapper` exists to prevent for the fields it
does handle.

## Decision

**A hand-rolled, line/paragraph-oriented structural parser for Track C's bounded grammar.** It
walks `WORKING-STORAGE` for PIC/`COMP-3` field declarations and `PROCEDURE DIVISION` for paragraph
boundaries and `PERFORM` call structure, and resolves straight `COPY` by textual inclusion. It does
not attempt `REDEFINES`, `OCCURS DEPENDING ON`, or `COPY REPLACING` — those constructs, if
encountered, are reported as unsupported and routed to a human-in-the-loop gate rather than
guessed at.

**Track B requires swapping this for a real grammar-based parser** before touching
`REDEFINES`/`OCCURS DEPENDING ON` — noted here so that decision is not deferred silently. The
alias-analysis problem those constructs pose needs a parser that actually models COBOL's data
division structure, not the paragraph-and-field scanner built here.

## Consequences

Track C's parser is simple enough to unit-test directly against real copybook text (see
`CVACT01Y.cpy`'s field list in the golden fixtures) without a grammar-generator toolchain in the
build. It also means this repo stays pure Python — no JVM dependency for Track C.

It has a hard boundary: any Track C input containing `REDEFINES`, `OCCURS DEPENDING ON`, or
`COPY REPLACING` must be detected and rejected to a human gate, not partially parsed. The
construct-support-matrix audit in Milestone C1 exists partly to confirm the four Track C programs
and their copybooks genuinely stay inside this boundary before any extraction logic runs against
them — an assumption worth checking against the real source, not assuming from the matrix alone.

Track B inherits a decision it must revisit, not extend: swapping the parser is a replacement of
this module, not an addition to it.
