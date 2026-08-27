# ADR-0058: The wheel carries what the repository tracks — checked in both directions

## Status

**Accepted** (2026-08-27). Closes the remainder of the defect
[ADR-0055](0055-control-plane-obtains-this-cli-as-a-pinned-wheel.md) found and partly fixed.
Written **immediately before cutting this repository's first tag**, which is why it exists at all:
a pinned ref makes whatever is in the wheel permanent for every consumer.

## Context

ADR-0055 established that a built wheel of this repository contained **zero non-Python files**,
moved the four runtime data directories into `cobol_modernizer/data/`, and added
`tests/system/test_packaging.py` to prove an installed copy can read its own data.

That fix was real and it was not complete. Preparing the tag that
`agentic-sdlc-control-plane` step 47 installs, the wheel was compared against the repository file
by file for the first time:

| | |
|---|---|
| Tracked under `src/cobol_modernizer/data/`, missing from the wheel | **1** |
| In the wheel, tracked by nothing | **26** |

**The missing file is load-bearing.** It is
`data/templates/target-spring-boot-baseline/.mvn/wrapper/maven-wrapper.properties`, which `mvnw`
reads to learn which Maven distribution to fetch. `local_compiler` prefers `./mvnw` over a system
`mvn` precisely so the target project pins its own Maven version — so this is the build path, not a
fallback. Run from a wheel-installed baseline:

```
Get-Content : Cannot find path '...\.mvn\wrapper\maven-wrapper.properties' because it does not exist.
Cannot start maven from wrapper
EXITCODE=1
```

Maven never starts. With the file restored, the same command reports Maven 3.9.16 on JDK 25 and
exits 0. **Every wheel this repository has ever built had this defect**, including the one
`test_packaging.py` was added to guard.

**Neither existing check could see it.** `test_the_wheel_contains_every_runtime_data_file` compares
against `REQUIRED_DATA`, a hand-written list of four paths. `test_the_wheel_carries_the_whole_java_baseline`
counts `.java` files and compares the count. A tracked `.properties` file inside a dot-directory is
invisible to both. This is the shape maintenance rule 7 in the audit names: the second instance of
"the wheel does not contain what the repository does" wants a mechanism, not a third entry.

## Decision

### 1. Name the dot-directory explicitly, because `**/*` does not reach it

`setuptools` expands `package-data` patterns with `glob.glob(..., recursive=True)`, and `*` does not
match a leading dot. `data/templates/**/*` therefore silently skips everything under `.mvn/`.
`data/templates/**/.mvn/wrapper/*` is added beside it.

### 2. Derive the expectation from `git ls-files`, in both directions

The replacement check builds a real wheel and compares its `cobol_modernizer/data/` payload against
what git tracks:

- **Missing** — a tracked file absent from the wheel. This is ADR-0055's defect in its general form,
  and it fails today without the fix in § 1.
- **Extra** — a file in the wheel that the repository does not track. This is the direction nobody
  had looked at, and it was worse than the first.

A hand-written list can only ever catch the omissions someone already thought of. This one catches
the next.

### 3. The 26 extra files needed two fixes, and the pairing was measured rather than reasoned

A developer who has run the Java baseline's build once has `target/` output sitting inside the
package data directory — compiled `.class` files, a `.jar`, `maven-status/*.lst`. `.gitignore` has
no say in what `include_package_data` collects, so all 26 were eligible to ship to every consumer as
if they were baseline sources. A release cut from a working tree would have shipped them.

Two levers were required, and **the first version of this record got the attribution wrong** on a
control that varied the wrong variable. The full 2×2:

| `[tool.setuptools.exclude-package-data]` | `src/*.egg-info` | `target/` files in wheel |
|---|---|---|
| present | stale | **26** |
| present | regenerated | **0** |
| absent | regenerated | **26** |

The exclusion is consulted for files matched by `package-data` globs; a stale `SOURCES.txt` reaches
the wheel by the manifest path, which does not consult it. So the exclusion is declared **and** the
test fixture clears `src/*.egg-info` before building. The fixture already cleared `build/` as a
determinism precaution and documented it as such — `build/` was the wrong directory, and the right
one was never cleared.

A `MANIFEST.in prune` was the third candidate. It works, it is redundant once the other two hold,
and it is **not** in this repository — a third mechanism for the same property is how the next
person ends up unable to say which one is load-bearing.

## Consequences

**The tag this ADR precedes points at a wheel whose baseline can actually build.** That was not true
of any earlier commit, and a pinned ref would have frozen it.

**A build from a working tree now matches a build from a clean checkout.** Worth stating because
`pip install "… @ git+…@<tag>"` always clones fresh and would have hidden the `target/` leak
indefinitely — the exposure was only ever to a locally cut release, which is exactly what was about
to happen.

**The check needs a checkout, not an unpacked sdist**, since it shells out to `git ls-files`. It
asserts rather than skips when git is unavailable: a guard that silently skips is the failure this
repository has already paid for once.

**What is not verified here.** That the *installed* baseline builds end to end — `mvnw -v` starting
Maven proves the wrapper resolves, not that `mvnw test` passes from a wheel-installed copy. Running
the baseline's own `BaselineStackTest` needs a Docker daemon (Testcontainers), which belongs with
the specialist-capable image control-plane is provisioning, not here. The narrower claim this ADR
makes is the one that was false before it: the file is present and the wrapper starts.
