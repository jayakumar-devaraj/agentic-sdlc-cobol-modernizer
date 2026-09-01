"""What a real `design.json` has to be for control-plane to pause on it (gap G7).

**The seam is tested from both sides, each in its own repo.** ADR-0003 specifies the exchange, and
until now it was verified only here, on the producing side -- control-plane had never received one.
A spike over there (`tests/test_specialist_artifact_gate.py`) now drives an opaque artifact through
the real durable checkpointer and a real `interrupt()` gate, and settles what the receiving side
requires. This module asserts that a **real** `design.json` meets those requirements.

The split is deliberate rather than convenient. ADR-0001 forbids tenant vocabulary in control-plane
and its own ADR-0001 says the platform ships no tenant fixtures, so a CardDemo `design.json` cannot
live over there -- the artifact in that spike is deliberately about nothing. The realism has to be
asserted here, where the vocabulary belongs. Neither half is sufficient alone, which is exactly why
the gap survived this long: each side looked complete from inside itself.

Every requirement below is one the spike established by running, not one either repo assumed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import (
    DesignDocument,
    ProgramDesignEntry,
    build_design_document,
)
from cobol_modernizer.nodes.spec_critic import critique_spec
from cobol_modernizer.nodes.spec_extractor import extract_spec

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
PROGRAM = "CBACT04C"


@pytest.fixture(scope="module")
def document() -> DesignDocument:
    """A real `design.json`, built the way `design` builds one."""

    def narrate(model, system_prompt, user_content):
        return user_content.split(f'<untrusted-cobol-source label="{PROGRAM}">')[0]

    extraction = extract_spec(FIXTURE_ROOT, PROGRAM, narrate=narrate)
    critique = critique_spec(FIXTURE_ROOT, extraction, critique=lambda m, s, u: "[]")
    return build_design_document(
        [ProgramDesignEntry(program_name=PROGRAM, spec_extraction=extraction, critique=critique)]
    )


@pytest.fixture(scope="module")
def serialized(document) -> str:
    return document.model_dump_json()


def test_the_document_is_plain_json_with_no_type_control_plane_must_declare(serialized):
    """The spike's first finding: the artifact crosses as plain JSON or not at all.

    `build_serde` over there restricts msgpack to control-plane's *own* state models. A
    `design.json` that only round-tripped as a Pydantic instance would have needed a package change
    in the other repo before it could even be persisted -- and therefore before a gate could pause
    on it. It does not: this is JSON containing nothing but objects, arrays, strings, numbers,
    booleans and nulls.
    """
    reloaded = json.loads(serialized)
    assert json.loads(json.dumps(reloaded)) == reloaded

    def primitive(value) -> bool:
        if isinstance(value, dict):
            return all(isinstance(k, str) and primitive(v) for k, v in value.items())
        if isinstance(value, list):
            return all(primitive(v) for v in value)
        return value is None or isinstance(value, (str, int, float, bool))

    assert primitive(reloaded)


def test_it_is_self_contained_and_names_no_local_path(serialized):
    """ADR-0003 requires the artifact stand alone; a gate may read it on another machine.

    A path from the machine that produced it is the specific way "self-contained" fails quietly:
    the document still parses, and the reviewer following the reference finds nothing. This repo's
    own documentation standard forbids the same thing in committed files, for the same reason.
    """
    for marker in ("C:\\\\", "C:/", "/home/", "/Users/", "file://", "\\\\\\\\"):
        assert marker not in serialized, f"design.json leaks a local path ({marker})"


def test_the_gate_facts_travel_with_it_and_carry_no_verdict(document):
    """The spike showed the gate surfaces facts and decides nothing. This is the other half.

    Each item is a category, a program and prose. There is no severity, no score and no
    `blocks: true` -- whether a finding should stop anything is control-plane's gate policy
    (ADR-0008 decision 3), and a specialist that shipped a verdict would be making that call for
    every deployment that ever consumes it.
    """
    assert document.gate_items, "CBACT04C has real findings; an empty list would be the bug"

    for item in document.gate_items:
        fields = set(item.model_dump())
        assert fields == {"category", "program_name", "summary", "detail"}
        assert not {"severity", "score", "blocks", "status", "decision"} & fields


def test_a_reviewer_can_see_what_they_are_approving_without_this_repo(serialized):
    """The spike reads the artifact back from the store *before* anyone resumes.

    That is what an approval interface would render, so the fields it needs must be present in the
    document itself rather than reconstructible only by re-running the specialist.
    """
    reloaded = json.loads(serialized)

    assert reloaded["schema_version"], "a consumer must be able to tell which contract this is"
    assert reloaded["generated_at"], "and when it was produced"
    assert [entry["program_name"] for entry in reloaded["programs"]] == [PROGRAM]
    # The narration a reviewer actually reads, reachable without re-running extraction.
    assert reloaded["programs"][0]["spec_extraction"]["spec_markdown"].strip()


def test_the_schema_version_is_stated_rather_than_implied(document):
    # A consumer that guessed the contract version from the shape would break silently on the next
    # additive change. This session moved it 2.0.0 -> 3.0.0 twice over; the field is how the other
    # side notices.
    assert document.schema_version.count(".") == 2
    major = int(document.schema_version.split(".")[0])
    assert major >= 3, "schema_version must move when a required field is added (ADR-0022)"
