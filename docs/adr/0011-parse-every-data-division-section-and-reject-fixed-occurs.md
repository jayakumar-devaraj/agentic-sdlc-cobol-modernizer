# ADR-0011: Parse every `DATA DIVISION` section, and reject a fixed `OCCURS` rather than flatten it

## Status

Accepted (2026-08-07).

## Context

`parsing/cobol_parser.py`'s field extraction read only the `WORKING-STORAGE SECTION`: it found
that header, then stopped at the next section or division header. Everything else a COBOL program
declares fields in — the `FILE SECTION`'s `FD` record layouts, the `LINKAGE SECTION`'s parameters —
was never parsed.

The consequence is worse than incompleteness. Those fields were **neither mapped nor flagged as
unsupported**. They were absent. Every other boundary in this repo is built to fail loudly:
`UnsupportedPicConstructError` exists precisely so an ambiguous construct reaches a human instead of
getting a plausible-looking answer, and ADR-0002 frames the whole parser around "detected and
rejected, never guessed at". Silent absence defeats that. A reviewer reading `spec.md` sees a field
reference table that looks complete, with no indication anything was omitted.

Three real things were hidden, all confirmed against the actual fixture source rather than reasoned
about:

- `CBACT01C` declares `OUT-ACCT-CURR-CYC-DEBIT` and `ARR-ACCT-CURR-CYC-DEBIT` as
  `PIC S9(10)V99 USAGE IS COMP-3`. These are the **only** `COMP-3` fields anywhere in Track C, and
  `COMP-3` is named explicitly in Milestone C2's gate. `pic_mapper` has always detected `COMP-3`
  correctly; nothing could reach it.
- `CBACT04C`'s `PARM-LENGTH` belongs to `EXTERNAL-PARMS` — the record named by the program's own
  `PROCEDURE DIVISION USING EXTERNAL-PARMS` clause. It is the program's input parameter.
- All four programs' `FD` record layouts, which describe the files each batch job reads and writes.
  These are what a Spring Batch reader/writer (ADR-0009) has to be designed against.

**How this was found matters for the process, not just the fix.** Existing tests did not catch it
and could not have: `test_spec_extractor.py` asserted `len(mappings) == 75` — a number produced by
running the code, so it encoded the defect as the expectation — and the per-program tests spot-check
individual fields, which only ever confirms that fields that *are* present are correct. Neither
asks the different question "is anything missing?". It surfaced only when Milestone C2's gate was
taken at its literal wording ("100% of numeric/`COMP-3` fields, manually cross-checked") and every
numeric field was hand-derived from source and compared as an exact set. That style of check —
derive independently, then compare sets, rather than assert what the code returned — is the reason
this ADR exists.

Extending the parser then forced a second decision. `CBACT01C`'s `FILE SECTION` contains Track C's
only fixed `OCCURS`:

```cobol
05  ARR-ACCT-ID                PIC 9(11).
05  ARR-ACCT-BAL OCCURS 5  TIMES.
  10  ARR-ACCT-CURR-BAL        PIC S9(10)V99.
  10  ARR-ACCT-CURR-CYC-DEBIT  PIC S9(10)V99
                               USAGE IS COMP-3.
```

`pic_mapper` deliberately allowed this, with a test asserting so: only `OCCURS ... DEPENDING ON` was
rejected, on the reasoning that a fixed `OCCURS` is unambiguous. That reasoning is correct and beside
the point. Until this change the construct was unreachable, so the decision had never been exercised
against real data.

## Decision

**1. Field extraction covers the whole `DATA DIVISION`, not one named section.**

The region runs from the `DATA DIVISION` header to the `PROCEDURE DIVISION` header, spanning `FILE
SECTION`, `WORKING-STORAGE SECTION`, and `LINKAGE SECTION` together — rather than slicing out each
known section by name. A section this parser has never seen (`LOCAL-STORAGE SECTION`, say) is then
included by default rather than silently skipped. Given that silent skipping is the exact defect
being fixed, inclusion is the right direction to be wrong in: an unexpected field that reaches
`pic_mapper` either maps correctly or raises, and both outcomes are visible.

Two existing behaviors are preserved: a source fragment with only a `WORKING-STORAGE SECTION` header
keeps the old section-scoped behavior, and a copybook with no division headers at all is read whole.

