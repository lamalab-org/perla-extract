import pytest

from perla_extract.study_extraction.inventory import EvidenceInventory
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceBlock,
    EvidenceCitation,
    PaperMetadata,
    StudyExtraction,
)
from perla_extract.study_extraction.spans import build_evidence_spans
from perla_extract.study_extraction.transport import (
    compact_to_span_citations,
    expand_span_citations,
    span_citation_schema,
)


def block() -> EvidenceBlock:
    return EvidenceBlock(
        block_id="b1",
        source="main",
        page=1,
        section_path=["Results"],
        kind="paragraph",
        text="The control device was measured. Its PCE was 20.0%.",
    )


def study() -> StudyExtraction:
    citation = EvidenceCitation(
        block_id="b1", quote="The control device was measured. Its PCE was 20.0%."
    )
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
                absorbers=[],
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


def test_schema_replaces_citations_with_known_span_ids():
    spans = build_evidence_spans([block()])
    schema = span_citation_schema(StudyExtraction, spans)

    citation = schema["$defs"]["EvidenceCitation"]
    assert citation["type"] == "string"
    assert citation["enum"] == sorted(span.span_id for span in spans)
    assert (
        "separate ReportedValue objects"
        in schema["$defs"]["ReportedValue"]["properties"]["raw_value"]["description"]
    )
    assert "material_form" in schema["$defs"]["Layer"]["properties"]


def test_span_response_expands_to_exact_public_citations():
    spans = build_evidence_spans([block()])
    compact = study().model_dump(mode="json")
    compact["device_families"][0]["evidence"] = [spans[0].span_id]

    expanded = StudyExtraction.model_validate(expand_span_citations(compact, spans))

    assert expanded == study()


def test_public_study_compacts_and_round_trips_span_references():
    spans = build_evidence_spans([block()])
    original = study()
    compact = compact_to_span_citations(original, spans)

    assert compact["device_families"][0]["evidence"] == [spans[0].span_id]
    assert (
        StudyExtraction.model_validate(expand_span_citations(compact, spans))
        == original
    )


def test_span_expansion_handles_singular_inventory_evidence():
    spans = build_evidence_spans([block()])
    payload = {
        "items": [],
        "exclusions": [
            {
                "evidence": spans[0].span_id,
                "reason": "document furniture",
            }
        ],
    }

    expanded = EvidenceInventory.model_validate(expand_span_citations(payload, spans))

    assert expanded.exclusions[0].evidence.quote == spans[0].text


def test_span_response_rejects_an_unknown_reference():
    spans = build_evidence_spans([block()])
    compact = study().model_dump(mode="json")
    compact["device_families"][0]["evidence"] = ["missing"]

    with pytest.raises(ValueError, match="unknown evidence span"):
        expand_span_citations(compact, spans)


def test_compaction_rejects_a_quote_that_is_not_a_generated_span():
    spans = build_evidence_spans([block()])
    payload = study().model_dump(mode="json")
    payload["device_families"][0]["evidence"][0]["quote"] = "not in the source"

    with pytest.raises(ValueError, match="does not resolve"):
        compact_to_span_citations(payload, spans)
