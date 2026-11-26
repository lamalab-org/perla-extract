"""Interpret explicitly named photovoltaic measurement conditions once.

Identity matching and fact scoring consume the same concepts. Unknown fields stay
literal: measured outcomes never become protocol just because their values happen
to distinguish two observations. No conditions are inferred from other records.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Literal

from .models import PerformanceObservation, PopulationStatistic, ReportedValue
from .scoring_claims import CompoundClaim, ScalarClaim, equal_claims, normalize_claim
from .scoring_vocabulary import property_name
from .scoring_vocabulary import protocol_name as protocol_name
from .units import canonical_quantity

# A closed grammar separates a quantity from its stated method/statistic. Do not
# strip arbitrary words: "relative PCE improvement" is not an absolute PCE.
_PV_NAME = re.compile(
    r"(?:(?P<stat>(?:population[- ]+)?(?:average|mean))\s+)?"
    r"(?P<metric>pce|power conversion efficiency|ff|fill factor|voc|open circuit voltage|"
    r"jsc|short circuit current density)"
    r"(?P<method>\s+(?:extracted\s+)?from\s+(?:the\s+)?j[- ]?v(?:\s+curve)?)?"
)


def metric_definition(name: str) -> tuple[str, str | None, str | None, str | None]:
    """Return the base quantity and only qualifiers explicitly present in its name."""
    suffix = re.fullmatch(
        r"(.+?)\s*(?:\[([^\[\]()]+)\]|\(([^\[\]()]+)\))", name.strip()
    )
    unit = (suffix[2] or suffix[3]).strip() if suffix else None
    base = suffix[1].strip() if suffix else name
    # A full-name/abbreviation pair such as "fill factor (FF)" is a concept
    # alias, not a unit. The vocabulary validates that the two names agree.
    if unit and property_name(unit) == property_name(base):
        return property_name(name), None, None, None
    text = " ".join(base.casefold().replace("–", "-").replace("−", "-").split())
    match = _PV_NAME.fullmatch(text)
    if match:
        key = property_name(match["metric"])
        if unit:
            # Parentheses also carry qualifiers ("corrected", "min"). Only a
            # recognized, dimensionally appropriate unit may leave the name.
            expected = {"pce": "%", "ff": "%", "voc": "V", "jsc": "A/m2"}[key]
            stated = canonical_quantity(1, unit)
            target = canonical_quantity(1, expected)
            if stated is None or target is None or stated[1] != target[1]:
                return property_name(name), None, None, None
        return (
            key,
            "mean" if match["stat"] else None,
            "jv_scan" if match["method"] else None,
            unit,
        )
    return property_name(name), None, None, None


def metric_value(
    value: ReportedValue, quantity: str
) -> tuple[ReportedValue, Literal["field_name", "percentage_points"] | None]:
    """Apply the fixed percentage-point convention only after explicit unit evidence.

    Unitless absolute PCE/FF are expressed in percentage points: 0.2 means 0.2%.
    Explicit units in the unit field, label or raw claim retain their meaning.
    Qualified, inconsistent and unsupported raw claims are never repaired here.
    Each input is interpreted independently, without consulting a match.
    """
    if value.unit is not None and value.unit.strip():
        return value, None
    _, _, _, unit = metric_definition(value.name)
    if unit:
        return value.model_copy(update={"unit": unit}), "field_name"
    if quantity in {"pce", "ff"}:
        # An empty unit field is also absent. Parse the complete raw claim first
        # so an explicit fraction/other suffix cannot acquire a percentage scale.
        if value.unit is not None:
            value = value.model_copy(update={"unit": None})
        claim = normalize_claim(value)
        if isinstance(claim, (ScalarClaim, CompoundClaim)) and claim.unit is None:
            return value.model_copy(update={"unit": "%"}), "percentage_points"
    return value, None


def performance_name(name: str, measurement_type: str | None) -> str:
    """Eta denotes power efficiency only in an explicitly photovoltaic protocol."""
    key, _, _, _ = metric_definition(name)
    if key in {"η", "efficiency"} and measurement_type in {
        "jv_scan",
        "stabilized_power_output",
        "certified_measurement",
    }:
        return "pce"
    return protocol_name(name) or key


def protocol_claims(
    record: PerformanceObservation | PopulationStatistic,
) -> tuple[tuple[str, ReportedValue], ...]:
    """Keep complete conditions and their multiplicity, independent of metric order."""
    return tuple((item.name, item.value) for item in canonical_conditions(record))


@dataclass(frozen=True)
class MeasurementCondition:
    """One assertion and every original location of its redundant representation."""

    name: str
    value: ReportedValue
    source_paths: tuple[str, ...]
    supported: bool


def _condition_value(name: str, value: ReportedValue) -> ReportedValue:
    """Canonicalize explicit air-mass notation, preserving unknown spectrum prose."""
    if (
        name == "illuminationspectrum"
        and re.fullmatch(
            r"am\s*1[.]5\s*g(?:\s+(?:irradiation|illumination|spectrum))?",
            value.raw_value.strip(),
            re.I,
        )
        and value.value_number is None
        and value.unit is None
    ):
        return value.model_copy(update={"raw_value": "AM1.5G"})
    return value


def _condition_name(value: ReportedValue) -> str:
    """Recognize explicitly stated air-mass illumination as a spectrum condition."""
    name = protocol_name(value.name) or property_name(value.name)
    if (
        name in {"illumination", "illuminationstandard"}
        and _condition_value("illuminationspectrum", value).raw_value == "AM1.5G"
    ):
        return "illuminationspectrum"
    return name


def canonical_conditions(
    record: PerformanceObservation | PopulationStatistic, path: str = ""
) -> tuple[MeasurementCondition, ...]:
    """Read explicit conditions and registered legacy metrics through one boundary.

    Only an unambiguous, roundoff-equivalent copy across containers is redundant.
    Repeated assertions within a container and conflicting copies remain separate.
    Unknown explicit conditions retain their literal name; treatment and aging
    containers are never inspected. This function does not mutate either input.
    """
    legacy = [
        MeasurementCondition(
            _condition_name(value),
            _condition_value(_condition_name(value), value),
            (f"{path}/metrics/{i}",),
            True,
        )
        for i, value in enumerate(record.metrics)
        if protocol_name(value.name) is not None
    ]
    explicit = [
        MeasurementCondition(
            _condition_name(value),
            _condition_value(_condition_name(value), value),
            (f"{path}/measurement_conditions/{i}",),
            protocol_name(value.name) is not None,
        )
        for i, value in enumerate(record.measurement_conditions)
    ]
    result = list(legacy)
    for item in explicit:
        old = [i for i, other in enumerate(legacy) if other.name == item.name]
        new = [other for other in explicit if other.name == item.name]
        if len(old) == len(new) == 1 and equal_claims(
            normalize_claim(legacy[old[0]].value),
            normalize_claim(item.value),
            16 * sys.float_info.epsilon,
            0,
        ):
            previous = result[old[0]]
            result[old[0]] = MeasurementCondition(
                previous.name,
                previous.value,
                previous.source_paths + item.source_paths,
                previous.supported,
            )
        else:
            result.append(item)
    return tuple(result)
