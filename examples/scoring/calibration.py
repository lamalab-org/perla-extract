"""Synthetic context controls derived from a frozen study, with known answers.

These are software experiments, not new observations from a paper. Each case
retains path lineage and states allowed/forbidden truth matches before scoring.
The evaluator is used only to test those expectations, never to generate them.
"""

from dataclasses import dataclass

from perla_extract.study_extraction.models import ReportedValue, StudyExtraction
from perla_extract.study_extraction.scoring_protocol import (
    performance_name,
    protocol_name,
)
from perla_extract.study_extraction.units import canonical_quantity

TRANSFORMS = (
    "protocol_relocation",
    "protocol_duplicate",
    "protocol_units",
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
            base,
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
