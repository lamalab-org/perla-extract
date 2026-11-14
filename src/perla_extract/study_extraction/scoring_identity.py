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
    StabilityTest,
)
from .scoring_claims import scientific_text
from .scoring_context import UnorderedContext, equal_value
from .scoring_protocol import protocol_claims
from .scoring_vocabulary import material_name, material_stage, property_name


def _tokens(values: Iterable[object]) -> set[str]:
    return {
        token
        for value in values
        if value is not None
        for token in re.sub(r"[^a-z0-9.+%-]+", " ", str(value).casefold()).split()
    }


def _jaccard(left: set[str], right: set[str]) -> float:
    return len(left & right) / len(left | right) if left or right else 1.0


def record_similarity(
    left: object, right: object, relative: float, absolute: float
) -> float:
    """Compare declared identity and protocol, independently of measured outcomes."""

    # A family describes a design. Recipe completeness and measured properties
    # must not determine its identity or suppress credit for unchanged children.
    if isinstance(left, DeviceFamily) and isinstance(right, DeviceFamily):
        return _jaccard(_family_features(left), _family_features(right))
    a, b = _identity_features(left), _identity_features(right)
    shared, total = len(a & b), len(a | b)
    left_protocol, right_protocol = _protocol(left), _protocol(right)
    if left_protocol or right_protocol:
        # Missing conditions do not prove that two measurements differ. Compare
        # only explicitly shared condition names for identity; strict fact context
        # still requires the complete protocol. This preserves ambiguity when a
        # second observation differs only in annotation completeness.
        names = {v[:-1] for v in left_protocol} & {v[:-1] for v in right_protocol}
        a_protocol = UnorderedContext(
            tuple(v for v in left_protocol if v[:-1] in names)
        )
        b_protocol = UnorderedContext(
            tuple(v for v in right_protocol if v[:-1] in names)
        )
        same = equal_value(a_protocol, b_protocol, relative, absolute)
        shared += int(same)
        total += 1 if same else 2
    return shared / total if total else 1.0


def _identity_features(record: object) -> set[str]:
    """Keep specimen labels and experimental protocol; exclude results and counts.

    Complete field values preserve distinctions such as A/B versus B/A. Device
    properties, outcome metrics and population sizes are scored after pairing
    and cannot establish that pairing. Explicit protocols are compared separately
    with the same numeric tolerance used for fact credit.
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
    return features


def _protocol(record: object) -> tuple[tuple[object, ...], ...]:
    if isinstance(record, (PerformanceObservation, PopulationStatistic)):
        return protocol_claims(record)
    if isinstance(record, StabilityTest):
        values = [("test", property_name(v.name), v) for v in record.conditions]
        for checkpoint in record.checkpoints:
            if checkpoint.time is not None:
                values.append(
                    (
                        "checkpoint_time",
                        property_name(checkpoint.time.name),
                        checkpoint.time,
                    )
                )
            values.extend(
                ("checkpoint_condition", property_name(v.name), v)
                for v in checkpoint.conditions
            )
        return tuple(values)
    return ()


def family_pair_evidence(
    left: DeviceFamily, right: DeviceFamily
) -> dict[str, list[str]]:
    """Expose the exact design features behind a selected family's Jaccard score."""
    a, b = _family_features(left), _family_features(right)
    return {
        "shared": sorted(a & b),
        "truth_only": sorted(a - b),
        "prediction_only": sorted(b - a),
    }


def _family_features(family: DeviceFamily) -> set[str]:
    """Pair designs using structure, never outcomes or verbosity of annotations.

    Materials and their ordered positions identify the stack; constituent names
    and absorber formulas add composition. Variant distinguishes designs when the
    source supplies it. These features select candidates, not scientific credit.
    """

    values: list[object] = [family.variant]
    for layer in family.layers:
        values.append(layer.material)
        values.extend(
            c.name for c in layer.constituents if material_stage(c.role) != "recipe"
        )
    for absorber in family.absorbers:
        values.append(absorber.formula.raw_value if absorber.formula else None)
        values.extend(
            c.name for c in absorber.constituents if material_stage(c.role) != "recipe"
        )
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
