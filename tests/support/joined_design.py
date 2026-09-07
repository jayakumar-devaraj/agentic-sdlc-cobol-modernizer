"""The live `CBACT04C` designs with their split enrichment folded in (ADR-0076).

**Why this is a function and not a fixture file.** `tests/fixtures/live_designs/` holds what models
actually wrote, pinned; those files are evidence and rewriting one to make a test pass would destroy
the only record of the shape ADR-0076 exists to refuse. So the correction is applied here, in the
open, where a reader can see exactly what changed and that nothing else did.

**The fold is a deletion.** A design that splits the enrichment writes
`TranCatBal -> TranCatBalWithAccount -> RatedCategoryBalance` and then a step that consumes
`RatedCategoryBalance` -- and that last step already declares the full composite as its `input_type`,
because the chain rule (ADR-0072) requires it. So removing the join steps is the whole correction:
the consuming step becomes the head of the chain, `reads_a_file` answers yes for it, and
`reader_path_parameters` builds it the four-path reader that reads the driving stream and every
lookup beside it. Nothing has to be rewritten, which is the strongest evidence available that the
renderer already supported this shape and only the design shape was in the way.
"""

from __future__ import annotations

from cobol_modernizer.core.contracts import BatchJobDesign, UnifiedDesign
from cobol_modernizer.rendering.java_job import is_chunk_step, unsupplied_components


def fold_the_join(
    job: BatchJobDesign, design: UnifiedDesign, program_name: str = "CBACT04C"
) -> BatchJobDesign:
    """`job` with every step that joins a keyed lookup it cannot read removed.

    The steps that remain are the ones ADR-0076 leaves renderable. Raises if there is nothing to
    fold: a caller asking for the corrected form of a design that was never bent is asking the wrong
    question, and silently returning the input would let a test claim to exercise the correction
    while exercising the original.
    """
    joins = [
        step.step_name
        for step in job.steps
        if is_chunk_step(step) and unsupplied_components(step, design, program_name)
    ]
    if not joins:
        raise AssertionError(
            f"job {job.job_name!r} has no split enrichment to fold -- this helper corrects the "
            "ADR-0076 shape, and a design without it needs no correction"
        )
    return job.model_copy(
        update={"steps": [step for step in job.steps if step.step_name not in joins]}
    )
