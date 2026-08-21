# ADR-0042: A lookup the program tolerates missing is declared per step, not derived from `INVALID KEY`

## Status

**Accepted** (2026-08-21). Schema **3.9.0**. Found while wiring `CBTRN02C`'s sequential step
([ADR-0041](0041-a-sequential-step-shares-one-working-set-and-is-not-restartable.md)) — the fourth
contract gap that program has exposed, after
[ADR-0037](0037-a-file-written-both-ways-renders-as-an-upsert.md),
[ADR-0039](0039-cbtrn02c-posting-is-declared-and-refused-because-its-decision-reads-its-own-writes.md)
and [ADR-0040](0040-a-step-that-reads-its-own-writes-declares-it-rather-than-being-detected.md).

## Context

`java_reader` wraps every keyed lookup in `require(...)`, which throws when the lookup finds
nothing. Its own comment justifies this as *"The COBOL abends when a keyed read finds nothing;
substituting a default would post an interest figure against data that does not exist."*

**That is a renderer choice, not a parsed fact**, and `CBTRN02C` is where it stops holding.
`2700-UPDATE-TCATBAL` reads a balance row and, `INVALID KEY`, **creates one**:

```cobol
READ TCATBAL-FILE INTO TRAN-CAT-BAL-RECORD
   INVALID KEY
     DISPLAY 'TCATBAL record not found for key : ' FD-TRAN-CAT-KEY '.. Creating.'
     MOVE 'Y' TO WS-CREATE-TRANCAT-REC
END-READ.
```

It does that **44 times on this corpus** — which is precisely how the 50 balance rows the job starts
from become the 94 the oracle asserts (`run-oracle.sh` checks that number). A rendered reader would
abend on the first daily transaction posting to a category its account has no balance row for.

**The obvious derivation does not work.** Both Track C programs write `INVALID KEY` clauses whose
bodies only `DISPLAY` and continue — `CBACT04C`'s account, xref and disclosure-group reads all do —
so "has an `INVALID KEY` clause" does not separate a handled miss from an unhandled one.
`CBACT04C`'s reader refusing a miss is not a faithful translation of its COBOL either; it is
untested behaviour that happens to be right because no lookup misses on its corpus.

## Decision

**`BatchStepDesign.optional_lookups: list[str]`** — the entity names whose lookup this step
tolerates finding nothing. The rendered reader omits `require(...)` for those and passes `null` into
the item, so the processor translates the COBOL's `INVALID KEY` branch itself.

**The component is not parsed when it is absent.** The record parser slices fixed offsets and would
throw on the null the lookup is now allowed to produce, so the reader renders
`x == null ? null : toX(x)`. The refusal and the parse are two separate places, and relaxing only
the first produces a reader that fails one line later with a message about offsets rather than about
a missing record.

**Empty by default**, so every existing step refuses exactly as it does today.

## Options considered

**(a) Derive it from the `INVALID KEY` clause.** The faithful model, and it would fix `CBACT04C`'s
reader too. Rejected **on blast radius rather than on merit**: it changes a reader measured green at
500 of 500 and 598 of 600, whose bodies do not null-check, so taking it means changing those bodies
and re-running the round trip as the gate. It stays the standing upgrade, and it should cite this
record when it is taken.

**(b) Make it a property of the composite component** — a nullable component means an optional
lookup. No new field, and rejected because it overloads nullability with I/O semantics: nothing in
the design says today which components may be null, so the fact would have to be added anyway, just
somewhere less obvious.

## Consequences

- `CBTRN02C`'s reader can now run past the first created balance row. Nothing else changes: the
  other two lookups still refuse a miss, asserted directly rather than assumed.
- **The declaration is only as good as whoever writes it.** A step that omits a lookup which really
  is optional gets an abend on real data; one that declares a lookup optional when the program
  abends gets a `null` its body may not expect. The same cost as ADR-0040's flag, for the same
  reason, and named here rather than discovered.
- **This is the fourth contract fact one program has forced**, and that is worth stating as a
  finding in itself: the renderer was complete for `CBACT04C` and is four declarations short of
  complete for the next program with real business logic. Whatever the third program needs is not
  yet known, and the honest reading is that "the renderer works" still means "the renderer works on
  the programs it has been run against".
