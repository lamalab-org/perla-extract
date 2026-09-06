"""Conservative unit conversion shared by downstream projections."""

from __future__ import annotations

import re
from functools import lru_cache
from tokenize import TokenError

from .models import ReportedValue


def _unit_key(unit: str) -> str:
    """Normalize a percentage label without interpreting other unit semantics."""

    return re.sub(r"[^a-z0-9]+", "", unit.casefold())


@lru_cache(maxsize=1)
def _unit_registry():
    """Create Pint lazily so importing schemas has no registry side effects."""

    from pint import UnitRegistry

    registry = UnitRegistry()
    registry.define("sun = 1000 * watt / meter ** 2")
    return registry


def _pint_unit(unit: str) -> str:
    """Translate OCR typography, not scientific meaning, before unit parsing.

    PDF text occasionally contains C0 control characters where a mathematical glyph
    failed to decode. They carry no safely recoverable unit meaning, so replacing them
    with spaces lets Pint reject the unit without allowing malformed OCR to crash the
    extraction run.
    """

    superscript = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
    printable_unit = "".join(" " if ord(character) < 32 else character for character in unit)
    value = (
        printable_unit.strip()
        .replace("℃", "degree_Celsius")
        .replace("°C", "degree_Celsius")
        .replace("° C", "degree_Celsius")
        .replace("·", " * ")
        .replace("−", "-")
        .replace("–", "-")
        .replace("\uf02d", "-")
    )
    value = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+",
        lambda match: "**" + match.group(0).translate(superscript),
        value,
    )
    value = value.replace("^", "**")
    value = re.sub(r"([+-])\s+(?=\d)", r"\1", value)
    value = re.sub(r"(?<=[A-Za-z])([+-]\d+)(?=\s|$|[/)*])", r"**\1", value)
    return re.sub(r"(?<=[A-Za-z])\s+([+-]?\d+)(?=\s|$|[/)*])", r"**\1", value)


def convert_reported_value(value: ReportedValue, target_unit: str) -> float | None:
    """Convert only an explicit number carrying an explicit compatible unit."""

    from pint.errors import PintError

    if value.value_number is None or value.unit is None:
        return None
    unit = value.unit.strip()
    if target_unit == "percent":
        return (
            value.value_number
            if _unit_key(unit) in {"percent", "percentage"} or unit == "%"
            else None
        )
    try:
        quantity = _unit_registry().Quantity(value.value_number, _pint_unit(unit))
        return float(quantity.to(target_unit).magnitude)
    except (PintError, TokenError, TypeError, ValueError):
        return None


def is_concentration_unit(unit: str | None) -> bool:
    """Recognize explicit amount, mass, or fraction concentrations dimensionally."""

    from pint.errors import PintError

    if unit is None:
        return False
    compact = _unit_key(unit)
    if unit.strip() == "%" or compact in {
        "percent",
        "percentage",
        "wt",
        "wtpercent",
        "vol",
        "volpercent",
    }:
        return True
    try:
        quantity = _unit_registry().Quantity(1, _pint_unit(unit))
        return any(
            quantity.is_compatible_with(target)
            for target in ("mole / liter", "gram / liter")
        )
    except (PintError, TokenError, TypeError, ValueError):
        return False
