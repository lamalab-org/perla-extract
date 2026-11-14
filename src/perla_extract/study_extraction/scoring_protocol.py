"""Interpret explicitly named photovoltaic measurement conditions once.

Identity matching and fact scoring consume the same concepts. Unknown fields stay
literal: measured outcomes never become protocol just because their values happen
to distinguish two observations. No conditions are inferred from other records.
"""

from __future__ import annotations

from .models import PerformanceObservation, PopulationStatistic, ReportedValue
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
    return tuple(
        (name, value)
        for value in record.metrics
        if (name := protocol_name(value.name)) is not None
    )
