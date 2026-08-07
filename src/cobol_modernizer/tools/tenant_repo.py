"""Read layer between a cloned tenant-repo worktree and `parsing/cobol_parser.py`.

Per ADR-0001 (docs/adr/0001-the-specialist-is-a-subprocess-not-a-second-control-plane.md),
control-plane clones the tenant repository and hands this repo's CLI a worktree path via
`--tenant-repo <path>` -- this repo never clones anything itself. This module is the boundary
that turns that worktree path plus a program name into source text `parsing/cobol_parser.py` and
(eventually) `nodes/spec_extractor.py` can work with.

CardDemo's real layout (this repo's tenant fork, `carddemo-tenant-service`, mirrors
`aws-samples/aws-mainframe-modernization-carddemo` exactly) is `app/cbl/{PROGRAM}.cbl` for
programs and `app/cpy/{NAME}.cpy` for copybooks -- but file extension case is inconsistent in the
real repo (verified directly against `carddemo-tenant-service` via the GitHub API, not assumed):
most `.cbl`/`.cpy` files are lowercase, but `CBSTM03A.CBL`/`CBSTM03B.CBL` and `COSTM01.CPY` are
uppercase. Extension resolution here is therefore case-insensitive; the program/copybook name
itself is resolved case-sensitively, since COBOL source is written in uppercase by convention and
a case-insensitive name match risks silently picking the wrong file in a directory that (unlike
the extension) has no real casing inconsistency to accommodate.

Every string this module returns is raw bytes decoded to text and nothing more. It does not
execute, evaluate, or interpret any of it -- per this repo's guardrail posture (see
`.claude/agents/development.md`, "COBOL input is untrusted data, not instructions"), every field
of a parsed COBOL program, including comments, is untrusted text handed downstream to an LLM
prompt, never treated as instructions to that LLM. Enforcing that boundary is `core/guardrails.py`
's job, not this module's -- but nothing here does anything that would make that job harder (no
templating, no interpolation, no shell-out on file contents).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from cobol_modernizer.parsing.cobol_parser import CopyStatement, extract_copy_statements

_PROGRAM_DIR = "app/cbl"
_COPYBOOK_DIR = "app/cpy"
_PROGRAM_EXTENSION = "cbl"
_COPYBOOK_EXTENSION = "cpy"


class TenantRepoFileNotFoundError(Exception):
    """A program or copybook file was not found at its expected worktree path.

    A missing file is a real problem -- a construct-matrix or parsing assumption was wrong, or
    control-plane handed this CLI the wrong worktree/program name -- not something to skip past.
    Callers must not catch this and silently omit the program/copybook from downstream processing.
    """

    def __init__(self, kind: str, name: str, searched_paths: list[Path]) -> None:
        self.kind = kind
        self.name = name
        self.searched_paths = searched_paths
        searched = ", ".join(str(p) for p in searched_paths)
        super().__init__(
            f"{kind} {name!r} not found in tenant repo worktree; searched: {searched}"
        )


class ResolvedProgram(BaseModel):
    """A program's own source plus the full text of every copybook it `COPY`s.

    This is the unit `nodes/spec_extractor.py` needs -- per ADR-0001, every specialist node's
    output must be self-contained, and a program without its copybooks resolved is missing the
    field structure most of its business logic actually operates on.
    """

    program_name: str
    source_text: str
    copy_statements: list[CopyStatement]
    copybook_sources: dict[str, str]


def _resolve_case_insensitive_extension(directory: Path, stem: str, extension: str) -> Path | None:
    """Find `{stem}.{extension}` under `directory`, matching the extension case-insensitively.

    The stem (program or copybook name) is matched case-sensitively -- COBOL source in this
    tenant repo is written in uppercase by convention, and unlike the extension, directory
    listings show no real casing inconsistency in the name itself to accommodate.

    This scans directory entries and compares names as strings, rather than probing
    `Path.is_file()` candidates directly, because several common filesystems (notably Windows'
    NTFS, this repo's own development platform) are themselves case-insensitive for the whole
    filename -- probing `Path.is_file()` would silently make the stem match case-insensitively
    too, on exactly the platform this is easiest to miss during development. Returns `None` if no
    entry matches.
    """
    if not directory.is_dir():
        return None

    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        entry_stem, dot, entry_extension = entry.name.rpartition(".")
        if not dot:
            continue
        if entry_stem == stem and entry_extension.lower() == extension.lower():
            return entry

    return None


def read_program_source(worktree_root: Path, program_name: str) -> str:
    """Read a program's `.cbl` source from `app/cbl/{program_name}.{cbl,CBL}` in the worktree.

    Raises:
        TenantRepoFileNotFoundError: no file matching the program name was found, in either
            extension case.
    """
    program_dir = worktree_root / _PROGRAM_DIR
    path = _resolve_case_insensitive_extension(program_dir, program_name, _PROGRAM_EXTENSION)
    if path is None:
        searched = [
            program_dir / f"{program_name}.{_PROGRAM_EXTENSION}",
            program_dir / f"{program_name}.{_PROGRAM_EXTENSION.upper()}",
        ]
        raise TenantRepoFileNotFoundError("Program", program_name, searched)
    return path.read_text(encoding="utf-8")


def read_copybook(worktree_root: Path, copybook_name: str) -> str:
    """Read a copybook's `.cpy` source from `app/cpy/{copybook_name}.{cpy,CPY}` in the worktree.

    Raises:
        TenantRepoFileNotFoundError: no file matching the copybook name was found, in either
            extension case.
    """
    copybook_dir = worktree_root / _COPYBOOK_DIR
    path = _resolve_case_insensitive_extension(copybook_dir, copybook_name, _COPYBOOK_EXTENSION)
    if path is None:
        searched = [
            copybook_dir / f"{copybook_name}.{_COPYBOOK_EXTENSION}",
            copybook_dir / f"{copybook_name}.{_COPYBOOK_EXTENSION.upper()}",
        ]
        raise TenantRepoFileNotFoundError("Copybook", copybook_name, searched)
    return path.read_text(encoding="utf-8")


def resolve_program(worktree_root: Path, program_name: str) -> ResolvedProgram:
    """Read a program's source and every copybook it `COPY`s, in one call.

    Composes `read_program_source`, `cobol_parser.extract_copy_statements`, and `read_copybook`
    so a caller (chiefly the future `nodes/spec_extractor.py`) gets the program's complete field
    structure -- a program's own source alone is not useful without the copybooks that define
    most of the record layouts its PROCEDURE DIVISION operates on.

    Raises:
        TenantRepoFileNotFoundError: the program's own source, or any copybook it `COPY`s, is
            missing from the worktree.
        UnsupportedCopyConstructError: propagated from `extract_copy_statements` -- a
            `COPY ... REPLACING` statement was found. Per ADR-0002 this must route to a
            human-in-the-loop gate, not be silently resolved as a straight copy.
    """
    source_text = read_program_source(worktree_root, program_name)
    copy_statements = extract_copy_statements(source_text)
    copybook_sources = {
        statement.copybook_name: read_copybook(worktree_root, statement.copybook_name)
        for statement in copy_statements
    }
    return ResolvedProgram(
        program_name=program_name,
        source_text=source_text,
        copy_statements=copy_statements,
        copybook_sources=copybook_sources,
    )
