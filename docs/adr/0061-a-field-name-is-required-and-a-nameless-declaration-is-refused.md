# ADR-0061: A field name is required, and a nameless declaration is refused rather than carried as `None`

## Status

**Accepted** (2026-09-01). Arises from the first `mypy` run over `src/`, added in the structure
work's tooling PR.

## Context

`PicMapping.field_name` was declared `str | None = None`. The `None` was reachable:
`_extract_field_name` matches `^\s*(\d+)\s+([A-Za-z0-9\-]+)` against the declaration text and returns
`None` when a declaration carries no level and name — a bare `PIC X(10).` fragment, verified rather
than assumed:

```
>>> _extract_field_name('05  ACCT-ID PIC 9(11).')  -> 'ACCT-ID'
>>> _extract_field_name('PIC X(10).')              -> None
```

**No consumer handled that `None`.** Five call sites across two modules assumed a name:
`data_loader` called `.upper()` on it and passed it to `_column_name`, and `solution_architect`
passed it into `_to_camel_case` and into a `str` parameter of `DomainField`. The optional type
described a state the code had no branch for, so the declaration and the program disagreed and
nothing said so. `tests/system/test_numeric_field_coverage.py` had already written
`assert mapping.field_name is not None` — the invariant was believed and asserted, just not
expressible.

Worth recording because it was initially diagnosed wrong: this looked like a `FILLER` defect.
`record_layout.py` does set `FieldLayout.field_name = None` for filler, and `data_loader` does
compare against the string `"FILLER"` — which reads like a mismatch. It is not. Those are two
different types, and a real FILLER declaration produces `field_name='FILLER'`, because
`05  FILLER  PIC X(10).` matches the level-and-name pattern perfectly well. The `None` case is
malformed input, not filler. A plausible mechanism is not a measured one.

## Decision

**`PicMapping.field_name` is `str`, and `map_pic_clause` raises `ValueError` on a declaration it
cannot name** — alongside the two `ValueError`s it already raises for a PIC clause that mixes
numeric and alphanumeric tokens, and for one with no recognized tokens. A declaration with no
identity belongs to the same class: input this parser cannot map without guessing.

This follows the repository's established error posture (`UnsupportedPicConstructError`: fail loudly
on an unambiguous case, never guess) and `CLAUDE.md`'s rule that a recurring defect class earns a
mechanism rather than another instance fix. Guarding five call sites would have been five places to
forget; making the state unrepresentable is one.

The alternative — keep `str | None` and add `if ... is None` at each consumer — was rejected because
every one of those branches would have been dead code written for a state no caller produces, and
each would have had to invent a substitute name for a column or a Java field.

## Consequences

**A behaviour change, bounded and stated.** Text that previously yielded a mapping with
`field_name=None` now raises. No caller reaches it: all three call sites pass
`FieldDeclaration.raw_text`, and `cobol_parser` only constructs a `FieldDeclaration` after matching
both a level and a name token, so the name is present by construction. No test passed a nameless
declaration either. A caller invoking `map_pic_clause` directly on a fragment gets an exception
where it previously got a mapping whose name was `None` — which is the honest outcome, since every
existing consumer would have crashed on it one step later.

**It surfaced a real defect in `solution_architect`, which is the stronger argument for the change.**
Fixing the same `mypy` run's shadowing report — `binding` bound by a `for` loop and then reused for
an optional lookup — required renaming the second variable. One use at line 395 sat outside the
window first inspected and was missed, and the suite caught it immediately:
`read_line` came back `444` where the test expected `None`. That is exactly the failure the
shadowing always permitted, because Python leaves a loop variable bound after the loop, so a missed
rename reads a stale non-`None` value rather than failing. The type error and the correctness hazard
were the same defect; the checker found it before a user did.

**What this does not establish.** `mypy` runs clean on default settings only. `strict = true` is
still off, several hundred `disallow_untyped_defs` reports are unaddressed, and nothing yet checks
`tests/`. Clean here means "no error at this setting", not "fully typed".
