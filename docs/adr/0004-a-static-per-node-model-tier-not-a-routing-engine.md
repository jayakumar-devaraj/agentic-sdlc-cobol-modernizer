# 0004 - A static per-node model tier, not a routing engine

## Context

Track P1 (in `agentic-sdlc-control-plane`) routes *which specialist CLI* control-plane invokes for
a given scenario type — it has no visibility into, or control over, what happens inside this
repo's own five-node pipeline once invoked. That's a genuinely separate question this ADR answers.

The five nodes have different task shapes. `spec_extractor`, `solution_architect`, and
`modernization_engineer` produce the artifacts everything downstream trusts — a wrong business-rule
extraction, a wrong domain design, or wrong generated code all propagate. `spec_critic` and
`build_validator` are narrower: re-checking already-extracted rules against source, and parsing a
compiler's error output into a structured diagnosis. Using one fixed model for all five (the same
shape as control-plane's own `coder.py`, which the original platform code review found hardcodes
one model for everything) would carry every node's cost and latency at the tier the most demanding
node needs, even for the narrower tasks — this is the concrete form of the Pillar 1/24 gap
(Model Strategy & Routing, Cost & Latency Optimization) found absent platform-wide.

The alternative extreme — a dynamic routing engine that decides a model at runtime based on some
scoring heuristic — is a bigger investment than this repo's actual scope warrants. There are
exactly five node types, known and fixed in advance. This isn't an open-ended agent that needs
adaptive routing; it's a fixed pipeline.

## Decision

**A static, config-driven mapping from node name to model — not a dynamic routing engine.** A
config file (`config/model_routing.yaml`, landing alongside the first node that reads it) maps each
of the five node names to a model identifier, read once per invocation. This mirrors the shape of
Track P1's own `config/scenario_specialists.yaml` deliberately — two config-driven routing
mechanisms with the same shape are easier to reason about together than two ad-hoc ones, even
though they answer different questions (which specialist vs. which model within one).

**Tentative default tiers, not benchmarked final numbers**: `spec_extractor`, `solution_architect`,
and `modernization_engineer` default to the strongest available coding/reasoning model, since they
produce the artifacts everything else trusts. `spec_critic` and `build_validator` default to a
cheaper/faster tier, since their tasks are narrower. **`spec_critic` is a deliberate exception to
finalize carefully**: per ADR-0001's consequences, its confidence score is "the only independent
check on extraction quality" the human-in-the-loop gate sees. Once Milestone C2's golden fixtures
exist, this repo will empirically compare the cheaper tier's critique quality against the stronger
one before committing to it for `spec_critic` specifically — a cheap-but-wrong confidence score is
worse than an expensive-and-right one, and asserting a cost saving here without measuring it would
be exactly the unverified doc claim this platform's own conventions forbid.

## Consequences

This ADR commits to the *mechanism* (static config-driven mapping), not to specific tier
assignments — those are tentative defaults until real nodes exist and golden fixtures make an
actual quality comparison possible, not benchmarked claims dressed up as decisions.

Per-call (not just per-node) routing — e.g. escalating `spec_critic` to a stronger model only when
its own confidence comes back low — is a real possible enhancement but explicitly out of scope
here. A static per-node mapping is the simplest thing that could plausibly work, not a placeholder
assumed to need replacing later.

This is scoped entirely to what happens *inside* one specialist invocation. It does not change, and
has no dependency on, Track P1's control-plane-side routing — the two are config-driven for the
same reason, not because either depends on the other.
