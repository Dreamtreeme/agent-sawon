"""Conservative, locale-independent document value normalization."""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, InvalidOperation

from sawon_interfaces import PackageCount

from .schemas import FieldName

_PACKAGE_UNITS = {
    "CT": "CT",
    "CTN": "CT",
    "CTNS": "CT",
    "CARTON": "CT",
    "CARTONS": "CT",
    "PK": "PK",
    "PKG": "PK",
    "PKGS": "PK",
    "PACKAGE": "PK",
    "PACKAGES": "PK",
    "PLT": "PLT",
    "PALLET": "PLT",
    "PALLETS": "PLT",
    "PC": "PC",
    "PCS": "PC",
    "PIECE": "PC",
    "PIECES": "PC",
    "BOX": "BOX",
    "BOXES": "BOX",
    "BAG": "BAG",
    "BAGS": "BAG",
}
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


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def number(value: str) -> Decimal:
    text = unicodedata.normalize("NFKC", value).strip()
    if not re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?", text):
        raise ValueError("ambiguous_or_invalid_number")
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError("invalid_number") from exc


def normalize_value(
    name: FieldName, value: str, unit: str | None = None
) -> str | float | PackageCount:
    text = " ".join(unicodedata.normalize("NFKC", value).split())
    if not text:
        raise ValueError("empty_value")
    if name in {"gross_weight_kg", "packages"}:
        # The extractor must preserve units explicitly; do not assume kg/cartons.
        chosen_unit = (unit or "").strip().upper().rstrip(".")
        parsed = number(text)
        if name == "gross_weight_kg":
            if chosen_unit not in _WEIGHT_UNITS:
                raise ValueError("unknown_weight_unit")
            return float(parsed * _WEIGHT_UNITS[chosen_unit])
        if chosen_unit not in _PACKAGE_UNITS:
            raise ValueError("unknown_package_unit")
        if parsed != parsed.to_integral_value() or parsed <= 0:
            raise ValueError("invalid_package_count")
        return PackageCount(count=int(parsed), unit=_PACKAGE_UNITS[chosen_unit])
    if name in {"hbl_no", "mbl_no", "container_no", "pol", "pod"}:
        return unicodedata.normalize("NFKC", value).strip().upper()
    return text


def container_is_valid(value: str) -> bool:
    """ISO 6346 owner/category + six-digit serial + check digit."""
    code = unicodedata.normalize("NFKC", value).strip().upper()
    if not re.fullmatch(r"[A-Z]{3}[UJZ][0-9]{7}", code):
        return False
    # Letter values omit multiples of 11, as required by ISO 6346.
    letter_values = [number for number in range(10, 39) if number % 11]
    total = 0
    for index, character in enumerate(code[:10]):
        digit = int(character) if character.isdigit() else letter_values[ord(character) - 65]
        total += digit * (2**index)
    return (total % 11) % 10 == int(code[10])
