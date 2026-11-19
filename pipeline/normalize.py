"""Conservative, locale-independent document value normalization."""

from __future__ import annotations

import re
import unicodedata

from sawon_interfaces import PackageCount

from sawon_runtime.normalization import convert_mass, number, package_unit

from .schemas import FieldName


def normalized_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


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
            return float(convert_mass(parsed, chosen_unit, "KG"))
        canonical_unit = package_unit(chosen_unit)
        if parsed != parsed.to_integral_value() or parsed <= 0:
            raise ValueError("invalid_package_count")
        return PackageCount(count=int(parsed), unit=canonical_unit)
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