**2. The function is renamed `extract_working_storage_fields` → `extract_record_fields`.**

Not cosmetic. The old name is a substantial part of why the defect survived review — reading a
program's fields through a function called `extract_working_storage_fields` looks correct until you
think to ask what else there is. The name asserted a scope that nobody re-examined.

**3. A fixed `OCCURS` is rejected, reversing the earlier decision to map it.**

`PicMapping` has no cardinality field. Mapping `ARR-ACCT-CURR-BAL` returns precision 12, scale 2,
signed — every one of which is right — attached to a shape that says "one `BigDecimal`" where the
record really holds five. Generating Java from that produces `BigDecimal arrAcctCurrBal` instead of a
collection: code that compiles, passes type checking, and is wrong. That is this module's stated
worst case ("a wrong answer looks exactly like a right one") and the plan's own High-impact
"compiles but semantically wrong" risk.

Being unambiguous was never sufficient. What matters is whether the result can be *expressed*
without loss, and it cannot. So it routes to the human gate, carrying the construct name
`"OCCURS (fixed)"` so a reviewer can tell it apart from the genuinely ambiguous
`OCCURS ... DEPENDING ON`.

The check runs against `adjacent_text`, not the field's own line, because `OCCURS` is declared on the
parent group line and never on the `PIC`-carrying children it multiplies — a field-local check would
miss every field that matters.

## Consequences

**`COMP-3` is genuinely reachable.** `OUT-ACCT-CURR-CYC-DEBIT` maps with `usage=COMP_3`, precision
12, scale 2, signed — verified, along with the fact that it stays the only mapped `COMP-3` field in
Track C. `docs/cobol-construct-support-matrix.md` recorded `COMP-3` as "not present in Track C's
scope"; that was derived from copybooks only and has been corrected, and the matrix now separates
"in scope" from "currently reached by the parser", which is the distinction it had been collapsing.

**Every numeric declaration is now accounted for.** Across all four programs, each one lands in
either `field_mappings` or `unsupported_fields`. Previously 20 were in neither. Field counts move:
`CBACT04C` 75 → 93, `CBTRN02C` 88 → 102, `CBACT01C`'s unsupported 28 → 32.

**`ARR-ARRAY-REC`'s four fields are over-flagged, deliberately.** `ARR-ACCT-ID` and `ARR-FILLER` sit
beside the array without being inside it, but the parser hands `pic_mapper` a whole `01`-level group
with no nesting information, so the whole group is isolated. This is the same over-flagging ADR-0006
already documents for `REDEFINES`, accepted for the same reason: over-flagging sends an unambiguous
field to a human, which costs review time; under-flagging ships wrong code. Fixing it properly means
teaching the parser real structural nesting, which is Track B's alias-analysis module (B3), not this
change.

**A real cost: two `COMP-3` fields, and the array they describe, now reach the gate rather than the
design.** `ARR-ACCT-CURR-CYC-DEBIT` is `COMP-3` *and* inside the fixed `OCCURS`, so it is isolated
rather than mapped. This is the intended trade — its `raw_text` is carried verbatim into the gate
item, so nothing is lost, but a reviewer has to act on it.

**Domain entities are unchanged.** `build_domain_entities` only turns copybook-sourced fields into
entities (ADR-0010), and `FILE SECTION` layouts are program-local, so all 7 entities stand. The new
fields are visible in `spec.md` and in the Known Facts block the model reasons over, which is what
this change is for. Whether an `FD` record layout should itself become a domain entity is a real
open question for Milestone C4, when a Spring Batch reader needs a type to read into — deliberately
not answered here.

**The golden fixture's Field reference table was regenerated, not hand-edited** — 18 rows added,
zero removed, hand-verified prose byte-identical, confirmed by diffing. This is exactly the property
that section was generated for.

**The eventual fix for cardinality is to carry it in `PicMapping`**, alongside a `DomainField` that
can express a collection. Deferred rather than done here: it widens `PicMapping`, `DomainField`, the
generated schemas, and `solution_architect` all at once, for one occurrence in Track C that sits in
a file record layout no node consumes yet. Revisit when Milestone C4 generates a reader for that
file, or when Track B's B3 module takes on alias analysis.
