"""`tools/data_loader.py` against CardDemo's **real, byte-verified** data files.

The fixture under `tests/fixtures/tenant_repo_sample/app/data/ASCII/` was fetched from
`carddemo-tenant-service` and each file's git blob SHA checked against the remote before it was
committed, the same way PR #10 verified the copybook fixtures. That matters more here than usual:
two of the three defects this module exists for are *properties of the bytes* — a record width that
contradicts its copybook, and line endings that are not uniform within one file — so a fixture that
git had silently normalised would make every test below vacuous.

`.gitattributes` already covers this path with `-text`, which is what keeps the mixed CR/LF intact.
"""

from __future__ import annotations

import itertools
from decimal import Decimal
from pathlib import Path

import pytest

from cobol_modernizer.nodes.spec_extractor import group_field_mappings_by_source
from cobol_modernizer.tools.data_loader import (
    DataFormatError,
    FixedWidthField,
    apply_spring_batch_schema,
    decode_zoned_decimal,
    derive_layout,
    derive_table_ddl,
    field_byte_width,
    load_file,
    measure_record_length,
    parse_record,
    read_records,
    spring_batch_schema_sql,
)
from cobol_modernizer.tools.tenant_repo import resolve_program

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "tenant_repo_sample"
DATA = FIXTURE_ROOT / "app" / "data" / "ASCII"


@pytest.fixture(scope="module")
def groups():
    return group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))


@pytest.fixture(scope="module")
def trn_groups():
    """`CBTRN02C`'s copybooks -- which is where `dailytran.txt`'s layout (`CVTRA06Y`) comes from."""
    return group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBTRN02C"))


# --- The sign overpunch, which is where money is lost ---------------------------------------------


def test_the_overpunch_carries_a_digit_as_well_as_a_sign():
    # The plan's own example. Stripping the `{` gives 19.40 -- a factor of ten low, silently, and in
    # the direction that makes a balance look smaller.
    assert decode_zoned_decimal("00000001940{", scale=2, signed=True) == Decimal("194.00")


def test_the_negative_overpunch_is_the_same_digit_with_the_opposite_sign():
    assert decode_zoned_decimal("00000001940}", scale=2, signed=True) == Decimal("-194.00")


@pytest.mark.parametrize(
    ("final", "expected"),
    [("{", "0"), ("A", "1"), ("E", "5"), ("I", "9")],
)
def test_every_positive_overpunch_letter_decodes_to_its_digit(final, expected):
    assert decode_zoned_decimal(f"12{final}", scale=0, signed=True) == Decimal(f"12{expected}")


@pytest.mark.parametrize(
    ("final", "expected"),
    [("}", "0"), ("J", "1"), ("N", "5"), ("R", "9")],
)
def test_every_negative_overpunch_letter_decodes_to_its_digit(final, expected):
    assert decode_zoned_decimal(f"12{final}", scale=0, signed=True) == Decimal(f"-12{expected}")


def test_an_unsigned_field_is_read_as_written():
    assert decode_zoned_decimal("000123", scale=2, signed=False) == Decimal("1.23")


def test_an_unrecognised_overpunch_raises_rather_than_being_guessed_at():
    # A byte that is neither a digit nor an overpunch means the layout is wrong, and reading past it
    # would produce a plausible number from the wrong columns.
    with pytest.raises(DataFormatError, match="unrecognised sign overpunch"):
        decode_zoned_decimal("0000019Z", scale=2, signed=True)


def test_a_non_numeric_field_raises():
    with pytest.raises(DataFormatError, match="non-numeric"):
        decode_zoned_decimal("00XX19", scale=2, signed=False)


# --- Record width: measured, not read off the copybook --------------------------------------------


def test_cardxrefs_real_width_contradicts_its_copybook(groups):
    """The finding, detected mechanically instead of asserted in prose.

    `CVACT03Y` documents `RECLN 50`; the file is 36 bytes per record, because the trailing
    `FILLER X(14)` is absent. A reader that trusted the copybook would slice every field of every
    record past its end.
    """
    mapped, _ = groups["CVACT03Y"]
    copybook_width = sum(f.length for f in derive_layout(mapped))
    file_width = measure_record_length((DATA / "cardxref.txt").read_bytes())

    assert copybook_width == 50
    assert file_width == 36
    assert copybook_width != file_width


