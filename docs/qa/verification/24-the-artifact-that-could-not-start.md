# 24. The artifact that could not start

Verified 2026-09-08. Covers
[ADR-0081](../../adr/0081-a-generated-reader-and-writer-open-their-files-when-the-step-runs.md), the
defect [ADR-0080](../../adr/0080-batch-infrastructure-belongs-to-the-application.md) measured and
deliberately left for its own record.

Read [23](23-a-live-design-that-reads-every-file.md) first. That entry established the pipeline
delivers a *correct* translation. This one is about the delivered artifact's own build, which is a
different claim and was failing.

**The claim, in one line: a generated project now starts as a Spring Boot application — the same
project, rendered by the same design and the same scripted processors, goes from `BaselineStackTest`
5 errors / BUILD FAILURE to 0 errors / BUILD SUCCESS, and the missing file that used to kill the
context now fails the step that wanted it.**

## The measurement

Both projects rendered from `live-run-step61/design-step61.json` offline, processors scripted
`return null;`, no `data/` directory — the delivered condition, where `application.properties`
defaults `cobol.file.base=data`. The **only** difference between them is the renderer.

| | before (`main` at `b338cde`) | after |
|---|---|---|
| `BaselineStackTest` | 5 run, **5 errors** | 5 run, **0 errors** |
| Maven | **BUILD FAILURE** | **BUILD SUCCESS** |
| where `NoSuchFileException: data\TCATBALF` is thrown | context startup, before any test body | `ComputeMonthlyInterestItemReader.open`, inside the step |

After the fix the job still reports `FAILED` — correctly, there is no data — but it *starts*:

```
Caused by: java.nio.file.NoSuchFileException: data\TCATBALF
    at com.modernized.batch.reader.ComputeMonthlyInterestItemReader.open(…:53)
Step: [computeMonthlyInterest] executed in 47ms
Job: [SimpleJob: [name=monthlyInterestPostingJob]] … status: [FAILED]
```

That is the whole intent of the record: a missing file is a job-start failure with the path in the
message, rather than an application-context failure that takes five unrelated tests with it.

## The scope in the brief was wrong in both directions

`step-62`'s plan said the fix "touches `render_item_reader` and the aggregating reader beside it".
Rendering the design offline — seconds — said otherwise:

| rendered class | file work in its constructor | changed |
|---|---|---|
| `ComputeMonthlyInterestItemReader` | 4 × `fixedRecords` | yes |
| `PostAccountInterestItemReader` (aggregating) | **none** — already lazy | **no** |
| `PostAccountInterestItemWriter` (`REWRITE`) | reads the file it updates | yes |
| `WriteInterestTransactionItemWriter` (`WRITE`) | `Files.createDirectories`, `Files.deleteIfExists` | yes |

**The readers were only what failed first.** Resolution order, not the extent of the problem — and
ADR-0080's measurement stopped at the first exception, which is how the writers stayed hidden.

**One of the writers is a data-loss bug rather than a startup failure.** `Files.deleteIfExists` in a
constructor deletes the file the job exists to produce whenever a Spring context refreshes, which
Boot does while resolving the job graph and which every `@SpringBootTest` in a generated project
does. Nothing had noticed because nothing had ever refreshed a context that owned one.

## The sweep, rather than the classes that came to mind

Having been wrong about scope once, the closing check was mechanical rather than by inspection: parse
every `public class` constructor in the rendered project and report any containing `fixedRecords`,
`Files.`, `newInputStream` or `readAllBytes`.

```
constructors doing file IO: 0
```

## What the guards assert, and that they can fail

Both new guards are stated over the **constructor body**, not over the file. "There is an `open`
somewhere" would have passed on a broken class that had both, which is the failure mode
`step-62`'s bite 3 names — a rule without a condition.

Each was proven to fail by putting the reads back and watching it go red:

| guard | damage | result |
|---|---|---|
| `test_the_constructor_takes_the_paths_and_opens_nothing` | `assignments + loads` in the reader constructor | **red** |
| `test_a_writer_truncates_when_the_step_starts_and_not_before` | `Files.deleteIfExists` back in the writer constructor | **red** |

## Tiers

| tier | result |
|---|---|
| unit | **865 passed**, 3 skipped |
| integration (local, Postgres up on 5434) | **283 passed**, 4 skipped, 33m 09s |
| CI on PR #150 | `test` **pass** (12m 16s), `template-build` **pass** |

## Corrections to the record

- **The integration tier is 33 minutes, not 1h 24m.** `step-62` gave 1h 24m as measured and warned an
  earlier "15–25 minutes" was wrong by a factor of four. Measured here with Postgres up: **1989.78s**.
  The figure to carry forward is ~33m; the earlier one was probably measured with something retrying.
- **`CLAUDE.md` states the commit author email as `jayakumar.d10@gmail.com`.** Every one of the
  repository's commits, and the local git config, use
  `18530526+jayakumar-devaraj@users.noreply.github.com` — and GitHub's email-privacy setting *rejects*
  a push authored with the gmail address. The documented value cannot be used. Not changed here
  because it is unrelated to this record; noted so it is fixed deliberately.
- **`@Lazy` was documented as load-bearing on a mechanism that never worked.** ADR-0080 had already
  measured that Boot's `jobOperator` defeats it. `java_file_bindings`' docstring is corrected.

## What this does not close

**The delivered branch on `card-service` is unaffected.** `PR #1`'s CI fails on ADR-0080's bean
collision, not on this — its Java was rendered under `v0.4.7` and contains neither fix, so the reader
is never reached. **Only a fresh delivery carries either.** No run was made this session, so "a
delivered branch whose own CI passes" remains open and is not claimed here.

**The working set has the same defect and was left alone**, for the reason ADR-0081 states: no
pipeline-generated project can contain one, and fixing it needs a mechanism nothing exercises.

**This does not make generated jobs restartable.** It removes what made restartability impossible —
a reader with no `open`/`close` and no `ExecutionContext` can never be restartable — without
claiming the jobs now are.

**Pre-flight found the scope and could not have found the defect.** Rendering offline in seconds
corrected the plan's scope in both directions; the original failure needed a full Boot context and a
real Maven build. `step-62`'s bite 8 holds in both directions, which is the useful form of it.
