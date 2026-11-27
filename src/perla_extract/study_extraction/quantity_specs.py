"""Scientific quantity definitions shared by validation, scoring and export.

Names identify a quantity, not its owner or experimental role. For example,
temperature during preparation and temperature during aging use the same units
but their surrounding records still establish different scientific assertions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class QuantitySpec:
    kind: str
    names: tuple[str, ...]
    unit: str
    percentage_points: bool = False
    unit_aliases: tuple[tuple[str, str], ...] = ()


QUANTITY_SPECS = {
    spec.kind: spec
    for spec in (
        QuantitySpec("pce", ("PCE", "power conversion efficiency"), "%", True),
        QuantitySpec("ff", ("FF", "fill factor"), "%", True),
        QuantitySpec("voc", ("Voc", "open circuit voltage"), "V"),
        QuantitySpec("jsc", ("Jsc", "short circuit current density"), "A/m2"),
        QuantitySpec(
            "temperature",
            (
                "temperature",
                "annealing temperature",
                "measurement temperature",
                "treatment temperature",
            ),
            "K",
            unit_aliases=(("C", "°C"), ("ºC", "°C")),
        ),
        QuantitySpec(
            "duration",
            (
                "duration",
                "time",
                "annealing duration",
                "annealing time",
                "treatment duration",
            ),
            "s",
        ),
        QuantitySpec(
            "thickness", ("thickness", "layer thickness", "film thickness"), "m"
        ),
        QuantitySpec(
            "area", ("area", "active area", "masked active area", "device area"), "m2"
        ),
        QuantitySpec(
            "spin_speed",
            ("spin speed", "spin-coating speed", "spin coating speed"),
            "rpm",
        ),
        QuantitySpec("relative_humidity", ("relative humidity", "RH"), "%"),
    )
}


def quantity_name_key(name: str) -> str:
    return re.sub(r"[\s_-]+", "", name.casefold())


QUANTITY_ALIASES = {
    quantity_name_key(name): kind
    for kind, spec in QUANTITY_SPECS.items()
    for name in spec.names
}

# These explicit unit bases remain distinct from ordinary dimensionless percent.
# They do not create new property-name concepts in the quantity registry.
BASIS_PERCENT_UNITS = {"wt%": "mass_fraction", "vol%": "volume_fraction"}
_BASIS_PERCENT_ALIASES = {
    **dict.fromkeys(
        ("wt%", "wtpercent", "weight%", "weightpercent", "mass%", "masspercent"), "wt%"
    ),
    **dict.fromkeys(("vol%", "volpercent", "volume%", "volumepercent"), "vol%"),
}


def basis_percent_unit(unit: str | None) -> str | None:
    """Recognize a complete explicit mass/volume-percent unit, retaining its basis."""

    return (
        _BASIS_PERCENT_ALIASES.get(re.sub(r"[\s.]+", "", unit.casefold()))
        if unit
        else None
    )


def resolve_quantity_kind(name: str) -> str | None:
    """Recognize complete checked names; unknown qualifiers keep names unresolved."""

    key = quantity_name_key(name)
    direct = QUANTITY_ALIASES.get(key)
    if direct:
        return direct
    # These qualifiers change a measurement's context but not its physical units.
    # They are not discarded from the source name or the scoring context.
    match = re.fullmatch(
        r"(?:certified|mean|average|population average)\s+(.+)", name.strip(), re.I
    )
    if match:
        kind = QUANTITY_ALIASES.get(quantity_name_key(match[1]))
        return kind if kind in {"pce", "ff", "voc", "jsc"} else None
    return None