@pytest.mark.parametrize(("copybook", "datafile"), [("CVTRA01Y", "tcatbal"), ("CVTRA02Y", "discgrp")])
def test_the_other_two_copybooks_do_agree_with_their_files(groups, copybook, datafile):
    # Stated so the test above reads as a real discrepancy rather than as this module being unable
    # to derive a width at all.
    mapped, _ = groups[copybook]
    assert sum(f.length for f in derive_layout(mapped)) == measure_record_length(
        (DATA / f"{datafile}.txt").read_bytes()
    )


def test_a_ragged_file_raises_instead_of_picking_a_width():
    with pytest.raises(DataFormatError, match="not a uniform width"):
        measure_record_length(b"12345\n123\n")


def test_an_empty_file_raises():
    with pytest.raises(DataFormatError, match="no records"):
        measure_record_length(b"\n\n")


# --- Line endings that are not uniform within one file --------------------------------------------


def test_tcatbal_really_does_mix_its_line_endings():
    # The property the reader is built around, asserted on the bytes so the fixture cannot be
    # silently normalised without this failing.
    raw = (DATA / "tcatbal.txt").read_bytes()
    assert raw.count(b"\r") == 49
    assert raw.count(b"\n") == 50


def test_every_record_is_read_despite_the_mixed_endings():
    # Splitting on CRLF would merge the one LF-only record into its neighbour and yield 49.
    assert len(read_records(DATA / "tcatbal.txt")) == 50


def test_no_record_carries_a_stray_carriage_return():
    # Not stripping \r corrupts the final field of the 49 records that have one.
    for record in read_records(DATA / "tcatbal.txt"):
        assert "\r" not in record


def test_a_record_of_the_wrong_width_is_refused(tmp_path):
    path = tmp_path / "ragged.txt"
    path.write_bytes(b"1234567890\n1234567890\n12345\n")
    with pytest.raises(DataFormatError):
        read_records(path)


# --- Layout derivation ------------------------------------------------------------------------------


def test_offsets_are_contiguous_and_start_at_zero(groups):
    mapped, _ = groups["CVTRA01Y"]
    layout = derive_layout(mapped)
    assert layout[0].start == 0
    for previous, current in itertools.pairwise(layout):
        assert current.start == previous.start + previous.length


def test_a_signed_numeric_field_keeps_its_computed_scale(groups):
    mapped, _ = groups["CVTRA01Y"]
    balance = next(f for f in derive_layout(mapped) if f.name == "TRAN-CAT-BAL")
    assert balance.scale == 2
    assert balance.signed is True


def test_a_field_with_no_derivable_width_raises():
    class _Unsized:
        field_name = "MYSTERY"
        raw_pic = "PIC ????"
        string_length = None
        precision = None

    with pytest.raises(DataFormatError, match="unknown"):
        field_byte_width(_Unsized())


# --- A real record, end to end ----------------------------------------------------------------------


def test_a_real_tcatbal_record_parses_through_its_derived_layout(groups):
    mapped, _ = groups["CVTRA01Y"]
    record = read_records(DATA / "tcatbal.txt")[0]
    values = parse_record(record, derive_layout(mapped))

    assert values["TRANCAT-ACCT-ID"] == Decimal(1)
    assert values["TRANCAT-TYPE-CD"] == "01"
    # The overpunched `{` in this record is a positive zero -- the decoder's job, at real offsets.
    assert values["TRAN-CAT-BAL"] == Decimal("0.00")


def test_every_tcatbal_record_parses(groups):
    mapped, _ = groups["CVTRA01Y"]
    layout = derive_layout(mapped)
    for record in read_records(DATA / "tcatbal.txt"):
        parse_record(record, layout)


def test_a_field_running_past_the_record_end_raises():
    with pytest.raises(DataFormatError, match="of a 5-byte record"):
        parse_record("12345", [FixedWidthField(name="X", start=0, length=10)])


def test_an_empty_numeric_field_raises_rather_than_reading_as_zero():
    # A blank where a number should be means the layout is wrong or the record is truncated.
    # Reading it as zero would put a plausible balance into an equivalence test.
    with pytest.raises(DataFormatError, match="empty numeric field"):
        decode_zoned_decimal("      ", scale=2, signed=True)


def test_a_short_record_is_named_in_the_error(tmp_path):
    # The message has to say which record, or a 50-record file gives a reviewer nothing to look at.
    path = tmp_path / "short.txt"
    path.write_bytes(b"1234567890\n12345\n")
    with pytest.raises(DataFormatError, match="not a uniform width"):
        read_records(path)


# --- What the real data does and does not exercise ------------------------------------------------


