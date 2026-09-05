# ADR-0067: A file's path is deployment configuration, and lives in Spring properties

## Status

**Accepted** (2026-09-05). Written before the code it governs.

The other half of [ADR-0066](0066-generate-renders-the-job-wiring-and-the-stopgap-retires.md), which
decides that `generate` renders the wiring. This decides the single fact that wiring needs and the
design does not carry.

Rests on [ADR-0020](0020-batch-steps-declare-their-types-and-composites-are-declared-not-inferred.md)'s
declare-rather-infer rule, and on the posture
[ADR-0030](0030-job-wiring-is-rendered-eventually-and-hand-written-once-first.md) took when it
refused option (b): a model emitting access details is `pic_mapper`'s objection in a new costume.

## Context

`CBACT04C` says:

```cobol
SELECT TCATBAL-FILE ASSIGN TO TCATBALF
```

`TCATBALF` is a **DD name** — an environment binding resolved by JCL at run time, on a system this
program will never run on again. The COBOL states that a file exists, what record it yields, how it
is organised and what it is keyed by. `parsing/file_control.py` reads all of that and
`FileAccessPath` carries it. **What the COBOL does not state, anywhere, is a path**, because on the
mainframe it was never in the program.

So the fact is genuinely absent from the source, and there are only two honest places to get it:
invent it, or ask the operator. This record picks the second and says how.

The stopgap found this and said so plainly:

> Binding them to locations is deployment, and **arguably never belongs in a design at all.**

That parenthetical is the whole decision, and it is right. A path is not a property of the program;
it is a property of one deployment of it. `design.json` is reviewed by a human at a gate and then
frozen — putting a filesystem layout inside it would make the artifact under review differ per
environment, which is the opposite of what a gate is for.

## Options

| Option | Cost |
|---|---|
| **Spring properties, with rendered defaults** | Keeps deployment out of the design; needs a property naming convention. **Chosen.** |
| `FileAccessPath` gains a path | Puts deployment *into* `design.json`, contradicting the argument above and ADR-0030's posture |
| CLI arguments to `generate` | Control-plane would have to know tenant file layout — a specialist detail leaking into the domain-agnostic orchestrator, against its own ADR-0001 |
| A rendered profile the operator overrides | Formalises what the fixture does by hand, and keeps the job un-runnable under the default profile |

## Decision

### 1. Each file's path is one Spring property, named from its `ASSIGN TO`

```
cobol.file.<assign-to, lower-cased>
```

`TCATBALF` becomes `cobol.file.tcatbalf`. **Derived from a declared fact, not chosen**, which is the
same rule the rendered reader already follows for its constructor parameter names — they are
`_camel(assign_to)` today, so the property and the argument it fills trace to one source and cannot
drift apart.

Not named after the entity, the step or the select name. `assign_to` is the name an operator already
knows, because it is the one the JCL bound; the others are target-side inventions or COBOL-internal.

### 2. The rendered configuration injects them; it never constructs a path

```java
@Bean
ItemReader<TranCatBalWithRate> computeInterestItemReader(
        @Value("${cobol.file.tcatbalf}") Path tcatbalf,
        @Value("${cobol.file.acctfile}") Path acctfile, ...) throws Exception {
    return new ComputeInterestItemReader(tcatbalf, acctfile, ...);
}
```

Arguments in the rendered class's own parameter order, which is `ASSIGN TO` declaration order. What
the bean supplies is **paths and nothing else** — not layout, not keys, not joins. Those are all
already rendered from the design, and this boundary is what keeps deployment from reaching them.

### 3. Defaults are rendered, and they are a convention rather than a claim

```properties
cobol.file.base=data
cobol.file.tcatbalf=${cobol.file.base}/TCATBALF
```

into the generated project's `application.properties`.

**The default asserts nothing about where the tenant's data is.** It exists so the project starts,
so `--cobol.file.base=/some/where` is the whole of the common case, and so a reviewer can see every
file the job touches in one list. A wrong default surfaces as a missing file at read time, with the
path in the message.

Rendered per file rather than a single directory scan: a job that silently picks up whatever is in a
folder is how the wrong month's data gets posted.

### 4. Overriding one path never requires knowing the others

Each property stands alone and `cobol.file.base` only feeds the defaults. An operator with three
files in one place and one somewhere else sets the base and one override — the case the fixture
actually has, where the account file is both an input and the file rewritten in place.

## Consequences

**`generate` now produces a project with a deployment surface**, and that is new. Until now its
output was code; it now also has configuration that is wrong until someone sets it. The
`application.properties` it renders is the documentation of that surface, which is why every path is
listed explicitly even though a base directory would have covered the default case in one line.

**The property names are part of the contract with whoever runs the job.** Renaming one silently
breaks a deployment. They are derived from `assign_to`, so they change only when the COBOL's own
`ASSIGN TO` changes — which is as stable a source as this repository has.

**A path is not validated at render time**, and cannot be: the specialist renders on one machine and
the job runs on another. A non-existent path is a runtime failure with the path in the message,
which is the loudest thing available and is deliberately not a `generate`-time refusal.

**This does not make the generated job multi-tenant or multi-environment.** One property set, one
deployment. Profiles, secrets and per-environment layering are Spring's to provide and this record
takes no position on them beyond not preventing them.

## Alternatives considered

**Put the path on `FileAccessPath`.** Rejected above, and worth naming the specific damage: the
design document is what a human approves at control-plane's gate, and two deployments of the same
approved design would produce two different `design.json` files. The gate's artifact would no longer
identify the thing being approved.

**Default each path to the bare `ASSIGN TO` name in the working directory.** Simpler by one property
and rejected: a job whose default is a relative path in whatever directory it was launched from is
the failure mode where a test run overwrites something real. A named base makes the location a
decision someone made.

**Fail to start with no default at all.** Genuinely defensible, and closest to this repository's
fail-loudly-rather-than-guess posture — a missing `@Value` placeholder stops the context before
anything reads a byte. Rejected because the failure it prevents (reading the wrong file) is already
prevented by rendering every path explicitly, while the cost is that no generated project can be
started, even to see it start, without configuration a reviewer does not yet have. The default is
overridable in one flag; the ceremony is not worth it.

**Let `generate` take the paths as CLI arguments and bake them in.** Rejected as in the table:
control-plane invokes this CLI, and control-plane is by its own ADR-0001 forbidden tenant-specific
vocabulary. A `--tcatbalf` flag is exactly that.
