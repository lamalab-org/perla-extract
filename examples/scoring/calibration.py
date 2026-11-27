"""Synthetic scientific controls with independently specified answers.

Context perturbations retain source-path lineage and explicit credit expectations.
Arithmetic and notation probes state their expected equivalence directly. These
are software checks, not new paper observations or expert calibration labels.
"""

from dataclasses import dataclass

from perla_extract.study_extraction.models import ReportedValue, StudyExtraction
from perla_extract.study_extraction.scoring_context import equal_value
from perla_extract.study_extraction.scoring_protocol import (
    performance_name,
    protocol_name,
)
from perla_extract.study_extraction.units import canonical_quantity

TRANSFORMS = (
    "protocol_relocation",
    "protocol_duplicate",
    "protocol_units",
    "voltage_range_alias",
    "missing_protocol",
    "conflicting_protocol",
    "measurement_type",
    "scan_direction",
    "wrong_value",
    "wrong_dimension",
    "raw_parsed_conflict",
    "missing_unit",
    "value_plus_relocation",
    "protocol_in_device_properties",
    "renamed_ids",
    "duplicate_prediction",
)


@dataclass
class CalibrationCase:
    name: str
    truth: StudyExtraction
    prediction: StudyExtraction
    allowed: tuple[str, ...]
    forbidden: tuple[str, ...]
    synthetic_paths: tuple[str, ...]
    source_records: dict[str, str]
    identity_unchanged: bool = False
    prediction_duplicates: int = 0


