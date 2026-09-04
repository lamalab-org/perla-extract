import pytest
from pydantic import ValidationError

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
    Layer,
    PaperMetadata,
    PerformanceObservation,
    PopulationStatistic,
    ProcessingStep,
    ReportedValue,
    StabilityCheckpoint,
    StabilityTest,
    StudyExtraction,
)
from perla_extract.study_extraction.validation import validate_study


def test_schema_rejects_inconsistent_champion_semantics():
    """A device cannot be called champion through only one of two linked fields."""

    evidence = [EvidenceCitation(block_id="a", quote="champion device")]
    with pytest.raises(ValidationError, match="must occur together"):
        IndividualDevice(
            device_id="d",
            family_id=None,
            label="device",
            variant=None,
            champion_status="yes",
            selection_basis="not_reported",
            evidence=evidence,
        )


def test_schema_rejects_stability_link_without_its_identifier():
    """link_status must not claim a relationship that its identifiers cannot express."""

    evidence = [EvidenceCitation(block_id="a", quote="stable")]
    value = ReportedValue(
        name="retained PCE",
        raw_value="90%",
        value_number=90,
        unit="%",
        evidence=evidence,
    )
    with pytest.raises(ValidationError, match="requires device_id"):
        StabilityTest(
            test_id="s",
            family_id=None,
            device_id=None,
            specimen_label="device",
            link_status="explicit_device_link",
            conditions=[],
            checkpoints=[
                StabilityCheckpoint(
                    checkpoint_id="c",
                    time=None,
                    outcomes=[value],
                    evidence=evidence,
                )
            ],
            evidence=evidence,
        )


def test_reported_numbers_must_be_finite():
    """JSON artifacts and numeric comparisons cannot safely represent NaN or infinity."""

    evidence = [EvidenceCitation(block_id="a", quote="value")]
    with pytest.raises(ValidationError, match="finite number"):
        ReportedValue(
            name="PCE",
            raw_value="NaN",
            value_number=float("nan"),
            unit="%",
            evidence=evidence,
        )


def test_ocr_spacing_does_not_destroy_real_source_boundaries():
    """Formula-internal OCR spaces may differ without joining surrounding prose."""

    formula = "Cs0.3FA0.6DMA0.1Pb(I0.7Br0.3)3"

    assert source_contains_text(
        "based on Cs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3. We compare",
        formula,
    )
    assert not source_contains_text("xCs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3", formula)


def test_ocr_missing_internal_dash_is_treated_as_a_transport_artifact():
    """A lost range or label dash must not invalidate otherwise verbatim evidence."""

    assert source_contains_text("a 200 300-nm-thick film", "200–300-nm")
    assert source_contains_text("distortion in the I V curves", "I–V curves")


def test_ocr_dash_tolerance_never_discards_a_unary_minus():
    """Sign changes alter scientific meaning and therefore remain strict."""

    assert not source_contains_text("the bias was 0.5 V", "-0.5 V")


def test_ocr_font_mapping_can_corrupt_an_explicit_uncertainty_separator():
    """Accept one non-ASCII font-map glyph only where the query explicitly has ±."""

    assert source_contains_text("area 0.2090 士 0.0004 cm^2", "0.2090 ± 0.0004 cm^2")
    assert not source_contains_text("area 0.2090 x 0.0004 cm^2", "0.2090 ± 0.0004 cm^2")


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
        result["verified_values"][0]["path"]
        == "$.device_families[0].absorbers[0].formula"
    )


def test_reported_value_can_reuse_a_unit_printed_once_for_a_list():
    """A coordinated list need not repeat its source-printed unit after every value."""

    evidence = [EvidenceCitation(block_id="a", quote="rates of 0.2 and 0.1 Å s-1")]
    family = DeviceFamily(
        family_id="f",
        label="device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=None,
        absorber_properties=[
            ReportedValue(
                name="first rate",
                raw_value="0.2 Å s-1",
                value_number=0.2,
                unit="Å s-1",
                evidence=evidence,
            )
        ],
        absorber_constituents=[],
        processing_steps=[],
        evidence=evidence,
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
            block_id="a",
            source="main",
            page=1,
            kind="text",
            text="The layers were deposited at rates of 0.2 and 0.1 Å s-1.",
        )
    ]

    result = validate_study(extraction, blocks)

    assert result["status"] == "verified"
    assert result["counts"]["source_verified_values"] == 1
    assert result["counts"]["source_assembled_values"] == 1


