"""Interpret reported claims once for scientific equality and record features.

This module preserves unsupported claims literally. Normalization does not infer
chemical synonyms, missing units, bounds, or statistical qualifiers.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
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
    subscripts. Prose, charges, symbolic stoichiometry and algebra stay literal.
    This changes notation only: it never reorders atoms or expands abbreviations.
    """

    text = scientific_text(value)
    formula = text.translate(str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789"))
    formula = re.sub(r"_\{(\d+(?:\.\d+)?)\}|_(\d+)", lambda m: m[1] or m[2], formula)
    if re.fullmatch(r"(?:[A-Z][a-z]?(?:\d+(?:\.\d+)?)?)+", formula):
        return formula
    return text


def is_plain_number(value: ReportedValue) -> bool:
    """Allow unit conversion only for a single unqualified, internally consistent number.

    Ranges, inequalities, uncertainties and chemical formulas do not qualify for
    this path. Their central numeric value alone does not represent the claim.
    The consistency check has no absolute allowance: a small nonzero raw number
    must never qualify as a parsed zero, regardless of the chosen unit.
    """

    raw = scientific_text(value.raw_value)
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)", raw)
    return bool(
        match
        and value.value_number is not None
        and math.isfinite(value.value_number)
        and math.isclose(float(match[1]), value.value_number, rel_tol=1e-9, abs_tol=0.0)
        and (
            scientific_text(match[2]) in {"", scientific_text(value.unit or "")}
            or (
                value.unit is not None
                and equivalent_unit_spelling(match[2], value.unit)
            )
        )
    )


_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
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

    match = _COMPOUND_QUANTITY.fullmatch(scientific_text(value.raw_value))
    if match is None:
        return None
    first, second = float(match[1]), float(match[3])
    kind: Literal["range", "uncertainty"] = (
        "uncertainty" if match[2] in {"±", "+/-"} else "range"
    )
    suffix = match[4]
    if suffix and (
        value.unit is None
        or (
            scientific_text(suffix) != scientific_text(value.unit)
            and not equivalent_unit_spelling(suffix, value.unit)
        )
    ):
        return None
    if not all(math.isfinite(number) for number in (first, second)):
        return None
    if kind == "range":
        if first > second or value.value_number is not None:
            return None
    elif second < 0 or (
        value.value_number is not None
        and not math.isclose(first, value.value_number, rel_tol=1e-9, abs_tol=0)
    ):
        return None
    if value.unit is None:
        return kind, first, second, None
    a, b = canonical_quantity(first, value.unit), canonical_quantity(second, value.unit)
    if a is None or b is None:
        return None
    if kind == "uncertainty":
        zero, one, two = (canonical_quantity(n, value.unit) for n in (0, 1, 2))
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

    literal = LiteralClaim(
        text=scientific_value_text(value.raw_value),
        unit=scientific_text(value.unit or ""),
        number=value.value_number,
        source_unit=value.unit,
        raw_text=scientific_text(value.raw_value),
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
        if (left.literal.source_unit is None) != (right.literal.source_unit is None):
            return False
        a, b = left.number, right.number
        if left.literal.source_unit is not None:
            if left.canonical is None or right.canonical is None:
                return (
                    left.literal.raw_text == right.literal.raw_text
                    and left.literal.source_unit == right.literal.source_unit
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


def claim_feature_values(claim: NormalizedClaim) -> tuple[object, ...]:
    """Produce the existing lexical inputs; rounding affects pairing only."""

    if isinstance(claim, ScalarClaim) and claim.canonical is not None:
        return format(claim.canonical.magnitude, ".12g"), claim.canonical.unit
    if isinstance(claim, CompoundClaim):
        return (
            claim.kind,
            format(claim.first, ".12g"),
            format(claim.second, ".12g"),
            claim.unit,
        )
    literal = claim if isinstance(claim, LiteralClaim) else claim.literal
    return literal.text, literal.source_unit
