"""Conservative numeric parsing and declared mass-unit conversion."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

_WEIGHT_UNITS = {
    "KG": Decimal(1),
    "KGS": Decimal(1),
    "KILOGRAM": Decimal(1),
    "KILOGRAMS": Decimal(1),
    "G": Decimal("0.001"),
    "GRAM": Decimal("0.001"),
    "GRAMS": Decimal("0.001"),
    "T": Decimal(1000),
    "MT": Decimal(1000),
    "TONNE": Decimal(1000),
    "TONNES": Decimal(1000),
    "LB": Decimal("0.45359237"),
    "LBS": Decimal("0.45359237"),
}


def number(value: str) -> Decimal:
    text = unicodedata.normalize("NFKC", value).strip()
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise ValueError("ambiguous_or_invalid_number")
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("invalid_number") from exc


def _mass_factor(unit: str) -> Decimal:
    normalized = unit.strip().upper().rstrip(".")
    if normalized not in _WEIGHT_UNITS:
        raise ValueError("unknown_weight_unit")
    return _WEIGHT_UNITS[normalized]


def convert_mass(value: Decimal, source_unit: str, target_unit: str) -> Decimal:
    return value * _mass_factor(source_unit) / _mass_factor(target_unit)


def normalize_numeric_input(raw: str, *, integer: bool = False, unit: str | None = None) -> str:
    """Format a numeric reading for the declared input type and target mass unit."""
    text = unicodedata.normalize("NFKC", raw).strip()
    if unit is None:
        value = number(text)
    else:
        with_unit = re.fullmatch(r"(.*?)\s*([A-Za-z]+\.?)", text)
        if with_unit is None:
            value = number(text)
            _mass_factor(unit)
        else:
            value = convert_mass(number(with_unit[1]), with_unit[2], unit)
    if integer and value != value.to_integral_value():
        raise ValueError("invalid_integer")
    formatted = format(value, "f")
    return formatted.rstrip("0").rstrip(".") if "." in formatted else formatted
