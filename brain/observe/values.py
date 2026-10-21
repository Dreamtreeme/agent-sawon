"""Record-to-UI value comparison shared by every execution and evidence path.

Document parsing has different unit conversion rules and remains in pipeline.normalize.
"""

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from sawon_interfaces import ShipmentRecord

from brain.task_instruction import TaskInputRecord

RecordInput = ShipmentRecord | TaskInputRecord


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


def field_matches(field: str, actual: str | None, values: dict[str, str]) -> bool:
    if actual is None:
        return False
    expected = values[field]
    if field in {"gross_weight_kg", "packages.count"}:
        clean = unicodedata.normalize("NFKC", actual).strip()
        if field == "packages.count":
            unit = re.escape(values["packages.unit"])
            clean = re.sub(rf"\s*{unit}\s*$", "", clean, flags=re.IGNORECASE)
            # A focused numeric input may expose its caret as a trailing OCR glyph.
            clean = re.sub(r"(?<=\d)\|$", "", clean)
        if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", clean):
            return False
        try:
            return Decimal(clean.replace(",", "")) == Decimal(expected)
        except InvalidOperation:
            return False
    if field == "packages.unit":
        if normalize(actual) == normalize(expected):
            return True
        return normalize(actual) == normalize(values["packages.count"] + " " + expected)
    return normalize(actual) == normalize(expected)
