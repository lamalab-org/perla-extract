"""Interpret reported claims once for scientific equality and record features.

This module preserves unsupported claims literally. Normalization does not infer
chemical synonyms, missing units, bounds, or statistical qualifiers.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from .models import ReportedValue
from .units import (
    canonical_quantity,
    canonical_reported_quantity,
    equivalent_unit_spelling,
)


def scientific_text(value: str) -> str:
    """Normalize spacing without flattening chemical superscripts or subscripts.

    Compatibility normalization (NFKC) conflates charge and atom-count notation,
    for example Fe³⁺ and Fe₃⁺. NFC preserves those scientific distinctions.
    """

    return " ".join(unicodedata.normalize("NFC", value).replace("−", "-").split())


def scientific_value_text(value: str) -> str:
    """Recognize numeric LaTeX subscripts in simple formulas, not chemical synonyms.

    The complete result must be a sequence of element-like tokens and numeric
    subscripts, including a bounded symbolic coefficient such as ``3-x``.
    Prose, charges and arbitrary algebra stay literal.
    This changes notation only: it never reorders atoms or expands abbreviations.
    """

    text = scientific_text(value)
    formula = text.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
    coefficient = r"(?:\d+(?:\.\d+)?(?:\s*-\s*[xyz])?|[xyz])"
    formula = re.sub(
        rf"_\{{\s*({coefficient})\s*\}}|_({coefficient})",
        lambda m: m[1] or m[2],
        formula,
    )
    formula = re.sub(r"(?<=[A-Za-z])\s+(?=\d)", "", formula)
    if re.fullmatch(rf"(?:[A-Z][a-z]?\s*(?:{coefficient})?\s*)+", formula):
        return formula.replace(" ", "")
    return text


_MANTISSA = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_NUMBER = rf"{_MANTISSA}(?:[eE][+-]?\d+)?"
_SCIENTIFIC_NUMBER = re.compile(
    rf"({_MANTISSA})\s*(?:×|\\times)\s*10\s*"
    r"(?:\^\{([+-]?\d+)\}|\^([+-]?\d+)|([+-]\s*\d+)|([⁺⁻]?[⁰¹²³⁴⁵⁶⁷⁸⁹]+))"
)
_SUPERSCRIPT_DIGITS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")


def _quantity_text(raw: str) -> str:
    """Read printed powers of ten without changing the literal claim.

    A signed baseline exponent covers PDF text such as ``2 × 10 -6``. The
    caller must still match the entire scalar/range/uncertainty and check any
    supplied number and unit. Prose, bounds and statistical qualifiers survive.
    """

    def exponent(match: re.Match) -> str:
        power = next(group for group in match.groups()[1:] if group is not None)
        return match[1] + "e" + power.translate(_SUPERSCRIPT_DIGITS).replace(" ", "")

    text = _SCIENTIFIC_NUMBER.sub(exponent, scientific_text(raw))
    # A single variable with an optional textual subscript is notation, not a
    # qualifier: E = 1.0 V, t = 10 s, Q_pol = 10 mC/cm2. The complete RHS must
    # still pass the quantity grammar and parsed-number/unit consistency checks.
    # Prose such as "mean = ...", inequalities and multiple assignments survive.
    assignment = re.fullmatch(
        r"[A-Za-zα-ωΑ-Ω](?:(?:_|\s+)[A-Za-z]+|_\{[A-Za-z]+\})?\s*=\s*(.+)", text
    )
    return assignment[1] if assignment else text


def _finite_number(text: str) -> float | None:
    """Reject overflow and nonzero magnitudes that would underflow to zero."""

    number = float(text)
    nonzero_mantissa = any(c in "123456789" for c in text.lower().split("e")[0])
    if not math.isfinite(number) or (number == 0 and nonzero_mantissa):
        return None
    return number


def is_plain_number(value: ReportedValue) -> bool:
    """Allow unit conversion only for a single unqualified, internally consistent number.

    Ranges, inequalities, uncertainties and chemical formulas do not qualify for
    this path. Their central numeric value alone does not represent the claim.
    The consistency check has no absolute allowance: a small nonzero raw number
    must never qualify as a parsed zero, regardless of the chosen unit.
    """

    match = re.fullmatch(rf"({_NUMBER})\s*(.*)", _quantity_text(value.raw_value))
    number = _finite_number(match[1]) if match else None
    return bool(
        match
        and number is not None
        and value.value_number is not None
        and math.isfinite(value.value_number)
        and math.isclose(number, value.value_number, rel_tol=1e-9, abs_tol=0.0)
        and (
            scientific_text(match[2]) in {"", scientific_text(value.unit or "")}
            or (
                value.unit is not None
                and equivalent_unit_spelling(match[2], value.unit)
            )
        )
    )


_COMPOUND_QUANTITY = re.compile(
    rf"({_NUMBER})\s*(±|\+/-|–|—|\bto\b|-)\s*({_NUMBER})\s*(.*)"
)


def compound_quantity(
    value: ReportedValue,
) -> tuple[Literal["range", "uncertainty"], float, float, str | None] | None:
    """Compare complete ranges or symmetric uncertainties, never their centre alone.

    Accept only two numbers and an optional unit suffix consistent with the unit
    field. A range must be ordered and have no invented scalar value. An uncertainty
    must be nonnegative; a supplied parsed value must agree with its centre.
    Offset temperature units shift endpoints/centres, but only scale uncertainties.
    Anything outside this grammar retains the existing literal comparison.
    """

    match = _COMPOUND_QUANTITY.fullmatch(_quantity_text(value.raw_value))
    if match is None:
        return None
    first, second = _finite_number(match[1]), _finite_number(match[3])
    if first is None or second is None:
        return None
    kind: Literal["range", "uncertainty"] = (
        "uncertainty" if match[2] in {"±", "+/-"} else "range"
    )
    suffix, unit = match[4], value.unit
    if (
        suffix
        and unit is not None
        and (
            scientific_text(suffix) != scientific_text(unit)
            and not equivalent_unit_spelling(suffix, unit)
        )
    ):
        return None
    # A unit explicitly present in the raw claim needs no redundant unit field.
    # Unknown suffixes still fail canonical conversion below and remain literal.
    unit = unit if unit is not None else suffix or None
    if kind == "range":
        if first > second or value.value_number is not None:
            return None
    elif second < 0 or (
        value.value_number is not None
        and not math.isclose(first, value.value_number, rel_tol=1e-9, abs_tol=0)
    ):
        return None
    if unit is None:
        return kind, first, second, None
    a, b = canonical_quantity(first, unit), canonical_quantity(second, unit)
    if a is None or b is None:
        return None
    if kind == "uncertainty":
        zero, one, two = (canonical_quantity(n, unit) for n in (0, 1, 2))
        if zero is None or one is None or two is None:
            return None
        # An error bar of 1 °C is 1 K, not 274.15 K. Reject nonlinear/log units.
        scale = one[0] - zero[0]
        if scale <= 0 or not all(
            math.isclose(
                converted, zero[0] + scale * original, rel_tol=1e-12, abs_tol=1e-12
            )
            for original, converted in ((2, two[0]), (first, a[0]), (second, b[0]))
        ):
            return None
        second = scale * second
    else:
        second = b[0]
    return (kind, a[0], second, a[1]) if math.isfinite(second) else None


@dataclass(frozen=True)
class LiteralClaim:
    """The conservative fallback, retained even when a claim can be interpreted."""

    text: str
    unit: str
    number: float | None
    source_unit: str | None
    raw_text: str


@dataclass(frozen=True)
class CanonicalQuantity:
    magnitude: float
    unit: str


@dataclass(frozen=True)
class ScalarClaim:
    number: float
    unit: str | None
    canonical: CanonicalQuantity | None
    literal: LiteralClaim


@dataclass(frozen=True)
class CompoundClaim:
    kind: Literal["range", "uncertainty"]
    first: float
    second: float
    unit: str | None
    literal: LiteralClaim


NormalizedClaim = ScalarClaim | CompoundClaim | LiteralClaim


def normalize_claim(value: ReportedValue) -> NormalizedClaim:
    """Retain the full claim and use the existing conservative parsing rules.

    The literal fallback matters when only one side can be interpreted, or a
    scalar has an unknown unit. It must not acquire an inferred dimension or lose
    an inconsistent parsed number just because another representation is valid.
    """

    return _normalize_fields(value.raw_value, value.value_number, value.unit)


@lru_cache(maxsize=8192)
def _normalize_fields(
    raw: str, number: float | None, unit: str | None
) -> NormalizedClaim:
    # Equality examines many candidate pairs. Interpret each distinct claim once,
    # excluding property names and citations, which cannot change its quantity.
    # The caller already holds a validated ReportedValue; no synthetic evidence
    # or mutation of the source record is needed for this private projection.
    value = ReportedValue.model_construct(raw_value=raw, value_number=number, unit=unit)
    literal = LiteralClaim(
        text=scientific_value_text(value.raw_value),
        unit=scientific_text(value.unit or ""),
        number=value.value_number,
        source_unit=value.unit,
        raw_text=scientific_text(value.raw_value),
    )
    # A null convenience field does not erase information explicitly present in
    # the raw claim. Parse only a complete scalar; a raw unit must be recognized.
    # Never replace conflicting supplied numbers or units. This is scorer-local:
    # the prediction and its content hash retain the original missing fields.
    match = (
        re.fullmatch(
            rf"({_NUMBER})\s*([A-Za-zµμ°℃%ΩΩÅ].*)?", _quantity_text(value.raw_value)
        )
        if value.value_number is None or value.unit is None
        else None
    )
    if match:
        number, suffix = _finite_number(match[1]), (match[2] or "").strip()
        if (
            number is not None
            and (not suffix or canonical_quantity(number, suffix) is not None)
            and (
                value.value_number is None
                or math.isclose(number, value.value_number, rel_tol=1e-9, abs_tol=0)
            )
            and (
                not suffix
                or value.unit is None
                or equivalent_unit_spelling(suffix, value.unit)
            )
        ):
            value = value.model_copy(
                update={"value_number": number, "unit": suffix or value.unit}
            )
    compound = compound_quantity(value)
    if compound is not None:
        kind, first, second, unit = compound
        return CompoundClaim(kind, first, second, unit, literal)
    if is_plain_number(value):
        assert value.value_number is not None
        canonical = canonical_reported_quantity(value)
        return ScalarClaim(
            value.value_number,
            value.unit,
            CanonicalQuantity(*canonical) if canonical is not None else None,
            literal,
        )
    return literal


def equal_claims(
    left: NormalizedClaim, right: NormalizedClaim, relative: float, absolute: float
) -> bool:
    """Compare typed claims without changing literal or missing-unit semantics."""

    if isinstance(left, CompoundClaim) and isinstance(right, CompoundClaim):
        return (
            left.kind == right.kind
            and left.unit == right.unit
            and math.isclose(
                left.first, right.first, rel_tol=relative, abs_tol=absolute
            )
            and math.isclose(
                left.second, right.second, rel_tol=relative, abs_tol=absolute
            )
        )
    if isinstance(left, ScalarClaim) and isinstance(right, ScalarClaim):
        if (left.unit is None) != (right.unit is None):
            return False
        a, b = left.number, right.number
        if left.unit is not None:
            if left.canonical is None or right.canonical is None:
                return (
                    left.literal.raw_text == right.literal.raw_text
                    and left.unit == right.unit
                )
            if left.canonical.unit != right.canonical.unit:
                return False
            a, b = left.canonical.magnitude, right.canonical.magnitude
        return math.isclose(a, b, rel_tol=relative, abs_tol=absolute)
    literal_left = left if isinstance(left, LiteralClaim) else left.literal
    literal_right = right if isinstance(right, LiteralClaim) else right.literal
    return (
        literal_left.text == literal_right.text
        and literal_left.unit == literal_right.unit
        and literal_left.number == literal_right.number
    )