def test_every_real_balance_is_zero_which_step_45_must_not_ignore():
    """A finding, pinned so it cannot be forgotten between here and the equivalence test.

    `CBACT04C` computes `(TRAN-CAT-BAL * DIS-INT-RATE) / 1200`, and **every `TRAN-CAT-BAL` in the
    shipped CardDemo data is zero**. So every interest it computes on that data is zero -- and an
    equivalence test run against these files alone would pass for *any* implementation that returns
    zero, including a badly wrong one. It would be a test that cannot fail.

    What this does **not** mean, and did until `dailytran.txt` was read: that the zero is arbitrary,
    or that step 45's inputs have to be fabricated. `CBTRN02C` is the program that writes this file
    -- `ADD DALYTRAN-AMT TO TRAN-CAT-BAL`, at its `2700-A`/`2700-B` paragraphs -- so what ships here
    is the state **before posting has run**. The balances step 45 needs are stage one of CardDemo's
    own pipeline over `dailytran.txt`, not invented input. See
    `test_the_shipped_balances_are_the_pre_posting_state` below.
    """
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))
    layout = derive_layout(groups["CVTRA01Y"][0])
    balances = {
        parse_record(record, layout)["TRAN-CAT-BAL"]
        for record in read_records(DATA / "tcatbal.txt")
    }
    assert balances == {Decimal("0.00")}


def test_the_real_rates_do_decode_to_plausible_values():
    # The other side of the finding: `discgrp.txt` is real, varied data, so the reader is not
    # merely returning zero for everything.
    groups = group_field_mappings_by_source(resolve_program(FIXTURE_ROOT, "CBACT04C"))
    layout = derive_layout(groups["CVTRA02Y"][0])
    rates = {
        parse_record(record, layout)["DIS-INT-RATE"]
        for record in read_records(DATA / "discgrp.txt")
    }
    assert rates == {Decimal("0.00"), Decimal("15.00"), Decimal("25.00")}


def test_cbact04cs_own_two_files_only_ever_carry_the_zero_overpunch(groups):
    """Scoped to the two files, because stated of *the shipped data* it was false.

    Until `dailytran.txt` was read, this test asserted `seen == {"{"}` under the name
    `test_only_the_zero_overpunch_appears_in_real_signed_fields`, and the QA report drew the
    conclusion that the other nineteen forms were "covered by construction only". Both were
    generalisations from the only two files step 40a happened to load -- `tcatbal` and `discgrp`,
    which are `CBACT04C`'s, and which really do only ever carry `{`. See
    `test_the_real_data_exercises_every_overpunch_form` for what a third file contains.
    """
    seen = set()
    for copybook, datafile in (("CVTRA01Y", "tcatbal"), ("CVTRA02Y", "discgrp")):
        layout = derive_layout(groups[copybook][0])
        signed = [f for f in layout if f.signed]
        for record in read_records(DATA / f"{datafile}.txt"):
            for field in signed:
                seen.add(record[field.start + field.length - 1])
    assert seen == {"{"}


# --- `dailytran.txt`, the third real file, and what it overturns -----------------------------------


def _dailytran_amounts(trn_groups) -> list[Decimal]:
    layout = derive_layout(trn_groups["CVTRA06Y"][0])
    return [
        parse_record(record, layout)["DALYTRAN-AMT"]
        for record in read_records(DATA / "dailytran.txt")
    ]


def test_dailytrans_copybook_agrees_with_its_file(trn_groups):
    # Unlike `cardxref`, this one has no width discrepancy -- worth pinning, because the value of
    # this file rests on `CVTRA06Y` describing it correctly.
    derived = sum(f.length for f in derive_layout(trn_groups["CVTRA06Y"][0]))
    assert derived == 350
    assert measure_record_length((DATA / "dailytran.txt").read_bytes()) == 350


def test_the_real_data_exercises_every_overpunch_form(trn_groups):
    """The correction. All twenty forms appear in one real, shipped CardDemo file.

    `DALYTRAN-AMT` is `PIC S9(09)V99`, and `dailytran.txt`'s 300 records carry every positive form
    (`{ABCDEFGHI`) and every negative one (`}JKLMNOPQR`). The nineteen forms recorded as reachable
    "by construction only" were reachable by real data the whole time, in a file two directories
    away from the two that were loaded.
    """
    layout = derive_layout(trn_groups["CVTRA06Y"][0])
    amount = next(f for f in layout if f.name == "DALYTRAN-AMT")
    assert amount.signed is True and amount.scale == 2

    final_bytes = {
        record[amount.start + amount.length - 1] for record in read_records(DATA / "dailytran.txt")
    }
    assert final_bytes == set("{ABCDEFGHI") | set("}JKLMNOPQR")


