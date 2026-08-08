# 0006 - Per-field construct isolation, and source-label provenance for now

## Context

`nodes/spec_extractor.py` is the first module to run `pic_mapper.map_pic_clause` across an entire
real program's `WORKING-STORAGE`, not a hand-picked list of individual field declarations the way
`test_pic_mapper.py` does. Running it against the real `CBACT04C.cbl` fixture surfaced two
concrete design questions ADR-0002 doesn't answer on its own.

**Question 1 — what happens to the rest of a program when one field is unsupported?**
`CBACT04C`'s own `WORKING-STORAGE` genuinely contains two real `REDEFINES` groups
(`TWO-BYTES-ALPHA REDEFINES TWO-BYTES-BINARY`, `FILLER REDEFINES DB2-FORMAT-TS`) sitting among 75
otherwise-ordinary fields. ADR-0002 says `REDEFINES` "must be detected and rejected to a human
gate, not partially parsed" — but it doesn't say at what granularity. Read one way, any
`UnsupportedPicConstructError` anywhere in a program could mean "abandon spec extraction for this
program entirely, escalate the whole thing." Read the other way, it means "this specific field is
unresolvable; the other 75 are not affected by it and gain nothing from being blocked too."

**Question 2 — what does "trace back to the exact COBOL source line" mean for the first node that
actually produces an artifact?** `CLAUDE.md`'s standing provenance goal is line-precise. But
`parsing/cobol_parser.py` — already merged, independently tested (28 tests, 98% coverage) before
this node existed — does not carry line numbers through `extract_working_storage_fields` (renamed
`extract_record_fields` by [ADR-0011](0011-parse-every-data-division-section-and-reject-fixed-occurs.md))
or `extract_paragraphs`; it discards them once a field/paragraph's raw text is assembled. Line-level
provenance would mean extending that module's data model, not just consuming it. Still true after
ADR-0011, which widened which sections are parsed without adding line numbers.

## Decision

**1. Isolate `UnsupportedPicConstructError`/`ValueError` per field, not per program.**
`extract_field_mappings` catches both exceptions inside its per-field loop and records them in
`unsupported_fields`, rather than letting either propagate out of the whole extraction. Every
other field in the same program — including fields in the very same 01-level group as an
unsupported one, once they don't themselves reference the offending clause — is still mapped and
still reaches the model's narration. This keeps ADR-0002's actual guarantee (an unresolvable
construct is never guessed at, always surfaced for human review) while not making one ambiguous
field in a 75-field program a reason to produce nothing for the other 75.

A related, purely observational finding from running this against real data: `CBACT04C`'s
`FILLER REDEFINES DB2-FORMAT-TS` block has several continuation fields with trailing comment
characters after their line's terminating period (visible in the real source as stray `E`/`-`/`M`
markers past column 72). `cobol_parser._iter_field_sentences`' sentence-boundary logic — a line
"closes" a field declaration only when its stripped text ends in `.` — treats those trailing
characters as meaning the sentence hasn't closed yet, and merges several physically separate field
lines into one `FieldDeclaration`. The practical effect here is benign: every field in that block
is inside the `REDEFINES` group regardless, so whether they surface as one merged unsupported
entry or seven separate ones, the ADR-0002 outcome (flagged, not guessed at) is identical. Noted
here as a real parser characteristic worth hardening in a future parser pass, not fixed now —
changing `cobol_parser.py`'s sentence-boundary logic is out of scope for a node that only consumes
it, and the one real case it affects today doesn't change any field's Java shape.

**2. Provenance is source-label-level today, not line-level.** Every fact `spec_extractor` emits
(`UnsupportedField.source_label`, and each wrapped `<untrusted-cobol-source label="...">` prompt
section) is attributed to the exact source unit it came from — the program itself, or a specific
named copybook, by real, checkable name, not a vague "somewhere in the input." This is real
provenance and is exercised end-to-end against real data (see
`tests/system/test_spec_extractor.py`). It is deliberately not yet line-precise: doing so would
require adding line-number tracking to `parsing/cobol_parser.py`'s public `FieldDeclaration` and
`Paragraph` models, which is a change to an already-shipped, independently-verified module's
contract — a larger, separately-scoped piece of work, not a side effect of the node that merely
consumes it.

## Consequences

A human reviewing a `spec_extractor` gate item today knows exactly which program or copybook a
flagged field came from, and can open that file — just not jump to the exact line without reading
it. Line-level provenance remains a real, tracked gap (`docs/qa/verification-report.md`), to be
closed by a dedicated `cobol_parser.py` change when a consumer's need for it justifies extending
that module's contract, not assumed away.

The per-field isolation decision means a program with genuinely widespread unsupported constructs
(hypothetically, a program that is mostly `REDEFINES`) would still "succeed" at spec extraction
with a `spec.md` that is mostly empty of narration and mostly gate items — this is correct
behavior (every fact is honestly surfaced), not a bug, but a caller further downstream
(`spec_critic`, or eventually a human gate) should treat a spec with a high proportion of flagged
fields as a signal in itself, not just read each flagged item in isolation.
