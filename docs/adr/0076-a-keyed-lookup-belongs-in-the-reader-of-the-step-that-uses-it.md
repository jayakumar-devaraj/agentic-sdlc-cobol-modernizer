# ADR-0076: A keyed lookup belongs in the reader of the step that uses it

## Status

**Accepted** (2026-09-07). Closes the gap
[ADR-0075](0075-the-pipeline-runs-the-job-it-generated.md) named in its own Consequences: the
pipeline runs the job it generated, and on every live design that job would run without three of
`CBACT04C`'s five files.

Same family as [ADR-0070](0070-a-step-that-changes-its-items-type-is-a-processor.md),
[ADR-0072](0072-a-step-is-ordered-where-its-input-exists.md) and
[ADR-0074](0074-a-mid-chain-step-reads-its-predecessor-not-a-file.md): a design shape a model
reasonably wrote, which the renderer accepted and could not honour.

## Context

`CBACT04C` reads `TCATBALF` sequentially and then, per record, `XREFFILE`, `ACCTFILE` and `DISCGRP`
by key. **Three independent live designs** — the pinned `cbact04c-design.json` and the `step56` and
`step58` fixtures — all decomposed that into a chain of resolve steps:

```
resolveAccountAndCardXref  TranCatBal            -> TranCatBalWithAccount
resolveInterestRate        TranCatBalWithAccount -> RatedCategoryBalance
computeMonthlyInterest     RatedCategoryBalance  -> AccruedCategoryInterest
```

That is a fair reading of COBOL that `READ`s each file in its own paragraph. It is also the one
shape a generated job cannot honour, and **nothing refused it**. `reads_a_file` answered yes, a
one-path reader rendered, every processor compiled, and the wiring reported *"every renderable step
is wired."*

Rendering all three offline gives the same three properties and no others:

```
cobol.file.tcatbalf = ${cobol.file.base}/TCATBALF
cobol.file.acctfile = ${cobol.file.base}/ACCTFILE
cobol.file.transact = ${cobol.file.base}/TRANSACT
```

`XREFFILE` and `DISCGRP` are declared in `file_access_paths`, resolvable, and bound to nothing;
`ACCTFILE` is bound only as a writer.

### The stranding is younger than the design shape

Added after the live run that closed this record, because the first version of it implied the shape
had never worked and that is not what the artifacts say.

`agentic-patch/step55-cbact04c-20260906-090845`, generated under **v0.4.4**, ships the same split
design and three file readers over one driving stream:

```java
ResolveAccountAndCardXrefItemReader(Path tcatbalf)
ResolveInterestRateItemReader(Path tcatbalf, Path acctfile, Path xreffile)
ComputeMonthlyInterestItemReader(Path tcatbalf, Path acctfile, Path xreffile, Path discgrp)
```

So the lookup files *were* read — by three separate steps, each re-iterating `TCATBALF` from the
start and redoing the keyed reads the step before it had already done. That is the defect
[ADR-0074](0074-a-mid-chain-step-reads-its-predecessor-not-a-file.md) names in its own words, and
fixing it was right. **For this design shape it also left the only file-reading step as the first
resolve step, holding a one-path reader** — which is when `XREFFILE` and `DISCGRP` stopped reaching a
reader at all. `git show v0.4.4:docs/adr` carries no ADR-0074; `v0.4.5` does.

v0.4.4 read them redundantly and wrongly; v0.4.5 fixed the redundancy and stranded them; this record
has each read once, in one reader, at the step that uses them. Worth stating because a fix that
closes one defect and opens another is this repository's most frequent shape, and naming the
predecessor is the only thing that makes the pattern visible.

### Why a processor cannot do it

`render_processor` gives a processor exactly one method, `process(item)`, with constructor
parameters only for *job parameters*. The one component of a rendered step that opens a file is its
**reader**, and `reader_path_parameters` builds that reader from the step's `input_type`. So
`ResolveAccountAndCardXrefProcessor` must return a
`TranCatBalWithAccount(categoryBalance, account, cardXref)` with nothing to build the last two from,
and `ResolveInterestRateProcessor` must attach a disclosure group it cannot read.

### The renderer already supported the fix

`_order_lookups` and `_lookup` exist for exactly this join, and they work. Folding the two resolve
steps into the step that consumes their output — measured, not reasoned — gives:

```
bound: acctfile, discgrp, tcatbalf, transact, xreffile     (all five)
skipped: []
ComputeMonthlyInterestItemReader(Path tcatbalf, Path acctfile, Path xreffile, Path discgrp)
```

Constructor order is derived, not declared: `DISCGRP`'s key comes from `ACCT-GROUP-ID`, a field of
the account record, so the account read is ordered first. The `'DEFAULT'` fallback on status `'23'`
stays in the reader's key handling where it already lived.

