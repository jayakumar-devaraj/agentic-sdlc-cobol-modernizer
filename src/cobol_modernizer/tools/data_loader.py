"""Read CardDemo's fixed-width data files the way the COBOL that owns them does.

Step 40a. Step 45's equivalence test compares a generated Java program against the COBOL it was
translated from, on the same inputs -- so those inputs have to be read correctly first, and
"correctly" here means three specific things that a naive reader gets wrong. All three were found by
looking at the real bytes rather than at the copybooks (PR #26), and all three are verified against
the byte-exact fixture in `tests/fixtures/tenant_repo_sample/app/data/ASCII/`:

1. **The declared record length can be a lie.** `CVACT03Y` documents `RECLN 50` for the card
   cross-reference; `cardxref.txt` is **36 bytes per record**, because the trailing `FILLER X(14)`
   is simply absent from the file. A reader that trusts the copybook slices every field past the
   first record's end.
2. **Signed numerics carry a zoned-decimal sign overpunch.** The final byte encodes the last digit
   *and* the sign together: `00000001940{` is `+19400`, not `1940`. Stripping the `{` loses a factor
   of ten **and** the sign, and `new BigDecimal(String)` on it throws.
3. **Line endings are not uniform within one file.** `tcatbal.txt` carries **49 `CR` against 50
   `LF`** -- one record ends `LF` alone. Splitting on `\\r\\n` drops a record; not stripping `\\r`
   corrupts the last field of 49 of them.

**Nothing here calls a model, and nothing here guesses.** A record whose length does not match what
was measured is an error, not something to pad or truncate: this module feeds an equivalence test,
and a reader that quietly repairs its input makes that test meaningless.
"""

from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from cobol_modernizer.tools.pic_mapper import PicMapping

logger = logging.getLogger(__name__)

#: Zoned-decimal sign overpunch. The final byte of a signed numeric field encodes the last digit and
#: the sign together. `{`/`}` are the zero forms; `A`-`I` and `J`-`R` carry digits 1-9.
_OVERPUNCH: dict[str, tuple[str, int]] = {
    "{": ("0", 1), "A": ("1", 1), "B": ("2", 1), "C": ("3", 1), "D": ("4", 1),
    "E": ("5", 1), "F": ("6", 1), "G": ("7", 1), "H": ("8", 1), "I": ("9", 1),
    "}": ("0", -1), "J": ("1", -1), "K": ("2", -1), "L": ("3", -1), "M": ("4", -1),
    "N": ("5", -1), "O": ("6", -1), "P": ("7", -1), "Q": ("8", -1), "R": ("9", -1),
}


class DataFormatError(Exception):
    """A record does not have the shape the file was measured to have.

    Fails loudly rather than padding, truncating, or skipping. This module's output feeds step 45's
    equivalence test: a reader that quietly repairs malformed input turns a failed comparison into a
    passing one and hides the defect it existed to find.
    """


@dataclass(frozen=True)
class FixedWidthField:
    """One field's position in a record, and how to interpret it.

    `precision`/`scale` come from `pic_mapper`, never from a hand-written table -- they are the same
    computed numbers the generated Java carries, so the loaded value and the code that operates on
    it cannot disagree about where the decimal point is.
    """

    name: str
    start: int
    length: int
    #: `None` for alphanumerics. Set for numerics, from `pic_mapper`.
    scale: int | None = None
    signed: bool = False


def field_byte_width(mapping: PicMapping) -> int:
    """How many bytes one field occupies in a `DISPLAY` record.

    Derived from `pic_mapper`'s own output rather than counted off the `PIC` string a second time:
    an alphanumeric is its `string_length`, and a zoned-decimal numeric is its `precision`. Neither
    the implied decimal point (`V`) nor the sign (`S`) takes a byte -- `V` is positional and `S` is
    overpunched into the final digit, which is exactly why `decode_zoned_decimal` has to exist.
    """
    if mapping.string_length is not None:
        return mapping.string_length
    if mapping.precision is not None:
        return mapping.precision
    raise DataFormatError(
        f"{mapping.field_name} has neither a string length nor a precision, so its width in a "
        f"fixed-width record is unknown ({mapping.raw_pic!r})"
    )


def derive_layout(mappings: list[PicMapping]) -> list[FixedWidthField]:
    """Field offsets for a record, from the copybook's declaration order.

    **Offsets are computed, never declared.** A hand-written offset table is a second source of
    truth that drifts from the copybook silently, and the failure mode is every field past the drift
    being read one column off -- which produces plausible-looking wrong numbers rather than an
    error.

    The total width this produces is what `measure_record_length` should be compared against. For
    `CVACT03Y` the two disagree, and that disagreement is the finding: the copybook documents a
    trailing `FILLER` the file does not contain.
    """
    fields: list[FixedWidthField] = []
    offset = 0
    for mapping in mappings:
        width = field_byte_width(mapping)
        fields.append(
            FixedWidthField(
                name=mapping.field_name,
                start=offset,
                length=width,
                scale=mapping.scale,
                signed=mapping.signed,
            )
        )
        offset += width
    return fields


