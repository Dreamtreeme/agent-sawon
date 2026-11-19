"""Record-to-UI value comparison shared by execution, observations and memory."""

import re
import unicodedata
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation

from sawon_interfaces import ShipmentRecord

from brain.task_instruction import COLLECTED_INPUT_PREFIX, TaskInputFormat, TaskInputRecord
from sawon_runtime.normalization import normalize_numeric_input

RecordInput = ShipmentRecord | TaskInputRecord


class TaskInputValues(dict[str, str]):
    """Executable strings with their taught format and receipt-bound count units."""

    def __init__(
        self,
        values: Mapping[str, str],
        formats: Mapping[str, TaskInputFormat],
        receipt_units: Mapping[str, str] | None,
    ):
        super().__init__(values)
        self.formats = formats
        self.receipt_units = receipt_units

    def copy(self) -> "TaskInputValues":
        return TaskInputValues(self, self.formats, self.receipt_units)

    def numeric_text(self, field: str, raw: str) -> str:
        specification = self.formats[field]
        count_unit = None
        if specification.count_unit_from:
            count_unit = (
                self.receipt_units.get(field)
                if self.receipt_units is not None
                else self.get(specification.count_unit_from)
            )
        return normalize_numeric_input(
            raw,
            integer=specification.type == "integer",
            unit=specification.unit,
            count_unit=count_unit,
        )


def normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).upper()


def compact_normalize(value: str | None) -> str:
    """Normalize labels used as compact semantic keys."""
    return "".join((value or "").split()).casefold()


def values_for(record: RecordInput) -> dict[str, str]:
    if isinstance(record, TaskInputRecord):
        return dict(record.inputs)
    values: dict[str, str] = {}
    for name, field in record.fields:
        if name == "packages":
            values["packages.count"] = str(field.value.count)
            values["packages.unit"] = field.value.unit
        else:
            values[name] = str(field.value)
    return values


def field_matches(field: str, actual: str | None, values: Mapping[str, str]) -> bool:
    if actual is None:
        return False
    expected = values[field]
    key = field.removeprefix(COLLECTED_INPUT_PREFIX)
    prefix = COLLECTED_INPUT_PREFIX if key != field else ""
    legacy_number = key in {"gross_weight_kg", "packages.count"}
    count_unit = values.get(prefix + "packages.unit") if key == "packages.count" else None
    if isinstance(values, TaskInputValues) and field in values.formats:
        try:
            return values.numeric_text(field, actual) == expected
        except ValueError:
            if not legacy_number:
                return False
            if values.formats[field].count_unit_from:
                # A declared count unit is validated against the bound receipt or taught input.
                count_unit = None
    if legacy_number:
        clean = unicodedata.normalize("NFKC", actual).strip()
        if key == "packages.count":
            if count_unit:
                clean = re.sub(rf"\s*{re.escape(count_unit)}\s*$", "", clean, flags=re.IGNORECASE)
            # A focused numeric input may expose its caret as a trailing OCR glyph.
            clean = re.sub(r"(?<=\d)\|$", "", clean)
        if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", clean):
            return False
        try:
            return Decimal(clean.replace(",", "")) == Decimal(expected)
        except InvalidOperation:
            return False
    if key == "packages.unit":
        if normalize(actual) == normalize(expected):
            return True
        count = values.get(prefix + "packages.count")
        return count is not None and normalize(actual) == normalize(count + " " + expected)
    return normalize(actual) == normalize(expected)
