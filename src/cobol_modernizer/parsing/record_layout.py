"""Byte offsets and record lengths: the layout half of what a rendered reader needs (G31, F1).

**Why this exists.** `DomainField` carries a width -- a `PIC X(n)` length, or a numeric precision --
in copybook order, and nothing else. It carries **no byte offset and no record length**, so a reader
built from the design has to assume fields are contiguous from byte zero. That assumption happens to
hold for `CVTRA01Y`, `CVTRA02Y`, `CVACT01Y` and `CVACT03Y` because every `FILLER` in them is
trailing, and it is false the moment a copybook has one in the middle. Finding **F1** from the
hand-written wiring, which is where the assumption was actually made.

**Offsets are computed over every declaration, `FILLER` included**, which is what makes an interior
`FILLER` a non-event rather than a silent mis-slice. `FILLER` occupies bytes and carries no data, so
it shifts every field after it and appears in no entity.

**Only elementary items contribute.** A group item (`05 TRAN-CAT-KEY.` with `10` children) has no
`PIC` of its own and no width; counting it would double every byte its children occupy. Group
membership is otherwise irrelevant here -- the record is a flat byte string.

**Widths are `pic_mapper`'s, never re-derived.** The rule that a currency field's precision is not
something to recompute applies to its width too: one implementation, in the module that already
owns it.

**`USAGE` other than `DISPLAY` is refused.** `COMP` and `COMP-3` occupy a different number of bytes
than their digit count, and a layout that assumed otherwise would be wrong by a factor of about two
for every field after the first packed one -- silently, since every offset would still look
plausible. No record in this corpus uses one, which is exactly why the refusal needs a test.
"""

from __future__ import annotations

from pydantic import BaseModel

from cobol_modernizer.parsing.cobol_parser import extract_record_fields
from cobol_modernizer.tools.pic_mapper import (
    PicFieldType,
    PicMapping,
    UnsupportedPicConstructError,
    UsageClause,
    map_pic_clause,
)


class UnsupportedRecordLayoutError(Exception):
    """A record's byte layout cannot be computed from its declarations.

    Joins the `UnsupportedPicConstructError` family. Raised for a `USAGE` this module cannot size,
    and for a field `pic_mapper` itself rejects -- because a layout missing one field's width is not
    a partial answer, it is a wrong offset for every field that follows.
    """


class FieldLayout(BaseModel):
    """One elementary field's position in the record. `field_name` is `None` for `FILLER`."""

    field_name: str | None
    byte_offset: int
    byte_width: int
    is_filler: bool = False


class RecordLayout(BaseModel):
    """One `01`-level record: where each field sits, and how long the whole thing is."""

    record_name: str
    record_length: int
    fields: list[FieldLayout]

    def offset_of(self, field_name: str) -> int | None:
        """The byte offset of a named field, or `None` if the record has no such field."""
        for field in self.fields:
            if field.field_name == field_name:
                return field.byte_offset
        return None


def display_width(mapping: PicMapping) -> int:
    """How many bytes one elementary field occupies on disk.

    Alphanumeric is its declared length. Numeric `DISPLAY` is one byte per digit: `V` is an implied
    decimal point that occupies nothing, and the sign of a signed field rides the final digit as an
    overpunch rather than taking a byte of its own -- which is why `S9(10)V99` is twelve bytes and
    not thirteen, and why the data loader has to decode overpunches at all.

    Raises:
        UnsupportedRecordLayoutError: a `USAGE` whose byte width is not its digit count.
    """
    if mapping.usage is not UsageClause.DISPLAY:
        raise UnsupportedRecordLayoutError(
            f"field {mapping.field_name!r} is USAGE {mapping.usage.value}, whose byte width is not "
            "its digit count; a layout computed as if it were would put every following field at "
            "the wrong offset"
        )
    if mapping.field_type is PicFieldType.ALPHANUMERIC:
        if mapping.string_length is None:
            raise UnsupportedRecordLayoutError(
                f"field {mapping.field_name!r} is alphanumeric with no declared length"
            )
        return mapping.string_length
    if mapping.precision is None:
        raise UnsupportedRecordLayoutError(
            f"field {mapping.field_name!r} is numeric with no precision"
        )
    return mapping.precision


def compute_record_layouts(source_text: str, *, skip_unsizable: bool = False) -> list[RecordLayout]:
    """Every `01`-level record in `source_text`, with each field's offset and the total length.

    A copybook yields one; a program's `DATA DIVISION` yields one per `01` level, which is why this
    returns a list rather than the single record a copybook happens to contain.

    `skip_unsizable` drops a record this module cannot size instead of raising. Off by default,
    because for a copybook -- one record, and the entity is built from it -- a partial answer is a
    wrong answer. It is switched on for a *program*, where the `DATA DIVISION` legitimately contains
    records this cannot size (`CBACT04C`'s `DB2-FORMAT-TS` is a `REDEFINES` group the construct
    matrix already routes to a human) and the caller wants the `FD` records regardless. Skipping is
    then safe for the same reason it is unsafe above: whole records are dropped, never fields, so no
    surviving offset is affected.

    Raises:
        UnsupportedRecordLayoutError: any field whose width cannot be determined, unless
            `skip_unsizable`. Deliberately not partial within a record -- see the exception.
    """
    layouts: list[RecordLayout] = []
    current_name: str | None = None
    fields: list[FieldLayout] = []
    offset = 0
    unsizable = False

    def flush() -> None:
        nonlocal current_name, fields, offset, unsizable
        # A record with an unsizable field is dropped whole rather than emitted short: every offset
        # after the skipped field would be wrong, which is worse than not describing the record.
        if current_name is not None and not unsizable:
            layouts.append(
                RecordLayout(record_name=current_name, record_length=offset, fields=fields)
            )
        current_name, fields, offset, unsizable = None, [], 0, False

    for declaration in extract_record_fields(source_text):
        if declaration.level == "01":
            flush()
            current_name = declaration.name
            if "PIC" not in declaration.raw_text.upper():
                continue

        if "PIC" not in declaration.raw_text.upper():
            # A group item. It owns the bytes of its children and none of its own.
            continue
        if current_name is None:
            # A field before any `01` level: not part of a record this module can describe.
            continue

        try:
            mapping = map_pic_clause(
                declaration.raw_text, adjacent_text=declaration.sibling_text
            )
            width = display_width(mapping)
        except (UnsupportedPicConstructError, UnsupportedRecordLayoutError, ValueError) as exc:
            if skip_unsizable:
                unsizable = True
                continue
            raise UnsupportedRecordLayoutError(
                f"{current_name}: field {declaration.name or 'FILLER'!r} cannot be mapped, so "
                f"every offset after it would be wrong -- {exc}"
            ) from exc

        fields.append(
            FieldLayout(
                field_name=None if declaration.is_filler else declaration.name,
                byte_offset=offset,
                byte_width=width,
                is_filler=declaration.is_filler,
            )
        )
        offset += width

    flush()
    return layouts
