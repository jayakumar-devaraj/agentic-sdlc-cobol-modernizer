"""Putting the oracle's own inputs where a generated job will read them (ADR-0075).

**Why this exists at all.** `harness.compare_project_output` has been able to return a verdict since
ADR-0064 and has never returned one, because nothing gave it output to compare: `generate` rendered a
job, compiled it, and stopped. The mechanism to stage inputs and run the job existed only in
`tests/integration/test_hand_written_round_trip.py`, where a hand-written `HandWrittenRemainder`
supplied the file paths -- so the round trip proved the *hand-written* wiring runs and could say
nothing about what the pipeline generates. This module is that staging, moved to where the pipeline
can reach it.

**Staging and the property overrides come from one function on purpose.** A rendered binding resolves
`cobol.file.tcatbalf` against `${cobol.file.base}`, defaulting to `data`; this points it at the file
this module actually wrote. Deriving "what to stage" and "where to tell the job to look" separately is
how the two start disagreeing -- one list grows a file the other does not -- and the disagreement
would surface as `not_run` with no indication which half was wrong. `stage_oracle_inputs` returns the
overrides it made true.

**Only what the job binds is staged**, from `file_binding_properties`. A design that never binds
`XREFFILE` gets no `cardxref.dat`, because staging a file nothing opens would suggest the job reads
it. That is not hypothetical: both live `CBACT04C` designs to date bind three of the program's five
files, and the two they strand are the reason a run of this job cannot match the oracle.

**The tenant-shaped-data tension, named again rather than hidden.** `SOURCES` maps a COBOL `ASSIGN TO`
name to the corpus file that stands in for it, which is knowledge about CardDemo sitting in
otherwise domain-general machinery -- the same trade ADR-0064 made for the oracle itself and for the
same reason: the guarantee stays inside the artifact that makes the claim. Both roots are parameters,
so a caller with a better source is not blocked by the default.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from cobol_modernizer.core.contracts import BatchJobDesign, UnifiedDesign
from cobol_modernizer.rendering.java_file_bindings import (
    file_binding_properties,
    property_name,
)
from cobol_modernizer.rendering.java_job import _has_file_sink, plan_steps
from cobol_modernizer.rendering.java_writer import writer_path_parameters

logger = logging.getLogger(__name__)

#: Where a staged run reads and writes, relative to the project root. These are the two directories
#: `harness` looks in, and they are spelled there too -- deliberately, so that a change to either is
#: a visible disagreement rather than a silent one. `harness` is the authority; this follows it.
INPUT_DIR = Path("roundtrip") / "input"
OUTPUT_DIR = Path("roundtrip") / "output"


@dataclass(frozen=True)
class _Source:
    """Where one `ASSIGN TO` name's data comes from, and how it must be framed."""

    #: Name the file is staged under. `harness.ACCOUNT_OUTPUT` names `acctdata-stage1.dat`, so this
    #: is not free.
    staged_name: str
    #: `oracle` -- ships in the wheel; `corpus` -- lives in the tenant's own repository.
    origin: str
    #: Path within that root.
    relative: str
    #: Record length to frame a text corpus file at, or `None` when the bytes are already records.
    record_length: int | None = None


#: The corpus file standing in for each of `CBACT04C`'s files. `TRANSACT` is absent because nothing
#: stages an output: the job creates it, and a pre-existing one would let a job that wrote nothing
#: report a comparison.
SOURCES: dict[str, _Source] = {
    # The state *between* the two stages: `CBTRN02C` rewrites both of these before `CBACT04C` reads
    # them, so the corpus originals are the wrong bytes and the oracle's own are the right ones.
    "TCATBALF": _Source("tcatbal-posted.dat", "oracle", "tcatbal-posted.dat"),
    "ACCTFILE": _Source("acctdata-stage1.dat", "oracle", "acctdata-stage1.dat"),
    # Untouched by either program, so the corpus copies are the same bytes the oracle run saw --
    # but shipped as 36-character text lines for a 50-byte record, which is a distribution format
    # and not what a fixed-width reader expects. `LOADIDX` converts them in the real pipeline.
    "XREFFILE": _Source("cardxref.dat", "corpus", "cardxref.txt", 50),
    "DISCGRP": _Source("discgrp.dat", "corpus", "discgrp.txt", 50),
}

#: Where the tenant repository keeps the ASCII corpus, relative to its root.
CORPUS_RELATIVE = Path("app") / "data" / "ASCII"


class StagingError(Exception):
    """A file the job binds has no source to stage from.

    Raised rather than skipped, because a job started with one of its inputs missing fails at read
    time with a path in the message and nothing linking it back to here.
    """


