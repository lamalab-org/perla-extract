"""Interpret explicitly named photovoltaic measurement conditions once.

Identity matching and fact scoring consume the same concepts. Unknown fields stay
literal: measured outcomes never become protocol just because their values happen
to distinguish two observations. No conditions are inferred from other records.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from .models import PerformanceObservation, PopulationStatistic, ReportedValue
from .scoring_claims import equal_claims, normalize_claim
from .scoring_vocabulary import property_name

PROTOCOL_NAMES = {
    "illuminationintensity": "illuminationintensity",
    "irradiance": "illuminationintensity",
    "illumination": "illumination",
    "illuminationspectrum": "illuminationspectrum",
    "illuminationstandard": "illuminationstandard",
    "scanrate": "scanrate",
    "jvscanrate": "scanrate",
    "scanspeed": "scanrate",
    "scanvoltagerange": "scanvoltagerange",
    "voltagestep": "voltagestep",
    "measurementtemperature": "measurementtemperature",
    "measurementatmosphere": "measurementatmosphere",
    "measurementcondition": "measurementcondition",
    "storagebeforemeasurement": "storagebeforemeasurement",
    "storageinglovebox": "storageinglovebox",
}


def protocol_name(name: str) -> str | None:
    """Recognize declared measurement conditions; unknown labels stay unclassified."""
    return PROTOCOL_NAMES.get(property_name(name))


def performance_name(name: str, measurement_type: str | None) -> str:
    """Eta denotes power efficiency only in an explicitly photovoltaic protocol."""
    key = property_name(name)
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
        MeasurementCondition(name, value, (f"{path}/metrics/{i}",), True)
        for i, value in enumerate(record.metrics)
        if (name := protocol_name(value.name)) is not None
    ]
    explicit = [
        MeasurementCondition(
            protocol_name(value.name) or property_name(value.name),
            value,
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
