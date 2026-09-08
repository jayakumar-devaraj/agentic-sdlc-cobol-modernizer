# ADR-0078: A step bean names the reader it wants

## Status

**Accepted** (2026-09-08). Found by run `step60-cbact04c-20260908-080040`, the second live design
written under prompt `v1_6_0` — the run taken to confirm
[ADR-0076](0076-a-keyed-lookup-belongs-in-the-reader-of-the-step-that-uses-it.md) holds on an
independent design. It does. This is what was behind it.

Completes [ADR-0073](0073-a-staging-store-belongs-to-the-step-that-fills-it.md): that record gave
each staged edge its own store; this one stops a store being mistaken for the reader beside it.

## Context

`step60`'s design bound five of five files, stranded nothing, skipped nothing, and compiled. The
wiring reported *"every renderable step is wired."* Then:

```
Job run: FAILED -- 'interestCalculatorJob' did not reach COMPLETED
```

```
No qualifying bean of type 'ItemReader<com.modernized.batch.domain.RatedCategoryBalance>'
available: expected single matching bean but found 2:
  computeCategoryFeesStaging, computeCategoryFeesItemReader
```

**A staging store `implements ItemWriter<T>, ItemReader<T>`** — it has to, because the step after it
reads from it. So a step that is *both* a passthrough (`input_type == output_type`) *and* the head
of the chain has two beans of one type in scope: the file reader it reads from, and the store it
writes to. The step bean asked for the interface:

```java
Step computeCategoryFeesStep(JobRepository, PlatformTransactionManager,
                             ItemReader<dom.RatedCategoryBalance> reader,
                             ComputeCategoryFeesStaging computeCategoryFeesStaging)
```

Spring resolves that by type, finds two candidates, and the context cannot start.

### Why nothing refused it

`java_file_bindings` has an ambiguity refusal, and it is aimed one step to the left. It tracks
`claimed[bean_type] = step_name` across steps that read files, so it catches *two steps* both
needing an `ItemReader<T>`. Here only one step reads a file. The collision is between that step's
reader and **its own** store, and the check has no notion that a staging store is also an
`ItemReader<T>`.

`step59`'s design never reached this: it ordered a type-changing step at the head, so the head's
input and output types differed and only one bean matched.

## Decision

**A step bean names the bean it wants, where two beans could answer.** The file reader is injected
as `@Qualifier("<readerBeanName>") ItemReader<T> reader` **when some staging store also carries
`T`** — and as a bare `ItemReader<T>` otherwise. The writer follows the same rule against the same
condition.

**The condition is the decision, and the first version did not have one.** Qualifying every reader
and writer is the obvious form of this fix, it is what was written first, and it is wrong. The bean
named is rendered by `java_file_bindings`; a caller that renders a job configuration while supplying
its *own* bindings names that bean differently, and `test_hand_written_round_trip` is exactly such a
caller. Unqualified, its `computeInterestStep` resolved one candidate by type and worked. Qualified
unconditionally, it asked for a bean named `computeInterestItemReader` that nothing had rendered:

```
No qualifying bean of type 'ItemReader<...TranCatBalWithRate>' available:
expected at least 1 bean which qualifies as autowire candidate.
Dependency annotations: {@Qualifier("computeInterestItemReader")}
```

**Nineteen integration errors, measured rather than reasoned**, and none of them visible in the 940
unit and contract tests that passed. An unconditional qualifier trades an ambiguity that arises in
one design shape for a name coupling that binds two modules in every shape — the worse trade, and
the reason this rule carries a condition.

**The writer is covered by the same condition, though no run has failed on it.** A staging store is
an `ItemWriter<T>` as well, so a step writing a file whose `output_type` some store also carries is
ambiguous in exactly this way. Guarded now rather than after a run finds it.

**Qualified rather than refused.** The alternative was to extend the bindings' `claimed` map to
include staging stores, which would have refused this design. That is the wrong direction: a
passthrough at the head of a chain is a legitimate decomposition — a model wrote it while obeying
every rule this repository states — and the renderer can wire it correctly. A refusal would push
work onto the architect prompt for something the renderer knows how to do.

**The existing `claimed` refusal is left alone.** Qualifiers would make *that* collision resolvable
too, and relaxing it is a separate decision with its own evidence to gather. Narrowing this change
to the defect it fixes.

## Consequences

**The fix was measured on the artifact that failed, not on a synthetic one.** The generated project
was copied out of the container with its real processor bodies, its configuration re-rendered by the
fixed renderer, and its job re-run:

| | job run | differential |
|---|---|---|
| as delivered | `FAILED` — context could not start | `not_run` |
| re-wired | **`completed`** | **`mismatched`, 3 of 1100** |

Those three are ADR-0066's account-break divergence — the same three fields, the same values, as
`step59`. **Two independent designs now produce the same correct output.**

**ADR-0075's job-run verdict is what caught this**, and it is worth naming because the phase is one
release old. Without it this run would have reported *"wiring rendered and compiled; every renderable
step is wired"* and then `not_run` with nothing saying why — indistinguishable from every run before
v0.4.6. It also classified it correctly: *"a defect in it rather than in the comparison."*

**Pre-flight would not have found this one**, and that is a first. Every previous record in this
sequence was found by rendering a design offline in seconds. This defect is invisible until a Spring
context starts: the configuration compiles, every bean exists, and the ambiguity is a *runtime*
resolution failure. The habit stands, and its limit is now known.

**The property test asserts a biconditional, and that is not tidiness.** "Every reader is
qualified" is true of the broken version; only "qualified exactly when a store shares the type"
separates it from the correct one. A test that states one direction of a rule cannot catch the
failure of the other, and here the other direction was the defect.

**A test helper broke in a way worth recording.** `_readers` matched a step bean's parameter list
with `\\(([^)]*)\\)`, which stops at the first `)` — now the one inside `@Qualifier("...")`. It
reported the qualifier as the whole parameter. Fixed by matching to the `)` that precedes the method
body and stripping the annotation, so the helper still answers what it always answered.

**This is the sixth "last thing in the way" in six records**, and the first found by a live run
rather than by pre-flight. What is verified is stated rather than what is left: two independent
designs bind five of five files and produce byte-identical correct output. `step60`'s own delivered
branch still carries the broken configuration; it was not regenerated.
