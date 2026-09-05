# 16. A generated project that runs

Verified 2026-09-05. Covers ADR-0066 (`generate` renders the job wiring) and ADR-0067 (a file's path
is deployment configuration).

Read [15 — the gate that catches half a class](15-the-gate-that-catches-half-a-class.md) first. That
entry closed the unit-granularity half of ADR-0064's gap and stated plainly that the end-to-end half
stayed open, because `generate` produced no project that runs. This is that half.

**The claim, in one line: a project `generate` wired by itself builds, runs the job to `COMPLETED`,
and its output is compared field by field against COBOL's — with no hand-written wiring copied in.**
ADR-0030 deferred this in August as *"the architecturally right answer"* and priced it as weeks of
parser, contract and renderer work.

## Why it cost a day rather than weeks

Because nearly all of it was already built and reachable from nothing. This was established by
grep before anything was written:

```
$ grep -n "render_item_reader\|render_item_writer\|render_staging\|render_job_configuration" \
      src/cobol_modernizer/graph/generate_pipeline.py
(no matches)
```

Four renderers — `java_job`, `java_reader`, `java_writer`, `java_aggregation` — ship in the package,
each verified in its own entry of this report, and every one of them was called only from
`tests/integration/test_hand_written_round_trip.py`. That is the identical defect entry 15 found one
level down, and the third instance in a month: **a complete, tested renderer wired into nothing.**

A correction was carried forward rather than re-derived. An earlier reading of `plan_steps` claimed
three *steps* were unrenderable and concluded a renderer was missing; `6 of 9 renderable` is a
property of that design, whose skipped steps have outputs going nowhere. The renderers were
complete.

## What actually had to be built

One thing, and the hand-written stopgap had already identified it. ADR-0030's point 3 required the
stopgap to be written against `design.json` so that anything it needed and the design lacked would
be *a finding about the design*. After four renderers landed, `HandWrittenRemainder.java` was down
to **three beans that all do the same job** — bind a path to an already-rendered class — and its own
docstring named the reason:

> the COBOL says `ASSIGN TO TCATBALF` — an environment name — and nothing anywhere says what that
> resolves to. **Binding them to locations is deployment.**

So `java_file_bindings.py` renders those three beans, taking paths from Spring properties named from
`ASSIGN TO` (ADR-0067). Rendered against the real design, it produces exactly the fixture's three
beans, with the same types and the same argument order:

```java
@Bean
ItemReader<...TranCatBalWithRate> computeInterestItemReader(
        @Value("${cobol.file.tcatbalf}") Path tcatbalf,
        @Value("${cobol.file.acctfile}") Path acctfile,
        @Value("${cobol.file.xreffile}") Path xreffile,
        @Value("${cobol.file.discgrp}") Path discgrp) throws Exception {
```

**One detail is worth naming because nothing was told to do it.** The account writer resolves to
`cobol.file.acctfile` — the *same* property the reader uses. That is the in-place `REWRITE` the
fixture wires by hand with a comment explaining it, and it falls out of the rendered path for free,
because both come from the same `ASSIGN TO`.

## Argument order is derived once, not twice

`reader_path_parameters` and `writer_path_parameters` were extracted from the two renderers so the
constructor and the bean that fills it read the same answer. This is not tidiness: a reader takes
four arguments that are **all `Path`**, so handing them over in the wrong sequence compiles, runs,
and reads the discount groups out of the account file. The extraction was verified to change nothing
before anything was built on it — `tests/integration/test_hand_written_round_trip.py` passed
unchanged, `8 passed, 1 skipped in 103.44s`.

## The command, and the measurement

```
JAVA_HOME=... .venv/Scripts/python.exe -m pytest \
  tests/integration/test_generate_renders_the_wiring.py -q
```

```
8 passed in 73.62s (0:01:13)

rendered wiring: 3 field(s) differ from what CBACT04C wrote over the same corpus
account half: 597 of 600 fields matched; 0 excluded by decision; 3 expected mismatch(es)
```

| What was checked | Result |
|---|---|
| `generate` renders the readers, writers, staging, job configuration, bindings and properties | rendered; `wiring.status == "rendered"` |
| Nothing hand-written was copied in | the `handwritten` package is asserted **absent** from the project |
| The rendered project compiles | yes, in the pipeline's own post-wiring compile |
| The job runs | `COMPLETED`, started from the rendered `InterestJobConfiguration` + `InterestJobFileBindings` |
| Transaction half vs the oracle | **matched**, all 50 records, every field but ADR-0026's exclusions |
| Account half vs the oracle | **597 of 600 fields, 0 excluded**, 3 expected mismatches |

