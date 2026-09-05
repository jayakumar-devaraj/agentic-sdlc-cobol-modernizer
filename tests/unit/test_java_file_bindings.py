"""`java_file_bindings` binds paths to the readers and writers that take them -- and only those.

**The regression this module exists for.** `render_job_wiring` has always asked
`aggregation_source` before `_has_file_source`, because a control-break step is fed by a rendered
aggregation over staged output rather than by a file. This module did not, and asked
`_has_file_source` unconditionally. The first live design to put a control break on
`postAccountInterest` therefore refused the **whole job's** wiring:

    UnrenderableReaderError: step 'postAccountInterest' needs exactly one driving stream and its
    input resolves to 0 (none)

Caught by a pre-flight check against the real design before a paid `generate` run, not by a test --
which is why the tests below exist now.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    BatchJobDesign,
    ProgramDesignEntry,
    UnifiedDesign,
)
from cobol_modernizer.nodes.solution_architect import (
    attach_control_breaks,
    build_domain_entities,
    build_file_access_paths,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_file_bindings import (
    bindings_class_name,
    file_binding_properties,
    property_name,
    render_application_properties,
    render_file_bindings,
)
from tests.support.interest_design import COMPLETE_STEP, COMPOSITE, OUTPUT_COMPOSITE, STEP
from tests.support.posting_design import POSTING
from tests.support.posting_design import STEP as POSTING_STEP

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
DOMAIN = "com.modernized.batch.domain"
READERS = "com.modernized.batch.reader"
WRITERS = "com.modernized.batch.writer"
JOBS = "com.modernized.batch.job"


@pytest.fixture(scope="module")
def design() -> UnifiedDesign:
    """The interest job with a control break attached, which is the shape that broke this."""
    extraction = extract_spec(FIXTURE_ROOT, "CBACT04C", narrate=lambda m, s, u: "narration")
    entry = ProgramDesignEntry(
        program_name="CBACT04C",
        spec_extraction=extraction,
        critique=critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]"),
    )
    entities = build_domain_entities(FIXTURE_ROOT, [entry])
    job = BatchJobDesign(
        job_name="interestJob",
        program_name="CBACT04C",
        domain_entities=[entity.name for entity in entities],
        steps=[STEP, COMPLETE_STEP, POSTING_STEP],
    )
    return UnifiedDesign(
        domain_entities=entities,
        composite_types=[COMPOSITE, OUTPUT_COMPOSITE, POSTING],
        batch_jobs=attach_control_breaks(FIXTURE_ROOT, [job], [entry]),
        rest_endpoints=[],
        file_access_paths=build_file_access_paths(FIXTURE_ROOT, [entry]),
    )


def _render(design: UnifiedDesign) -> str:
    return render_file_bindings(
        design.batch_jobs[0],
        design,
        "CBACT04C",
        package=JOBS,
        domain_package=DOMAIN,
        reader_package=READERS,
        writer_package=WRITERS,
    )


def test_a_control_break_step_gets_no_reader_binding(design):
    """**The regression.** Its reader is an in-memory aggregation and needs no path at all.

    Asserted as an absence rather than by the render merely succeeding, because the defect's first
    symptom was a refusal and its second would be a bean supplying an argument nothing asks for.
    """
    rendered = _render(design)

    assert "ItemReader<com.modernized.batch.domain.AccountInterestPosting>" not in rendered, (
        "a control-break step's reader is rendered as an aggregation over staged output and takes "
        "no Path; binding one would supply an argument the constructor does not have"
    )
    # The step is still wired -- it writes a file, so its *writer* is bound.
    assert "ItemWriter<com.modernized.batch.domain.Account>" in rendered


def test_the_file_reading_step_still_gets_its_reader_binding(design):
    """The guard must not have turned the whole thing off."""
    rendered = _render(design)

    assert f"ItemReader<{DOMAIN}.TranCatBalWithRate>" in rendered
    assert f"@Value(\"${{{property_name('TCATBALF')}}}\") Path tcatbalf" in rendered


def test_every_property_is_named_from_its_assign_to(design):
    """ADR-0067: derived from a declared fact, never chosen."""
    properties = file_binding_properties(design.batch_jobs[0], design, "CBACT04C")

    assert set(properties) == {
        "cobol.file.tcatbalf",
        "cobol.file.acctfile",
        "cobol.file.xreffile",
        "cobol.file.discgrp",
        "cobol.file.transact",
    }
    for name, default in properties.items():
        assert default.startswith("${cobol.file.base}/"), f"{name} defaults outside the base"


def test_the_account_file_is_one_property_for_both_reader_and_writer(design):
    """The in-place REWRITE, which falls out of `ASSIGN TO` rather than being arranged.

    `CBACT04C` reads accounts from `ACCTFILE` and rewrites them to the same file. The hand-written
    stopgap wired that by hand with a comment explaining it; here both sides resolve to
    `cobol.file.acctfile` because both come from the same `ASSIGN TO`, and an operator overriding
    one cannot accidentally move only half of it.
    """
    rendered = _render(design)

    assert rendered.count('@Value("${cobol.file.acctfile}")') == 2


def test_the_rendered_configuration_is_lazy(design):
    """Not a performance choice: a rendered reader opens its files in its constructor.

    `BatchApplication` component-scans this package, so an eager binding reads from disk while the
    context is starting and takes every `@SpringBootTest` in the generated project down with it.
    CI caught exactly that.
    """
    rendered = _render(design)

    assert "@Lazy" in rendered
    assert "import org.springframework.context.annotation.Lazy;" in rendered


def test_the_properties_file_documents_every_file_and_its_override(design):
    text = render_application_properties(design.batch_jobs[0], design, "CBACT04C")

    assert "cobol.file.base=data" in text
    for name in file_binding_properties(design.batch_jobs[0], design, "CBACT04C"):
        assert f"{name}=" in text
    # The override is shown, because a property list nobody knows how to change is not documentation.
    assert "--cobol.file.base=" in text


def test_the_class_is_named_from_the_job(design):
    assert bindings_class_name(design.batch_jobs[0]) == "InterestJobFileBindings"
