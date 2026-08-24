# ADR-0051: Track C's dry run records four programs and round-trips the two that carry business logic

## Status

**Accepted** (2026-08-24). Settles the question
[ADR-0048](0048-cbtrn02c-counts-and-its-exclusions-are-earned-not-inherited.md) left open when it
moved the headline metric to `2 of 4` and named step 52's wording — not the count — as the remaining
Track C question.

Depends on [ADR-0035](0035-fixed-occurs-stays-unrepresentable-and-cbact01c-demo-outputs-stay-out-of-generate.md)
for what `CBACT01C` contributes, and on ADR-0048 for what `2 of 4` measures.

## Context

Track C's completion criterion, step 52, reads:

> Full recorded Track C dry run, all four programs.

`2 of 4` is not a shortfall against that criterion — it is the **ceiling**, and the ceiling is a
scoping decision this platform took deliberately and recorded. `CBCUS01C` is a customer listing.
`CBACT01C` is a COBOL feature demonstration wearing the shape of an account-listing program, whose
`OCCURS` group holds hard-coded literals (`CBACT01C.cbl:255-260`) and whose three sequential demo
outputs ADR-0035 excludes from `generate`. Between them they contribute a sequential read and a
print. **There is no business rule in either to preserve, so there is nothing for a differential to
compare.**

Read as *"all four programs round-trip"*, step 52 is therefore unreachable by construction, and a
completion criterion nothing can satisfy is not a bar — it is a permanently red light that stops
meaning anything. That is the state Track C has been in since ADR-0048.

### The wording conflates two different things, and only one of them is bounded

The distinction was missed because *"dry run"* was read as *"round trip"*. They are not the same
span of the pipeline, and the repository already contains the evidence:

| | covers | status |
|---|---|---|
| **The dry run** — `spec_extractor` → `spec_critic` → `solution_architect` over the corpus, one `design.json` for the set | **all four programs**, by contract | Already real: a live four-program run writes a 413 KB `design.json` (ADR-0012), and its measurements are what ADR-0013, ADR-0014 and ADR-0017 are built on |
| **The round trip** — generated logic compiled, run, and compared field-for-field against the program's own output under GnuCOBOL | **the two programs that have output to compare** | `2 of 4`, wiring hand-written (ADR-0048): `CBACT04C` and `CBTRN02C` |

`design.json` covers all four programs *together*, not one file per program (ADR-0003), so the dry
run's denominator is four for a structural reason rather than an aspirational one. Dropping the
criterion to two programs outright — the obvious fix — would have **narrowed a bar the repository
already clears**, which is the mirror image of the overclaim this platform watches for and no better.

## Decision

**Step 52 is reworded rather than reduced**, and it names the two halves separately because they
have different denominators:

> Full recorded Track C dry run: the design phase across **all four programs**, and a round trip
> against the COBOL for **every program that carries business logic** — `CBACT04C` and `CBTRN02C`
> (2 of 4). `CBCUS01C` and `CBACT01C` are out of the round-trip denominator by ADR-0035 and G17, not
> outstanding against it.

**The denominator is derived, not declared.** *"Every program that carries business logic"* is a
property of the corpus, checkable against each program's own source — it is how G17 established the
figure in the first place, from the programs' `FUNCTION` comments and, for `CBACT01C`, from the
literals inside its `OCCURS`. A fifth program with real logic raises the bar automatically; naming
`CBACT04C` and `CBTRN02C` as the criterion would have frozen it at the two that happen to exist.

## Consequences

**A completion criterion changed after the fact needs its cost stated rather than its convenience.**
What is given up is real: step 52 will be marked complete without any Java ever having been generated
for two of Track C's four programs, and someone reading only *"Track C complete"* would reasonably
assume otherwise. That is why the criterion carries the exclusion and its citation in its own text,
rather than in a footnote nobody follows.

**What is not given up.** The dry-run half keeps its `all four programs` unchanged, so the reworded
criterion is weaker than the original in exactly one respect and identical in the other. The
round-trip half's qualifier — *generated logic inside hand-written wiring* (ADR-0030) — is untouched
and stays machine-enforced (`test_the_readme_never_states_the_round_trip_count_without_its_qualifier`).

**This settles a wording question; it completes nothing.** Step 52 remains open. What changes is that
it is now open on work that can be done — a *recorded* dry run, which no artifact in this repository
currently is — rather than on two programs that will never have anything to record.

**The scoping is stated once and cited, not restated.** ADR-0035 owns why `CBACT01C` is excluded and
G17 owns the *2 of 4* figure. This record adds neither; it makes Track C's completion criterion agree
with both, which it did not.

### Why this is an ADR and not an edit to the plan

The plan the step lives in is not committed anywhere, so a criterion quietly reworded there leaves no
trace of the reasoning and reads later as a moved goalpost — the *"a second copy of a fact is a fact
that will eventually be wrong"* failure, in its worst form: the copy that survives is the one with no
argument attached. The plan is edited too, and points here.
