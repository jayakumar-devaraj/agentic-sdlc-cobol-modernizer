# ADR-0037: A file a program both creates and updates renders as an upsert, not an append

## Status

**Accepted** (2026-08-20). Found by rendering `CBTRN02C` — the second Track C program with real
business logic (**G17**) — through the renderers built for `CBACT04C`
([ADR-0032](0032-a-declared-chain-with-no-store-renders-as-in-memory-staging.md) and the reader and
writer work before it). Schema **3.7.0**.

**Counts superseded in part by [ADR-0047](0047-the-corpus-sign-representation-is-converted-inside-the-oracle-pipeline.md)** (2026-08-21). The decision below — that a file
written both ways renders as an `upsert` — is unchanged and was never in question. Its *numbers*
were measured against an oracle whose runtime could not read the corpus's sign overpunches: read
**100 rows out, 50 created, and 150 for an appending writer** wherever this record says 94, 44 and
144. The argument is unaffected; only the corpus arithmetic behind it moved, and it moved in the
direction that makes the append defect larger rather than smaller.

## Context

`CBTRN02C` writes `TCATBAL-FILE` two ways in one program. It moves the three key components, reads
the row, and then either

- `REWRITE FD-TRAN-CAT-BAL-RECORD` (line 528) when the row exists, or
- `WRITE FD-TRAN-CAT-BAL-RECORD` (line 510) when it does not — `WS-CREATE-TRANCAT-REC` is the flag
  that decides.

That is COBOL's ordinary read-by-key create-or-update, and it is not incidental to this program: the
oracle pipeline asserts that 50 balance rows go in and **94** come out, because `CBTRN02C` creates
44 rows for `(account, type, category)` combinations the corpus has no balance for.

`extract_write_bindings` has always found both statements — deliberately, with a test
(`test_a_file_written_both_ways_keeps_both_bindings`) that says collapsing them would erase the
program's ability to *create* a row. **`build_file_access_paths` then kept `first_write` only.** So
`design.json` carried `is_update = False`, the `REWRITE` did not exist as far as the contract was
concerned, and `render_item_writer` produced an appending writer.

**What that costs is invisible to the differential.** An appending writer over the same input leaves
144 rows — the original 50, plus 94 written on top — and every one of the 144 records is
individually correct. ADR-0029 compares fields, so nothing in the comparison sees it; only the row
count does. This is precisely the failure `java_writer`'s own module docstring was written to
prevent ("an update of fifty accounts into fifty new records… only the file's length would say"),
arriving through the *contract* rather than through the renderer.

**`CBACT04C` could not have shown this.** Each of its files is written exactly one way, so
`first_write` and "every write" were the same set for the only program the renderer had ever seen.
This is the fourth instance of the same defect class in this repo's register — G21, G24 and G28 were
each a fact the deterministic layer already held that was dropped one step before its consumer.

## Decision

**`FileAccessPath.write_mode` replaces `is_update`, and it is derived from every binding for the
file rather than from the first.** Three values:

| mode | when | rendered as |
|---|---|---|
| `append` | every binding is a `WRITE` | open, truncate, append each chunk |
| `replace` | every binding is a `REWRITE` | load by key, replace, **refuse an absent key** |
| `upsert` | both appear | load by key, replace when present, add when not |

`None` — not `append` — when the program never writes the file, so "not written" stays distinct from
"written by appending"; defaulting would make every read-only lookup file look like an output.

**`write_line` becomes `write_lines: list[int]`**, every statement in source order. An upsert is two
statements, and the generated Javadoc citing only the first is what made a create-or-update read as
a create. Provenance to the exact source line is `CLAUDE.md`'s standing requirement, and one line
was the wrong shape for it.

**The absent-key guard belongs to `replace` alone.** It is load-bearing in both directions: a
`REWRITE`-only file must never gain a record, and an `upsert` file must be allowed to — rendering
the guard for `upsert` would abend on the first of the 44 rows `CBTRN02C` creates.

## Options considered

**(a) Carry every binding as a list and let the renderer reduce it.** More faithful — no derivation
in the contract at all. Rejected because the reduction still has to happen, so this moves the
decision downstream without removing it, and every consumer would repeat it.

**(b) Refuse to render a file written both ways.** Consistent with this repo's fail-loudly habit,
and it invents nothing. Rejected because the semantics here are not ambiguous: the COBOL states them
in five lines, and refusing would mean hand-writing the one part of `CBTRN02C`'s wiring the source
is most explicit about.

**(c) Keep `is_update` and add a second boolean.** Two booleans for three states, with a fourth
state (`True, True`) that means nothing. Rejected on the record because an unrepresentable state
that compiles is how a wrong writer gets rendered.

## Consequences

- Schema **3.7.0**. `is_update` is gone rather than deprecated; it had one consumer in `src/`.
- `CBACT04C`'s two writers are unchanged in behaviour — `TRANSACT` derives `append`, `ACCOUNT`
  derives `replace` — and the round trip's numbers stay 500 of 500 and 598 of 600.
- **An open question this does not settle, stated rather than discovered later**: an `upsert` writer
  emits *loaded order, then created rows appended*, while an unload of an INDEXED file reads in
  **key** order. For `CBACT04C` the two coincide, because no records are created. For `CBTRN02C`'s
  44 created rows they will not, unless the created keys happen to sort last. Whether the writer
  should emit in key order for an INDEXED file — derivable from `organization` and the key position,
  so not an invention — is a decision the first real `CBTRN02C` comparison should force, and it is
  deliberately not pre-empted here on the reasoning that a run measures it and this document would
  only be guessing.