def calibration_cases(truth: StudyExtraction):
    """Use one explicitly linked, uniquely described measurement per paper.

    A synthetic voltage step supplies a known protocol. Its stated purpose and
    exact arithmetic are the oracle; neither is guessed from the source article.
    Keep only that measurement and its explicitly linked specimen/family. The
    source-record map preserves lineage; type/scan and voltage step are synthetic.
    No source citation is added. This is a local rule test, not an identity benchmark.
    """
    selected = next(
        (
            (i, o)
            for i, o in enumerate(truth.performance_observations)
            if any(d.device_id == o.device_id for d in truth.individual_devices)
            and any(
                performance_name(v.name, o.measurement_type)
                in {"pce", "voc", "jsc", "ff"}
                and v.value_number is not None
                and v.unit is not None
                for v in o.metrics
            )
            and all(
                protocol_name(v.name) != "voltagestep"
                for v in (*o.metrics, *o.measurement_conditions)
            )
        ),
        None,
    )
    if selected is None:
        return
    i, original = selected
    base = truth.model_copy(deep=True)
    observation = base.performance_observations[i]
    source_records = {"/performance_observations/0": f"/performance_observations/{i}"}
    devices = [
        (j, d)
        for j, d in enumerate(base.individual_devices)
        if d.device_id == observation.device_id
    ]
    device_index, device = devices[0]
    source_records["/individual_devices/0"] = f"/individual_devices/{device_index}"
    families = [
        (j, f)
        for j, f in enumerate(base.device_families)
        if f.family_id == device.family_id
    ]
    base.device_families = [f for _, f in families]
    if families:
        source_records["/device_families/0"] = f"/device_families/{families[0][0]}"
    base.individual_devices = [device]
    base.performance_observations = [observation]
    base.population_statistics = []
    base.stability_tests = []
    i = 0
    synthetic_paths = [
        "/performance_observations/0/measurement_type",
        "/performance_observations/0/scan_direction",
    ]
    observation.measurement_type = "jv_scan"
    observation.scan_direction = "reverse"
    observation.measurement_conditions.append(
        ReportedValue(
            name="voltage step",
            raw_value="0.011",
            value_number=0.011,
            unit="V",
            evidence=original.evidence,
        )
    )
    index = len(observation.measurement_conditions) - 1
    prefix = f"/performance_observations/{i}"
    protocol_path = f"{prefix}/measurement_conditions/{index}"
    outcomes = tuple(
        f"{prefix}/metrics/{j}"
        for j, v in enumerate(observation.metrics)
        if protocol_name(v.name) is None
    )
    target = next(
        j
        for j, v in enumerate(observation.metrics)
        if performance_name(v.name, observation.measurement_type)
        in {"pce", "voc", "jsc", "ff"}
        and v.value_number is not None
        and v.unit is not None
    )
    target_path = f"{prefix}/metrics/{target}"
    for name in TRANSFORMS:
        if name == "voltage_range_alias" and any(
            protocol_name(v.name) == "scanvoltagerange"
            for v in (*original.metrics, *original.measurement_conditions)
        ):
            # Adding a different range would make this a contradiction control,
            # not a pure synonym control. Count it as inapplicable for this paper.
            continue
        case_truth = base
        prediction = base.model_copy(deep=True)
        record = prediction.performance_observations[i]
        condition = record.measurement_conditions[index]
        allowed, forbidden = (*outcomes, protocol_path), ()
        if name in {"protocol_relocation", "value_plus_relocation"}:
            record.metrics.append(record.measurement_conditions.pop(index))
        if name == "protocol_duplicate":
            record.metrics.append(condition.model_copy(deep=True))
        if name == "protocol_units":
            condition.raw_value, condition.value_number, condition.unit = "11", 11, "mV"
        if name == "voltage_range_alias":
            case_truth = base.model_copy(deep=True)
            reference = case_truth.performance_observations[i].measurement_conditions[
                index
            ]
            for item in (reference, condition):
                item.name = "scan voltage range"
                item.raw_value, item.value_number, item.unit = (
                    "1.2 to -0.05 V",
                    None,
                    "V",
                )
            condition.name = "voltage scanning protocol"
        if name == "missing_protocol":
            record.measurement_conditions.pop(index)
            allowed, forbidden = (), (*outcomes, protocol_path)
        if name == "conflicting_protocol":
            condition.raw_value, condition.value_number = "0.022", 0.022
            allowed, forbidden = (), (*outcomes, protocol_path)
        if name in {"measurement_type", "scan_direction"}:
            if name == "measurement_type":
                record.measurement_type = (
                    "eqe_integrated_current"
                    if record.measurement_type != "eqe_integrated_current"
                    else "jv_scan"
                )
            else:
                record.scan_direction = (
                    "forward" if record.scan_direction == "reverse" else "reverse"
                )
            allowed, forbidden = (), (*outcomes, protocol_path)
        if name in {
            "wrong_value",
            "wrong_dimension",
            "raw_parsed_conflict",
            "missing_unit",
            "value_plus_relocation",
        }:
            value = record.metrics[target]
            missing_unit_has_same_meaning = False
            if name in {"wrong_value", "value_plus_relocation"}:
                # A distant value cannot equal another outcome of this sole
                # measurement. This precondition avoids accidental false passes.
                assert all(v.value_number != 1e12 for v in original.metrics)
                value.raw_value, value.value_number = "1000000000000", 1e12
            elif name == "wrong_dimension":
                value.raw_value, value.unit = str(value.value_number), "kg"
            elif name == "raw_parsed_conflict":
                value.raw_value = "1000000000000"
            else:
                # The oracle is unit arithmetic, independent of the matcher.
                # Removing % preserves PCE/FF meaning under the fixed convention;
                # removing a voltage/current-density unit still loses its meaning.
                missing_unit_has_same_meaning = performance_name(
                    value.name, record.measurement_type
                ) in {"pce", "ff"} and canonical_quantity(
                    value.value_number, value.unit
                ) == canonical_quantity(value.value_number, "%")
                value.raw_value, value.unit = str(value.value_number), None
            if not missing_unit_has_same_meaning:
                allowed = tuple(p for p in allowed if p != target_path)
                forbidden = (target_path,)
        if name == "protocol_in_device_properties":
            device = next(
                d
                for d in prediction.individual_devices
                if d.device_id == record.device_id
            )
            device.reported_properties.append(record.measurement_conditions.pop(index))
            allowed, forbidden = (), (*outcomes, protocol_path)
        if name == "renamed_ids":
            for device in prediction.individual_devices:
                old = device.device_id
                device.device_id += "-renamed"
                for item in prediction.performance_observations:
                    if item.device_id == old:
                        item.device_id = device.device_id
                for test in prediction.stability_tests:
                    if test.device_id == old:
                        test.device_id = device.device_id
        if name == "duplicate_prediction":
            record.metrics.append(record.metrics[target].model_copy(deep=True))
        yield CalibrationCase(
            name,
            case_truth,
            prediction,
            allowed,
            forbidden,
            tuple((*synthetic_paths, protocol_path)),
            source_records,
            identity_unchanged=name
            in {
                "wrong_value",
                "wrong_dimension",
                "raw_parsed_conflict",
                "missing_unit",
            },
            prediction_duplicates=int(name == "duplicate_prediction"),
        )