`compare_project_output` has returned `not_run` for every real design since ADR-0064, with a reason
naming this gap. It now returns a verdict.

## The three mismatches are COBOL's, and this entry will not round them away

They are on the last account, in exactly the three fields `1050-UPDATE-ACCOUNT` writes.
`CBACT04C`'s loop is `PERFORM UNTIL END-OF-FILE = 'Y'` with the account-break post in the `ELSE` of
`IF END-OF-FILE = 'N'`, so that branch never runs and the final account keeps a balance excluding the
interest the same run wrote for it.

**The rendered wiring is held to the hand-written wiring's own assertion**, imported rather than
restated: `assert_account_half_matches_except_the_last` pins that exactly one record differs, that it
is the last account, that the fields are that paragraph's write set, and that the balance differs by
exactly that account's uncredited interest. A divergence with a different cause fails it.

That helper's docstring already records the temptation and the answer: the wiring could skip the last
account and make this green, and doing so would be **encoding a defect to improve a number**. It
applies unchanged here.

**The consequence for the gate is stated rather than left to be met.** `GenerateCliResult.equivalence`
will read `mismatched` for `CBACT04C` on every run, permanently, and a reviewer who reads only that
word will conclude the generated code is wrong. The verdict's `mismatches` list names the record and
the three fields, which is the honest surface; ADR-0066's Consequences says the same.

## The probe that corrected the test rather than the code

The first run of `test_the_differential_matches_the_cobol_oracle` failed:

```
AssertionError: mismatched: 3 field(s) differ from what CBACT04C wrote over the same corpus
  accounts: record 49 ACCT-CURR-BAL: got Decimal('1964.64') want Decimal('1945.87')
```

The test was wrong, not the wiring — it asserted `matched` against a differential that excludes
nothing on the account half, for a program with a known unreachable branch. Comparing the numbers to
the hand-written round trip's own assertions showed the same record, the same three fields and the
same direction. The test was rebuilt to assert the shape through the shared helper, and the finding
was written into ADR-0066 rather than absorbed.

## What is not covered

**One program.** `CBACT04C` only, and `CLAUDE.md`'s standing rule is that a capability is complete
when a *second* instance exercises it. G31's closure is therefore written as **"the wiring renders
for `CBACT04C`"**. `CBTRN02C` is maintenance rule 6 and needs the piece this work deliberately
refuses: a `reads_own_writes` step's reader and writer take a working set, and **no `@Bean` renders
one anywhere** — so `java_file_bindings` raises rather than producing a context that cannot start.

**The job is started by a test-written runner**, not by `BatchApplication`. Boot's autoconfiguration
wants a `DataSource` and a container to put it in, neither of which is what is being measured. The
runner registers no bean the pipeline should have rendered — it supplies property *values* and a
`main`-equivalent, which is exactly the deployment surface ADR-0067 designed.

**Paths are not validated at render time**, and cannot be: the specialist renders on one machine and
the job runs on another.

## The defect CI caught that the local run did not

The first version of this work rendered `InterestJobFileBindings` as an ungated `@Configuration`.
CI failed:

```
CompileResult(succeeded=False, exit_code=1, diagnostics=(), ...)
tests/integration/test_hand_written_round_trip.py:474: AssertionError
```

No located diagnostic, because it was not a compile failure: the Spring context would not start.
`BatchApplication` component-scans `com.modernized.batch`, a rendered reader opens its files **in
its constructor**, and so the baseline template's `BaselineStackTest` — a full `@SpringBootTest`,
which by its own docstring never skips — died looking for `data/TCATBALF`.

**The hand-written fixture had already written this down**, and it was read and overridden:

> Gated behind the rendered configuration's profile because `BatchApplication` component-scans
> `com.modernized.batch`: without it this wiring would join the context of every Spring Boot test in
> the generated project.

ADR-0066 rejected the profile on a *policy* argument — a job that runs only under a non-default
profile is not the program — which is sound and does not address the mechanism. Rendering the
configurations `@Lazy` keeps the policy and removes the mechanism: nothing is constructed until
something asks for the `Job`.

**Why the local run missed it.** `test_hand_written_round_trip.py` was run to verify the
`reader_path_parameters` extraction changed nothing, and passed — *before* the call site that renders
wiring existed. It was not re-run after. The check that would have caught it is running both round
trips together, which is now what this entry's command does:

```
JAVA_HOME=... pytest tests/integration/test_generate_renders_the_wiring.py \
                     tests/integration/test_hand_written_round_trip.py -q
16 passed, 1 skipped in 224.62s (0:03:44)
```

Running the new round trip alone was never sufficient: the regression was entirely in what the
rendered wiring does to *other* contexts, which only the older module exercises.
