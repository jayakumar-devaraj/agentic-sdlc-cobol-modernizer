# ADR-0026: Job parameters reach a rendered processor; the per-run counter is scoped out

## Status

Accepted (2026-08-11). Closes the open half of gap **G29** — the half PR #45 deliberately left, having
refused ambient state without supplying an alternative.

Depends on [ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md)
for the posture that a step declares what it needs rather than having it inferred, and on
[ADR-0019](0019-postgresql-persistence-and-a-bounded-generate-scope-for-card-service.md) for the
processor-only generation scope this ADR partly tests.

## Context

A real Opus 5 body filled `CBACT04C`'s DB2 timestamp with `LocalDateTime.now()`. It compiled, it read
correctly, and it made the same input produce a different record on every run — including a restart
that reprocesses one chunk. PR #45 made that a refusal (`NonDeterministicBodyError`) rather than a
review note, because it had been caught only because the model volunteered it.

**That left a dead end by construction.** The model is now told *don't read a clock* and given nothing
to read instead, so the fields stay `null`. `1300-B-WRITE-TX`'s logic is otherwise generated and
correct (PR #44) — fourteen `MOVE`s, the `STRING` into `TRAN-DESC`, the padded alphanumerics. Three
fields are missing, and all three trace to the same thing: **a stateless `ItemProcessor` has no access
to properties of the invocation.**

Read from the source rather than assumed, the three are not one problem:

| COBOL | What it is | Where it comes from |
|---|---|---|
| `PARM-DATE` `PIC X(10)` | A **job parameter**, unambiguously — it is in the `LINKAGE SECTION`, and `PROCEDURE DIVISION USING EXTERNAL-PARMS` receives it from JCL | outside the program |
| `DB2-FORMAT-TS` `PIC X(26)` | `FUNCTION CURRENT-DATE`, reformatted, read **per record** inside `1300-B-WRITE-TX` | the wall clock |
| `WS-TRANID-SUFFIX` `PIC 9(06) VALUE 0` | `ADD 1 TO WS-TRANID-SUFFIX` per written transaction — a per-run sequential counter | accumulated state |

`TRAN-ID` is `STRING PARM-DATE, WS-TRANID-SUFFIX DELIMITED BY SIZE` — 10 + 6 characters, exactly
filling `PIC X(16)`. So it needs the first and the third.

## Decision

**Declare job parameters in the design and inject them into the rendered processor. Supply the run
timestamp as one of them, recording the divergence that creates. Scope the per-run counter out, and
route it to a gate rather than generating something that compiles.**

### 1. The mechanism

`BatchJobDesign.job_parameters` declares them; `BatchStepDesign.job_parameters` names the ones a step
consumes, so a constructor carries what that step needs and no more — ADR-0020's posture, applied to
invocation facts instead of types. Both are **optional with an empty default**, deliberately unlike
ADR-0022's required `guard_condition`: a guard needed `null` to be distinguishable from "nobody
checked", whereas `[]` already says *consumes none* and has no silent state to be confused with.
That keeps the change additive — schema **3.1.0**, not a breaking bump.

The rendered processor gains `@StepScope` and constructor injection. `NonDeterministicBodyError` is
untouched: the model still may not call a clock, it reads an injected field.

### 2. The run timestamp is a job parameter, and that is a divergence

**Stated plainly because it is the one place this ADR knowingly differs from the COBOL.**
`FUNCTION CURRENT-DATE` is read *per record* and carries milliseconds, so a COBOL run writing records
across a millisecond boundary stamps them differently. One timestamp supplied per run collapses that:
every record in a run shares an instant.

Taken anyway, because the alternative is worse in the dimension this platform has already decided.
PR #45's standing rule is that a batch record must be identical across runs over identical input,
including a restart reprocessing a chunk. A per-record clock read satisfies COBOL's letter and makes
the output unreproducible, which is the failure class the deterministic core exists to prevent —
arriving through the one part a model writes.

Recorded rather than asserted, in ADR-0021's manner: this is a **known divergence with a stated
cost**, not an equivalence. It becomes load-bearing at the first byte-for-byte record comparison,
which is the same trigger ADR-0021 names for needing a real COBOL runtime.

### 3. The per-run counter is scoped out, not faked

`WS-TRANID-SUFFIX` **cannot be faithfully produced by a stateless processor**, and this is the
decision worth the most scrutiny because a plausible implementation is one line away.

An `AtomicLong` in a `@StepScope` bean compiles, reads correctly, and is wrong: Spring Batch
reprocesses a chunk on restart, so the counter advances where COBOL's — reinitialised to
`VALUE 0` — reproduces its original sequence. Under partitioning it is worse: concurrent partitions
interleave. The result is a transaction id that differs between two runs over identical input, which
is precisely what § 2 above refuses for the timestamp. **A model already flagged this**, unprompted,
in the run that produced G29, and it was right.

So no counter is generated. `TRAN-ID` stays unpopulated and the design emits a `GateItem` naming the
COBOL and the reason, which makes the limitation **visible at the gate rather than invisible
everywhere** — ADR-0023's posture for a step this pipeline does not render, applied to a field.

What would resolve it is a design decision this ADR does not pre-empt: a database sequence, an
identifier derived deterministically from the item, or a stateful writer — the last being out of
scope by ADR-0019 and the same open question G27 carries.

## Consequences

**Good.** Two of the three fields become populatable from declared facts rather than from a clock,
and the third has a named reason and a gate item instead of a silent `null`. The rendered processor's
shape changes once, now, rather than after more processors depend on the no-arg form. `PARM-DATE`
stops being narrated: it is a declared parameter with a type, which is the same move `pic_mapper`
makes for precision and `DomainField.length` makes for width.

**The test that makes it real** is not that it compiles. Two runs over identical input with identical
job parameters must produce **byte-identical records**, asserted against real Maven. That is the
property G29 exists for and the one a scripted body cannot fake by looking correct.

**Accepted cost.** Generated processors are no longer no-arg `@Component`s, so the rendered shape is
more complex and `@StepScope` is now part of what a reviewer must understand. And the timestamp
divergence in § 2 is real: this repo now knowingly produces a record COBOL would not produce
byte-for-byte across a millisecond boundary.

**`TRAN-ID` remains unpopulated**, so `CBACT04C`'s transaction record is still incomplete and the
round-trip metric does not move. This closes the *mechanism* half of G29 and deliberately declines the
counter half. Reopen if a decision is made about identifier generation.

**What this does not touch.** G27's accumulator remains open — `1050-UPDATE-ACCOUNT` needs cross-item
state, which is the same stateless-processor limit reached from the other direction, and ADR-0019
still scopes this pipeline to processors.
