"""`rendering/java_records.py` against the real Track C entities, not hand-built ones.

The point of a deterministic renderer is that its output is a pure function of `design.json`, so
these tests run it over the entities `build_domain_entities` really produces from the four real
Track C programs and their real copybooks -- the same path `generate` will take. A test that
rendered a hand-written `DomainEntity` would prove the template's syntax and nothing about whether
it survives the field shapes CardDemo actually contains.

No model is involved anywhere in this module, which is the property under test as much as any
individual assertion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import DomainEntity, DomainField, ProgramDesignEntry
from cobol_modernizer.nodes.solution_architect import build_domain_entities
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec
from cobol_modernizer.rendering.java_records import (
    UnrenderableJavaNameError,
    render_record,
)

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"
ALL_PROGRAMS = ["CBACT04C", "CBCUS01C", "CBACT01C", "CBTRN02C"]
PACKAGE = "com.modernized.batch.domain"


def _faithful_narrate(program_name: str):
    def narrate(model: str, system_prompt: str, user_content: str) -> str:
        return user_content.split(f'<untrusted-cobol-source label="{program_name}">')[0]

    return narrate


def _no_op_critique(model: str, system_prompt: str, user_content: str) -> str:
    return "[]"


@pytest.fixture(scope="module")
def real_entities() -> list[DomainEntity]:
    entries: list[ProgramDesignEntry] = []
    for program_name in ALL_PROGRAMS:
        extraction = extract_spec(
            FIXTURE_ROOT, program_name, narrate=_faithful_narrate(program_name)
        )
        critique = critique_spec(FIXTURE_ROOT, extraction, critique=_no_op_critique)
        entries.append(
            ProgramDesignEntry(
                program_name=program_name, spec_extraction=extraction, critique=critique
            )
        )
    return build_domain_entities(FIXTURE_ROOT, entries)


# --- Every real entity renders, and renders the same way twice --------------------------------


def test_every_real_track_c_entity_renders(real_entities):
    assert real_entities, "fixture produced no entities; the rest of this module proves nothing"
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        assert source.startswith(f"package {PACKAGE};")
        assert f"public record {entity.name}(" in source


def test_rendering_is_deterministic(real_entities):
    # The property that makes rendered output reviewable once instead of per-run.
    for entity in real_entities:
        assert render_record(entity, package=PACKAGE) == render_record(entity, package=PACKAGE)


def test_every_real_field_appears_with_its_computed_type(real_entities):
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        for field in entity.fields:
            assert f"{field.java_type} {field.java_field_name}" in source


# --- Provenance is in the generated file, not only in a side channel --------------------------


def test_each_record_names_its_source_copybook_and_using_programs(real_entities):
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        assert f"generated from copybook {entity.source_copybook}" in source
        for program in entity.used_by_programs:
            assert program in source


def test_each_component_names_the_cobol_field_it_came_from(real_entities):
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        for field in entity.fields:
            assert f"@param {field.java_field_name} from COBOL {field.cobol_field_name}" in source


def test_numeric_components_carry_the_computed_precision_and_scale(real_entities):
    # pic_mapper computed these; the renderer must not round-trip them through a model or restate
    # them approximately. A wrong scale on a currency column looks exactly like a right one.
    checked = 0
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        for field in entity.fields:
            if field.precision is None:
                continue
            expected = f"PIC precision {field.precision}, scale {field.scale}"
            assert expected in source
            checked += 1
    assert checked > 0, "no numeric fields in the real corpus; this test would be vacuous"


# --- Imports track the real field types -------------------------------------------------------


def test_bigdecimal_is_imported_exactly_when_a_component_needs_it(real_entities):
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        needs_it = any(field.java_type == "BigDecimal" for field in entity.fields)
        assert ("import java.math.BigDecimal;" in source) is needs_it


# --- Illegal Java names fail loudly rather than being quietly mangled -------------------------


def _field(java_name: str) -> DomainField:
    return DomainField(
        java_field_name=java_name,
        cobol_field_name="SOME-COBOL-NAME",
        java_type="String",
        precision=None,
        scale=None,
        signed=False,
    )


def _entity_with_field(java_name: str) -> DomainEntity:
    return DomainEntity(
        name="Account",
        source_copybook="CVACT01Y",
        used_by_programs=["CBACT01C"],
        fields=[_field(java_name)],
    )


@pytest.mark.parametrize("reserved", ["class", "new", "int", "static", "null", "true"])
def test_a_reserved_word_field_name_raises_rather_than_being_renamed(reserved):
    # Renaming to dodge the collision (`class` -> `class_`) is the quiet fix that makes generated
    # code stop matching the COBOL it claims to implement. The COBOL name is in the message so the
    # report points at the source, not at the generated file.
    with pytest.raises(UnrenderableJavaNameError, match="reserved word"):
        render_record(_entity_with_field(reserved), package=PACKAGE)


@pytest.mark.parametrize("malformed", ["2ndField", "has-a-hyphen", "has space", ""])
def test_a_malformed_field_name_raises(malformed):
    with pytest.raises(UnrenderableJavaNameError, match="not a legal Java identifier"):
        render_record(_entity_with_field(malformed), package=PACKAGE)


def test_the_error_names_the_cobol_field_so_the_report_points_at_the_source():
    with pytest.raises(UnrenderableJavaNameError, match="SOME-COBOL-NAME"):
        render_record(_entity_with_field("class"), package=PACKAGE)


def test_an_entity_with_no_fields_renders_an_empty_record_rather_than_invalid_java():
    # solution_architect drops these before they reach the renderer (ADR-0010: a copybook that
    # maps zero fields produces no entity), so this is the defensive branch. It is covered rather
    # than excused, because "unreachable today" and "unreachable" differ by one refactor.
    empty = DomainEntity(
        name="Placeholder", source_copybook="CVEMPTY", used_by_programs=["CBACT04C"], fields=[]
    )
    source = render_record(empty, package=PACKAGE)
    assert "public record Placeholder() {}" in source
    assert "import java.math.BigDecimal;" not in source


def test_a_reserved_entity_name_raises_too():
    entity = DomainEntity(
        name="class", source_copybook="CVACT01Y", used_by_programs=["CBACT01C"], fields=[]
    )
    with pytest.raises(UnrenderableJavaNameError, match="Entity name"):
        render_record(entity, package=PACKAGE)


# --- No persistence mapping is asserted -------------------------------------------------------


def test_no_jpa_identity_is_invented(real_entities):
    # A copybook does not declare a primary key. Guessing one from a name that looks key-ish is
    # exactly the class of inference this repo fails loudly on; step 40a decides it against the
    # real data files. Until then nothing here may claim a mapping exists.
    for entity in real_entities:
        source = render_record(entity, package=PACKAGE)
        for annotation in ("@Id", "@Entity", "@Table", "@Column", "jakarta.persistence"):
            assert annotation not in source
