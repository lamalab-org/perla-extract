"""Record identity features used before comparing scientific outcomes.

Every supported record type declares its identity fields here. Measured outcomes
and their completeness cannot influence these features. Parent relationships and
assignment ambiguity are handled by the caller.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from .identity_text import identity_text, identity_tokens
from .models import (
    DeviceFamily,
    IndividualDevice,
    PerformanceObservation,
    PopulationStatistic,
    StabilityTest,
    StudyExtraction,
)
from .scoring_context import (
    UnorderedContext,
    device_identity,
    device_treatment,
    equal_value,
    has_subject_identity,
    subject_context,
)
from .scoring_layers import ordered_layers, stack_similarity
from .scoring_protocol import canonical_conditions
from .scoring_vocabulary import (
    material_name,
    material_stage,
    property_name,
)


def sample_code(
    record: DeviceFamily | IndividualDevice | PopulationStatistic,
) -> str | None:
    """Read an explicitly named compact sample code, preserving case and punctuation.

    Device A and Device A, Table 1 identify the same named sample. Only a single
    letter sequence or a token containing digits qualifies. Prose such as 'control device'
    and generated JSON identifiers supply no code. Multiple codes stay unresolved.
    """
    codes = set()
    token = r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*"
    for raw in (record.label, getattr(record, "variant", None)):
        text = identity_text(raw)
        candidates = re.findall(
            rf"\b(?:device|sample|cell|specimen|film)\s+({token})\b",
            text,
            re.I,
        )
        if re.fullmatch(token, text):
            candidates.append(text)
        for code in candidates:
            if re.fullmatch(r"[A-Za-z](?:[-_/][A-Za-z])*", code) or any(
                c.isdigit() for c in code
            ):
                codes.add(code)
    return next(iter(codes)) if len(codes) == 1 else None


MeasurementUse = tuple[str, str | None]


def family_label_features(families: list[DeviceFamily]) -> dict[str, set[str]]:
    """Remove words shared by every design label before using labels to align arms.

    Generic headings such as 'perovskite solar cell' must not choose between
    otherwise indistinguishable designs. Use each study's own labels only; no
    words are learned from the other study or from measurement outcomes.
    """
    words = {family.family_id: identity_tokens(family.label) for family in families}
    common = set.intersection(*words.values()) if len(words) > 1 else set()
    return {key: value - common for key, value in words.items()}


def device_measurements(
    study: StudyExtraction,
) -> dict[str, frozenset[MeasurementUse]]:
    """Describe specimen use through explicit measurement kind and scan direction.

    Multiplicity, values, metric names and annotation length have no weight. An
    unreported or generic 'other' method supplies no identity evidence. Missing or
    inapplicable scan direction supplies no direction evidence.
    """
    uses: dict[str, set[MeasurementUse]] = {}
    for observation in study.performance_observations:
        if observation.measurement_type not in {"not_reported", "other"}:
            direction = (
                observation.scan_direction
                if observation.scan_direction in {"forward", "reverse"}
                else None
            )
            uses.setdefault(observation.device_id, set()).add(
                (observation.measurement_type, direction)
            )
    return {key: frozenset(value) for key, value in uses.items()}


def measurement_use_similarity(
    reference: frozenset[MeasurementUse], prediction: frozenset[MeasurementUse]
) -> float:
    """Corroborate a specimen using reference-reported measurement metadata.

    Method sets use Jaccard overlap. If the reference reports scan directions,
    average that overlap with direction overlap for those methods. Unreported
    prediction directions add no corroboration; they never exclude a candidate.
    Extra directions for methods without reference scan metadata are ignored.
    Outcome values and the number of repeated observations cannot affect this.
    """
    methods = ({kind for kind, _ in reference}, {kind for kind, _ in prediction})
    overlap = (
        len(methods[0] & methods[1]) / len(methods[0] | methods[1])
        if all(methods)
        else 0.0
    )
    known = {(kind, scan) for kind, scan in reference if scan is not None}
    if not known:
        return overlap
    measured = {kind for kind, _ in known}
    stated = {
        (kind, scan)
        for kind, scan in prediction
        if kind in measured and scan is not None
    }
    directions = len(known & stated) / len(known | stated)
    return (overlap + directions) / 2


def measurement_subject(record: object) -> tuple[str, str] | None:
    """Identify a measurement's subject independently of its outcome container.

    An observation ID groups fields in JSON; its explicit device link supplies
    scientific ownership. Full measurement context is still required per fact.
    Cohorts and stability tests retain their own identity: the schema does not
    provide an independent cohort/test subject that would justify pooling them.
    A missing link is never replaced by paper-wide ownership.
    """
    if isinstance(record, PerformanceObservation) and record.device_id is not None:
        return "individual_devices", record.device_id
    return None


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
    left: object,
    right: object,
    relative: float,
    absolute: float,
    *,
    family_labels: tuple[set[str], set[str]] | None = None,
) -> float:
    """Compare declared identity and protocol, independently of measured outcomes."""

    # A family describes a design. Recipe completeness and measured properties
    # must not determine its identity or suppress credit for unchanged children.
    if isinstance(left, DeviceFamily) and isinstance(right, DeviceFamily):
        structural = stack_similarity(left, right, label_features=family_labels)
        if structural is not None:
            return structural
        return _jaccard(_family_features(left), _family_features(right))
    if isinstance(left, PopulationStatistic) and isinstance(right, PopulationStatistic):
        if (
            left.subjects
            and right.subjects
            and all(has_subject_identity(s) for s in (*left.subjects, *right.subjects))
        ):
            return float(
                equal_value(
                    UnorderedContext(
                        tuple(
                            subject_context(s, identity_only=True)
                            for s in left.subjects
                        )
                    ),
                    UnorderedContext(
                        tuple(
                            subject_context(s, identity_only=True)
                            for s in right.subjects
                        )
                    ),
                    relative,
                    absolute,
                )
            )
    if isinstance(left, IndividualDevice) and isinstance(right, IndividualDevice):
        if device_treatment(left) is not None and device_treatment(right) is not None:
            return float(
                equal_value(
                    device_identity(left, identity_only=True),
                    device_identity(right, identity_only=True),
                    relative,
                    absolute,
                )
            )
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

    Literal device-label words allow reordered and partially paraphrased labels.
    Internal separators preserve distinctions such as A/B versus B/A. Device
    properties, outcome metrics and population sizes are scored after pairing
    and cannot establish that pairing. Explicit protocols are compared separately
    with the same numeric tolerance used for fact credit.
    """

    fields: tuple[str, ...]
    if isinstance(record, IndividualDevice):
        fields = ("variant", "champion_status", "selection_basis")
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
        f"{name}:{identity_text(str(value))}"
        for name in fields
        if (value := getattr(record, name)) not in (None, "", "not_reported")
        and identity_text(str(value))
    }
    features.add(f"record_type:{type(record).__name__}")
    if isinstance(record, IndividualDevice):
        features.update(f"label:{token}" for token in identity_tokens(record.label))
    return features


