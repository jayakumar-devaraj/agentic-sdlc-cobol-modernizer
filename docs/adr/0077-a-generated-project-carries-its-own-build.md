# ADR-0077: A generated project carries its own build

## Status

**Accepted** (2026-09-07). Extends
[ADR-0055](0055-control-plane-obtains-this-cli-as-a-pinned-wheel.md), which moved the baseline
template under the package, and
[ADR-0058](0058-the-wheel-carries-what-the-repository-tracks.md), which made the wheel carry what
the repository tracks.

## Context

Everything this pipeline verifies about a generated project happens **inside the specialist
container**: `materialize_target_project` scaffolds it, `compile_project` builds it, ADR-0075's
runner starts the job, and the differential compares its output against the COBOL oracle. Then the
project is committed and pushed to the routed output repository, and the workspace is discarded.

The output repository re-checked none of it. Four deliveries had landed there and the repository had
**never run a workflow** — measured, not inferred: no `.github/` on the default branch, none on any
delivered branch, and `actions/runs` reporting `total_count: 0`. The baseline template carries
`.mvn`, `mvnw`, `pom.xml`, `src` and no `.github` at all, so every project this pipeline has ever
generated was delivered without a build.

The consequence is narrow but real. The only evidence a delivered artifact compiled was a log line
from a machine that had already deleted the code, and nobody reviewing the delivered branch could
confirm even that much. It also made a pull request less useful than it looks: a PR gives a reviewer
a diff, and without a workflow it gives them no check.

## Decision

**The baseline template carries `.github/workflows/build.yml`**, so every generated project arrives
with a build that runs where it was delivered.

**`verify`, not `test`.** The baseline ships `BaselineStackTest`, a `@SpringBootTest`
`@Testcontainers` case that starts a real PostgreSQL — ADR-0034's gate on the pinned JDK, and the
only check that the Spring context can start at all. `test` would skip it.

**Through `./mvnw`, on the JDK the pom pins.** The wrapper pins the Maven distribution to an exact
version, asserted by `test_the_maven_wrapper_pins_an_exact_maven_version`; a runner's own `mvn` is
whatever its image ships. `java-version` is asserted against
`pom.xml`'s `java.version` by a test rather than restated as a literal, for the reason the sibling
assertion on this repository's own CI already gives.

**No `-Dsurefire.failIfNoSpecifiedTests=false`.** The pipeline passes that flag when it runs one
narrowed test, and ADR-0075 records what it costs: a build that matched *no test* exits 0 exactly
like one that passed. An unnarrowed `verify` in the delivered repository has no such excuse, and
inheriting the flag would make this workflow green on a project whose tests had all been deleted. A
test asserts the flag is absent from the workflow's commands — over the `run:` lines specifically,
because the file's own header names the flag in explaining why it is not there.

## Consequences

**The packaging glob is not optional, and ADR-0058 is why we knew that in advance.** `**/*` does not
descend into a dot-directory: that is exactly how every wheel built before ADR-0058 shipped without
`.mvn/wrapper/maven-wrapper.properties`, leaving `./mvnw` in an installed baseline exiting 1 before
Maven started. `data/templates/**/.github/workflows/*` is listed for the same reason and would have
been the same silent failure — a template delivered without the workflow that is the whole point of
this record.

**The existing packaging test covers it with no new test.**
`test_the_wheel_carries_every_data_file_the_repository_tracks` derives its expectation from
`git ls-files` rather than a hand-written list, precisely so the *next* omission is loud rather than
the next-but-one. Tracking the file was sufficient to bring it under that guard. This is the second
time that design has paid.

**`materialize_target_project` needed no change**, verified by materialising the template into a
temporary directory rather than by reading `iterdir`'s documentation: it iterates the template root,
so a dot-directory is copied like any other.

**This does not retroactively give delivered branches a build.** The template is read at generation
time, so the workflow appears in projects generated after this release. Branches already delivered
have no `.github/` and gain one only by regeneration.

**A first run of this workflow has not happened.** It is asserted here to be correct by construction
and by three checks — the JDK matches the pom, the command goes through the wrapper, the flag is
absent — and none of those is the workflow going green on a real runner. The first delivery after
this release is the test, and if `BaselineStackTest`'s Testcontainers requirement turns out not to be
satisfiable on a hosted runner, this is the record to amend.
