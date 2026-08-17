from perla_extract.study_extraction.evidence import (
    repair_noncontiguous_citation_quotes,
    repair_unique_citation_pointers,
    source_contains_text,
)
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
from perla_extract.study_extraction.validation import validate_study


def test_ocr_spacing_does_not_destroy_real_source_boundaries():
    """Formula-internal OCR spaces may differ without joining surrounding prose."""

    formula = "Cs0.3FA0.6DMA0.1Pb(I0.7Br0.3)3"

    assert source_contains_text(
        "based on Cs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3. We compare",
        formula,
    )
    assert not source_contains_text("xCs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3", formula)


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


def test_unique_quote_match_repairs_only_the_source_pointer():
    wrong = EvidenceCitation(block_id="invented", quote="reported control device")
    family = DeviceFamily(
        family_id="f",
        label="control",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=None,
        absorber_properties=[],
        absorber_constituents=[],
        processing_steps=[],
        evidence=[wrong],
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
        EvidenceBlock(
            block_id="real",
            source="main",
            page=2,
            kind="text",
            text="The reported control device was measured.",
        )
    ]

    repaired, audit = repair_unique_citation_pointers(extraction, blocks)

    assert repaired.device_families[0].evidence[0].block_id == "real"
    assert repaired.device_families[0].evidence[0].quote == wrong.quote
    assert audit["repair_count"] == 1
    assert audit["repairs"][0]["rule"] == "unique_normalized_quote_match"


def test_ambiguous_quote_match_is_not_repaired():
    citation = EvidenceCitation(block_id="missing", quote="same reported wording")
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[
            DeviceFamily(
                family_id="f",
                label="control",
                variant=None,
                architecture=None,
                polarity="not_reported",
                full_stack_raw=None,
                layers=[],
                absorber_formula=None,
                absorber_properties=[],
                absorber_constituents=[],
                processing_steps=[],
                evidence=[citation],
            )
        ],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    blocks = [
        EvidenceBlock(
            block_id="a",
            source="main",
            page=1,
            kind="text",
            text="same reported wording",
        ),
        EvidenceBlock(
            block_id="b",
            source="supplement",
            page=1,
            kind="text",
            text="same reported wording",
        ),
    ]

    repaired, audit = repair_unique_citation_pointers(extraction, blocks)

    assert repaired == extraction
    assert audit["repair_count"] == 0
    assert audit["unresolved"][0]["matching_block_ids"] == ["a", "b"]


def test_bare_value_is_not_used_to_repair_a_citation_pointer():
    citation = EvidenceCitation(block_id="missing", quote="21.5%")
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[
            DeviceFamily(
                family_id="f",
                label="control",
                variant=None,
                architecture=None,
                polarity="not_reported",
                full_stack_raw=None,
                layers=[],
                absorber_formula=None,
                absorber_properties=[],
                absorber_constituents=[],
                processing_steps=[],
                evidence=[citation],
            )
        ],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    blocks = [
        EvidenceBlock(
            block_id="result", source="main", page=1, kind="text", text="PCE was 21.5%."
        )
    ]

    repaired, audit = repair_unique_citation_pointers(extraction, blocks)

    assert repaired == extraction
    assert audit["repair_count"] == 0
    assert audit["unresolved"][0]["reason"] == "quote_too_short_for_safe_repair"


def test_ordered_source_content_repairs_a_noncontiguous_model_quote():
    first = "The complete photovoltaic device stack contains the reported absorber."
    second = "The same source passage reports the measured power conversion efficiency."
    source = f"{first} Intervening source material is retained separately. {second}"
    stitched = f"{first} {second}"
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
        evidence=[EvidenceCitation(block_id="a", quote=stitched)],
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

    repaired, audit = repair_noncontiguous_citation_quotes(
        extraction,
        [EvidenceBlock(block_id="a", source="main", page=1, kind="text", text=source)],
    )

    repaired_quotes = [item.quote for item in repaired.device_families[0].evidence]
    assert repaired_quotes == [source]
    assert all(source_contains_text(source, quote) for quote in repaired_quotes)
    assert audit["repair_count"] == 1
    assert validate_study(repaired, [EvidenceBlock(
        block_id="a", source="main", page=1, kind="text", text=source
    )])["status"] == "verified"


def test_long_block_repairs_stitched_quote_as_two_exact_citations():
    first = "The complete device stack contains a supported absorber layer and contact."
    second = "The same experiment reports a supported efficiency under illumination."
    source = f"{first} {'intervening ' * 150} {second}"
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
        evidence=[EvidenceCitation(block_id="a", quote=f"{first} {second}")],
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

    repaired, audit = repair_noncontiguous_citation_quotes(
        extraction,
        [EvidenceBlock(block_id="a", source="main", page=1, kind="text", text=source)],
    )

    quotes = [item.quote for item in repaired.device_families[0].evidence]
    assert len(quotes) == 2
    assert all(source_contains_text(source, quote) for quote in quotes)
    assert audit["repair_count"] == 1


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
