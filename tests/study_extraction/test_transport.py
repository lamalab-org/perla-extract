import pytest

from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceCitation,
    PaperMetadata,
    StudyExtraction,
)
from perla_extract.study_extraction.transport import (
    compact_study,
    compact_study_schema,
    expand_compact_study,
)


def study() -> StudyExtraction:
    citation = EvidenceCitation(block_id="b1", quote="control device")
    return StudyExtraction(
        paper=PaperMetadata(title="Paper", doi=None),
        device_families=[
            DeviceFamily(
                family_id="f1",
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


def test_compact_schema_shares_citations_and_constrains_source_ids():
    schema = compact_study_schema(["b2", "b1", "b1"])

    assert schema["$defs"]["EvidenceCitation"]["type"] == "string"
    assert (
        "separate ReportedValue objects"
        in schema["$defs"]["ReportedValue"]["properties"]["raw_value"]["description"]
    )
    entry = schema["$defs"]["EvidenceCatalogEntry"]
    assert entry["properties"]["block_id"]["enum"] == ["b1", "b2"]
    assert "evidence_catalog" in schema["required"]
    assert "reported_properties" in schema["$defs"]["IndividualDevice"]["properties"]
    assert "conditions" in schema["$defs"]["StabilityCheckpoint"]["properties"]


def test_compact_response_expands_to_the_public_schema():
    compact = study().model_dump(mode="json")
    compact["device_families"][0]["evidence"] = ["citation-1"]
    compact["evidence_catalog"] = [
        {
            "citation_id": "citation-1",
            "block_id": "b1",
            "quote": "control device",
        }
    ]

    expanded = StudyExtraction.model_validate(expand_compact_study(compact))

    assert expanded == study()


def test_existing_study_compacts_and_round_trips_shared_citations():
    original = study()
    compact = compact_study(original)

    assert compact["device_families"][0]["evidence"] == ["citation-1"]
    assert len(compact["evidence_catalog"]) == 1
    assert StudyExtraction.model_validate(expand_compact_study(compact)) == original


def test_compact_response_rejects_an_unknown_citation_reference():
    compact = study().model_dump(mode="json")
    compact["device_families"][0]["evidence"] = ["missing"]
    compact["evidence_catalog"] = []

    with pytest.raises(ValueError, match="unknown citation_id"):
        expand_compact_study(compact)