def test_reported_value_can_reuse_a_unit_from_its_table_header():
    """A row value remains grounded when its column prints the unit in the header."""

    citation = EvidenceCitation(block_id="table", quote="Fresh | 1.13 | 22.7")
    family = DeviceFamily(
        family_id="f",
        label="device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=None,
        absorber_properties=[
            ReportedValue(
                name="open-circuit voltage",
                raw_value="1.13 V",
                value_number=1.13,
                unit="V",
                evidence=[citation],
            )
        ],
        absorber_constituents=[],
        processing_steps=[],
        evidence=[citation],
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
            block_id="table",
            source="main",
            page=1,
            kind="table",
            text="Sample | V OC (V) | PCE (%)\nFresh | 1.13 | 22.7",
        )
    ]

    result = validate_study(extraction, blocks)

    assert result["status"] == "verified"
    assert result["counts"]["source_assembled_values"] == 1


def test_table_unit_reuse_retains_an_uncertainty_for_ocr_matching():
    """Do not normalize away ± before checking a corrupted embedded-font glyph."""

    citation = EvidenceCitation(block_id="table", quote="Fresh | 1.13 士 0.01")
    family = DeviceFamily(
        family_id="f",
        label="device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=None,
        absorber_properties=[
            ReportedValue(
                name="open-circuit voltage",
                raw_value="1.13 ± 0.01 V",
                value_number=1.13,
                unit="V",
                evidence=[citation],
            )
        ],
        absorber_constituents=[],
        processing_steps=[],
        evidence=[citation],
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
            block_id="table",
            source="main",
            page=1,
            kind="table",
            text="Sample | V OC (V)\nFresh | 1.13 士 0.01",
        )
    ]

    assert validate_study(extraction, blocks)["status"] == "verified"


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
    assert (
        validate_study(
            repaired,
            [
                EvidenceBlock(
                    block_id="a", source="main", page=1, kind="text", text=source
                )
            ],
        )["status"]
        == "verified"
    )


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


def test_full_evidence_list_repairs_stitched_quote_without_growing():
    first = "The complete device stack contains a supported absorber layer and contact."
    second = "The same experiment reports a supported efficiency under illumination."
    source = f"{first} {'intervening ' * 150} {second}"
    citations = [EvidenceCitation(block_id="a", quote=f"{first} {second}")]
    citations.extend(
        EvidenceCitation(block_id="b", quote="independent source evidence")
        for _ in range(7)
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
        evidence=citations,
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
        EvidenceBlock(block_id="a", source="main", page=1, kind="text", text=source),
        EvidenceBlock(
            block_id="b",
            source="main",
            page=2,
            kind="text",
            text="independent source evidence",
        ),
    ]

    repaired, audit = repair_noncontiguous_citation_quotes(extraction, blocks)

    quotes = [item.quote for item in repaired.device_families[0].evidence]
    assert len(quotes) == 8
    assert quotes[0] == max((first, second), key=len)
    assert validate_study(repaired, blocks)["status"] == "verified"
    assert audit["repairs"][0]["rule"] == "retain_longest_exact_source_span"


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


def test_validation_flags_exact_duplicate_records_with_different_ids():
    """Repeated content should remain visible without similarity-based guessing."""

    evidence = [EvidenceCitation(block_id="a", quote="reported 20%")]
    value = ReportedValue(
        name="PCE", raw_value="20%", value_number=20, unit="%", evidence=evidence
    )
    device = IndividualDevice(
        device_id="d",
        family_id=None,
        label="device",
        variant=None,
        champion_status="not_reported",
        selection_basis="not_reported",
        evidence=evidence,
    )
    first = PerformanceObservation(
        observation_id="o-1",
        device_id="d",
        measurement_type="jv_scan",
        scan_direction="reverse",
        metrics=[value],
        evidence=evidence,
    )
    second = first.model_copy(update={"observation_id": "o-2"})
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[],
        individual_devices=[device],
        performance_observations=[first, second],
        population_statistics=[],
        stability_tests=[],
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

    assert result["counts"]["issues_by_reason"] == {
        "exact duplicate record content at indexes 0, 1": 1
    }