def test_real_negative_amounts_decode_through_the_negative_overpunch(trn_groups):
    # The half of `decode_zoned_decimal` that no real byte had ever reached: a sign that is not
    # positive, read at a real offset out of a real record rather than out of a literal in a test.
    amounts = _dailytran_amounts(trn_groups)
    assert len(amounts) == 300
    assert min(amounts) == Decimal("-998.33")
    assert max(amounts) == Decimal("999.77")
    assert sum(1 for a in amounts if a < 0) == 50


def test_the_amounts_are_varied_enough_to_distinguish_implementations(trn_groups):
    # The property that makes this file worth having at all, and the one `tcatbal` lacks: 299
    # distinct values across 300 records. Arithmetic over these cannot pass by returning a constant.
    amounts = _dailytran_amounts(trn_groups)
    assert len(set(amounts)) == 299
    assert sum(amounts) == Decimal("104801.54")


def test_the_shipped_balances_are_the_pre_posting_state():
    """Why every `TRAN-CAT-BAL` is zero -- asserted against the COBOL, not against a re-implementation.

    Deliberately *not* done by computing the posted balances in Python and comparing: that oracle
    would be this session's reading of `CBTRN02C`, and step 45 comparing generated Java against it
    would be comparing two renderings of the same interpretation. What is checkable here without
    inventing an oracle is the causal claim itself -- that the program which writes `tcatbal` adds
    `DALYTRAN-AMT` into the balance, so a zero file is one that has not been posted to yet.
    """
    source = (FIXTURE_ROOT / "app" / "cbl" / "CBTRN02C.cbl").read_text()
    assert source.count("ADD DALYTRAN-AMT TO TRAN-CAT-BAL") == 2  # the create and the update paths
    assert "REWRITE FD-TRAN-CAT-BAL-RECORD FROM TRAN-CAT-BAL-RECORD" in source


# --- Schema derivation ------------------------------------------------------------------------------


def test_numeric_columns_carry_pic_mappers_precision_and_scale(groups):
    # A hand-written schema is a third place the decimal point can be wrong, and the only one where
    # being wrong is silent: PostgreSQL rounds into a narrower NUMERIC rather than raising.
    ddl = derive_table_ddl("tran_cat_bal", groups["CVTRA01Y"][0])
    assert "tran_cat_bal NUMERIC(11, 2)" in ddl
    assert "trancat_type_cd VARCHAR(2)" in ddl


def test_filler_becomes_no_column(groups):
    # FILLER names padding, not data, and pic_mapper gives every FILLER the same name -- keeping
    # them would collide.
    assert "filler" not in derive_table_ddl("tran_cat_bal", groups["CVTRA01Y"][0]).lower()


def test_a_record_of_only_filler_has_no_table_to_make():
    with pytest.raises(DataFormatError, match="no non-FILLER fields"):
        derive_table_ddl("empty", [])


# --- The load itself, against a real PostgreSQL instance -------------------------------------------
#
# Same posture as `test_knowledge_store.py`: these need a real database and skip with a clear
# reason rather than weakening into an in-memory equivalent. CI provides the service container, so
# they run there for real rather than skipping forever.

_CREDENTIALS_FILE = FIXTURE_ROOT.parent / "db_credentials_sample" / "local.conn"
_SKIP_REASON = (
    "Could not reach the local Postgres instance via the credentials at "
    f"{_CREDENTIALS_FILE} (see docker-compose.yml's `postgres` service, localhost:5434). "
    "This system test requires a real running instance."
)


def _connect_or_none():
    from cobol_modernizer.tools import knowledge_store
    from cobol_modernizer.tools.knowledge_store import KnowledgeStoreCredentialsError

    try:
        return knowledge_store.connect(_CREDENTIALS_FILE)
    except (KnowledgeStoreCredentialsError, knowledge_store.psycopg.Error):
        return None


_BATCH_TABLES = (
    "BATCH_STEP_EXECUTION_CONTEXT, BATCH_JOB_EXECUTION_CONTEXT, BATCH_STEP_EXECUTION, "
    "BATCH_JOB_EXECUTION_PARAMS, BATCH_JOB_EXECUTION, BATCH_JOB_INSTANCE"
)
#: Spring Batch creates sequences as well as tables. A cleanup that drops only the tables leaves
#: `BATCH_STEP_EXECUTION_SEQ` behind, and the next `apply_spring_batch_schema` fails on it -- which
#: is how these tests first failed, and why the names are listed rather than assumed.
_BATCH_SEQUENCES = "BATCH_STEP_EXECUTION_SEQ, BATCH_JOB_EXECUTION_SEQ, BATCH_JOB_INSTANCE_SEQ"