**Nothing in the reader or the bindings had to change.** Only the design shape was in the way, and
that is the strongest evidence available for where the fix belongs.

### The two lookups are not the same problem

Worth stating because the obvious fix does not reach both. `resolveAccountAndCardXref` reads a file;
`resolveInterestRate` is mid-chain and reads its predecessor's staging store, because ADR-0074 rules
that the chain outranks the file. So *widening the reader* — the candidate that suggests itself —
binds `ACCTFILE` and `XREFFILE` and can never bind `DISCGRP`. Only folding reaches all five.

## Decision

**A step whose output composite carries a keyed-lookup entity its input does not is refused.**

`unsupplied_components(step, design, program_name)` answers which entities those are and which
`ASSIGN TO` each comes from. Two placements, deliberately different:

1. **`plan_steps` reports it as a skip**, with a reason naming the files and the move. It is not
   raised there because `plan_steps` is not only a gate: `solution_architect` asks it to find the one
   move that would wire a job it refused, and a planner that raised would break the machinery that
   tells a model what to change — which is the only thing that fixes this shape at its source. For
   the same reason that search now considers only the skips a move could fix; a join skip is present
   before and after every move, so counting it made ADR-0072's refusal go silent on the very design
   it was written for.
2. **`render_job_wiring` raises**, reaching `WiringVerdict(status="refused")` with the step named.
   That is the one place a project is produced.

**`v1_6_0` of the architect prompt states the rule**, in the two-halves shape every prompt version
since `v1_1_0` has used: enforcing a rule the prompt never stated would punish a model for following
the contract it was given.

**Only keyed lookups count.** A component the step *computes* is ordinary work — `TranWithContext`
carries a `Tran` and `TRANSACT` is a sink. The first version of this check counted every added
component and refused the interest calculation itself; `is_keyed_lookup`, a fact the design already
carries, is what separates the two.

## Consequences

**Refused, not skipped, and the difference was measured.** Every other skip in `plan_steps` is
survivable because the steps around it do not depend on it having run. This one they do. With the
joins left out of the `step58` design:

- `computeMonthlyInterest` still reads `ResolveInterestRateStaging`, whose producer is gone;
- the bindings collapse to `acctfile` and `transact`, losing `TCATBALF`, the job's own driving input.

It would compile and start. A run of that job reports a differential about files it never opened,
which is worse than the defect being fixed, not better.
`test_skipping_the_join_would_leave_a_store_nothing_fills` pins both numbers.
`test_every_store_a_step_fills_is_read_by_the_step_after_it` asserts the *other* direction — no store
is filled that nothing reads — and is blind to this one, which is why the new test exists rather than
a strengthened old one.

**No live design in this repository renders unchanged.** All three carry the shape. The fixtures are
evidence and were not rewritten; `tests/support/joined_design.py` folds them in the open, and the
fold is a *deletion* — the consuming step already declares the full composite as its `input_type`,
because ADR-0072's chain rule requires it. `tests/integration/test_a_live_design_wires.py` now builds
the folded design and passes under Maven, and `test_the_design_as_written_is_refused` holds the
original up to the refusal.

**The refusal was proven in both directions before being trusted** (the ADR-0075 lesson, third record
running). Neutering `unsupplied_components` fails four of the five new tests. The fifth —
`test_a_component_the_step_computes_is_not_a_stranded_lookup` — cannot fail that way, because it
asserts the check does *not* fire; it guards against a broader predicate rather than against no
predicate, and that limit is stated here rather than left to be discovered.

**This is the fifth "last thing in the way" in five records, and it is not the last one either.**
ADR-0071 said design ordering; ADR-0072 closed that and ADR-0073 was behind it; ADR-0074 was behind
that; ADR-0075 ran the job; this is behind that. What is verified is stated rather than what is left:
the job now binds five of five files and the wiring compiles. **No live run has been made against a
folded design, so no correctness verdict exists for one.** That is the next thing, and it is the
first time the differential will be comparing output from a job that opened every file its COBOL
names.

## Alternatives considered

**Widen the reader when the output composite needs components the input lacks.** Rejected on
measurement: it cannot reach `DISCGRP`, whose step is mid-chain, without contradicting ADR-0074. It
would have bound four of five files and looked like a fix.

**Let a mid-chain step take its driving stream from its predecessor's store and its lookups from
files.** The truest model of the two-step design, and the largest change — a new reader capability
and an extension of ADR-0074. Not rejected on merit; deferred because folding needs no new capability
at all, and a change that adds none is the one to make first.

**Have the renderer collapse consecutive join steps automatically.** Rejected: the renderer would be
silently rewriting what the model wrote, against the refuse-don't-guess grain of every record from
ADR-0032 onward. A design that renders as something other than what it says is worse than one that is
refused with the move named.
