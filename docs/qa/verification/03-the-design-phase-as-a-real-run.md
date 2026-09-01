# The design phase as a real LangGraph run

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Functional verification

### `parsing/cobol_parser.py` — every `DATA DIVISION` section, and the fixed-`OCCURS` decision (ADR-0011)

**Verified**: the fix for the defect above, against real source for all four programs.

- **Nothing is silently absent any more.** An independent re-scan of every real source file for
  level-numbered numeric `PIC` declarations, diffed against what the pipeline produces, reports
  **zero unaccounted fields for all four programs** — every declaration lands in either
  `field_mappings` or `unsupported_fields`. Before the fix, 20 were in neither (1 `CBCUS01C`,
  10 `CBACT01C`, 3 `CBTRN02C`, 6 `CBACT04C`).
- **`COMP-3` is genuinely reached and correctly typed.** `CBACT01C`'s `OUT-ACCT-CURR-CYC-DEBIT`
  maps as `BigDecimal`, precision 12, scale 2, signed, `usage=COMP_3` — with its `USAGE` clause on
  a continuation line, which also confirms the parser joins a wrapped declaration into one sentence
  before mapping it. Confirmed it is the only mapped `COMP-3` field in Track C.
- **`CBACT04C`'s `LINKAGE SECTION` is reached**: `PARM-LENGTH` (`PIC S9(04) COMP`, precision 4,
  signed) — the program's real `PROCEDURE DIVISION USING EXTERNAL-PARMS` input parameter.
