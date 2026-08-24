import json

import pytest

from perla_extract.study_extraction.enrichment import (
    CompositionProposal,
    CompositionProposalResult,
    EnrichmentAudit,
    ProcessingConditionAssignment,
    ProcessingMaterialAssignment,
    ProcessingProposalResult,
    ProcessingStepProposal,
    ProposedIon,
)
from perla_extract.study_extraction.models import (
    AbsorberComponent,
    DeviceFamily,
    EvidenceCitation,
    IndividualDevice,
    Layer,
    MaterialConstituent,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ProcessingStep,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
)
from perla_extract.study_extraction.nomad import to_nomad_with_report
from perla_extract.study_extraction.nomad_contract import (
    NOMAD_M_DEF,
    NOMAD_SCHEMA_COMMIT,
    NOMAD_SCHEMA_VERSION,
)
from perla_extract.study_extraction.workflow import _write_nomad_artifacts

EVIDENCE = [EvidenceCitation(block_id="table-1", quote="reported value")]


def value(name: str, raw: str, number: float | None, unit: str | None) -> ReportedValue:
    return ReportedValue(
        name=name,
        raw_value=raw,
        value_number=number,
        unit=unit,
        evidence=EVIDENCE,
    )


def study_fixture() -> StudyExtraction:
    coefficient = value("stoichiometric coefficient", "1", 1, None)
    family = DeviceFamily(
        family_id="f1",
        label="treated device",
        variant="SAM",
        architecture="ITO/SAM/perovskite/C60/Ag",
        polarity="p-i-n",
        full_stack_raw="ITO/SAM/perovskite/C60/Ag",
        layers=[
            Layer(
                layer_id="absorber",
                sequence=2,
                role="absorber",
                material="FA Pb I3",
                reported_properties=[value("thickness", "0.5 um", 0.5, "um")],
                evidence=EVIDENCE,
            ),
            Layer(
                layer_id="substrate",
                sequence=1,
                role="substrate",
                material="glass/ITO",
                reported_properties=[],
                evidence=EVIDENCE,
            ),
        ],
        absorbers=[
            AbsorberComponent(
                absorber_id="a1",
                layer_id="absorber",
                label="FA Pb I3 absorber",
                formula=value("formula", "FAPbI3", None, None),
                properties=[value("band gap", "1.50 eV", 1.5, "eV")],
                constituents=[
                    MaterialConstituent(
                        name="FA",
                        role="A-site cation",
                        amount=coefficient,
                        evidence=EVIDENCE,
                    ),
                    MaterialConstituent(
                        name="Pb",
                        role="B-site cation",
                        amount=coefficient,
                        evidence=EVIDENCE,
                    ),
                    MaterialConstituent(
                        name="I",
                        role="X-site anion",
                        amount=value("stoichiometric coefficient", "3", 3, None),
                        evidence=EVIDENCE,
                    ),
                ],
                evidence=EVIDENCE,
            )
        ],
        processing_steps=[
            ProcessingStep(
                step_id="anneal",
                sequence=1,
                operation="Thermal annealing",
                target_layer_ids=["absorber"],
                materials=[],
                conditions=[
                    value("temperature", "373.15 K", 373.15, "K"),
                    value("duration", "10 min", 10, "min"),
                ],
                evidence=EVIDENCE,
            )
        ],
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
    return StudyExtraction(
        paper=PaperMetadata(title="Example", doi="10.1234/example"),
        device_families=[family],
        individual_devices=[device],
        performance_observations=[
            PerformanceObservation(
                observation_id="o1",
                device_id="d1",
                measurement_type="jv_scan",
                scan_direction="reverse",
                metrics=[
                    value("PCE", "24.0%", 24.0, "%"),
                    value("Jsc", "25.0 mA cm -2", 25.0, "mA cm -2"),
                ],
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
                metrics=[value("PCE", "22.0%", 22.0, "%")],
                evidence=EVIDENCE,
            )
        ],
        stability_tests=[
            StabilityTest(
                test_id="s1",
                family_id="f1",
                device_id=None,
                specimen_label="aged cell",
                link_status="explicit_family_link",
                conditions=[value("temperature", "85 °C", 85, "°C")],
                checkpoints=[
                    StabilityCheckpoint(
                        checkpoint_id="c0",
                        time=value("time", "0 h", 0, "h"),
                        outcomes=[value("PCE", "20%", 20, "%")],
                        evidence=EVIDENCE,
                    ),
                    StabilityCheckpoint(
                        checkpoint_id="c1",
                        time=value("time", "1000 h", 1000, "h"),
                        outcomes=[value("retained PCE", "90%", 90, "%")],
                        evidence=EVIDENCE,
                    ),
                ],
                evidence=EVIDENCE,
            )
        ],
        unresolved_notes=[],
    )


def test_nomad_export_keeps_reporting_levels_in_separate_archives():
    exported = to_nomad_with_report(study_fixture(), model="provider/model")

    assert [item.source_kind for item in exported.mappings] == [
        "performance_observation",
        "population_statistic",
        "stability_test",
    ]
    observation, population, stability = [archive.data for archive in exported.archives]
    assert observation.pce == 24.0
    assert observation.jsc == 25.0
    assert observation.number_devices is None
    assert population.pce == 22.0
    assert population.number_devices == 12
    assert population.averaged_quantities is True
    assert stability.pce is None
    assert stability.stability.PCE_at_start == 20.0
    assert stability.stability.PCE_at_end is None
    assert json.loads(observation.additional_notes)["champion_status"] == "yes"


def test_older_records_default_new_atomic_scopes_and_layer_semantics():
    payload = study_fixture().model_dump(mode="json")
    payload["individual_devices"][0].pop("reported_properties")
    for checkpoint in payload["stability_tests"][0]["checkpoints"]:
        checkpoint.pop("conditions")
    for layer in payload["device_families"][0]["layers"]:
        layer.pop("constituents")
        layer.pop("material_form_raw")
        layer.pop("material_form")

    migrated = StudyExtraction.model_validate(payload)

    assert migrated.individual_devices[0].reported_properties == []
    assert all(
        checkpoint.conditions == []
        for checkpoint in migrated.stability_tests[0].checkpoints
    )
    assert all(not layer.constituents for layer in migrated.device_families[0].layers)
    assert all(
        layer.material_form == "not_reported"
        for layer in migrated.device_families[0].layers
    )


def test_nomad_additional_notes_preserve_layer_form_and_constituents():
    study = study_fixture()
    layer = study.device_families[0].layers[0]
    layer.material_form_raw = "reported value"
    layer.material_form = "other"
    layer.constituents = [
        MaterialConstituent(
            name="FAI",
            role="precursor",
            amount=None,
            evidence=EVIDENCE,
        )
    ]

    exported = to_nomad_with_report(study)
    details = json.loads(exported.archives[0].data.additional_notes)["family"][
        "layer_details"
    ][0]

    assert details["material_form"] == "other"
    assert details["material_form_raw"] == "reported value"
    assert details["constituents"][0]["name"] == "FAI"


def test_nomad_export_projects_only_explicit_chemistry_and_converts_units():
    exported = to_nomad_with_report(study_fixture())
    projection = exported.composition_projections[0]
    cell = exported.archives[0].data

    assert projection.status == "ready"
    assert projection.raw_formula == "FAPbI3"
    assert cell.m_def == NOMAD_M_DEF
    assert cell.DOI_number == "https://doi.org/10.1234/example"
    assert cell.perovskite_composition.long_form == "FAPbI3"
    assert cell.perovskite_composition.band_gap == 1.5
    assert cell.perovskite_composition.ions_a_site[0].abbreviation == "FA"
    assert cell.layers[0].name == "glass/ITO"
    absorber = cell.layers[1]
    assert absorber.thickness == pytest.approx(500.0)
    assert absorber.deposition[0].temperature == pytest.approx(100.0)
    assert absorber.deposition[0].duration == 600.0


@pytest.mark.parametrize(
    "unit",
    ["mA cm−2", "mA cm -2", "mA cm⁻²", "mA/cm²", "mA cm \uf02d 2"],
)
def test_nomad_export_accepts_equivalent_current_density_typography(unit):
    study = study_fixture()
    metric = study.performance_observations[0].metrics[1]
    metric.unit = unit

    exported = to_nomad_with_report(study)

    assert exported.archives[0].data.jsc == 25.0
    assert not any(
        issue.code == "incompatible_metric" and issue.path == "jsc"
        for issue in exported.issues
    )


@pytest.mark.parametrize("unit", ["°C", "° C", "℃"])
def test_nomad_export_accepts_equivalent_temperature_typography(unit):
    study = study_fixture()
    temperature = study.device_families[0].processing_steps[0].conditions[0]
    temperature.raw_value = f"100 {unit}"
    temperature.value_number = 100.0
    temperature.unit = unit

    exported = to_nomad_with_report(study)

    absorber = exported.archives[0].data.layers[1]
    assert absorber.deposition[0].temperature == pytest.approx(100.0)


def test_nomad_export_preserves_tandem_absorbers_without_selecting_one():
    study = study_fixture()
    family = study.device_families[0]
    second_layer = family.layers[0].model_copy(
        update={
            "layer_id": "absorber-narrow",
            "sequence": 3,
            "material": "FASnI3",
        }
    )
    second_absorber = family.absorbers[0].model_copy(
        deep=True,
        update={
            "absorber_id": "a2",
            "layer_id": "absorber-narrow",
            "label": "narrow-bandgap absorber",
            "formula": value("formula", "FASnI3", None, None),
            "constituents": [],
        },
    )
    family.layers.append(second_layer)
    family.absorbers.append(second_absorber)

    exported = to_nomad_with_report(study)

    assert [item.absorber_id for item in exported.composition_projections] == [
        "a1",
        "a2",
    ]
    assert all(
        archive.data.perovskite_composition is None for archive in exported.archives
    )
    assert any(
        issue.code == "multiple_absorbers_not_projectable"
        for issue in exported.issues
    )
    context = json.loads(exported.archives[0].data.additional_notes)["family"]
    assert [item["absorber_id"] for item in context["absorbers"]] == ["a1", "a2"]


def test_formula_without_reported_site_assignments_is_reviewable_not_invented():
    study = study_fixture()
    study.device_families[0].absorbers[0].constituents = []

    exported = to_nomad_with_report(study)

    projection = exported.composition_projections[0]
    assert projection.status == "partial"
    assert projection.nomad_composition.ions_a_site == []
    assert any(issue.code == "composition_needs_review" for issue in exported.issues)


def test_nomad_export_consumes_only_accepted_enrichment():
    study = study_fixture()
    study.device_families[0].absorbers[0].constituents = []
    step = study.device_families[0].processing_steps[0]
    step.materials = ["FAI", "DMF", "chlorobenzene"]
    step.conditions.append(value("FAI concentration", "1 M", 1, "M"))
    enrichment = EnrichmentAudit(
        composition_results=[
            CompositionProposalResult(
                proposal=CompositionProposal(
                    family_id="f1",
                    ions=[
                        ProposedIon(site="A", abbreviation="FA", coefficient="1"),
                        ProposedIon(site="B", abbreviation="Pb", coefficient="1"),
                        ProposedIon(site="X", abbreviation="I", coefficient="3"),
                    ],
                ),
                status="accepted",
            )
        ],
        processing_results=[
            ProcessingProposalResult(
                proposal=ProcessingStepProposal(
                    step_id="anneal",
                    condition_assignments=[
                        ProcessingConditionAssignment(
                            condition_index=0,
                            target_field="temperature",
                            atmosphere=None,
                        )
                    ],
                    material_assignments=[
                        ProcessingMaterialAssignment(
                            material_index=0,
                            role="solute",
                            concentration_condition_index=2,
                        ),
                        ProcessingMaterialAssignment(
                            material_index=1,
                            role="solvent",
                            concentration_condition_index=None,
                        ),
                        ProcessingMaterialAssignment(
                            material_index=2,
                            role="antisolvent",
                            concentration_condition_index=None,
                        ),
                    ],
                ),
                status="accepted",
            )
        ],
    )

    exported = to_nomad_with_report(study, enrichment=enrichment)
    absorber = exported.archives[0].data.layers[1]
    process = absorber.deposition[0]

    assert exported.composition_projections[0].status == "ready"
    assert process.solution.solutes[0].name == "FAI"
    assert process.solution.solutes[0].concentration == 1
    assert process.solution.solvents[0].name == "DMF"
    assert process.antisolvent == "chlorobenzene"


def test_nomad_export_revalidates_an_audit_marked_accepted():
    study = study_fixture()
    study.device_families[0].absorbers[0].constituents = []
    forged = EnrichmentAudit(
        composition_results=[
            CompositionProposalResult(
                proposal=CompositionProposal(
                    family_id="f1",
                    ions=[
                        ProposedIon(site="A", abbreviation="FA", coefficient="1"),
                        ProposedIon(site="B", abbreviation="Pb", coefficient="1"),
                        ProposedIon(site="X", abbreviation="Br", coefficient="3"),
                    ],
                ),
                status="accepted",
            )
        ]
    )

    exported = to_nomad_with_report(study, enrichment=forged)

    assert exported.composition_projections[0].status == "partial"
    assert any(
        issue.code == "accepted_enrichment_failed_revalidation"
        for issue in exported.issues
    )


def test_nomad_artifacts_are_standalone_and_pin_the_target(tmp_path):
    exported = to_nomad_with_report(study_fixture())
    _write_nomad_artifacts(tmp_path, exported)

    manifest = json.loads((tmp_path / "nomad" / "manifest.json").read_text())
    assert manifest["target_version"] == NOMAD_SCHEMA_VERSION
    assert manifest["target_commit"] == NOMAD_SCHEMA_COMMIT
    assert len(list((tmp_path / "nomad").glob("*.archive.json"))) == 3
    first = json.loads(
        (tmp_path / "nomad" / exported.mappings[0].archive_file).read_text()
    )
    assert first["data"]["m_def"] == NOMAD_M_DEF
