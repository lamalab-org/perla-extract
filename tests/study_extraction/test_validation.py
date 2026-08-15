from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
)
from perla_extract.study_extraction.validation import _contains, validate_study


def test_ocr_spacing_does_not_destroy_real_source_boundaries():
    """Formula-internal OCR spaces may differ without joining surrounding prose."""

    formula = "Cs0.3FA0.6DMA0.1Pb(I0.7Br0.3)3"

    assert _contains(
        formula,
        "based on Cs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3. We compare",
    )
    assert not _contains(formula, "xCs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3")


def test_reported_value_can_be_an_exact_join_of_multiple_verified_quotes():
    """A tandem value may join two exact source values without inventing content."""

    references = [
        EvidenceCitation(block_id="a", quote="CsPbI3"),
        EvidenceCitation(block_id="b", quote="FASnI3"),
    ]
    family = DeviceFamily(
        family_id="f",
        label="tandem",
        variant=None,
        architecture="tandem",
        polarity="tandem",
        full_stack_raw=None,
        layers=[],
        absorber_formula=ReportedValue(
            name="absorber formulas",
            raw_value="CsPbI3; FASnI3",
            value_number=None,
            unit=None,
            evidence=references,
        ),
        absorber_properties=[],
        absorber_constituents=[],
        processing_steps=[],
        evidence=references,
    )
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[family],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    blocks = [
        EvidenceBlock(block_id="a", source="main", page=1, kind="text", text="CsPbI3"),
        EvidenceBlock(block_id="b", source="main", page=2, kind="text", text="FASnI3"),
    ]

    result = validate_study(extraction, blocks)

    assert result["status"] == "verified"
    assert result["counts"]["source_verified_values"] == 1
    assert result["counts"]["source_assembled_values"] == 1
    assert (
        result["verified_values"][0]["path"] == "$.device_families[0].absorber_formula"
    )


def test_reported_value_with_one_invalid_citation_is_not_in_grounded_subset():
    """Require every citation to validate before calling a value source-verified."""

    valid = EvidenceCitation(block_id="a", quote="CsPbI3")
    invalid = EvidenceCitation(block_id="missing", quote="CsPbI3")
    family = DeviceFamily(
        family_id="f",
        label="device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=ReportedValue(
            name="absorber",
            raw_value="CsPbI3",
            value_number=None,
            unit=None,
            evidence=[valid, invalid],
        ),
        absorber_properties=[],
        absorber_constituents=[],
        processing_steps=[],
        evidence=[valid],
    )
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[family],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    blocks = [
        EvidenceBlock(block_id="a", source="main", page=1, kind="text", text="CsPbI3")
    ]

    result = validate_study(extraction, blocks)

    assert result["status"] == "needs_review"
    assert result["counts"]["source_verified_values"] == 0
    assert result["verified_values"] == []


def test_validation_reports_duplicate_ids_for_every_entity_collection():
    """Semantic validation must not hide ambiguous identifiers in sets."""

    evidence = [EvidenceCitation(block_id="a", quote="reported")]
    reported_value = ReportedValue(
        name="PCE",
        raw_value="20%",
        value_number=20.0,
        unit="%",
        evidence=evidence,
    )
    family = DeviceFamily(
        family_id="f",
        label="device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=None,
        absorber_properties=[],
        absorber_constituents=[],
        processing_steps=[],
        evidence=evidence,
    )
    device = IndividualDevice(
        device_id="d",
        family_id="f",
        label="device",
        variant=None,
        champion_status="not_reported",
        selection_basis="not_reported",
        evidence=evidence,
    )
    observation = PerformanceObservation(
        observation_id="o",
        device_id="d",
        measurement_type="not_reported",
        scan_direction="not_reported",
        metrics=[reported_value],
        evidence=evidence,
    )
    population = PopulationStatistic(
        population_id="p",
        family_id="f",
        label="population",
        statistic_type="not_reported",
        sample_size=None,
        metrics=[reported_value],
        evidence=evidence,
    )
    stability = StabilityTest(
        test_id="s",
        family_id="f",
        device_id="d",
        specimen_label="device",
        link_status="explicit_device_link",
        conditions=[],
        checkpoints=[
            StabilityCheckpoint(
                checkpoint_id="c",
                time=None,
                outcomes=[reported_value],
                evidence=evidence,
            )
        ],
        evidence=evidence,
    )
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[family, family.model_copy()],
        individual_devices=[device, device.model_copy()],
        performance_observations=[observation, observation.model_copy()],
        population_statistics=[population, population.model_copy()],
        stability_tests=[stability, stability.model_copy()],
        unresolved_notes=[],
    )

    result = validate_study(
        extraction,
        [
            EvidenceBlock(
                block_id="a",
                source="main",
                page=1,
                kind="text",
                text="reported 20%",
            )
        ],
    )

    reasons = result["counts"]["issues_by_reason"]
    assert {
        "duplicate family_id",
        "duplicate device_id",
        "duplicate observation_id",
        "duplicate population_id",
        "duplicate test_id",
    } <= reasons.keys()
