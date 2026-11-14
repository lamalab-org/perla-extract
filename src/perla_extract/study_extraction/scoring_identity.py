"""Record identity features used before comparing scientific outcomes.

Every supported record type declares its identity fields here. Measured outcomes
and their completeness cannot influence these features. Parent relationships and
assignment ambiguity are handled by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import (
    DeviceFamily,
    IndividualDevice,
    PerformanceObservation,
    PopulationStatistic,
    ReportedValue,
    StabilityTest,
)
from .scoring_claims import claim_feature_values, normalize_claim, scientific_text
from .scoring_facts import property_name
from .scoring_vocabulary import material_name


def _tokens(values: Iterable[object]) -> set[str]:
    return {
        token
        for value in values
        if value is not None
        for token in re.sub(r"[^a-z0-9.+%-]+", " ", str(value).casefold()).split()
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 1.0


def record_similarity(left: object, right: object) -> float:
    """Compare declared identity and protocol, independently of measured outcomes."""

    # A family describes a design. Recipe completeness and measured properties
    # must not determine its identity or suppress credit for unchanged children.
    if isinstance(left, DeviceFamily) and isinstance(right, DeviceFamily):
        return _jaccard(_family_features(left), _family_features(right))
    return _jaccard(_identity_features(left), _identity_features(right))


def _identity_features(record: object) -> set[str]:
    """Keep specimen labels and experimental protocol; exclude results and counts.

    Complete field values preserve distinctions such as A/B versus B/A. Device
    properties, performance metrics, population sizes and stability outcomes are
    scored after pairing and cannot establish that pairing. Stability conditions
    and checkpoint times describe the protocol, not its measured response.
    """

    fields: tuple[str, ...]
    if isinstance(record, IndividualDevice):
        fields = ("label", "variant", "champion_status", "selection_basis")
    elif isinstance(record, PerformanceObservation):
        fields = ("measurement_type", "scan_direction")
    elif isinstance(record, PopulationStatistic):
        fields = ("label", "statistic_type")
    elif isinstance(record, StabilityTest):
        fields = ("link_status",) + (
            ("specimen_label",) if record.device_id is None else ()
        )
    else:
        raise TypeError(f"No record identity rule for {type(record).__name__}")
    features = {
        f"{name}:{scientific_text(str(value))}"
        for name in fields
        if (value := getattr(record, name)) not in (None, "", "not_reported")
    }
    features.add(f"record_type:{type(record).__name__}")
    if isinstance(record, StabilityTest):

        def protocol_value(scope: str, value: ReportedValue) -> None:
            features.add(
                repr(
                    (
                        scope,
                        property_name(value.name),
                        claim_feature_values(normalize_claim(value)),
                    )
                )
            )

        for condition in record.conditions:
            protocol_value("test", condition)
        for checkpoint in record.checkpoints:
            if checkpoint.time is not None:
                protocol_value("checkpoint_time", checkpoint.time)
            for condition in checkpoint.conditions:
                protocol_value("checkpoint_condition", condition)
    return features


def _family_features(family: DeviceFamily) -> set[str]:
    """Pair designs using structure, never outcomes or verbosity of annotations.

    Materials and their ordered positions identify the stack; constituent names
    and absorber formulas add composition. Variant distinguishes designs when the
    source supplies it. These features select candidates, not scientific credit.
    """

    values: list[object] = [family.variant]
    for layer in family.layers:
        values.append(layer.material)
        values.extend(c.name for c in layer.constituents)
    for absorber in family.absorbers:
        values.append(absorber.formula.raw_value if absorber.formula else None)
        values.extend(c.name for c in absorber.constituents)
    if not family.layers:
        values.append(family.full_stack_raw or family.architecture)
    features = _tokens(material_name(v) for v in values if isinstance(v, str))
    # Lexical tokens support candidate recall; exact structural features retain
    # chemical case and sequence, which tokenization must never flatten away.
    features.update(
        f"material:{material_name(v)}" for v in values[1:] if isinstance(v, str)
    )
    if family.variant:
        features.add(f"variant:{scientific_text(family.variant)}")
    for layer in family.layers:
        if layer.sequence is not None:
            features.add(
                f"layer:{layer.sequence}:material:{material_name(layer.material)}"
            )
            features.update(
                f"layer:{layer.sequence}:{token}"
                for token in _tokens([material_name(layer.material)])
            )
    return features
