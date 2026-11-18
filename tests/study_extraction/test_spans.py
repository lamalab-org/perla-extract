from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.spans import (
    MAX_SPAN_CHARACTERS,
    build_evidence_spans,
    evidence_payload,
    evidence_spans_sha256,
)
from perla_extract.study_extraction.transport import expand_span_citations


def block(text: str, kind: str = "paragraph") -> EvidenceBlock:
    return EvidenceBlock(
        block_id="b1",
        source="main",
        page=2,
        section_path=["Methods"],
        kind=kind,
        text=text,
    )


def test_prose_sentences_and_table_rows_become_directly_citable_spans():
    prose = build_evidence_spans([block("First result. Second result!")])
    table = build_evidence_spans([block("Device | PCE\nControl | 20.0", "table")])

    assert [span.text for span in prose] == ["First result. Second result!"]
    assert [span.text for span in table] == ["Device | PCE", "Control | 20.0"]


def test_oversized_passages_are_bounded_and_ids_are_reproducible():
    source = block("word " * 600)
    first = build_evidence_spans([source])
    second = build_evidence_spans([source])

    assert first == second
    assert all(len(span.text) <= MAX_SPAN_CHARACTERS for span in first)
    assert evidence_spans_sha256(first) == evidence_spans_sha256(second)


def test_payload_keeps_block_location_once_and_nests_span_text():
    payload = evidence_payload([block("One. Two.")])

    assert payload[0]["block_id"] == "b1"
    assert payload[0]["page"] == 2
    assert list(payload[0]["spans"].values()) == ["One. Two."]


def test_subsets_reuse_the_call_catalog_without_reassigning_short_ids():
    context = block("Context statement.")
    primary = block("Primary result.").model_copy(update={"block_id": "b2"})
    spans = build_evidence_spans([context, primary])
    assert [span.span_id for span in spans] == [1, 2]
    payload = evidence_payload([primary], spans)
    assert payload[0]["spans"] == {2: "Primary result."}
    assert expand_span_citations({"evidence": [2]}, spans) == {
        "evidence": [{"block_id": "b2", "quote": "Primary result."}]
    }
    # The public citation survives renumbering in a different request catalog.
    assert build_evidence_spans([primary])[0].span_id == 1
    assert evidence_spans_sha256(spans) != evidence_spans_sha256(
        build_evidence_spans([primary])
    )


def test_reader_prompt_context_and_primary_share_one_decode_catalog():
    import json

    from perla_extract.study_extraction.extraction_prompts import _claim_ledger_prompt

    context = block("Context statement.")
    primary = block("Primary result.").model_copy(update={"block_id": "b2"})
    prompt = _claim_ledger_prompt([primary], [context])
    left, right = prompt.split("CONTEXT EVIDENCE:\n", 1)[1].split(
        "\n\nPRIMARY EVIDENCE:\n"
    )
    assert json.loads(left)[0]["spans"] == {"1": "Context statement."}
    assert json.loads(right)[0]["spans"] == {"2": "Primary result."}
