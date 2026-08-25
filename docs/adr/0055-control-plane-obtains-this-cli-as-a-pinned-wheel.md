# ADR-0055: Control-plane obtains this CLI as a pinned wheel, and the wheel had to be made to work first

## Status

**Accepted** (2026-08-25). Closes plan **step 46** — *"ADR: how control-plane's runtime obtains this
CLI (packaging)"* — the one remaining ADR before Milestone C5, named as such in the platform's
integration-boundary record.

## Context

[ADR-0001](0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md) settled the *shape* of
the integration: this repo is invoked as a subprocess, "the same shape as control-plane's existing
`coder` node invoking the `claude` CLI." It never settled how the binary gets onto the machine that
runs it, and nothing since has.

### What a build of this repo actually produced

Asked rather than assumed, because the answer turned out to matter more than the decision:

```
python -m build --wheel
```

The wheel contained **zero non-Python files**. `prompts/registry/`, `config/*.yaml` and
`templates/target-spring-boot-baseline/` all sat beside `src/` at the repository root, and
`[tool.setuptools.packages.find] where = ["src"]` packages what is under `src/`. Six modules reached
that data with `Path(__file__).resolve().parents[3]` — the repository root in a checkout,
`<venv>/Lib/` in an installation.

Installed into a clean virtualenv, the CLI failed on its first real invocation:

```
FileNotFoundError: [Errno 2] No such file or directory:
  '...\probe-venv\Lib\prompts\registry\spec_extractor\v1_0_0.md'
```

**Nothing in the repository could have caught this.** CI installs with `pip install -e ".[dev]"`, an
editable install that leaves the package inside the checkout, where `parents[3]` is still the
repository root. 1146 tests passed against a layout no consumer would ever have. This is the same
class as the caveat rule in `CLAUDE.md` — an assumption nothing executes — except that here nobody
had even written the caveat down.

### What the specialist needs that Python cannot deliver

| Requirement | `design` | `generate` |
|---|---|---|
| Python 3.12 + this package | ✅ | ✅ |
| `claude` CLI on `PATH` (ADR-0013's default backend) | ✅ | ✅ |
| JDK 25 + Maven | — | ✅ |
| A Docker daemon | — | ✅ (the baseline's `BaselineStackTest` uses Testcontainers) |

Control-plane's own image is `python:3.12-slim` and carries none of them.

### The precedent control-plane already set

Its `coder` node faces the identical question for `claude` and answers it explicitly: "Neither mode
is available by default in the shipped container image, and that is deliberate rather than an
omission… live mode is an opt-in built on a customized image, not a config toggle."
`_require_claude_cli` fails with that explanation "rather than letting a bare 'No such file or
directory' surface from subprocess."

## Decision

### 1. Control-plane installs a wheel from a pinned ref; this repo publishes nothing

```
pip install "cobol-modernizer @ git+https://github.com/…@<tag>"
```

A pinned git ref, not a floating branch and not a PyPI release. PyPI is rejected for now because
this package has exactly one consumer and publishing would add a release step, a name reservation
and a version-yanking story to serve nobody. A tag is already immutable enough for a build to be
reproducible, which is the property that matters.

**Rejected: vendoring a checkout and running from it.** It works — it is what CI does — but it makes
"the specialist" a directory layout rather than an artifact, and it is precisely the arrangement
that hid the defect above.

**Rejected: shipping the specialist as its own container image** invoked with `docker run`. It is the
only option that actually solves the JDK/Maven/Docker row rather than delegating it, and it may well
be right later. It is not right now: it reshapes ADR-0001's "subprocess, same shape as `coder`" into
a container invocation, and there is no step 47 yet to say what that interface needs. Revisit when
control-plane's router exists.

### 2. The package carries its own data, in one place, reached by one resolver

`prompts/`, `config/` and `templates/` move to `src/cobol_modernizer/data/`, declared as
`[tool.setuptools.package-data]`, and resolved through a single `core/package_data.py`:

```python
DATA_ROOT = Path(__file__).resolve().parent.parent / "data"
```

The same path in a source checkout and in an installed wheel — there is no "installed or not" branch
to get wrong and no environment variable to forget. Six `parents[3]` expressions become three
constants.

**`schemas/` deliberately does not move.** Its only readers are `scripts/generate_schemas.py` and
`tests/system/test_schemas.py`; nothing on the runtime path opens it. Moving it would have been
motion rather than a fix, and this record says so precisely so the asymmetry does not read as an
oversight later.

### 3. Missing data names the install, not the file

`PackageDataMissingError` follows `_require_claude_cli`'s pattern, for its stated reason. Anyone who
saw the `FileNotFoundError` above would start by looking for a missing prompt file; the real answer
is that the package is built wrong, and the message now says that.

### 4. The unmet runtime requirements are documented and unowned by this repo

JDK 25, Maven, a Docker daemon and the `claude` CLI must exist in whatever environment invokes
`generate`. This repo does not install them and does not check for them beyond what already exists
(`ToolchainNotFoundError` stops the heal loop when Maven is absent). Following control-plane's own
posture: the base image ships none of it, and a `generate`-capable deployment is a customized image,
not a config toggle. **Naming this is the point** — it is the difference between a decision and a
gap, and it is the substance of what step 47 will have to provision.

## Consequences

**Historical records are not rewritten to match the new paths.** Ten ADRs, nine verification-report
entries and one golden fixture mention `templates/…` or `prompts/registry/…` at their old locations.
The ADRs are accepted decisions and are superseded rather than edited; the verification entries state
the exact command run on a date, and rewriting them would make them claim commands were run at paths
that did not exist yet; `tests/fixtures/golden/CBACT04C/spec.md` is compared byte-for-byte by a test.
Only `README.md` and `docs/development-environment.md` — the two documents that must be correct for
someone acting *today* — are updated. This follows [ADR-0033](0033-the-verification-report-is-a-hub-with-one-spoke-per-phase.md)'s
decision about cross-references that go stale in bulk, and its cost is the same: a reader of an old
record may follow a path that has moved.

**`tests/system/test_packaging.py` builds a real wheel and installs it into a throwaway
virtualenv**, roughly 100 seconds. That is the only construction that can catch this defect class,
since every other test in the suite runs against an editable install. `build` is a declared dev
dependency rather than an optional one: the fixture skips when it is absent, and a guard that
silently skips is the decorative-check failure Open Issue 3 already cost this repo once.

**The wheel test does not run `design` or `generate`.** Both reach a live model on their first node.
While establishing the evidence for this record, an invocation intended to fail fast instead got
*past* the newly-fixed prompt load and into a real model call before it was killed — no output was
written, and at most one `spec_extractor` call on one program was billed. The test asserts what the
packaging question actually turns on: whether an installed package can read its own files.

**Step 47 inherits a named list rather than a discovery.** Whoever wires control-plane's router now
knows the install command, the pinned-ref requirement, and the four things the image must carry.
