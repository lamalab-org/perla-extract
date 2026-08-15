from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceRef,
    Fact,
    Paper,
    StudyExtraction,
)
from perla_extract.study_extraction.partitioning import EvidenceBlock
from perla_extract.study_extraction.validation import _contains, validate_study


def test_ocr_spacing_does_not_destroy_real_source_boundaries():
    """Formula-internal OCR spaces may differ without joining surrounding prose."""

    formula = "Cs0.3FA0.6DMA0.1Pb(I0.7Br0.3)3"

    assert _contains(
        formula,
        "based on Cs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3. We compare",
    )
    assert not _contains(formula, "xCs0.3FA0.6DMA0.1Pb (I 0.7 Br0.3)3")


def test_fact_can_be_an_exact_join_of_multiple_verified_quotes():
    """A tandem fact may join two exact source values without inventing content."""

    references = [
        EvidenceRef(block_id="a", quote="CsPbI3"),
        EvidenceRef(block_id="b", quote="FASnI3"),
    ]
    family = DeviceFamily(
        family_id="f",
        label="tandem",
        variant=None,
        architecture="tandem",
        polarity="tandem",
        full_stack_raw=None,
        layers=[],
        absorber_formula=Fact(
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
        paper=Paper(title=None, doi=None),
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
    assert result["counts"]["source_verified_facts"] == 1
    assert result["counts"]["source_assembled_facts"] == 1
    assert (
        result["verified_facts"][0]["path"] == "$.device_families[0].absorber_formula"
    )


def test_fact_with_one_invalid_citation_is_not_in_grounded_subset():
    """Require every attached citation to validate before calling a fact grounded."""

    valid = EvidenceRef(block_id="a", quote="CsPbI3")
    invalid = EvidenceRef(block_id="missing", quote="CsPbI3")
    family = DeviceFamily(
        family_id="f",
        label="device",
        variant=None,
        architecture=None,
        polarity="not_reported",
        full_stack_raw=None,
        layers=[],
        absorber_formula=Fact(
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
        paper=Paper(title=None, doi=None),
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
    assert result["counts"]["source_verified_facts"] == 0
    assert result["verified_facts"] == []
