"""Interpret complete reported quantities independently of scoring and export.

Raw source text is immutable. Known quantity definitions constrain units and the
few explicit conventions; unsupported wording remains a literal claim. Physical
consistency of raw and parsed fields is checked before numeric credit or export.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from .models import ReportedValue
from .quantity_specs import (
    BASIS_PERCENT_UNITS,
    QUANTITY_SPECS,
    basis_percent_unit,
    resolve_quantity_kind,
)
from .units import canonical_quantity, convert_quantity, equivalent_unit_spelling


@lru_cache(maxsize=8192)
def scientific_text(value: str) -> str:
    """Normalize spacing without flattening chemical superscripts or subscripts.

    Compatibility normalization (NFKC) conflates charge and atom-count notation,
    for example Fe³⁺ and Fe₃⁺. NFC preserves those scientific distinctions.
    """

    return " ".join(unicodedata.normalize("NFC", value).replace("−", "-").split())


@lru_cache(maxsize=8192)
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


@dataclass(frozen=True)
class LiteralClaim:
    """Retain unsupported source wording and distinguish explicit contradictions."""

    text: str
    unit: str
    number: float | None
    source_unit: str | None
    raw_text: str
    status: Literal["literal", "contradiction"] = "literal"
    reason: str = "unsupported_form"


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
    qualifier: Literal["approximate"] | None = None


@dataclass(frozen=True)
class CompoundClaim:
    kind: Literal["range", "uncertainty"]
    first: float
    second: float
    unit: str | None
    literal: LiteralClaim


NormalizedClaim = ScalarClaim | CompoundClaim | LiteralClaim


@dataclass(frozen=True)
class QuantityInterpretation:
    status: Literal["normalized", "literal", "contradiction"]
    reason: str
    claim: NormalizedClaim
    kind: str | None
    unit_source: str | None = None


_COMPOUND_QUANTITY = re.compile(
    rf"({_NUMBER})\s*(±|\+/-|–|—|\bto\b|-)\s*({_NUMBER})\s*(.*)"
)
_APPROXIMATE = re.compile(r"(?:[~∼≈]|approximately\b|about\b|approx\.?)\s*(.+)", re.I)
_SCALAR = re.compile(rf"({_NUMBER})\s*(.*)")


def _quantity_unit(unit: str | None, kind: str | None) -> str | None:
    if (basis_unit := basis_percent_unit(unit)) is not None:
        return basis_unit
    if unit is not None and (spec := QUANTITY_SPECS.get(kind or "")) is not None:
        for alias, standard in spec.unit_aliases:
            if scientific_text(unit) == alias:
                return standard
    if (
        kind == "relative_humidity"
        and unit is not None
        and re.fullmatch(
            r"(?:%|percent|percentage)\s*(?:RH|relative\s+humidity)", unit.strip(), re.I
        )
    ):
        return "%"
    return unit.strip() if unit is not None and unit.strip() else None


def _basis_unit(unit: str) -> tuple[float, str] | None:
    if unit in BASIS_PERCENT_UNITS:
        return 0.01, BASIS_PERCENT_UNITS[unit]
    if unit in BASIS_PERCENT_UNITS.values():
        return 1.0, unit
    return None


def _base_quantity(number: float, unit: str) -> tuple[float, str] | None:
    basis = _basis_unit(unit)
    return (number * basis[0], basis[1]) if basis else canonical_quantity(number, unit)


def _convert_physical(number: float, unit: str, target: str) -> float | None:
    source_basis = _basis_unit(unit)
    target = basis_percent_unit(target) or target
    target_basis = _basis_unit(target)
    if source_basis or target_basis:
        if source_basis and target_basis and source_basis[1] == target_basis[1]:
            return number * source_basis[0] / target_basis[0]
        return None
    return convert_quantity(number, unit, target)


def _same_unit_scale(left: str, right: str) -> bool:
    if _basis_unit(left) or _basis_unit(right):
        return left == right
    return equivalent_unit_spelling(left, right)


def _canonical(number: float, unit: str, kind: str | None) -> tuple[float, str] | None:
    spec = QUANTITY_SPECS.get(kind or "")
    if spec:
        number_in_target = _convert_physical(number, unit, spec.unit)
        return (
            _base_quantity(number_in_target, spec.unit)
            if number_in_target is not None
            else None
        )
    return _base_quantity(number, unit)


def _consistency_issue(
    raw_number: float,
    raw_unit: str | None,
    parsed: float | None,
    parsed_unit: str | None,
) -> Literal["raw_parsed_conflict", "ambiguous_unit_scale"] | None:
    if parsed is None:
        if not raw_unit or not parsed_unit or _same_unit_scale(raw_unit, parsed_unit):
            return None
        # A different scale may describe an intended converted convenience field,
        # but no parsed magnitude establishes that conversion. Preserve ambiguity.
        return (
            "ambiguous_unit_scale"
            if _convert_physical(1, raw_unit, parsed_unit) is not None
            else "raw_parsed_conflict"
        )
    if raw_unit and parsed_unit:
        converted = _convert_physical(parsed, parsed_unit, raw_unit)
        if converted is not None:
            return (
                None
                if math.isclose(raw_number, converted, rel_tol=1e-9, abs_tol=0)
                else "raw_parsed_conflict"
            )
        if scientific_text(raw_unit) != scientific_text(parsed_unit):
            return "raw_parsed_conflict"
    return (
        None
        if math.isclose(raw_number, parsed, rel_tol=1e-9, abs_tol=0)
        else "raw_parsed_conflict"
    )


def interpret_quantity(
    value: ReportedValue, kind: str | None = None
) -> QuantityInterpretation:
    """Interpret one input without consulting its paired reference or prediction."""

    kind = kind if kind is not None else resolve_quantity_kind(value.name)
    name_unit = None
    label = re.fullmatch(
        r"(.+?)\s*(?:\[([^\[\]()]+)\]|\(([^\[\]()]+)\))", value.name.strip()
    )
    if label:
        candidate_kind = kind or resolve_quantity_kind(label[1])
        suffix = (label[2] or label[3]).strip()
        candidate_unit = _quantity_unit(suffix, candidate_kind)
        spec = QUANTITY_SPECS.get(candidate_kind or "")
        if (
            spec is not None
            and candidate_unit is not None
            and _convert_physical(1, candidate_unit, spec.unit) is not None
        ):
            kind, name_unit = candidate_kind, candidate_unit
        elif (
            candidate_kind is not None
            and resolve_quantity_kind(suffix) == candidate_kind
        ):
            kind = candidate_kind
    return _interpret_fields(
        kind,
        value.raw_value,
        value.value_number,
        value.unit,
        name_unit,
    )


@lru_cache(maxsize=8192)
def _interpret_fields(
    kind: str | None,
    raw: str,
    number: float | None,
    unit: str | None,
    name_unit: str | None,
) -> QuantityInterpretation:
    literal = LiteralClaim(
        scientific_value_text(raw),
        scientific_text(unit or ""),
        number,
        unit,
        scientific_text(raw),
    )
    unit_source = "reported" if unit is not None and unit.strip() else None

    def fallback(reason: str, *, contradiction: bool = False) -> QuantityInterpretation:
        claim = LiteralClaim(
            literal.text,
            literal.unit,
            literal.number,
            literal.source_unit,
            literal.raw_text,
            "contradiction" if contradiction else "literal",
            reason,
        )
        return QuantityInterpretation(claim.status, reason, claim, kind, unit_source)

    text = _quantity_text(raw)
    approximation = _APPROXIMATE.fullmatch(text)
    qualifier: Literal["approximate"] | None = "approximate" if approximation else None
    if approximation:
        text = approximation[1]
    compound = _COMPOUND_QUANTITY.fullmatch(text) if qualifier is None else None
    scalar = _SCALAR.fullmatch(text) if compound is None else None
    form: Literal["scalar", "range", "uncertainty"]
    if compound:
        first, second = _finite_number(compound[1]), _finite_number(compound[3])
        form = "uncertainty" if compound[2] in {"±", "+/-"} else "range"
        suffix = compound[4].strip()
    elif scalar:
        first, second, form = _finite_number(scalar[1]), None, "scalar"
        suffix = scalar[2].strip()
    else:
        return fallback("unsupported_form")
    if first is None or (compound and second is None):
        return fallback("nonfinite_or_underflow")

    raw_unit = _quantity_unit(suffix or None, kind)
    parsed_unit = _quantity_unit(unit, kind)
    effective_unit = raw_unit or parsed_unit or name_unit
    unit_source = (
        "raw_value"
        if raw_unit and (not parsed_unit or not _same_unit_scale(raw_unit, parsed_unit))
        else "reported"
        if parsed_unit
        else "field_name"
        if name_unit
        else None
    )
    spec = QUANTITY_SPECS.get(kind or "")
    if effective_unit is None and spec is not None and spec.percentage_points:
        effective_unit = "%"
        unit_source = "percentage_points"

    # An unfamiliar suffix may be prose or unsupported unit notation. It does
    # not prove a physical contradiction with a supplied unit. Keep the complete
    # literal unless both unit expressions can actually be interpreted.
    if any(
        _base_quantity(first, candidate) is None
        for candidate in (raw_unit, parsed_unit)
        if candidate is not None
    ):
        return fallback("unsupported_unit")

    if form == "range":
        if second is None or first > second:
            return fallback("invalid_range")
        if number is not None:
            return fallback("range_has_scalar", contradiction=True)
    elif form == "uncertainty" and (second is None or second < 0):
        return fallback("invalid_uncertainty")

    consistency_issue = _consistency_issue(
        first, raw_unit or effective_unit, number, parsed_unit
    )
    if consistency_issue is not None:
        if (
            consistency_issue == "raw_parsed_conflict"
            and raw_unit
            and parsed_unit
            and not _same_unit_scale(raw_unit, parsed_unit)
            and re.search(r"\s", raw_unit)
            and not re.search(r"[/^*·°⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻+−-]", raw_unit)
        ):
            # Juxtaposed tokens can be a split prefix, qualifier, or an OCR-lost
            # exponent. A generic unit parser accepting them as a product does
            # not establish that this is the source's intended physical meaning.
            return fallback("ambiguous_unit_notation")
        return fallback(
            consistency_issue, contradiction=consistency_issue == "raw_parsed_conflict"
        )

    if effective_unit is not None:
        generic = _base_quantity(first, effective_unit)
        canonical = _canonical(first, effective_unit, kind)
        if generic is not None and canonical is None and spec:
            return fallback("incompatible_dimension", contradiction=True)
    else:
        canonical = None

    if form == "scalar":
        # Preserve the old literal fallback for unfamiliar unit spellings. Exact
        # agreement is still possible without giving the unit an invented meaning.
        claim = ScalarClaim(
            first,
            effective_unit,
            CanonicalQuantity(*canonical) if canonical else None,
            literal,
            qualifier,
        )
        status: Literal["literal", "normalized"] = (
            "literal"
            if effective_unit is not None and canonical is None
            else "normalized"
        )
        reason = (
            "unsupported_unit"
            if status == "literal"
            else "approximate"
            if qualifier
            else "scalar"
        )
        return QuantityInterpretation(status, reason, claim, kind, unit_source)

    assert second is not None
    if effective_unit is None:
        a, b, canonical_unit = first, second, None
    elif canonical is None:
        return fallback("unsupported_unit")
    else:
        a, canonical_unit = canonical
        if form == "range":
            end = _canonical(second, effective_unit, kind)
            if end is None:
                return fallback("unsupported_unit")
            b = end[0]
        else:
            zero, one, two = (_canonical(n, effective_unit, kind) for n in (0, 1, 2))
            if zero is None or one is None or two is None:
                return fallback("unsupported_unit")
            scale = one[0] - zero[0]
            check = _canonical(second, effective_unit, kind)
            if (
                check is None
                or scale <= 0
                or not all(
                    math.isclose(
                        converted,
                        zero[0] + scale * original,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                    for original, converted in (
                        (2, two[0]),
                        (first, a),
                        (second, check[0]),
                    )
                )
            ):
                return fallback("nonlinear_uncertainty")
            b = second * scale
    if not math.isfinite(a) or not math.isfinite(b):
        return fallback("nonfinite_or_underflow")
    result = CompoundClaim(form, a, b, canonical_unit, literal)
    return QuantityInterpretation("normalized", form, result, kind, unit_source)


def normalize_claim(value: ReportedValue) -> NormalizedClaim:
    return interpret_quantity(value).claim


def is_plain_number(value: ReportedValue) -> bool:
    """Identify unqualified scalars with a supplied, consistent convenience number."""

    result = interpret_quantity(value)
    return (
        value.value_number is not None
        and isinstance(result.claim, ScalarClaim)
        and result.claim.qualifier is None
    )


def convert_reported_value(
    value: ReportedValue, target_unit: str, *, allow_qualified: bool = False
) -> float | None:
    """Project only a complete scalar; qualified centers need an explicit caller policy.

    A caller allowing a qualified center must preserve the raw claim and qualifier
    alongside its scalar destination. Bounds, ranges and arbitrary literal text
    never acquire a center through the convenience-number field.
    """

    result = interpret_quantity(value)
    claim = result.claim
    if result.status != "normalized":
        return None
    if isinstance(claim, ScalarClaim):
        if claim.qualifier is not None and not allow_qualified:
            return None
        return (
            _convert_physical(claim.number, claim.unit, target_unit)
            if claim.unit is not None
            else None
        )
    if (
        allow_qualified
        and isinstance(claim, CompoundClaim)
        and claim.kind == "uncertainty"
        and claim.unit is not None
    ):
        return _convert_physical(claim.first, claim.unit, target_unit)
    return None


def equal_claims(
    left: NormalizedClaim, right: NormalizedClaim, relative: float, absolute: float
) -> bool:
    """Compare full claims; identical contradictions do not earn numerical credit."""

    if any(
        isinstance(claim, LiteralClaim) and claim.status == "contradiction"
        for claim in (left, right)
    ):
        return False
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
        if left.qualifier != right.qualifier or (left.unit is None) != (
            right.unit is None
        ):
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
