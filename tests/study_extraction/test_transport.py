import pytest

from perla_extract.study_extraction.claims import ClaimLedger
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


def test_schema_bounds_citations_without_document_sized_enum():
    spans = build_evidence_spans([block()])
    schema = span_citation_schema(StudyExtraction, spans)

    citation = schema["$defs"]["EvidenceCitation"]
    assert citation["type"] == "integer"
    assert "enum" not in citation
    assert citation["minimum"] == citation["maximum"] == 1
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


def test_span_expansion_handles_singular_experimental_object_evidence():
    spans = build_evidence_spans([block()])
    payload = {
        "objects": [
            {
                "object_id": "device",
                "label": "device",
                "role": "device_design",
                "scope": "target",
                "evidence": [spans[0].span_id],
            }
        ],
        "claims": [],
    }

    expanded = ClaimLedger.model_validate(expand_span_citations(payload, spans))

    assert expanded.objects[0].evidence[0].quote == spans[0].text


def test_span_response_rejects_an_unknown_reference():
    spans = build_evidence_spans([block()])
    compact = study().model_dump(mode="json")
    compact["device_families"][0]["evidence"] = [2]

    with pytest.raises(ValueError, match="unknown evidence span"):
        expand_span_citations(compact, spans)


def test_long_catalog_does_not_increase_schema_size_or_weaken_membership():
    spans = build_evidence_spans([block()])
    many = [spans[0].model_copy(update={"span_id": i}) for i in range(1, 1201)]
    large = span_citation_schema(StudyExtraction, many)
    assert large["$defs"]["EvidenceCitation"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 1200,
    }
    assert "enum" not in large["$defs"]["EvidenceCitation"]
    with pytest.raises(ValueError, match="unknown evidence span"):
        expand_span_citations({"evidence": [1201]}, many)
    assert (
        expand_span_citations({"evidence": [1200]}, many)["evidence"][0]["quote"]
        == spans[0].text
    )


def test_compaction_rejects_a_quote_that_is_not_a_generated_span():
    spans = build_evidence_spans([block()])
    payload = study().model_dump(mode="json")
    payload["device_families"][0]["evidence"][0]["quote"] = "not in the source"

    with pytest.raises(ValueError, match="does not resolve"):
        compact_to_span_citations(payload, spans)


@pytest.mark.parametrize("reference", [True, 1.0, "1"])
def test_only_integer_citation_numbers_are_accepted(reference):
    with pytest.raises(ValueError, match="integer span identifiers"):
        expand_span_citations(
            {"evidence": [reference]}, build_evidence_spans([block()])
        )


def test_selected_catalog_range_does_not_admit_an_invisible_passage():
    span = build_evidence_spans([block()])[0]
    selected = [span, span.model_copy(update={"span_id": 3})]
    schema = span_citation_schema(StudyExtraction, selected)
    assert schema["$defs"]["EvidenceCitation"]["maximum"] == 3
    with pytest.raises(ValueError, match="unknown evidence span"):
        expand_span_citations({"evidence": [2]}, selected)