def decode_zoned_decimal(raw: str, *, scale: int, signed: bool) -> Decimal:
    """Decode one zoned-decimal field, honouring the sign overpunch in its final byte.

    `decode_zoned_decimal("00000001940{", scale=2, signed=True)` is `Decimal("194.00")`, not
    `Decimal("19.40")`. The overpunch byte is the low-order **digit** as well as the sign, so
    dropping it shifts every value by a factor of ten -- silently, and in the direction that makes a
    balance look smaller.
    """
    text = raw.strip()
    if not text:
        raise DataFormatError("empty numeric field")

    sign = 1
    if signed:
        final = text[-1]
        if final in _OVERPUNCH:
            digit, sign = _OVERPUNCH[final]
            text = text[:-1] + digit
        elif not final.isdigit():
            raise DataFormatError(f"unrecognised sign overpunch {final!r} in {raw!r}")

    if not text.isdigit():
        raise DataFormatError(f"non-numeric zoned decimal {raw!r}")

    value = Decimal(text)
    if scale:
        value = value.scaleb(-scale)
    return value * sign


def measure_record_length(raw: bytes) -> int:
    """The real, uniform record length of a fixed-width file, in bytes, `CR`/`LF` excluded.

    Measured rather than read off a copybook's `RECLN` comment, because those disagree: `CVACT03Y`
    declares 50 and `cardxref.txt` is 36. Raises when the file is not uniform, since a fixed-width
    reader has nothing sensible to do with a ragged one.
    """
    lengths = {len(line.rstrip(b"\r")) for line in raw.split(b"\n") if line.strip()}
    if not lengths:
        raise DataFormatError("file contains no records")
    if len(lengths) > 1:
        raise DataFormatError(
            f"records are not a uniform width: found lengths {sorted(lengths)}"
        )
    return lengths.pop()


def read_records(path: Path) -> list[str]:
    """Every record in a fixed-width file, with line endings normalised away.

    Splits on `LF` and strips a trailing `CR` per record rather than splitting on `CRLF`:
    `tcatbal.txt` mixes the two within one file (49 `CR`, 50 `LF`), so a `CRLF` split silently
    merges two records into one and a missing `\\r` strip corrupts the final field of the rest.
    """
    raw = path.read_bytes()
    # `measure_record_length` is the width check: it raises when the file is ragged, so every
    # record here is already known to be the same length. A per-record re-check would be
    # unreachable, and unreachable code in a module whose whole job is refusing bad input is worse
    # than none -- it reads like a guarantee that nothing tests.
    width = measure_record_length(raw)
    records = [line.rstrip(b"\r") for line in raw.split(b"\n") if line.strip()]
    logger.info("data_loader: %s -- %d record(s) of %d bytes", path.name, len(records), width)
    return [record.decode("ascii") for record in records]


def parse_record(record: str, fields: list[FixedWidthField]) -> dict[str, str | Decimal]:
    """Slice one record into its fields, decoding numerics through the overpunch rules."""
    values: dict[str, str | Decimal] = {}
    for field in fields:
        chunk = record[field.start : field.start + field.length]
        if len(chunk) != field.length:
            raise DataFormatError(
                f"field {field.name} needs bytes {field.start}-{field.start + field.length} "
                f"of a {len(record)}-byte record"
            )
        if field.scale is None:
            values[field.name] = chunk
        else:
            values[field.name] = decode_zoned_decimal(
                chunk, scale=field.scale, signed=field.signed
            )
    return values


#: Where Spring Batch keeps its own DDL inside `spring-batch-core`.
SPRING_BATCH_SCHEMA_RESOURCE = "org/springframework/batch/core/schema-postgresql.sql"


def derive_table_ddl(table_name: str, mappings: list[PicMapping]) -> str:
    """`CREATE TABLE` for one copybook's record, with column types computed by `pic_mapper`.

    **`NUMERIC(p, s)` comes from the same precision and scale the generated Java carries.** A
    hand-written schema is a third place the decimal point can be wrong -- after the copybook and
    the Java -- and the one place where being wrong is silent, because a narrower column rounds on
    insert rather than raising. Deriving it means the column, the record and the code cannot
    disagree.

    `FILLER` is skipped: it names padding, not data, and `pic_mapper` gives every `FILLER` the same
    name, so keeping them would collide.
    """
    columns: list[str] = []
    for mapping in mappings:
        if mapping.field_name.upper() == "FILLER":
            continue
        column = _column_name(mapping.field_name)
        if mapping.precision is not None:
            columns.append(f"    {column} NUMERIC({mapping.precision}, {mapping.scale or 0})")
        else:
            columns.append(f"    {column} VARCHAR({mapping.string_length})")

    if not columns:
        raise DataFormatError(f"{table_name} has no non-FILLER fields to make columns from")

    body = ",\n".join(columns)
    return f"CREATE TABLE IF NOT EXISTS {table_name} (\n{body}\n);"


