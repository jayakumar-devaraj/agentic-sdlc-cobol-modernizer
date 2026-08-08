"""Tests for tenant_repo against a real (trimmed) tenant-repo worktree fixture.

`tests/fixtures/tenant_repo_sample/` mirrors `carddemo-tenant-service`'s real `app/cbl/` and
`app/cpy/` layout, populated with all four Track C programs and every copybook they `COPY`,
fetched directly:

    gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/cbl/{name}.cbl \
        --jq '.content' | base64 -d
    gh api repos/jayakumar-devaraj/carddemo-tenant-service/contents/app/cpy/{name}.cpy \
        --jq '.content' | base64 -d

`CBACT04C` (`CVTRA01Y`, `CVACT03Y`, `CVTRA02Y`, `CVACT01Y`, `CVTRA05Y`) was fetched first; the
other three Track C programs -- `CBCUS01C` (`CVCUS01Y`), `CBACT01C` (`CVACT01Y`, `CODATECN`), and
`CBTRN02C` (`CVTRA06Y`, `CVTRA05Y`, `CVACT03Y`, `CVACT01Y`, `CVTRA01Y`) -- were fetched afterward,
completing all four Track C programs the plan's Milestone C2 gate names. Every fetched file's git
blob SHA was verified byte-for-byte against the GitHub Contents API's own reported `sha`
(`git hash-object --no-filters` against the raw downloaded bytes) -- not assumed correct from a
successful download alone.

**A real corruption was caught during that verification, not after**: `CODATECN.cpy` is the one
file among these that genuinely has `CRLF` line endings in the real upstream repo (every other
fetched file is `LF`-only, confirmed via hex dump, not assumed uniform). `git add` on this
development machine (`core.autocrlf=true`) was silently converting its `CRLF` to `LF` while
staging -- the raw downloaded bytes matched the remote SHA exactly, but the *staged* git blob did
not, which would have committed a silently corrupted copy of real source despite every prior check
passing. Fixed with a repo-root `.gitattributes` (`tests/fixtures/tenant_repo_sample/** -text`)
disabling line-ending conversion entirely for this directory, on any machine's `autocrlf` setting
-- re-verified after adding it that every fixture file's staged blob SHA, old and new, still
matches the real repo exactly.

Real-repo casing, verified directly via the GitHub API against `app/cbl` and `app/cpy` (not
assumed): every file in the fixture below is lowercase `.cbl`/`.cpy`, matching the real repo for
these particular files. `CBSTM03A.CBL`/`CBSTM03B.CBL` and `COSTM01.CPY` are the real repo's
uppercase-extension outliers -- exercised here with a synthetic uppercase-extension file rather
than fetching those (out-of-Track-C) programs into this fixture.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cobol_modernizer.parsing.cobol_parser import UnsupportedCopyConstructError
from cobol_modernizer.tools.tenant_repo import (
    TenantRepoFileNotFoundError,
    read_copybook,
    read_program_source,
    resolve_program,
)

FIXTURE_ROOT = Path(__file__).parent.parent / "fixtures" / "tenant_repo_sample"

EXPECTED_COPYBOOKS = ["CVTRA01Y", "CVACT03Y", "CVTRA02Y", "CVACT01Y", "CVTRA05Y"]


# --- read_program_source -----------------------------------------------------------------------


def test_read_program_source_returns_real_content():
    source = read_program_source(FIXTURE_ROOT, "CBACT04C")
    assert "PROGRAM-ID.    CBACT04C." in source
    assert "PERFORM 1300-COMPUTE-INTEREST" in source


def test_read_program_source_missing_program_raises_clearly():
    with pytest.raises(TenantRepoFileNotFoundError) as exc_info:
        read_program_source(FIXTURE_ROOT, "CBNOPE99")
    assert exc_info.value.kind == "Program"
    assert exc_info.value.name == "CBNOPE99"


# --- read_copybook ------------------------------------------------------------------------------


def test_read_copybook_returns_real_content():
    source = read_copybook(FIXTURE_ROOT, "CVACT01Y")
    assert "01  ACCOUNT-RECORD." in source
    assert "ACCT-CURR-BAL" in source


def test_read_copybook_missing_copybook_raises_clearly():
    with pytest.raises(TenantRepoFileNotFoundError) as exc_info:
        read_copybook(FIXTURE_ROOT, "CVNOPE99Y")
    assert exc_info.value.kind == "Copybook"
    assert exc_info.value.name == "CVNOPE99Y"


# --- case-insensitive extension resolution -------------------------------------------------------


def test_uppercase_extension_program_resolves(tmp_path: Path):
    # Real repo has CBSTM03A.CBL / CBSTM03B.CBL alongside lowercase .cbl files -- a program name
    # whose real file happens to be uppercase-extension must not hard-fail.
    cbl_dir = tmp_path / "app" / "cbl"
    cbl_dir.mkdir(parents=True)
    (cbl_dir / "CBSTM03A.CBL").write_text(
        "       IDENTIFICATION DIVISION.\n       PROGRAM-ID. CBSTM03A.\n", encoding="utf-8"
    )
    source = read_program_source(tmp_path, "CBSTM03A")
    assert "PROGRAM-ID. CBSTM03A." in source


def test_uppercase_extension_copybook_resolves(tmp_path: Path):
    # Real repo has COSTM01.CPY uppercase-extension among mostly-lowercase .cpy files.
    cpy_dir = tmp_path / "app" / "cpy"
    cpy_dir.mkdir(parents=True)
    (cpy_dir / "COSTM01.CPY").write_text("       01  WS-DUMMY   PIC X.\n", encoding="utf-8")
    source = read_copybook(tmp_path, "COSTM01")
    assert "WS-DUMMY" in source


def test_missing_program_directory_raises_clearly_not_a_crash(tmp_path: Path):
    # No app/cbl directory at all in the worktree (e.g. an unexpected tenant-repo layout) must
    # still fail with the same clear error, not an unhandled FileNotFoundError/OSError.
    with pytest.raises(TenantRepoFileNotFoundError):
        read_program_source(tmp_path, "CBACT04C")


def test_subdirectory_entries_are_ignored_during_resolution(tmp_path: Path):
    # A stray subdirectory inside app/cbl sharing the program's stem must not be mistaken for
    # the program's source file.
    cbl_dir = tmp_path / "app" / "cbl"
    (cbl_dir / "CBACT04C.cbl").mkdir(parents=True)
    with pytest.raises(TenantRepoFileNotFoundError):
        read_program_source(tmp_path, "CBACT04C")


def test_extensionless_files_are_ignored_during_resolution(tmp_path: Path):
    # A file with no extension at all inside app/cbl must not confuse the rpartition-based
    # extension match.
    cbl_dir = tmp_path / "app" / "cbl"
    cbl_dir.mkdir(parents=True)
    (cbl_dir / "CBACT04C").write_text("not really COBOL", encoding="utf-8")
    with pytest.raises(TenantRepoFileNotFoundError):
        read_program_source(tmp_path, "CBACT04C")


def test_program_name_resolution_is_case_sensitive(tmp_path: Path):
    # The extension's case is forgiving; the program name itself is not -- a lowercase name
    # token must not silently match an uppercase-named file or vice versa.
    cbl_dir = tmp_path / "app" / "cbl"
    cbl_dir.mkdir(parents=True)
    (cbl_dir / "CBACT04C.cbl").write_text("PROGRAM-ID. CBACT04C.\n", encoding="utf-8")
    with pytest.raises(TenantRepoFileNotFoundError):
        read_program_source(tmp_path, "cbact04c")


# --- resolve_program ------------------------------------------------------------------------------


def test_resolve_program_returns_source_and_all_five_real_copybooks():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    assert resolved.program_name == "CBACT04C"
    assert "PROGRAM-ID.    CBACT04C." in resolved.source_text
    assert sorted(resolved.copybook_sources.keys()) == sorted(EXPECTED_COPYBOOKS)


def test_resolve_program_copy_statements_match_source_order():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    assert [s.copybook_name for s in resolved.copy_statements] == EXPECTED_COPYBOOKS


def test_resolve_program_copybook_sources_are_the_real_content():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT04C")
    assert "01  ACCOUNT-RECORD." in resolved.copybook_sources["CVACT01Y"]
    assert "TRAN-CAT-BAL-RECORD" in resolved.copybook_sources["CVTRA01Y"]


def test_resolve_program_missing_copybook_raises_clearly(tmp_path: Path):
    # A program that COPYs a copybook missing from the worktree is a real problem (a wrong
    # construct-matrix/parsing assumption), not something to silently skip past.
    cbl_dir = tmp_path / "app" / "cbl"
    (tmp_path / "app" / "cpy").mkdir(parents=True)
    cbl_dir.mkdir(parents=True)
    (cbl_dir / "CBFAKE01.cbl").write_text(
        "       WORKING-STORAGE SECTION.\n       COPY CVMISSING.\n", encoding="utf-8"
    )
    with pytest.raises(TenantRepoFileNotFoundError) as exc_info:
        resolve_program(tmp_path, "CBFAKE01")
    assert exc_info.value.kind == "Copybook"
    assert exc_info.value.name == "CVMISSING"


# --- Real-source byte fidelity, incl. line endings (see module docstring) ---------------------


def test_codatecn_retains_its_real_crlf_line_endings():
    # A canary for the exact corruption caught while adding this fixture: if .gitattributes ever
    # stops applying (or someone re-fetches this file without it in place), git would silently
    # normalize CODATECN.cpy's real CRLF to LF on the next add/checkout, and this is the cheapest
    # possible check that would catch it without needing network access to re-verify against
    # GitHub.
    raw = (FIXTURE_ROOT / "app" / "cpy" / "CODATECN.cpy").read_bytes()
    assert b"\r\n" in raw


def test_other_fixture_files_are_lf_only():
    # The inverse canary -- every other fetched file is genuinely LF-only in the real upstream
    # repo; a stray CRLF here would mean something (a manual edit, a bad re-fetch) altered real
    # source text this repo's own discipline requires staying byte-exact.
    for rel_path in [
        "app/cbl/CBACT04C.cbl",
        "app/cbl/CBCUS01C.cbl",
        "app/cbl/CBACT01C.cbl",
        "app/cbl/CBTRN02C.cbl",
        "app/cpy/CVACT01Y.cpy",
        "app/cpy/CVCUS01Y.cpy",
        "app/cpy/CVTRA06Y.cpy",
    ]:
        raw = (FIXTURE_ROOT / rel_path).read_bytes()
        assert b"\r\n" not in raw, f"{rel_path} unexpectedly contains CRLF"


# --- resolve_program: the other three Track C programs ------------------------------------------


def test_resolve_program_cbcus01c_returns_source_and_its_one_copybook():
    resolved = resolve_program(FIXTURE_ROOT, "CBCUS01C")
    assert "PROGRAM-ID.    CBCUS01C." in resolved.source_text
    assert sorted(resolved.copybook_sources.keys()) == ["CVCUS01Y"]
    assert "CUSTOMER-RECORD" in resolved.copybook_sources["CVCUS01Y"]


def test_resolve_program_cbact01c_returns_source_and_both_copybooks():
    resolved = resolve_program(FIXTURE_ROOT, "CBACT01C")
    assert "PROGRAM-ID.    CBACT01C." in resolved.source_text
    assert sorted(resolved.copybook_sources.keys()) == ["CODATECN", "CVACT01Y"]
    assert "CODATECN-REC" in resolved.copybook_sources["CODATECN"]


def test_resolve_program_cbtrn02c_returns_source_and_all_five_copybooks():
    resolved = resolve_program(FIXTURE_ROOT, "CBTRN02C")
    assert "PROGRAM-ID.    CBTRN02C." in resolved.source_text
    assert sorted(resolved.copybook_sources.keys()) == sorted(
        ["CVTRA06Y", "CVTRA05Y", "CVACT03Y", "CVACT01Y", "CVTRA01Y"]
    )
    assert "DALYTRAN-RECORD" in resolved.copybook_sources["CVTRA06Y"]


def test_resolve_program_copy_replacing_propagates_unsupported_error(tmp_path: Path):
    # Per ADR-0002, COPY ... REPLACING must route to a human gate, not be silently resolved --
    # resolve_program must propagate cobol_parser's rejection, not swallow it.
    cbl_dir = tmp_path / "app" / "cbl"
    cbl_dir.mkdir(parents=True)
    (cbl_dir / "CBFAKE02.cbl").write_text(
        "       WORKING-STORAGE SECTION.\n"
        "       COPY CVTRA01Y REPLACING ==:TAG:== BY ==WS==.\n",
        encoding="utf-8",
    )
    with pytest.raises(UnsupportedCopyConstructError):
        resolve_program(tmp_path, "CBFAKE02")