def _clean(connection) -> None:
    # Roll back first: a test that asserted on a raising statement leaves the transaction aborted,
    # and every later command in it is refused until it ends.
    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS systest_tran_cat_bal, systest_card_xref")
        cursor.execute(f"DROP TABLE IF EXISTS {_BATCH_TABLES} CASCADE")
        cursor.execute(f"DROP SEQUENCE IF EXISTS {_BATCH_SEQUENCES} CASCADE")
    connection.commit()


@pytest.fixture
def db():
    connection = _connect_or_none()
    if connection is None:
        pytest.skip(_SKIP_REASON)
    try:
        _clean(connection)
        yield connection
    finally:
        _clean(connection)
        connection.close()


def test_every_real_record_lands_in_postgres_with_its_computed_types(db, groups):
    loaded = load_file(
        db,
        table_name="systest_tran_cat_bal",
        data_path=DATA / "tcatbal.txt",
        mappings=groups["CVTRA01Y"][0],
    )
    assert loaded == 50

    with db.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM systest_tran_cat_bal")
        assert cursor.fetchone()[0] == 50
        # The column really is NUMERIC(11, 2), so the value comes back as a Decimal at that scale
        # rather than as a float that has already lost the cents.
        cursor.execute(
            "SELECT numeric_precision, numeric_scale FROM information_schema.columns "
            "WHERE table_name = 'systest_tran_cat_bal' AND column_name = 'tran_cat_bal'"
        )
        assert cursor.fetchone() == (11, 2)


def test_a_file_that_contradicts_its_copybook_is_refused_rather_than_partly_loaded(db, groups):
    # `cardxref.txt` is 36 bytes against `CVACT03Y`'s 50. The leading fields would read correctly,
    # so a partial load would look like success while silently dropping whatever follows.
    with pytest.raises(DataFormatError, match="would misread or silently drop"):
        load_file(
            db,
            table_name="systest_card_xref",
            data_path=DATA / "cardxref.txt",
            mappings=groups["CVACT03Y"][0],
        )

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = 'systest_card_xref'"
        )
        assert cursor.fetchone()[0] == 0, "a refused load must not leave a table behind"


# --- Spring Batch's own metadata schema, which nothing else creates ---------------------------------


def _spring_batch_jar() -> Path | None:
    """The `spring-batch-core` jar the target project resolves, from the local Maven repository."""
    root = Path.home() / ".m2" / "repository" / "org" / "springframework" / "batch" / "spring-batch-core"
    if not root.is_dir():
        return None
    jars = sorted(root.rglob("spring-batch-core-*.jar"))
    return jars[-1] if jars else None


def test_the_metadata_ddl_is_read_out_of_the_jar_on_the_classpath():
    """PR #27 found that Spring Boot 4 removed `spring.batch.jdbc.*`, so nothing creates these.

    Read from the jar rather than copied into this repo: a copy is a second source of truth that
    goes stale on the next version bump, silently, in a file nobody re-reads.
    """
    jar = _spring_batch_jar()
    if jar is None:
        pytest.skip("spring-batch-core is not in the local Maven repository; run a template build")
    sql = spring_batch_schema_sql(jar)
    assert "BATCH_JOB_INSTANCE" in sql.upper()
    assert sql.upper().count("CREATE TABLE") == 6


def test_a_jar_without_the_resource_raises_rather_than_returning_nothing(tmp_path):
    # Returning empty SQL would apply cleanly and create no tables -- exactly PR #27's failure,
    # where `initialize-schema=always` was accepted and counted zero tables.
    import zipfile

    jar = tmp_path / "empty.jar"
    with zipfile.ZipFile(jar, "w") as archive:
        archive.writestr("unrelated.txt", "x")
    with pytest.raises(DataFormatError, match="does not contain"):
        spring_batch_schema_sql(jar)


def test_the_metadata_tables_really_are_created(db):
    jar = _spring_batch_jar()
    if jar is None:
        pytest.skip("spring-batch-core is not in the local Maven repository; run a template build")

    assert apply_spring_batch_schema(db, jar) == 6

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name LIKE 'batch%'"
        )
        assert cursor.fetchone()[0] == 6