def stage_as_fixed_records(source: Path, destination: Path, record_length: int) -> None:
    """Convert a line-terminated corpus file into the fixed-length records COBOL would have written.

    Short lines are padded: the trailing `FILLER` of a record simply was not written out. A line
    *longer* than the record is a different file than the one expected and raises, rather than being
    truncated into something plausible.
    """
    records = []
    for line in source.read_text(encoding="latin-1").replace("\r\n", "\n").split("\n"):
        if not line:
            continue
        if len(line) > record_length:
            raise ValueError(
                f"{source.name}: {len(line)}-character line for a {record_length}-byte record"
            )
        records.append(line.ljust(record_length))
    destination.write_bytes("".join(records).encode("latin-1"))


def stage_oracle_inputs(
    job: BatchJobDesign,
    design: UnifiedDesign,
    program_name: str,
    project: Path,
    *,
    oracle_dir: Path,
    worktree_root: Path,
) -> dict[str, str]:
    """Stage every file this job binds, and return the property overrides that point it at them.

    The returned mapping is `cobol.file.<name>` -> absolute path, ready to be handed to the rendered
    job as a property source. Absolute rather than relative because the job's working directory at
    test time is Maven's, not the caller's, and a relative path that resolves differently in the two
    is the failure mode this whole module exists to make visible.

    Raises:
        StagingError: the job binds a file `SOURCES` has no entry for and which is not an output.
    """
    bound = file_binding_properties(job, design, program_name)
    corpus = worktree_root / CORPUS_RELATIVE
    overrides: dict[str, str] = {}

    (project / INPUT_DIR).mkdir(parents=True, exist_ok=True)
    (project / OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    sinks = _sink_names(job, design, program_name)

    for assign_to in _assign_names(bound):
        source = SOURCES.get(assign_to)
        if source is None:
            # **"Not in `SOURCES`" is not the same question as "is an output"**, and treating it as
            # one made this branch unreachable: every unrecognised *input* was silently pointed at
            # an empty path under `roundtrip/output/`, so the refusal below could never fire and the
            # job would have started and died on a missing file. The design says which files this
            # job writes; that is what decides it.
            if assign_to not in sinks:
                raise StagingError(
                    f"the job reads {assign_to}, which this knows no source for. Add it to "
                    f"SOURCES, or the job will start and fail on a file nothing staged"
                )
            # Nothing stages an output. It is the job's to create, and pointing at a path under
            # `roundtrip/output/` is the whole of what this has to do for it.
            destination = project / OUTPUT_DIR / _output_name(assign_to)
            overrides[property_name(assign_to)] = str(destination)
            logger.info("staging: %s is an output -> %s", assign_to, destination)
            continue

        destination = project / INPUT_DIR / source.staged_name
        origin = oracle_dir if source.origin == "oracle" else corpus
        from_path = origin / source.relative
        if not from_path.is_file():
            raise StagingError(
                f"{assign_to} stages from {from_path}, which does not exist. The "
                f"{source.origin} root is {origin}"
            )
        if source.record_length is None:
            shutil.copy2(from_path, destination)
        else:
            stage_as_fixed_records(from_path, destination, source.record_length)
        overrides[property_name(assign_to)] = str(destination)
        logger.info("staging: %s <- %s", destination, from_path)

    return overrides


#: The one output name `harness` asserts. Spelled here rather than derived from the `ASSIGN TO`
#: because `harness.TRANSACTION_OUTPUT` is `transact.dat` and `TRANSACT.lower()` is `transact` --
#: a coincidence for this program that would silently produce the wrong name for the next one.
OUTPUT_NAMES = {"TRANSACT": "transact.dat"}


def _output_name(assign_to: str) -> str:
    return OUTPUT_NAMES.get(assign_to, f"{assign_to.lower()}.dat")


def _sink_names(job: BatchJobDesign, design: UnifiedDesign, program_name: str) -> set[str]:
    """Every `ASSIGN TO` this job *writes*, from the same plan the bindings were rendered from.

    Asked of the design rather than inferred from `SOURCES` not holding the name, because those are
    different questions: a file absent from `SOURCES` is one this does not know how to supply, which
    for an input is a refusal and for an output is the normal case.
    """
    renderable, _skipped, _staged = plan_steps(job, design, program_name)
    return {
        assign_to
        for step in renderable
        if _has_file_sink(step, design, program_name)
        for assign_to in writer_path_parameters(step, design, program_name)
    }


def _assign_names(bound: dict[str, str]) -> list[str]:
    """The `ASSIGN TO` names behind `file_binding_properties`, in its order.

    Read out of the rendered *value* (`${cobol.file.base}/ACCTFILE`) rather than the key, because
    the key is lowercased and recovering the name from it means guessing the original case back.
    That guess is right for every `ASSIGN TO` this program has and is still a second derivation of a
    fact the mapping already carries exactly -- which is how the staged files and the bound
    properties start being different lists.
    """
    return [value.rsplit("/", 1)[-1] for value in bound.values()]
