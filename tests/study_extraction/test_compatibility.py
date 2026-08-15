import json

from perla_extract.study_extraction.compatibility import to_reduced_with_report
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceRef,
    Fact,
    IndividualDevice,
    Layer,
    Paper,
    PerformanceObservation,
    PopulationStatistic,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
)

EVIDENCE = [EvidenceRef(block_id="table-1", quote="PCE 24.0%")]


def fact(name: str, value: float, unit: str) -> Fact:
    return Fact(
        name=name,
        raw_value=f"{value} {unit}",
        value_number=value,
        unit=unit,
        evidence=EVIDENCE,
    )


def test_reduced_export_does_not_mix_champion_average_and_stability():
    family = DeviceFamily(
        family_id="f1",
        label="SAM device",
        variant="treated",
        architecture="ITO/SAM/perovskite/C60/Ag",
        polarity="p-i-n",
        full_stack_raw="ITO/SAM/perovskite/C60/Ag",
        layers=[
            Layer(
                layer_id="l1",
                sequence=1,
                role="absorber",
                material="perovskite",
                details=[fact("thickness", 500, "nm")],
                evidence=EVIDENCE,
            )
        ],
        absorber_properties=[],
        absorber_constituents=[],
        processing_steps=[],
        evidence=EVIDENCE,
    )
    device = IndividualDevice(
        device_id="d1",
        family_id="f1",
        label="champion",
        variant=None,
        champion_status="yes",
        selection_basis="champion",
        evidence=EVIDENCE,
    )
    study = StudyExtraction(
        paper=Paper(title="Paper", doi="10.1/test"),
        device_families=[family],
        individual_devices=[device],
        performance_observations=[
            PerformanceObservation(
                observation_id="o1",
                device_id="d1",
                measurement_type="jv_scan",
                scan_direction="reverse",
                metrics=[fact("PCE", 24.0, "%")],
                evidence=EVIDENCE,
            )
        ],
        population_statistics=[
            PopulationStatistic(
                population_id="p1",
                family_id="f1",
                label="12-device mean",
                statistic_type="mean",
                sample_size=12,
                metrics=[fact("PCE", 22.0, "%")],
                evidence=EVIDENCE,
            )
        ],
        stability_tests=[
            StabilityTest(
                test_id="s1",
                family_id="f1",
                device_id=None,
                specimen_label="encapsulated device",
                link_status="explicit_family_link",
                conditions=[],
                checkpoints=[
                    StabilityCheckpoint(
                        checkpoint_id="c1",
                        time=fact("time", 1000, "h"),
                        outcomes=[fact("retained PCE", 90, "%")],
                        evidence=EVIDENCE,
                    )
                ],
                evidence=EVIDENCE,
            )
        ],
        unresolved_notes=[],
    )

    exported = to_reduced_with_report(study)
    assert len(exported.cells.cells) == 3
    champion, mean, stability = exported.cells.cells
    assert champion.performance_aggregation == "champion"
    assert champion.pce.value == 24.0
    assert champion.layers[0].thickness is None
    assert (
        json.loads(champion.additional_notes)["family"]["layer_details"][0]["details"][
            0
        ]["raw_value"]
        == "500 nm"
    )
    assert mean.performance_aggregation == "mean"
    assert mean.number_devices == 12
    assert mean.pce.value == 22.0
    assert stability.pce is None
    assert json.loads(stability.additional_notes)["record_kind"] == "stability_test"
    assert [mapping.source_kind for mapping in exported.mappings] == [
        "performance_observation",
        "population_statistic",
        "stability_test",
    ]


def test_unitless_metric_is_retained_but_not_invented():
    observation_fact = Fact(
        name="PCE",
        raw_value="24.0",
        value_number=24.0,
        unit=None,
        evidence=EVIDENCE,
    )
    study = StudyExtraction(
        paper=Paper(title=None, doi=None),
        device_families=[],
        individual_devices=[
            IndividualDevice(
                device_id="d1",
                family_id=None,
                label="device",
                variant=None,
                champion_status="not_reported",
                selection_basis="not_reported",
                evidence=EVIDENCE,
            )
        ],
        performance_observations=[
            PerformanceObservation(
                observation_id="o1",
                device_id="d1",
                measurement_type="not_reported",
                scan_direction="not_reported",
                metrics=[observation_fact],
                evidence=EVIDENCE,
            )
        ],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    exported = to_reduced_with_report(study)
    assert exported.cells.cells[0].pce is None
    notes = json.loads(exported.cells.cells[0].additional_notes)
    assert notes["unprojected_metrics"][0]["raw_value"] == "24.0"
    assert exported.issues[0].code == "incompatible_metric_unit"


def test_common_unicode_unit_spelling_maps_without_numeric_conversion():
    source = fact("Jsc", 25.1, "mA/cm²")
    study = StudyExtraction(
        paper=Paper(title=None, doi=None),
        device_families=[],
        individual_devices=[
            IndividualDevice(
                device_id="d1",
                family_id=None,
                label="device",
                variant=None,
                champion_status="not_reported",
                selection_basis="not_reported",
                evidence=EVIDENCE,
            )
        ],
        performance_observations=[
            PerformanceObservation(
                observation_id="o1",
                device_id="d1",
                measurement_type="jv_scan",
                scan_direction="forward",
                metrics=[source],
                evidence=EVIDENCE,
            )
        ],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    exported = to_reduced_with_report(study)
    assert exported.cells.cells[0].jsc.value == 25.1
    assert exported.cells.cells[0].jsc.unit == "mA cm^-2"
    assert exported.issues == []
