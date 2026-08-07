# 0007 - Confidence-score composition is deterministic-first, weakest-link, no repair-retry yet

## Context

ADR-0001's consequences state plainly that `spec_critic`'s confidence score is "the only
independent check on extraction quality" the human-in-the-loop gate sees before a `design.json`
gets approved for Track C. That framing — a human is going to trust one number — makes three
implementation questions in `nodes/spec_critic.py` genuinely load-bearing, not stylistic:

1. `spec_critic` runs both mechanical fidelity checks (does `spec.md`'s Field Reference table
   actually match what `pic_mapper` computed? does it mention every paragraph and every flagged
   unsupported construct?) and a model call that scores each narrated business rule's own
   faithfulness. If the mechanical checks find a real, provable discrepancy but the model's scores
   are all high, which one should `overall_confidence` reflect?
2. The critic model returns a confidence per individual rule, not one number. Nine well-supported
   rules and one badly-supported one could aggregate to a comfortable "0.85 average" that a human
   skimming a gate item would read as "looks fine" — is that the right signal?
3. The critic model's structured JSON response can be malformed (not JSON, missing a field, an
   out-of-range confidence). Milestone C3 (plan step 35) will build a real structured-output
   repair-retry loop for `solution_architect`'s `design.json` — should `spec_critic` build a
   smaller version of the same thing now, or wait?

## Decision

**1. Deterministic fidelity checks are evaluated first and can force `overall_confidence` to
`0.0` outright, regardless of the critic model's own per-rule scores.** If
`check_field_reference_fidelity`, `check_paragraph_coverage`, or
`check_unsupported_constructs_carried_forward` finds anything, `overall_confidence` is `0.0` —
full stop, before the critic model's scores are even consulted for the final number (they're
still recorded in `rule_confidence` for a human to read, just not allowed to raise the score back
up). An "independent check" that a fluent, confident-sounding critique could talk its way past
despite a mechanically-provable defect in the thing it's supposed to be checking would not be
independent at all — it would just be a second layer of the same failure mode `spec_extractor`'s
narration could already have.

**2. `overall_confidence` is `min()` of the critic model's per-rule scores, never a mean.** A
human gate reviewer reading one number should read "the weakest claim in this spec is at least
this trustworthy," not "the typical claim is." Averaging would let nine confidently-correct rules
dilute one genuinely shaky one into an unremarkable-looking blended score — exactly the failure
mode a single trusted number exists to prevent.

**3. No repair-retry loop for `spec_critic`'s own structured output yet.** A malformed response
raises `SpecCritiqueParseError` and propagates — it is not caught, defaulted to an empty rule
list, or silently retried. Milestone C3's repair-retry loop (plan step 35) is real, separately
scoped work for `solution_architect`'s `design.json`; building a smaller, `spec_critic`-specific
version of it now would be exactly the kind of premature abstraction this repo's own conventions
warn against — two similar-but-not-identical retry mechanisms are harder to reason about than one
shared one built once, deliberately, when its actual contract is known. One pragmatic
accommodation, not a retry: `_parse_rule_confidence` strips a leading/trailing ` ```json ` fence
if the model added one anyway, despite the prompt explicitly forbidding it — stripping a
formatting wrapper around an otherwise-valid JSON payload is not "guessing at data" the way a
retry-with-a-different-prompt would be.

## Consequences

A `spec_critic` gate item is legible at a glance: `overall_confidence == 0.0` always means "a
mechanically-provable defect exists, go read `fidelity_issues`," never "the model just wasn't
sure." Any other value is the critic model's own weakest-link judgment on rule fidelity, with
`fidelity_issues` empty by construction.

This means a single false-positive fidelity check (e.g., a legitimate narration variation that
`check_field_reference_fidelity`'s admittedly-permissive table regex fails to parse) would
currently sink `overall_confidence` to `0.0` even when the underlying narration is fine. Accepted
for now, consistent with this repo's "fail loudly rather than guess" pattern — a human reviewing a
`0.0` score can check `fidelity_issues` and see it's a parsing artifact, which is a far safer
failure mode than a real defect silently passing because the check was made lenient to avoid this
case.

Until Milestone C3's structured-output repair-retry loop exists, a real invocation that hits a
malformed critic response fails the whole `design` phase rather than degrading gracefully — an
accepted, honest trade-off (see `docs/qa/verification-report.md`), not a gap to paper over.
