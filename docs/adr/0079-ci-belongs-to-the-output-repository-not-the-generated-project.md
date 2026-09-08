# ADR-0079: CI belongs to the output repository, not to the generated project

## Status

**Accepted** (2026-09-08). **Supersedes
[ADR-0077](0077-a-generated-project-carries-its-own-build.md)**, which put a CI workflow in the
baseline template. The goal ADR-0077 named — a delivered artifact answerable where it lands — was
right. Its mechanism was wrong in two independent ways, and the first delivery after it shipped
proved both.

## Context

ADR-0077 ended by naming its own untested claim: *"A first run of this workflow has not happened…
The first delivery after this release is the test, and if `BaselineStackTest`'s Testcontainers
requirement turns out not to be satisfiable on a hosted runner, this is the record to amend."*

It was the test. It failed, twice over, and neither failure was Testcontainers.

### The push was rejected before any runner saw it

Run `step61-cbact04c-20260908-122358` produced a correct job — five of five files bound, `completed`,
1097 of 1100 fields matching — and then could not be delivered at all:

```
! [remote rejected] HEAD -> agentic-patch/step61-cbact04c-20260908-122358
  (refusing to allow a Personal Access Token to create or update workflow
   `.github/workflows/build.yml` without `workflow` scope)
```

GitHub refuses a PAT push that creates or updates any `.github/workflows/*` file unless the token
carries `workflow` scope. Every generated project now contained one, so **none of them could be
published**. ADR-0077 traded "delivered without CI" for "not delivered", which is the worse of the
two.

The scope it asks for is not a formality. This platform's credential posture (ADR-0012, and
control-plane's ADR-0022) is built on the control plane holding the narrowest token that does the
job. `workflow` scope lets it write CI, and CI executes arbitrary code with the repository's
secrets — in a pipeline whose whole purpose is generating code from a model. Widening the token to
ship a convenience is the wrong direction.

### The workflow did not need to be there anyway

Measured rather than argued. The workflow was added once to the output repository's default branch,
and an *existing* pull request — whose head branch contains no `.github/` at all — was reopened:

```
verify   pending   .../actions/runs/34267197079
```

The first workflow run in that repository's history, on a branch carrying no workflow. For
`pull_request`, the workflow file is resolved from the base branch. **So a delivered branch never
needed to carry one**, and shipping a copy in every generated project was duplicating repository
configuration into every snapshot of the code it configures.

## Decision

**The baseline template ships no `.github/`.** The packaging glob, the template tests, and the
workflow file are removed with it.

**CI for delivered artifacts lives once on the output repository's default branch**, as deployment
configuration rather than generated content. Setting that up is an operator step, like the routed
`output_repository` itself, and it needs no scope the platform does not already hold.

## Consequences

**Publishing works again**, and with the unchanged PAT.

**ADR-0077's goal is met more cheaply than ADR-0077 met it.** One file in one repository, versus a
copy in every generated project plus a packaging glob plus two tests plus a widened credential.

**The experiment found a real defect on its first run, which is the point.** With CI finally running
where the code was delivered, `./mvnw -B verify` failed — and not on Testcontainers, which started
fine (`Found Docker environment with local Unix socket`):

```
BeanDefinitionOverrideException: bean 'jobRepository'
  BatchAutoConfiguration wants to register it
  ... already bound from interestCalculationJobConfiguration
```

The rendered job configuration declares its own `JobRepository`, `PlatformTransactionManager`,
`JobRegistry` and `JobOperator`. Under a full `@SpringBootTest` context Boot's `BatchAutoConfiguration`
declares them too. **The delivered artifact cannot start as a Spring Boot application**, and the
baseline's own `BatchApplication` says why that matters: *"Spring Boot's own auto-configuration
builds the `JobRepository` and `JobLauncher` against the configured DataSource."* A
`ResourcelessJobRepository` persists no job metadata at all.

Nothing had ever put those two together. The pipeline's runner builds a plain
`AnnotationConfigApplicationContext` on purpose (ADR-0075), so it never enables autoconfiguration;
the template's `BaselineStackTest` passes because a bare template has no generated job configuration.
The defect is as old as the renderer — it arrived with G31 — and was invisible until CI ran in the
repository the code was delivered to. That has its own record and its own fix.

**What is still unverified.** No delivered project has yet *passed* this workflow. The run above is
the only one, and it failed on the defect above rather than on anything about the workflow. The
workflow's own correctness — right JDK, right goal, through the wrapper — remains asserted by
construction rather than by a green build.