def test_validation_checks_nested_ids_processing_targets_and_stability_family():
    """Relationship checks must cover nested records, not only top-level IDs."""

    evidence = [EvidenceCitation(block_id="a", quote="reported 20%")]
    value = ReportedValue(
        name="PCE", raw_value="20%", value_number=20, unit="%", evidence=evidence
    )
    family = DeviceFamily(
        family_id="f1",
        label="family one",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorbers=[],
        processing_steps=[
            ProcessingStep(
                step_id="step",
                sequence=None,
                operation="coat",
                target_layer_ids=["missing"],
                materials=[],
                conditions=[],
                evidence=evidence,
            )
        ],
        evidence=evidence,
    )
    second = family.model_copy(update={"family_id": "f2", "label": "family two"})
    device = IndividualDevice(
        device_id="d",
        family_id="f1",
        label="device",
        variant=None,
        champion_status="not_reported",
        selection_basis="not_reported",
        evidence=evidence,
    )
    stability = StabilityTest(
        test_id="s",
        family_id="f2",
        device_id="d",
        specimen_label="device",
        link_status="explicit_device_link",
        conditions=[],
        checkpoints=[
            StabilityCheckpoint(
                checkpoint_id="checkpoint",
                time=None,
                outcomes=[value],
                evidence=evidence,
            )
        ],
        evidence=evidence,
    )
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[family, second],
        individual_devices=[device],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[stability],
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

    reasons = {issue["reason"] for issue in result["issues"]}
    assert "duplicate step_id" in reasons
    assert "unknown layer_id in this family" in reasons
    assert "family_id does not match the linked device" in reasons


def test_nested_identifiers_are_unique_in_their_actual_reference_scope():
    """Layer/checkpoint IDs may repeat across parents but not inside one parent."""

    evidence = [EvidenceCitation(block_id="a", quote="reported 20%")]
    layer = Layer(
        layer_id="shared-local-id",
        sequence=1,
        role="absorber",
        material="perovskite",
        material_form="not_reported",
        reported_properties=[],
        evidence=evidence,
    )
    family = DeviceFamily(
        family_id="f1",
        label="first",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[layer, layer.model_copy()],
        absorbers=[],
        processing_steps=[],
        evidence=evidence,
    )
    other_family = family.model_copy(
        update={"family_id": "f2", "label": "second", "layers": [layer.model_copy()]}
    )
    value = ReportedValue(
        name="PCE", raw_value="20%", value_number=20, unit="%", evidence=evidence
    )
    checkpoint = StabilityCheckpoint(
        checkpoint_id="shared-local-checkpoint",
        time=None,
        outcomes=[value],
        evidence=evidence,
    )
    stability = StabilityTest(
        test_id="s",
        family_id=None,
        device_id=None,
        specimen_label="specimen",
        link_status="stability_specimen_only",
        conditions=[],
        checkpoints=[checkpoint, checkpoint.model_copy()],
        evidence=evidence,
    )
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[family, other_family],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[stability],
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

    reasons = [issue["reason"] for issue in result["issues"]]
    assert reasons.count("duplicate layer_id within family") == 1
    assert reasons.count("duplicate checkpoint_id within stability test") == 1


def test_material_form_raw_is_checked_against_layer_evidence():
    source = EvidenceBlock(
        block_id="sam",
        source="main",
        page=1,
        section_path=["Methods"],
        kind="paragraph",
        text="A self-assembled monolayer of 2PACz was deposited on ITO.",
    )
    citation = EvidenceCitation(block_id="sam", quote=source.text)
    family = DeviceFamily(
        family_id="f1",
        label="SAM device",
        variant=None,
        architecture=None,
        polarity="p-i-n",
        full_stack_raw=None,
        layers=[
            Layer(
                layer_id="l1",
                sequence=1,
                role="hole_transport_layer",
                material="2PACz",
                material_form_raw="self-assembled monolayer",
                material_form="self_assembled_monolayer",
                reported_properties=[],
                evidence=[citation],
            )
        ],
        absorbers=[],
        processing_steps=[],
        evidence=[citation],
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

    assert validate_study(extraction, [source])["status"] == "verified"

    extraction.device_families[0].layers[0].material_form_raw = "SAM film"
    result = validate_study(extraction, [source])
    assert result["counts"]["issues_by_reason"] == {
        "material_form_raw not found in cited evidence": 1
    }
