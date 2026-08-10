"""Deterministic Java rendering -- the half of `generate` that never calls a model.

Milestone C4's generation step is deliberately split in two, and this package is the larger half.
`UnifiedDesign.domain_entities` is already structured data whose numeric precision and scale were
*computed* by `tools/pic_mapper.py`, not narrated by a model. Turning that into a JPA entity class
is a mechanical transform, so it is rendered here rather than asked for: a model given the same
input could return something subtly different every run, and a wrong `scale` on a currency column
looks exactly like a right one -- the same reasoning that keeps `pic_mapper` model-free
(ADR-0001's deterministic-core posture, and the "do not improve" rule that `pic_mapper` must never
call a model).

The model's remaining job in `generate` is the part that genuinely requires judgment: the body of
a business rule. Everything structural around it -- entities, columns, repositories, wiring --
comes from here.

Two consequences worth stating, because they are the reason this package exists at all:

1. **Rendered output cannot hallucinate.** It is a pure function of `design.json`, so it is
   reproducible across runs and reviewable by reading this code once rather than re-reading every
   generated file.
2. **Rendered output does not need per-file human review**, which is the dominant cost of a
   migration at scale -- far above inference. Shrinking the LLM-authored surface shrinks the
   review surface with it.
"""
