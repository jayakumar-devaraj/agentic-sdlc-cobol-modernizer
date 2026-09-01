# 13 — The structure test, and the seven damage probes that show it fires

Part of [`docs/qa/verification-report.md`](../verification-report.md). Scope: the repository's own
shape as an enforced property — `tests/contract/test_repository_structure.py` — and the evidence
that each of its assertions actually fails on the defect it names.

## Functional verification

### Why a structure test at all, and what it is allowed to assert

A structure audit on 2026-09-01 read the tree by hand and found three committed files naming paths
that did not resolve. Each had been wrong for a long time, none had ever failed anything:

| Defect | How it presented |
|---|---|
| `.gitattributes` guarded `templates/target-spring-boot-baseline/mvnw` | The template moved inside the package with ADR-0055. `git check-attr text eol` reported `unspecified` for the real file, so the CRLF guard was **off** while the rule sat there looking correct. The committed blob stayed LF only because this machine has `core.autocrlf=true`. |
| `.claude/skills/verify-self-healing-loop.md` named `tests/system/test_self_healing_loop.py` | `git log --all --` on that path returns nothing. It has never existed in any branch. |
| `.claude/skills/run-demo.md` named `scripts/demo_kind_up.sh` and `docs/demo-playbook.md` | Neither ever existed. The skill's body was "follow the playbook step by step". |

One defect class: **a committed file names a path, and nothing checks the path resolves.** Two of
the test's assertions are exactly that check, and the rest pin the layout this effort established.

The module asserts **shape, never content** — where things live, that a declared set is complete,
that a path still resolves. It sits in the contract tier and uses `pathlib` rather than
`git ls-files`, deliberately: shelling out would make it an integration test, and every property it
checks is answerable from the working tree.

### The probes

The rule this repository already applies to caveats applies to guards: **an assertion nobody has
seen fail is not known to work.** Each defect below was reintroduced into a clean tree, the single
assertion run, and the tree restored. All seven fail, and each names the actual problem rather than
reporting a bare `False`.

Every probe was run after the test was committed, so `git checkout --` could restore the tree
without discarding uncommitted work.

```bash
./.venv/Scripts/python -m pytest tests/contract/test_repository_structure.py::<name> -q
```

| # | Defect reintroduced | Assertion | Exit | Message |
|---|---|---|---|---|
| 1 | `.gitattributes` pointed back at the pre-ADR-0055 `mvnw` path | `test_every_gitattributes_pattern_matches_something` | 1 | `.gitattributes patterns matching no file, so their rules never apply: ['templates/target-spring-boot-baseline/mvnw']` |
| 2 | A skill given a line naming `tests/unit/test_self_healing_loop.py` | `test_every_repo_path_named_in_a_skill_exists` | 1 | `skills naming paths that do not exist: ...` |
| 3 | `tests/unit/test_java_reader.py` given `from tests.integration.test_interest_equivalence import PROGRAM` | `test_no_tier_imports_from_a_tier_above_it` | 1 | `a tier imports one above it: ...` |
| 4 | ADR 0030 moved out of `docs/adr/` | `test_adr_numbers_run_from_one_without_gaps` | 1 | `ADR numbering has gaps or duplicates: [... 29, 31, ...]` |
| 5 | `tests/test_stray.py` created outside every tier | `test_every_test_module_is_in_a_tier_directory` | 1 | `test modules outside a tier directory: ['tests/test_stray.py']` |
| 6 | `tests/scratch/` created without being declared | `test_the_tier_directories_are_exactly_the_declared_set` | 1 | `directories under tests/ ([... 'scratch' ...]) do not match conftest.TIERS` |
| 7 | `schemas/orphan.schema.json` committed with nothing exporting it | `test_every_exported_schema_has_a_committed_file` | 1 | `committed schemas nothing exports: ['orphan.schema.json']` |

Probe 1 is the strongest of the seven: it is not a synthetic defect but the **exact bytes** that
were in `.gitattributes` on `main` that morning. The test fails on the real historical state.

`git status --short` was empty after probes 1–4 and again after 5–7, so nothing leaked into the
branch.

### The false positive, handled rather than suppressed

The first run of `test_every_repo_path_named_in_a_skill_exists` failed on
`docs/adr/NNNN-descriptive-sentence-slug.md`, named by `.claude/skills/new-adr.md`. That file is
*supposed* not to exist — naming it is the instruction the skill gives. Placeholder tokens
(`NNNN`, `YYYY`, `<`, `>`, `*`) are skipped explicitly, rather than the assertion being weakened to
make the failure go away. A guard loosened to fit its first false positive usually stops catching
the true ones too.

### What this does not establish

- **It checks paths exist, not that they are the right ones.** A skill naming
  `tests/unit/test_pic_mapper.py` where it meant `tests/unit/test_record_layout.py` passes.
- **`.gitattributes` patterns are matched against the working tree, not the index.** A pattern
  matching only an untracked file would pass; the defect it was written for — a pattern matching
  *nothing* — is caught.
- **Prose paths outside `.claude/skills/` are unchecked.** In particular, roughly 25 references
  across 20 ADRs still name `tests/system/...`, which no longer exists. That is deliberate and
  recorded in ADR-0033: settled records are not rewritten, and the staleness is navigational rather
  than factual. Widening this test to ADRs would turn a documented, accepted cost into a red build.
