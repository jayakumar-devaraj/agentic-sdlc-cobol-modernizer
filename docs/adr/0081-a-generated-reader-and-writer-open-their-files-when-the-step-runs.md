# ADR-0081: A generated reader and writer open their files when the step runs

## Status

**Accepted** (2026-09-08). The defect [ADR-0080](0080-batch-infrastructure-belongs-to-the-application.md)
measured and deliberately did not fix, stated there as *"a generated job opens its files when the
context starts rather than when the step runs. That is its own defect and wants its own record"*.

## Context

Every rendered `ItemReader` did all of its file reading in its constructor:

```java
public ComputeMonthlyInterestItemReader(Path tcatbalf, Path acctfile, Path xreffile, Path discgrp)
        throws IOException {
    this.drivingRecords = CobolRecord.fixedRecords(tcatbalf, 50);
    for (String row : CobolRecord.fixedRecords(acctfile, 300)) { … }   // and xreffile, discgrp
}
```

The class implemented `ItemReader` and nothing else, so building the bean read four files. Measured
on the delivered `step61` project once ADR-0080's bean collision was out of the way:

```
computeMonthlyInterestItemReader -> NoSuchFileException: data\TCATBALF
```

Spring Boot's `jobOperator` resolves every `Job` bean eagerly. That forces the steps, which forces
the readers — so the files were opened while the application context was starting, and the five
`BaselineStackTest` cases died on a file none of them had asked for.

### `@Lazy` was the defence, and it could not work

`java_file_bindings` annotates its `@Configuration` `@Lazy`, and its module docstring called that
*"load-bearing rather than a performance choice"* for exactly this reason. The annotation makes the
bean definitions lazy-init; it does not make the job graph lazy. Boot builds the graph during
startup regardless, and building it is what asks for the readers. Deferring *when* a bean is
constructed cannot help when something during startup constructs it.

### The writers had the same defect, and one was worse

Scoping the fix by rendering a live design offline found that readers were only what failed *first* —
resolution order, not the extent of the problem. Both writer shapes do their file work in the
constructor too:

| rendered class | in its constructor |
|---|---|
| `PostAccountInterestItemWriter` (`REWRITE`) | reads the whole account master it is going to update |
| `WriteInterestTransactionItemWriter` (`WRITE`) | `Files.createDirectories` **and `Files.deleteIfExists`** |

The second is not a startup failure but a data-loss bug: refreshing a Spring context deletes the file
the job exists to produce, before anything has decided to run a step. Every `@SpringBootTest` in a
generated project refreshes a context, and so does Boot while resolving the job graph.

The control-break aggregating reader (`java_aggregation`) was checked and needed no change: its
constructor stores the staging store and its `aggregate()` already runs on first `read()`.

### The working set has the same defect and is deliberately not fixed here

`java_working_set` renders a constructor that loads the file it holds, which is the same mistake.
It is left alone because **no pipeline-generated project can contain one**: `_refuse_working_set`
refuses a `reads_own_writes` step before any binding is rendered, so the only working set that exists
is the hand-written `CBTRN02C` fixture, which declares its own bean. Fixing it would also need a
different mechanism — a working set is not an `ItemReader` or `ItemWriter`, so nothing auto-registers
it as a stream, and the step would have to declare `.stream(state)` or load it from the
`StepExecutionListener` that already flushes it.

Changing a renderer whose output nothing can reach, by a mechanism nothing exercises, is the kind of
unverifiable fix this repository refuses elsewhere. It is recorded so it is not rediscovered as new,
and it becomes real the day a working-set bean is rendered (register #14, ADR-0043, G7).

## Decision

**A rendered reader or writer takes its `Path`s in its constructor and touches the disk in
`ItemStream.open(ExecutionContext)`.** `IOException` is wrapped in `ItemStreamException`. The
constructors no longer declare `throws IOException`.

A chunk step registers an `ItemStream` reader or writer automatically, so no wiring changed — the
bean methods in `java_file_bindings` already declare `throws Exception` and are unaffected by a
constructor that throws less. The reader resets `next` in `open` so a second step execution re-reads
from the start rather than resuming past the end.

**The composite writer implements `ItemStream` only when it has a file to touch.** Every component of
its output can be read-modify-written, in which case the working set holds all of them and nothing
happens before `write`. Rendering `open` unconditionally would emit an empty `try` with a
`catch (IOException)`, which javac rejects as an exception never thrown. The condition is the same
one that decides whether there is anything to put in the block.

## Consequences

**A missing file now fails the step that wanted the data**, with the path in the message, rather than
failing the application context. That is both later and more honest, which is the property
`java_file_bindings`' docstring claimed for `@Lazy` and could not deliver.

**`@Lazy` stays and its docstring no longer claims to be load-bearing.** It was documented on the
strength of a mechanism ADR-0080 had already measured as insufficient. Removing the annotation is an
unrelated change with its own risk and is not taken here.

**The generated code now honours the framework's lifecycle contract**, which is worth separating from
the startup fix. An `ItemReader` doing I/O in its constructor has no `open`/`close` and no
`ExecutionContext`, so it can never be restartable however the rest of the job is configured. This
record does not make the generated jobs restartable — it removes the thing that made restartability
impossible to add.

**What this does not claim.** It does not claim the delivered branch on `card-service` turns green.
That branch's committed Java was rendered under `v0.4.7`, before ADR-0080, and its CI fails on the
bean collision — the reader never gets reached. Only a fresh delivery carries either fix. `PR #1`
stays red until something re-delivers, and saying otherwise would be reading this repository's fix as
evidence about an artifact that does not contain it.

**Two guards, stated over the constructor body rather than over the file.** "There is an `open`
somewhere" was true of no version and would be true of a broken one that had both; the assertions say
the constructor assigns and does nothing else. Both were proven to fail by moving the reads back and
watching them go red, per this repository's habit of damaging what a new check guards.

**Pre-flight found the scope; it could not have found the defect.** Rendering `design-step61.json`
offline in seconds is what showed the aggregating reader was already lazy and the writers were not —
correcting a scope this session started with. It could not have found the original failure, which
needed a full Boot context. That is bite 8 of the step-62 brief holding in both directions.
