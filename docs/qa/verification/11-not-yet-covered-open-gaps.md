# Not yet covered — the honest gaps

> Spoke of the [QA Verification Report](../verification-report.md) — this repo's hub index
> for unit-test coverage and functional verification. Every entry below is reproduced
> verbatim from the single-file report it was split out of, and states the exact command run
> and its real output, not a paraphrase.

## Not yet covered (honest gaps, not silently skipped)

- *(corrected 2026-08-08 — this entry was stale, not merely incomplete)* This previously read
  **"No full `design` run has been executed against real models yet."** That is false: real
  four-program `design` runs happened (~$2.31 at API rates), `tests/fixtures/narrations/CBCUS01C/
  spec.md` is verbatim output from one of them, and two benchmarks scored real narrations by hand
  (PRs #20, #21 — see the `spec_critic` and `spec_extractor` entries above). What remains
  genuinely uncovered is narrower and worth stating on its own terms: **output quality is
  spot-measured, not continuously evaluated.** `CBACT04C` was scored on six hand-verified facts and
  `CBCUS01C`'s critique on three planted errors; no standing harness re-scores narrations as the
  prompts or models change, and `solution_architect`'s batch/REST design has never been scored at
  all (Open Issue 6 — the hard part is that no golden unified design exists to score against).
  The LLM-as-judge harness that would close this is Milestone C4.
- **RAG is a storage layer, not a capability.** `tools/knowledge_store.py` is real and verified
  against real Postgres+pgvector, and has zero production callers. ADR-0016 decides the provider
  (Voyage AI, `voyage-code-3`) and then declines to wire retrieval on two gates: no
  `VOYAGE_API_KEY` exists here, and nobody has shown retrieval improves extraction over a corpus of
  four programs. Neither gate is closed, so no claim is made about retrieval quality — there is
  nothing to measure yet. `--db-credentials-file` is unimplemented for the same reason: its only
  consumer sits behind those gates (ADR-0005's amendment note).
- *(narrowed 2026-08-11, deliberately not closed — step 44 / ADR-0024)* The entry above ends **"the
  LLM-as-judge harness that would close this is Milestone C4."** That harness now exists
  (`tests/evaluations/`, G9 closed) and **it has never been run against a real model**, so the gap it
  was written for is smaller and still open. What changed: there is now a committed corpus, a rubric
  of four criteria each traceable to a real defect, and scoring shown to discriminate — a scripted
  judge that passes everything scores 0.00 detection, one that fails everything scores 1.00 false
  positives. What has not changed: **no number describes a real judge**, so nothing yet re-scores
  generator output as prompts and models change; the thing the gap is about is the *running*, not the
  building. Two further limits worth keeping visible rather than folding into a green tick — the
  corpus scores `modernization_engineer`'s method bodies only, so **`solution_architect` has still
  never been scored** (Open Issue 6, untouched by this work, and still blocked on there being no
  golden unified design to score against); and **nine cases with three faithful ones**, across two
  programs since ADR-0050, is still a floor that rules out a judge flagging everything rather than a
  settled false-positive rate — the rate over the corrected corpus is the one number pillar 22 waits
  on.
- *(historical, closed — kept because the original wording named the wrong cause)* The original
  form of this gap was that `_default_narrate`/`_default_critique`/`_default_architect` had never
  been invoked at all, with every test injecting a fake in their place, and it said to "revisit
  once a real credential is available." **No credential was ever needed** — the `claude` CLI on a
  Pro subscription was a usable backend the whole time (ADR-0013). All three defaults have now run
  against real models over real Track C source, ADR-0004's deferred question about `spec_critic`'s
  cheaper tier is answered by measurement (PR #20), and the deterministic steps upstream of each
  remain verified as described above. Left here rather than deleted: the gap was real and the
  stated cause was not, which is the specific mistake worth not repeating.
- **A fixed `OCCURS` cannot be represented, only flagged.** `PicMapping` has no cardinality field
  and `DomainField` cannot express a collection, so `CBACT01C`'s `ARR-ACCT-BAL OCCURS 5 TIMES`
  group is routed to the human gate rather than mapped (ADR-0011). That is the correct behavior
  given the current types — flattening an array to a scalar would be a wrong answer that looks
  right — but it is a capability gap, not a solved problem. Track C has exactly one occurrence and
  no node consumes that file yet; revisit when Milestone C4 generates a reader for it, or with
  Track B's B3 alias-analysis module.
- **The template has a scaffold, not a program.** `templates/target-spring-boot-baseline/` compiles
  and its stack is proven on JDK 25, but it contains one arithmetic helper and an application class.
  **No line of Java has been generated from COBOL yet** — `modernization_engineer` (step 39) is what
  makes the template earn its place, and until then a green `template-build` proves the target
  stack works, not that anything can be modernized into it.
- **`FD` record layouts do not become domain entities.** They are now parsed and visible in
  `spec.md`, but `build_domain_entities` only promotes copybook-sourced fields (ADR-0010), and
  these are program-local. Whether a file record layout should be an entity in its own right is a
  real open question for Milestone C4, when a Spring Batch reader needs a type to read into —
  deliberately not answered by ADR-0011.
- **Only `CBACT04C` has a hand-verified golden fixture**, and that is now a deliberate choice
  rather than an open question. `CBCUS01C`, `CBACT01C`, and `CBTRN02C` have real, byte-verified
  source, confirmed-working `spec_extractor`/`spec_critic` extraction (via the
  faithful-Known-Facts-narration technique), and — as of the entry above — exhaustive
  hand-cross-checked numeric-field verification. What they do not have is `CBACT04C`-level golden
  narrative *prose*. The original reason for deferring them — that no credential existed to produce
  real narrations to compare against — **no longer holds**, since real narrations now exist
  (`tests/fixtures/narrations/`). The deferral stands on a different and better reason: a golden
  fixture is only as good as the hand cross-check that produced it, `CBACT04C`'s took a careful
  paragraph-by-paragraph read of real source, and that read is what is missing for the other three
  — not model output. `CBACT04C`'s own fixture was also **factually wrong** about EOF posting until
  a model caught it, which is the strongest available argument against mass-producing three more on
  a schedule. Milestone C2's gate is closed on its literal numeric-field wording, which is
  checkable without a model, not on prose.
- **Auditing/provenance** (`CLAUDE.md`'s stated concern: every generated artifact traces back to
  the exact COBOL source line it came from) is now source-label-precise (every fact
  `spec_extractor` emits names the exact program or copybook it came from) but not yet
  line-precise — see ADR-0006 for why, and what closing this gap would require
  (`parsing/cobol_parser.py` carrying line numbers through field/paragraph extraction).
- **No `low_confidence_rule` gate item has ever been produced by a real model.**
  `LOW_CONFIDENCE_THRESHOLD = 0.7` (ADR-0008) and `spec_critic`'s cheaper model tier (ADR-0004)
  are both still uncalibrated for the same reason as the gap above: every critique in every test
  is a fake returning a chosen score. The threshold is exercised at its boundary by construction,
  but whether `0.7` separates anything meaningful in practice is unknown.
- **Control-plane's real gate has not been exercised against a real `design.json`.** The artifact
  now exists and is schema-valid, which was the missing half; wiring control-plane's side is
  Track P1 / Milestone C5. Until then, "control-plane's durable gate reviews this" is a design
  claim backed by control-plane's own verified gate machinery, not by an observed end-to-end run
  through it.
