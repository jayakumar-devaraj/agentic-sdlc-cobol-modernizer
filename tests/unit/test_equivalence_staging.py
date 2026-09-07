"""`equivalence.staging` puts the oracle's inputs where the generated job reads them (ADR-0075).

**The claim these tests exist to keep honest.** `harness.compare_project_output` looks for
`roundtrip/output/transact.dat` and `roundtrip/input/acctdata-stage1.dat`, and the rendered bindings
resolve `${cobol.file.base}` to `data`. Those two never met, and the only thing that had ever made
them meet was a hand-written `HandWrittenRemainder` in the round-trip fixture, which the pipeline
does not have. `test_the_overrides_land_where_the_harness_looks` is the assertion that they now do,
and it compares against `harness`'s own constants rather than restating the paths -- a copy of them
here would agree with itself while disagreeing with the comparison.

**The precondition is asserted before the claim** (the ADR-0073 lesson). A staging test that ran
against a design binding no lookups would pass while exercising nothing, so
`test_the_lookups_are_staged_when_a_design_binds_them` first asserts that widening the step's
`input_type` does bind them, and only then that they are staged.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.core.contracts import DesignDocument
from cobol_modernizer.core.package_data import ORACLE_ROOT
from cobol_modernizer.equivalence.harness import ACCOUNT_OUTPUT, TRANSACTION_OUTPUT
from cobol_modernizer.equivalence.staging import (
    StagingError,
    stage_as_fixed_records,
    stage_oracle_inputs,
)
from cobol_modernizer.rendering.java_file_bindings import file_binding_properties

LIVE_DESIGNS = Path(__file__).resolve().parents[1] / "fixtures" / "live_designs"
TENANT_SAMPLE = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
ORACLE = ORACLE_ROOT / "CBACT04C"

#: Both live `CBACT04C` designs that reached a rendered, compiled job. Parameterised rather than
#: picking one, because a fact true of only the design it was written against is what "closed for
#: one instance" means in this repository.
LIVE = ["cbact04c-design-step56.json", "cbact04c-design-step58.json"]


def _design(name: str):
    document = DesignDocument.model_validate_json((LIVE_DESIGNS / name).read_text(encoding="utf-8"))
    design = document.unified_design
    assert design is not None
    return design, design.batch_jobs[0]


@pytest.mark.parametrize("name", LIVE)
def test_the_overrides_land_where_the_harness_looks(tmp_path: Path, name: str) -> None:
    """The two files the differential opens are the two the job is pointed at.

    This is the whole of what ADR-0075's staging has to get right: every other property may point
    anywhere sensible, but these two are what `compare_project_output` opens, and a job writing one
    byte elsewhere reports as `not_run` with nothing saying why.
    """
    design, job = _design(name)
    project = tmp_path / "target-project"

    overrides = stage_oracle_inputs(
        job, design, job.program_name, project, oracle_dir=ORACLE, worktree_root=TENANT_SAMPLE
    )

    assert Path(overrides["cobol.file.transact"]) == project / TRANSACTION_OUTPUT
    assert Path(overrides["cobol.file.acctfile"]) == project / ACCOUNT_OUTPUT


@pytest.mark.parametrize("name", LIVE)
def test_only_what_the_job_binds_is_staged(tmp_path: Path, name: str) -> None:
    """Staging follows `file_binding_properties` exactly -- no extra file, no missing one.

    A staged file the job never opens would suggest the job reads it, which is the misreading this
    whole record exists to prevent: both live designs strand `XREFFILE` and `DISCGRP`, and a
    `cardxref.dat` sitting in `roundtrip/input/` would hide that.
    """
    design, job = _design(name)
    project = tmp_path / "target-project"

    overrides = stage_oracle_inputs(
        job, design, job.program_name, project, oracle_dir=ORACLE, worktree_root=TENANT_SAMPLE
    )

    assert set(overrides) == set(file_binding_properties(job, design, job.program_name))
    staged = {p.name for p in (project / "roundtrip" / "input").iterdir()}
    assert staged == {"tcatbal-posted.dat", "acctdata-stage1.dat"}
    # An output is not staged: the job creates it, and a pre-existing file would let a job that
    # wrote nothing report a comparison against whatever was already there.
    assert list((project / "roundtrip" / "output").iterdir()) == []


@pytest.mark.parametrize("name", LIVE)
def test_both_live_designs_strand_the_two_lookup_files(name: str) -> None:
    """`XREFFILE` and `DISCGRP` are declared, resolvable, and bound to nothing.

    Recorded as a test rather than prose because it is the reason a run of either design cannot
    match the oracle, and because it is the kind of fact that gets quietly fixed and then quietly
    regresses. When a design binds them, this test fails and says so -- which is the good direction.
    """
    design, job = _design(name)
    declared = {path.assign_to for path in design.file_access_paths if path.program_name == "CBACT04C"}
    assert {"XREFFILE", "DISCGRP"} <= declared

    bound = set(file_binding_properties(job, design, job.program_name))
    assert "cobol.file.xreffile" not in bound
    assert "cobol.file.discgrp" not in bound


def test_the_lookups_are_staged_when_a_design_binds_them(tmp_path: Path) -> None:
    """Widening a step's input to the composite binds all four reads -- and staging supplies them.

    **The precondition is asserted first.** Without it this test would pass against the design as
    written, staging two files and claiming to have exercised the corpus path it never reached.
    """
    design, job = _design("cbact04c-design-step58.json")
    step = next(s for s in job.steps if s.step_name == "resolveAccountAndCardXref")
    step.input_type = "RatedCategoryBalance"

    bound = file_binding_properties(job, design, job.program_name)
    assert {"cobol.file.xreffile", "cobol.file.discgrp"} <= set(bound), (
        "precondition: a composite input must bind the lookups, or this test stages nothing new"
    )

    project = tmp_path / "target-project"
    overrides = stage_oracle_inputs(
        job, design, job.program_name, project, oracle_dir=ORACLE, worktree_root=TENANT_SAMPLE
    )

    staged = project / "roundtrip" / "input"
    assert {p.name for p in staged.iterdir()} == {
        "tcatbal-posted.dat",
        "acctdata-stage1.dat",
        "cardxref.dat",
        "discgrp.dat",
    }
    # Framed as records, not copied as lines: the corpus ships 36-character text for a 50-byte
    # record, and a reader handed the text reads the second record out of the middle of the first.
    assert (staged / "cardxref.dat").stat().st_size % 50 == 0
    assert (staged / "discgrp.dat").stat().st_size % 50 == 0
    assert Path(overrides["cobol.file.xreffile"]) == staged / "cardxref.dat"


def test_a_bound_file_with_no_source_is_refused(tmp_path: Path) -> None:
    """A file the job opens and staging cannot supply stops the run, naming it.

    Refused rather than skipped, because the alternative is a job that starts and dies on a missing
    path with nothing linking the failure back to staging.
    """
    design, job = _design("cbact04c-design-step58.json")
    path = next(p for p in design.file_access_paths if p.assign_to == "TCATBALF")
    path.assign_to = "SOMEOTHERFILE"

    with pytest.raises(StagingError, match="SOMEOTHERFILE"):
        stage_oracle_inputs(
            job,
            design,
            job.program_name,
            tmp_path / "target-project",
            oracle_dir=ORACLE,
            worktree_root=TENANT_SAMPLE,
        )


def test_short_lines_are_padded_to_the_record(tmp_path: Path) -> None:
    """A COBOL `WRITE` produces fixed-length records; the trailing `FILLER` simply was not written."""
    source = tmp_path / "in.txt"
    source.write_text("ab\ncd\n", encoding="latin-1")
    destination = tmp_path / "out.dat"

    stage_as_fixed_records(source, destination, 5)

    assert destination.read_bytes() == b"ab   cd   "


def test_a_line_longer_than_the_record_raises(tmp_path: Path) -> None:
    """Truncating would produce something plausible and wrong, which is the worst available answer."""
    source = tmp_path / "in.txt"
    source.write_text("abcdefg\n", encoding="latin-1")

    with pytest.raises(ValueError, match="7-character line for a 5-byte record"):
        stage_as_fixed_records(source, tmp_path / "out.dat", 5)


def test_the_staged_account_file_is_the_oracles_own(tmp_path: Path) -> None:
    """`acctdata-stage1.dat` comes from the oracle, not the corpus -- the state *between* stages.

    `CBTRN02C` rewrites the account file before `CBACT04C` reads it, so the corpus original is the
    wrong bytes. Asserted because the two files are the same size and a mix-up would compare
    cleanly against the wrong baseline.
    """
    design, job = _design("cbact04c-design-step58.json")
    project = tmp_path / "target-project"

    stage_oracle_inputs(
        job, design, job.program_name, project, oracle_dir=ORACLE, worktree_root=TENANT_SAMPLE
    )

    staged = (project / ACCOUNT_OUTPUT).read_bytes()
    assert staged == (ORACLE / "acctdata-stage1.dat").read_bytes()
    assert staged != (TENANT_SAMPLE / "app" / "data" / "ASCII" / "acctdata.txt").read_bytes()
