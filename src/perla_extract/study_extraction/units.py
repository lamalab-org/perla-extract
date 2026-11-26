"""Conservative unit conversion shared by downstream projections.

Pint versions supported on older Python releases can raise AssertionError for
malformed expressions such as ``Ω/``. Catch it only at these conversion boundaries,
like other parser errors, so callers retain the source label instead of aborting.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from tokenize import TokenError

from .quantity_specs import basis_percent_unit

_UNIT_MINUS_SIGNS = str.maketrans(
    {
        "\u2212": "-",  # Mathematical minus.
        "\u2012": "-",  # Figure dash used in PDF unit exponents.
        "\u2013": "-",  # En dash used in PDF unit exponents.
        "\uf02d": "-",  # Legacy Symbol-font minus.
    }
)


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

    Ordinary layout whitespace is harmless. Undecodable control characters are
    rejected: erasing one may turn a lost minus into a positive exponent. A space
    after the explicit micro-prefix glyph is typography, not multiplication.
    """

    superscript = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
    if any(ord(character) < 32 and not character.isspace() for character in unit):
        raise ValueError("Undecodable control character in unit")
    printable_unit = "".join(
        " " if ord(character) < 32 else character for character in unit
    )
    value = (
        printable_unit.strip()
        .replace("℃", "degree_Celsius")
        .replace("°C", "degree_Celsius")
        .replace("° C", "degree_Celsius")
        .replace("·", " * ")
        .translate(_UNIT_MINUS_SIGNS)
    )
    value = re.sub(r"([µμ])\s+(?=[A-Za-z])", r"\1", value)
    value = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+",
        lambda match: "**" + match.group(0).translate(superscript),
        value,
    )
    if value.casefold() in {"percent", "percentage"}:
        value = "%"
    value = value.replace("^", "**")
    value = re.sub(r"([+-])\s+(?=\d)", r"\1", value)
    # Plain-text exports often lose superscripting: cm2 and mm3 are unit powers.
    value = re.sub(r"([A-Za-z]+)(\d+)(?=\s|$|[/)*])", r"\1**\2", value)
    value = re.sub(r"(?<=[A-Za-z])([+-]\d+)(?=\s|$|[/)*])", r"**\1", value)
    value = re.sub(r"(?<=[A-Za-z])\s+([+-]?\d+)(?=\s|$|[/)*])", r"**\1", value)
    # Sheet resistance uses ohms per square, not ohms per square metre. Restrict
    # this notation rule to a complete resistance unit so area/volume resistivity
    # and unrelated uses of "square" retain their dimensions. Property names are
    # checked separately by the scorer.
    sheet = re.fullmatch(
        r"([kMmµμu]?(?:Ω|Ω|ohms?))\s*"
        r"(?:(?:/|per)\s*(?:sq|square|□)|(?:sq|square)\s*\*\*-1)",
        value,
    )
    value = sheet[1].replace("Ω", "Ω") if sheet else value
    # Accept unit names and arithmetic only. Pint releases differ in whether they
    # ignore comments, braces and other punctuation. Unsupported notation must
    # remain literal, not acquire a meaning that depends on the installed parser.
    if not value or re.fullmatch(r"[\w\s%°/()*+.^-]+", value) is None:
        raise ValueError("Unsupported unit notation")
    return value


def canonical_quantity(number: float, unit: str) -> tuple[float, str] | None:
    """Convert an explicit magnitude; callers retain bounds and other qualifiers."""

    from pint.errors import PintError

    unit = unit.strip()
    if not unit:
        return None
    try:
        quantity = _unit_registry().Quantity(number, _pint_unit(unit)).to_base_units()
        magnitude = float(quantity.magnitude)
        return (magnitude, str(quantity.units)) if math.isfinite(magnitude) else None
    except (
        PintError,
        TokenError,
        AssertionError,
        TypeError,
        ValueError,
        OverflowError,
        ZeroDivisionError,
    ):
        return None


def convert_quantity(number: float, unit: str, target_unit: str) -> float | None:
    """Convert an explicit physical quantity, preserving scale and temperature offsets."""

    from pint.errors import PintError

    if not unit.strip() or not target_unit.strip() or not math.isfinite(number):
        return None
    try:
        quantity = _unit_registry().Quantity(number, _pint_unit(unit))
        converted = float(quantity.to(_pint_unit(target_unit)).magnitude)
        return converted if math.isfinite(converted) else None
    except (
        PintError,
        TokenError,
        AssertionError,
        TypeError,
        ValueError,
        OverflowError,
        ZeroDivisionError,
    ):
        return None


def equivalent_unit_spelling(left: str, right: str) -> bool:
    """Recognize alternate spellings without accepting a changed scale or offset.

    Used for a number's verbatim suffix versus its separate unit field. ``h`` and
    ``hour`` agree; ``h`` and ``s`` do not, even though they have the same dimension.
    Testing both zero and one also distinguishes Celsius from Kelvin.
    """

    from pint.errors import PintError

    if not left.strip() or not right.strip():
        return False
    try:
        registry = _unit_registry()
        source, target = _pint_unit(left), _pint_unit(right)
        return all(
            math.isclose(
                float(registry.Quantity(number, source).to(target).magnitude),
                number,
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
            for number in (0, 1)
        )
    except (
        PintError,
        TokenError,
        AssertionError,
        TypeError,
        ValueError,
        OverflowError,
        ZeroDivisionError,
    ):
        return False


def is_concentration_unit(unit: str | None) -> bool:
    """Recognize explicit amount, mass, or fraction concentrations dimensionally."""

    from pint.errors import PintError

    if unit is None:
        return False
    if basis_percent_unit(unit) is not None:
        return True
    try:
        source = _pint_unit(unit)
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
        quantity = _unit_registry().Quantity(1, source)
        return any(
            quantity.is_compatible_with(target)
            for target in ("mole / liter", "gram / liter")
        )
    except (
        PintError,
        TokenError,
        AssertionError,
        TypeError,
        ValueError,
        ZeroDivisionError,
    ):
        return False