- **The fixed-`OCCURS` group is isolated, not flattened.** `CBACT01C`'s `ARR-ACCT-BAL OCCURS 5
  TIMES` group produces four `unsupported_fields` entries carrying construct name
  `"OCCURS (fixed)"`, and zero mappings. Asserted explicitly, including that all four fields are
  flagged rather than only the two genuinely inside the array — the over-flagging is a real cost
  ADR-0011 accepts, so it is pinned by a test rather than left implicit.
- **Real counts moved and were re-verified, not assumed**: `CBACT04C` 75 → 93 mapped fields,
  `CBTRN02C` 88 → 102, `CBACT01C` unsupported 28 → 32 (28 still `REDEFINES`, 4 new
  `OCCURS (fixed)` — asserted separately so the two reasons can't be conflated).
- **The golden fixture was regenerated, not hand-edited.** `render_known_facts` was re-run against
  the real fixture to produce the Field reference table. Confirmed by diffing that this added
  exactly 18 rows, removed nothing, and left the hand-verified Overview / Paragraph flow / Business
  rules prose byte-identical — the property that section was generated for in the first place.
- **Two prior decisions were reversed, both with tests that had asserted the old behavior**: a
  fixed `OCCURS` mapping cleanly (`test_pic_mapper.py`), and `LINKAGE SECTION` fields being treated
  as a leak if they appeared (`test_cobol_parser.py`). Both tests were rewritten to assert the new
  behavior and state what changed, rather than deleted.
- **The `WORKING-STORAGE`-only fallback is now tested.** No real Track C fixture reaches it
  (programs have a `DATA DIVISION`, copybooks have no headers), so extending the region left a
  documented branch uncovered — caught by reading the coverage report, and closed with a fragment
  test rather than left to rot.

**Command**: `pytest tests/unit/test_cobol_parser.py tests/unit/test_pic_mapper.py tests/unit/test_numeric_field_coverage.py -v`
**Result**: 68/68 passed.

### `nodes/solution_architect.py` — cross-program domain-entity unification, against real data for all four programs

**Verified**: `build_domain_entities` against real `extract_spec`/`critique_spec` output for all
four Track C programs at once (`CBACT04C`, `CBCUS01C`, `CBACT01C`, `CBTRN02C`) — the first node in
this repo to look across every program together, not one at a time:

- **Real cross-program merge**: `Account` (from `CVACT01Y`) correctly merges into one entity used
  by all three programs that `COPY` it (`CBACT04C`, `CBACT01C`, `CBTRN02C`), with 12 non-`FILLER`
  fields and byte-exact `pic_mapper` data reused verbatim (`ACCT-CURR-BAL`: `BigDecimal`,
  precision 12, scale 2 — the same real value `test_spec_extractor.py` already verifies).
- **`CODATECN` correctly produces no domain entity at all** — confirmed it contributes zero
  successfully-mapped fields (all 28 are inside its four real `REDEFINES` groups), so there is
  nothing to represent, rather than an empty or guessed-at entity.
- **Structurally similar copybooks stay separate, confirmed directly**: `CVTRA06Y`'s
  `Dalytran` and `CVTRA05Y`'s `Tran` are both real 350-byte transaction-shaped records but remain
  two distinct entities — proving the merge-by-exact-copybook-name-only rule (ADR-0010 decision 1)
  actually holds against real, easily-confusable data, not just a description of intent.
- **A real bug in ADR-0010 itself was caught by running this against real data, not assumed
  correct from the design alone**: the ADR's own Consequences section originally claimed 6 domain
  entities; running `build_domain_entities` for real produced 7 (`Dalytran` was missed in the
  original count). Corrected in the same commit as the code that surfaced it.
- **Structured-output validation** (an architect response referencing an unknown program, domain
  entity, batch-step role, or REST method; a response missing required fields; a response covering
  only some of the four real programs) is exercised directly against real Known Facts data, not
  synthetic placeholders.

**Command**: `pytest tests/unit/test_solution_architect.py -v`
**Result**: 22/22 passed.

### The `design` subcommand end-to-end — a real LangGraph run, as a real process (ADR-0012)

**Verified**: `cobol-modernizer design` wired through the real graph over real fixture source, and
run as an actual OS process rather than only in-process, because the stdout/stderr split is the
contract with control-plane and `capsys` is an in-process approximation of it.

- **A real four-program run produces real artifacts.** `CBACT04C CBCUS01C CBACT01C CBTRN02C` →
  exit 0, a **413,532-byte `design.json`** plus one `spec.md` per program, 52
  `unsupported_construct` gate items (9 + 2 + 32 + 9, matching each program's own verified count),
  7 unified domain entities, programs in requested order. The written file re-validates through
  `DesignDocument.model_validate_json`.
- **The `--json` stdout contract holds under a successful run**, which is the harder case — 49 log
  lines went to stderr while stdout carried **exactly one line**, parsed as one JSON object. Also
  verified on the failure path through the installed console script
  (`.venv/Scripts/cobol-modernizer.exe design --tenant-repo /nonexistent`): exit 1, stdout still
  exactly one parseable object with `status="error"` and `TenantRepoFileNotFoundError` in `detail`,
  full traceback on stderr only.
- **Branches genuinely overlap.** Asserted by observed overlap (latest branch start < earliest
  branch end) plus more than one thread, rather than a wall-clock threshold that a loaded CI
  machine could make flaky. Independently confirmed against LangGraph directly: three 1.0s
  branches complete in 1.01s wall on a real `ThreadPoolExecutor`.
- **A measured assumption turned out to be wrong, and is recorded rather than quietly fixed.**
  `run_design` re-orders program entries because fan-in was assumed to follow completion order. It
  does not: LangGraph applies a reducer's writes in `Send` order, so the raw state is already
  deterministic — confirmed by deleting the re-ordering and watching the ordering test still pass,
  then measured directly with randomized per-branch delays over repeated runs. The normalization
  was kept (ADR-0012 decision 4) but the test now states that it passes either way, and a second
  test pins LangGraph's behavior so a future change is loud instead of silently becoming the only
  thing keeping `design.json` deterministic.
- **Reproducibility asserted directly**: two runs into different directories produce
  byte-equal `design.json` apart from `generated_at`.
- **Every node is confirmed to use its own registry prompt and its own routed model.** The test
  fake dispatches on *system-prompt identity* against the real registry files rather than sniffing
  substrings, so a prompt mix-up raises instead of silently producing a plausible design. Confirmed
  the real `config/model_routing.yaml` values resolve through the real lookup: `spec_extractor` and
  `solution_architect` → `claude-opus-5`, `spec_critic` → `claude-haiku-4-5-20251001`, with exactly
  one extraction and one critique per program and one architect call per run.
- **A real LangGraph constraint was found by running the graph**, not by reading docs: a sub-graph
  state key that collides with its parent's makes every concurrent branch write the same
  non-reducer channel in one superstep, and LangGraph rejects it
  (`InvalidUpdateError: At key 'worktree_root': Can receive only one value per step`) even though
  all branches write an identical value. Hence `branch_worktree_root`.
- **Failure policy**: one bad program name fails the whole invocation and **writes no partial
  `design.json`** — asserted directly, since a document silently covering three of four requested
  programs is indistinguishable at a review gate from a complete one.

**Command**: `pytest tests/unit/test_design_graph.py tests/unit/test_cli_design.py tests/contract/test_cli_contract.py -v`
**Result**: 30/30 passed.

### `run_id` correlation and `RunCost`, verified under real concurrency (ADR-0018)

**Verified**: both halves of ADR-0018 against a real four-program `run_design` on a real thread
pool — not sequentially, because sequential execution cannot distinguish a working implementation
from a broken one here.

- **`run_id` reaches every branch.** A fake `narrate` records `current_run_id()` per program;
  all four branches report the id bound before `invoke`, and the test separately asserts **more
  than one thread id was observed** — without that, the propagation assertion would pass trivially
  on a single thread and prove nothing.
- **`RunCost` sums across concurrent branches.** With `_call_anthropic_sdk` faked to a known
  result, a four-program run totals exactly `2 × 4 + 1 = 9` calls (4 extractor + 4 critic
  concurrent, 1 architect after fan-in) with token counts multiplying out exactly. **This is the
  assertion that catches the subtle failure**: had the accumulator been a `ContextVar` of running
  integers rather than a mutable object, each branch would have incremented a private copy and the
  parent would have read only the architect's call.
- **Partial cost is distinguishable from zero cost.** On a backend reporting no cost,
  `notional_cost_usd` stays `None` while `calls_without_reported_cost` equals the call count and
  token counts stay exact — so a consumer can tell "nothing cost anything" from "nobody said".
- **The `--json` stdout contract still holds.** `test_cli_design.py` passes unchanged: the new
  cost log line goes to stderr with everything else, and stdout carries exactly one object.

**A real defect was found by the tests, not after them.** `bind_run_id` mutates the ambient
context and never restores it — correct for a CLI process, wrong under pytest where all tests share
one context. A test asserting the unbound placeholder **passed in isolation and failed in suite
order**. Fixed with an autouse reset in `tests/conftest.py`, alongside the existing backend pin,
rather than by weakening the assertion.

**A coverage blind spot worth recording.** `telemetry/logging_config.py` reported **100% coverage
with zero tests** — every line executed because `cli.main()` calls `configure_logging` during the
CLI tests, so coverage confirmed the module ran while nothing asserted what the logging did.
`tests/unit/test_logging_config.py` now exists. Coverage measures execution, not verification.

**A code-review pass found the cost summary was unreachable on the failure path**, and it is fixed
here. Both the log line and the `RunCost` construction sat *after* `app.invoke` returned, so a run
that raised partway discarded the spend of every branch that had already completed — the exact
situation in which the question gets asked, and one where no `design.json` and no cost-bearing
`DesignCliResult` exist to fall back on. The summary now happens in a `finally`, driven by a test
that causes a real failure (one valid program, one missing) and asserts the line still reports
`model_calls=1`. **Confirmed falsifiable**: restoring the original shape makes exactly that test
fail, and nothing else.

**Command**: `pytest tests/unit/test_design_graph.py tests/unit/test_logging_config.py -v`
**Result**: 20/20 passed (12 pre-existing + 8 new).
