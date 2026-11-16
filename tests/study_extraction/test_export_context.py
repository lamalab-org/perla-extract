"""Exported values must retain preparation, measurement and aging distinctions."""

import json

import pytest

from perla_extract.study_extraction.compatibility import to_reduced_with_report
from perla_extract.study_extraction.models import (
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
    SubjectContext,
)
from perla_extract.study_extraction.nomad import to_nomad_with_report


@pytest.mark.parametrize("target", ["nomad", "reduced"])
def test_export_keeps_context_next_to_each_result(target):
    evidence = [EvidenceCitation(block_id="invented", quote="Synthetic export test")]

    def quantity(name, number, unit):
        return ReportedValue(
            name=name,
            raw_value=f"{number} {unit}",
            value_number=number,
            unit=unit,
            evidence=evidence,
        )

    preparation = SubjectContext(
        treatment_stage="before_device_completion",
        treated_material="perovskite film",
        conditions=[quantity("temperature", 100, "°C")],
    )
    devices = [
        IndividualDevice(
            device_id=identity,
            family_id=None,
            label=identity,
            variant=None,
            champion_status="not_reported",
            selection_basis="not_reported",
            subject_context=preparation,
            evidence=evidence,
        )
        for identity in ("measured", "unmeasured")
    ]
    illumination = quantity("irradiance", 100, "mW/cm2")
    study = StudyExtraction(
        paper=PaperMetadata(title="Invented context example", doi=None),
        device_families=[],
        individual_devices=devices,
        performance_observations=[
            PerformanceObservation(
                observation_id="jv",
                device_id="measured",
                measurement_type="jv_scan",
                scan_direction="reverse",
                measurement_conditions=[illumination],
                metrics=[quantity("PCE", 20, "%")],
                evidence=evidence,
            )
        ],
        population_statistics=[
            PopulationStatistic(
                population_id="cohort",
                family_id=None,
                label="Prepared films",
                statistic_type="mean",
                sample_size=12,
                subjects=[preparation],
                error_bar_type="standard_error",
                measurement_type="jv_scan",
                scan_direction="forward",
                measurement_conditions=[illumination],
                metrics=[quantity("PCE", 18, "%")],
                evidence=evidence,
            )
        ],
        stability_tests=[
            StabilityTest(
                test_id="aging",
                family_id=None,
                device_id="measured",
                specimen_label="measured",
                link_status="explicit_device_link",
                conditions=[quantity("temperature", 85, "°C")],
                checkpoints=[
                    StabilityCheckpoint(
                        checkpoint_id="after",
                        time=quantity("time", 1000, "h"),
                        outcomes=[quantity("retained PCE", 90, "%")],
                        evidence=evidence,
                    )
                ],
                evidence=evidence,
            )
        ],
        unresolved_notes=[],
    )
    if target == "nomad":
        exported = to_nomad_with_report(study, model="synthetic")
        cells = [archive.data for archive in exported.archives]
    else:
        exported = to_reduced_with_report(study)
        cells = exported.cells.cells
    notes = {
        mapping.source_kind: json.loads(cell.additional_notes)
        for mapping, cell in zip(exported.mappings, cells, strict=True)
    }
    for kind in ("performance_observation", "population_statistic"):
        assert notes[kind]["measurement_conditions"][0]["raw_value"] == "100 mW/cm2"
    for kind in ("performance_observation", "stability_test"):
        context = notes[kind]["device_subject_context"]
        assert context["treatment_stage"] == "before_device_completion"
        assert context["conditions"][0]["raw_value"] == "100 °C"
    assert (
        notes["individual_device"]["subject_context"]["treated_material"]
        == "perovskite film"
    )
    population = notes["population_statistic"]
    assert population["subjects"][0]["conditions"][0]["raw_value"] == "100 °C"
    assert population["error_bar_type"] == "standard_error"
    assert population["measurement_type"] == "jv_scan"
    assert population["scan_direction"] == "forward"
    aging = notes["stability_test"]
    if target == "reduced":
        aging = aging["rich_stability"]
    assert aging["conditions"][0]["raw_value"] == "85 °C"