def check_case(case: CalibrationCase, evaluate):
    """Check explicit expectations; retain the full derived inputs only on failure."""
    for study in (case.truth, case.prediction):
        StudyExtraction.model_validate(study.model_dump())
    before = case.truth.model_dump_json()
    report = evaluate(case.truth, case.prediction)
    score = report.core_facts
    credited = {m.truth_path for m in score.matches}
    failures = []
    if set(case.allowed) - credited:
        failures.append(
            "Unexpected lost credit: " + repr(sorted(set(case.allowed) - credited))
        )
    if set(case.forbidden) & credited:
        failures.append(
            "Unexpected credit: " + repr(sorted(set(case.forbidden) & credited))
        )
    if case.identity_unchanged:
        if any(m.truth_id != m.predicted_id for m in report.matches):
            failures.append("Outcome change altered record correspondence")
        if len(
            [m for m in report.matches if m.kind == "performance_observations"]
        ) != len(case.truth.performance_observations):
            failures.append("Outcome change removed a measurement pairing")
    if (
        case.prediction_duplicates
        and score.micro.predicted != score.micro.truth + case.prediction_duplicates
    ):
        failures.append("Duplicate prediction escaped its denominator")
    assert before == case.truth.model_dump_json()
    result = dict(
        case=case.name,
        synthetic=True,
        passed=not failures,
        failures=failures,
        lineage=case.source_records,
        synthetic_paths=case.synthetic_paths,
        allowed_truth_paths=case.allowed,
        forbidden_truth_paths=case.forbidden,
    )
    if failures:
        result["counterexample"] = {
            "truth": case.truth.model_dump(mode="json"),
            "prediction": case.prediction.model_dump(mode="json"),
        }
    return result


# Triples are raw text, parsed number and explicit unit. Ranges and uncertainty
# retain their full claims; matching only a central number would be incorrect.
EQUIVALENCE_CASES = [
    (
        "explicit_raw_unit_without_parsed_fields",
        ("125 nm", 125, "nm"),
        ("0.125 µm", None, None),
        True,
    ),
    ("contradictory_raw_unit", ("125 nm", 125, "nm"), ("125 nm", None, "m"), False),
    ("absent_unit_is_not_inferred", ("125 nm", 125, "nm"), ("125", None, None), False),
    ("percent_fraction", ("20", 20, "%"), ("0.20", 0.2, "dimensionless"), True),
    ("hours_seconds", ("1", 1, "h"), ("3600", 3600, "s"), True),
    ("temperature_offset", ("65", 65, "°C"), ("338.15", 338.15, "K"), True),
    ("nanometres_metres", ("1", 1, "nm"), ("1e-9", 1e-9, "m"), True),
    ("changed_value", ("20", 20, "%"), ("21", 21, "%"), False),
    ("wrong_unit_scale", ("23", 23, "mA/cm2"), ("23", 23, "A/cm2"), False),
    ("missing_unit", ("20", 20, "%"), ("20", 20, None), False),
    ("bound_not_point", (">20", 20, "%"), ("20", 20, "%"), False),
    ("uncertainty_not_point", ("20 ± 1", 20, "%"), ("20", 20, "%"), False),
    ("range_conversion", ("1–2", None, "h"), ("60–120", None, "min"), True),
    (
        "uncertainty_conversion",
        ("20 ± 1", 20, "%"),
        ("0.20 ± 0.01", 0.2, "dimensionless"),
        True,
    ),
    ("unicode_formula", ("CH₃NH₃PbI₃", None, None), ("CH3NH3PbI3", None, None), True),
    (
        "latex_formula",
        ("CH_{3}NH_{3}PbI_{3}", None, None),
        ("CH3NH3PbI3", None, None),
        True,
    ),
    ("chemical_case", ("CoO", None, None), ("COO", None, None), False),
]


def equivalence_probe():
    """Retain every discrepancy, including documented unsupported equivalences."""
    results = []
    for name, left, right, expected in EQUIVALENCE_CASES:
        values = [
            ReportedValue(
                name="synthetic property",
                raw_value=raw,
                value_number=number,
                unit=unit,
                evidence=[
                    {
                        "block_id": "synthetic",
                        "quote": "Synthetic arithmetic/notation probe, not paper evidence.",
                    }
                ],
            )
            for raw, number, unit in (left, right)
        ]
        actual = equal_value(*values, relative=1e-6, absolute=0)
        results.append(
            {
                "case": name,
                "left": left,
                "right": right,
                "expected_equivalent": expected,
                "scorer_equivalent": actual,
                "agrees": actual == expected,
            }
        )
    return results