def _column_name(cobol_field_name: str) -> str:
    """`TRAN-CAT-BAL` -> `tran_cat_bal`. Mechanical, like every other name transform here."""
    return cobol_field_name.lower().replace("-", "_")


def spring_batch_schema_sql(jar_path: Path) -> str:
    """Spring Batch's own metadata DDL, read out of the jar actually on the classpath.

    **Extracted rather than copied into this repo, and this step owns applying it at all.** PR #27
    found that Spring Boot 4 removed `spring.batch.jdbc.*` from `BatchProperties` entirely, so
    `initialize-schema=always` is silently ignored and *nothing* creates `BATCH_JOB_INSTANCE` and
    friends -- the first CI run set it and counted zero tables. ADR-0019 was amended to move schema
    ownership here as a result.

    Reading it from the jar keeps it pinned to the Spring Batch version the target project resolves.
    A copy in this repo would be a second source of truth that goes stale on the next version bump,
    silently, in a file nobody re-reads.
    """
    with zipfile.ZipFile(jar_path) as jar:
        try:
            return jar.read(SPRING_BATCH_SCHEMA_RESOURCE).decode("utf-8")
        except KeyError:
            raise DataFormatError(
                f"{jar_path.name} does not contain {SPRING_BATCH_SCHEMA_RESOURCE}; the Spring "
                "Batch version may have moved it, and the metadata tables would then never be "
                "created (PR #27)"
            ) from None


def load_file(
    connection: Any,
    *,
    table_name: str,
    data_path: Path,
    mappings: list[PicMapping],
) -> int:
    """Create `table_name` from the copybook and insert every record of `data_path`. Returns the count.

    One migration concern, not two (ADR-0019's amendment): the table's shape and the data that goes
    into it both come from the same `pic_mapper` output, so a column cannot be narrower than the
    value it will hold. That is not a theoretical tidiness -- PostgreSQL rounds a value into a
    narrower `NUMERIC` silently rather than raising, so a schema derived separately from the reader
    would corrupt balances with no error anywhere.

    Idempotent on the table (`CREATE TABLE IF NOT EXISTS`) and **not** on the rows: a caller that
    wants a clean load truncates first. Hiding a `DELETE` inside a function called `load` is the
    kind of surprise that costs someone a dataset.
    """
    layout = derive_layout(mappings)
    file_width = measure_record_length(data_path.read_bytes())
    copybook_width = sum(field.length for field in layout)
    if file_width != copybook_width:
        # `cardxref.txt` against `CVACT03Y` is the known case. Refuse rather than truncate: the
        # fields that *are* present would still be read correctly, and loading them would look like
        # success while silently dropping whatever the copybook says comes after.
        raise DataFormatError(
            f"{data_path.name} records are {file_width} bytes but {table_name}'s copybook layout "
            f"is {copybook_width}; loading would misread or silently drop fields"
        )

    insertable = [field for field in layout if field.name.upper() != "FILLER"]
    columns = ", ".join(_column_name(field.name) for field in insertable)
    placeholders = ", ".join(["%s"] * len(insertable))
    statement = f"INSERT INTO {table_name} ({columns}) VALUES ({placeholders})"

    with connection.cursor() as cursor:
        cursor.execute(derive_table_ddl(table_name, mappings))
        rows = [
            tuple(parse_record(record, layout)[field.name] for field in insertable)
            for record in read_records(data_path)
        ]
        cursor.executemany(statement, rows)
    connection.commit()

    logger.info("data_loader: loaded %d row(s) into %s from %s", len(rows), table_name, data_path.name)
    return len(rows)


def apply_spring_batch_schema(connection: Any, jar_path: Path) -> int:
    """Create Spring Batch's metadata tables. Returns how many `CREATE TABLE` statements ran.

    **Nothing else creates these.** Spring Boot 4 removed `spring.batch.jdbc.*`, so
    `initialize-schema=always` is accepted and ignored -- PR #27's first CI run set it and counted
    zero tables. A job repository without them fails at runtime, not at startup.

    **Not idempotent, unlike `load_file`'s `CREATE TABLE IF NOT EXISTS`.** Spring Batch's own DDL
    uses bare `CREATE TABLE` and `CREATE SEQUENCE`, so a second application raises
    `DuplicateTable` -- and it creates *sequences* as well as tables, which a cleanup that drops
    only tables will miss. That asymmetry is deliberate rather than smoothed over: this is Spring
    Batch's schema, and rewriting their DDL to add `IF NOT EXISTS` would mean maintaining a
    divergent copy of it, which is the drift this function reads from the jar to avoid. A caller
    that needs to re-run drops first.
    """
    sql = spring_batch_schema_sql(jar_path)
    with connection.cursor() as cursor:
        cursor.execute(sql)
    connection.commit()
    created = sql.upper().count("CREATE TABLE")
    logger.info("data_loader: applied Spring Batch schema (%d table(s)) from %s", created, jar_path.name)
    return created
