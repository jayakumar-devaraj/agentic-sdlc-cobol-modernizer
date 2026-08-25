# 0010 - `unified_design`'s real shape, and where the deterministic/LLM split falls

> **Amendment note (2026-08-25, ADR-0054).** Decision 5 — *"not built in miniature here a third
> time"* — is now satisfied rather than deferred: `solution_architect` calls the shared
> `core.structured_output.parse_with_repair`, and the third miniature was never written. Every
> validation this record specifies is untouched; the node still refuses a design referencing a
> program, entity, step role or REST method it did not offer. The loop buys one more attempt at a
> parseable answer, never a laxer definition of a valid one.

## Context

ADR-0008 deliberately left `DesignDocument.unified_design` untyped (`dict | None`) until
`nodes/solution_architect.py` existed to give it a real shape, rather than guessing at
`solution_architect`'s own design decisions in advance. That node is being built now, against
real data for all four Track C programs (`CBACT04C`, `CBCUS01C`, `CBACT01C`, `CBTRN02C`) and
ADR-0009's already-decided target architecture (Spring Batch for the migrated batch logic, a thin
REST layer, one deployable `card-service`). Two real design questions fall out of actually
building this, both answered here:

1. **Where does a unified domain model actually come from, mechanically?** Multiple Track C
   programs `COPY` the same copybook — `CVACT01Y` (`ACCOUNT-RECORD`) is `COPY`'d by `CBACT04C`,
   `CBACT01C`, *and* `CBTRN02C`; `CVTRA05Y`/`CVACT03Y`/`CVTRA01Y` are each shared by two of the
   four. Each of those should become **one** Java domain class, not one per program that happens
   to reference it. But some copybooks look structurally similar without actually being the same
   record — `CVTRA06Y`'s `DALYTRAN-RECORD` (daily incoming transactions, `CBTRN02C`'s own input)
   and `CVTRA05Y`'s `TRAN-RECORD` (the posted transaction log both `CBTRN02C` and `CBACT04C`
   write to) are both 350-byte transaction-shaped records — but they are two different real
   copybooks, not proven to be the same entity by any fact this repo can check.
2. **How much of `unified_design` should the model author, versus be handed as fact?** Field-level
   data (types, precision, scale) has a zero-drift guarantee this repo has enforced since
   `pic_mapper` — that guarantee is worthless if `solution_architect`'s prompt lets a model
   re-derive or paraphrase it. But naming a Java class, grouping COBOL paragraphs into Spring
   Batch steps, and deciding what a thin REST layer should expose are not even 1:1-checkable
   facts — they're real design judgment, the same kind `spec_extractor`'s narration already does
   for business-rule prose.

## Decision

**1. Domain entities are merged by copybook name — mechanically, never by structural
resemblance.** `build_domain_entities` groups every program's `group_field_mappings_by_source`
output by copybook name: the same copybook name across two programs is proof they share the exact
same physical record layout (verified, not assumed — every field byte-for-byte identical, since
it's literally the same source file). Two *different* copybook names are always kept as two
separate `DomainEntity` objects, even when structurally similar (`CVTRA06Y` and `CVTRA05Y` stay
`Dalytran` and `Tran`, not merged into one). Recognizing that they're related in a business sense
is exactly the kind of judgment call reserved for the LLM layer (decision 3) — the deterministic
layer only ever merges on a fact it can verify, never a resemblance it can't.

A copybook that contributes **zero** successfully-mapped fields does not produce a domain entity
at all. `CODATECN` (a date-format-conversion utility copybook, entirely `REDEFINES` groups) is the
real case: all 28 of its fields are unsupported, none mapped — there is nothing to represent, and
inventing an empty or REDEFINES-guessing entity for it would be exactly the kind of guess ADR-0002
already forbids for this exact construct.

**2. Entity and field names are a direct, mechanical transform of the COBOL name — not a business
rename.** `Account` from `ACCOUNT-RECORD` (strip the `-RECORD` suffix, kebab-case to PascalCase);
`acctCurrBal` from `ACCT-CURR-BAL` (kebab-case to camelCase). This is deliberately not "nice" —
`TranCatBal` from `TRAN-CAT-BAL-RECORD`, not `TransactionCategoryBalance` — because choosing the
nicer name is a semantic judgment call, the same category of decision reserved for the LLM layer.
The canonical `DomainEntity.name`/`DomainField.java_field_name` stay mechanically traceable back to
their exact COBOL origin; a human-readable description is the model's job, not this field's.

**3. Batch job and REST endpoint design is 100% LLM-authored, informed by (never re-deriving) the
deterministic entity data.** `build_domain_entities`'s output — real field types, real
`used_by_programs` — is handed to the model as fact, the same "Known Facts, reproduce exactly,
never recompute" contract `spec_extractor`'s prompt already uses. What the model *does* design:
grouping each program's real paragraph flow into Spring Batch `reader`/`processor`/`writer`/
`tasklet` steps, and proposing a thin REST layer's endpoints against the real domain entities. This
is the same shape of split `spec_extractor`/`spec_critic` already established (deterministic facts
never re-derived by a model; narrative/structural judgment always is) — Milestone C4's step 38
(`templates/target-spring-boot-baseline/`) is the next place this same split matters, not
duplicated logic to re-derive.

**4. Each program's `spec_markdown` is wrapped as untrusted data again, even though it's this
repo's own prior LLM output.** `spec_extractor`'s narration is generated *from* untrusted COBOL
source under a guardrail that isn't a guarantee (see `core/guardrails.py`'s own docstring: "defense
in depth, not a guarantee"). A narration a determined injection got past `spec_extractor`'s
guardrail could still carry instruction-like text into `solution_architect`'s prompt if it weren't
wrapped again here. `build_architect_prompt` wraps every program's `spec_markdown` with
`core/guardrails.wrap_untrusted_cobol` (generic enough despite its COBOL-specific name — it wraps
any text in the same delimiter scheme) alongside the deterministic domain-entity data, which is
never wrapped (it's this repo's own computed fact, not untrusted input).

**5. No repair-retry loop for `solution_architect`'s own structured output either.** Same posture
as `SpecCritiqueParseError` (ADR-0007): a malformed response raises `SolutionArchitectParseError`
and propagates. Milestone C3's real repair-retry loop (plan step 35) is still separately-scoped
work, not built in miniature here a third time.

## Consequences

`DesignDocument.unified_design` is now `UnifiedDesign | None` — a real, committed schema change
(`schemas/design_document.schema.json` regenerated), the one ADR-0008 explicitly anticipated and
deferred rather than guessed at.

A real, checkable consequence of decision 1: running `build_domain_entities` against all four real
Track C programs' data produces exactly 7 domain entities (`Account`, `Tran`, `CardXref`,
`TranCatBal`, `DisGroup`, `Customer`, `Dalytran`), correctly shows `Account` shared by three
programs, and correctly excludes `Codatecn` entirely — verified in
`tests/system/test_solution_architect.py`
against real data, not asserted from the design alone.

The mechanical naming in decision 2 means generated class/field names will look like
straightforward but not maximally idiomatic Java (`TranCatBal` rather than
`TransactionCategoryBalance`) unless and until a later step (Milestone C4, or a future revision of
this decision) adds a deliberate, reviewable renaming layer. Accepted for now: a traceable-but-plain
name is safer than a model quietly renaming its way into losing the COBOL-to-Java mapping's own
provenance.