def _protocol(record: object) -> tuple[tuple[object, ...], ...]:
    if isinstance(record, (PerformanceObservation, PopulationStatistic)):
        # Explicit but unfamiliar conditions remain required fact context. They
        # cannot identify observations: an outcome misplaced here is not an anchor.
        return tuple(
            (item.name, item.value)
            for item in canonical_conditions(record)
            if item.supported
        )
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
    left: DeviceFamily,
    right: DeviceFamily,
    label_features: tuple[set[str], set[str]] | None = None,
) -> dict[str, list[str]]:
    """Expose the ordered materials, or fallback features, used to pair a family."""
    left_code, right_code = sample_code(left), sample_code(right)
    codes = {
        "truth_sample_code": [left_code] if left_code else [],
        "prediction_sample_code": [right_code] if right_code else [],
        "truth_label_tokens": sorted(identity_tokens(left.label)),
        "prediction_label_tokens": sorted(identity_tokens(right.label)),
        "truth_distinguishing_label_tokens": sorted(label_features[0])
        if label_features
        else [],
        "prediction_distinguishing_label_tokens": sorted(label_features[1])
        if label_features
        else [],
    }
    left_order, right_order = ordered_layers(left), ordered_layers(right)
    if left_order is not None and right_order is not None:
        return {
            "truth_stack": [material_name(layer.material) for _, layer in left_order],
            "prediction_stack": [
                material_name(layer.material) for _, layer in right_order
            ],
            "truth_variant": [identity_text(left.variant)]
            if identity_text(left.variant)
            else [],
            "prediction_variant": [identity_text(right.variant)]
            if identity_text(right.variant)
            else [],
            **codes,
        }
    a, b = _family_features(left), _family_features(right)
    return {
        **codes,
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

    values: list[object] = [identity_text(family.variant)]
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
    if variant := identity_text(family.variant):
        features.add(f"variant:{variant}")
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
