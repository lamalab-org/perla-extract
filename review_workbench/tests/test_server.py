from __future__ import annotations

import hashlib
import json

import fitz
import pytest

from review_workbench.server import REVISION_CONFLICT_RESPONSE, ReviewApplication


def test_revision_conflict_message_is_actionable_without_internal_details():
    assert REVISION_CONFLICT_RESPONSE["code"] == "review_revision_conflict"
    assert "Load the latest saved version" in REVISION_CONFLICT_RESPONSE["error"]
    assert "revision 2" not in REVISION_CONFLICT_RESPONSE["error"]
    assert "stale" not in REVISION_CONFLICT_RESPONSE["error"]


def test_uploaded_review_workbook_is_retained_byte_for_byte(tmp_path):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    relative = app._uploaded_workbook_relative_path(
        "dev",
        "10.0000--example",
        2,
        "event-1",
        "reviewer-1",
        "reviewed workbook.xlsx",
    )
    body = b"exact xlsx bytes"

    assert app._archive_uploaded_workbook(relative, body) is True
    artifacts = app.uploaded_review_workbooks()

    assert len(artifacts) == 1
    assert artifacts[0]["data"] == body
    assert artifacts[0]["original_filename"] == "reviewed_workbook.xlsx"
    assert artifacts[0]["archive_path"].startswith("uploaded_workbooks/dev/")


def pdf_bytes(text: str) -> bytes:
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    value = document.tobytes()
    document.close()
    return value


def pdf_pages(*texts: str) -> bytes:
    document = fitz.open()
    for text in texts:
        page = document.new_page()
        page.insert_text((72, 72), text)
    value = document.tobytes()
    document.close()
    return value


def test_imports_extractor_bundle_and_both_documents(tmp_path, empty_study, document_payload):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    bundle = app.import_paper(
        "calibration", "10.0000--example", pdf_bytes("Main paper"),
        json.dumps(empty_study).encode(), supplement_bytes=pdf_bytes("Supplement"),
        document_bytes=json.dumps(document_payload).encode(),
        coverage_bytes=json.dumps({"counts": {"unmatched": 2}}).encode(),
        refinement_bytes=json.dumps({"collections": {}}).encode(),
        repair_bytes=json.dumps({"status": "accepted", "worklist": {"items": []}}).encode(),
        enrichment_bytes=json.dumps({
            "composition_results": [],
            "processing_results": [],
            "unresolved_composition_ids": [],
            "unresolved_processing_step_ids": [],
            "errors": [],
        }).encode(),
        reviewer_id="ada",
    )
    assert bundle["ground_truth"] == empty_study
    assert bundle["sources"] == ["main", "supplement"]
    assert bundle["manifest"]["main_pdf_sha256"] == hashlib.sha256(
        app.pdf_path("10.0000--example").read_bytes()
    ).hexdigest()
    assert bundle["manifest"]["supplement_pdf_sha256"] == hashlib.sha256(
        app.pdf_path("10.0000--example", "supplement").read_bytes()
    ).hexdigest()
    assert len(bundle["manifest"]["evidence_document_sha256"]) == 64
    assert bundle["manifest"]["quality_artifacts"]["coverage_audit"]["counts"]["unmatched"] == 2
    assert bundle["manifest"]["quality_artifacts"]["enrichment"]["composition_results"] == []
    assert bundle["manifest"]["quality_artifacts"]["targeted_repair"]["status"] == "accepted"
    assert app.get_paper("calibration", "10.0000--example")["sources"] == ["main", "supplement"]
    assert app.pdf_page_text("10.0000--example", "supplement", 1)["text"].startswith("Supplement")


def test_evidence_search_returns_source_and_page(tmp_path, empty_study, document_payload):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    app.import_paper(
        "calibration", "10.0000--example", pdf_bytes("Main"),
        json.dumps(empty_study).encode(), document_bytes=json.dumps(document_payload).encode(),
        reviewer_id="ada",
    )
    block = app.evidence_blocks("calibration", "10.0000--example", "distribution")[0]
    assert (block["source"], block["page"]) == ("supplement", 3)
    assert app.evidence_blocks("calibration", "10.0000--example", "main_p1")[0][
        "block_id"
    ] == "main_p1_text_1"
    assert app.evidence_block(
        "calibration", "10.0000--example", "supplement_p3_table_1"
    )["page"] == 3
    assert app.study_schema()["properties"]["individual_devices"]["type"] == "array"

    with pytest.raises(FileNotFoundError, match="evidence block missing"):
        app.evidence_block("calibration", "10.0000--example", "missing")


def test_concatenated_supplement_is_exposed_as_logical_si(
    tmp_path, empty_study, document_payload
):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    main = pdf_pages("Main page one", "Main page two")
    combined = pdf_pages(
        "Main page one",
        "Main page two",
        "Supporting information first page",
        "Supporting information second page with searchable detail",
    )
    app.import_paper(
        "calibration",
        "10.0000--example",
        main,
        json.dumps(empty_study).encode(),
        supplement_bytes=combined,
        document_bytes=json.dumps(document_payload).encode(),
        reviewer_id="ada",
    )

    page = app.pdf_page_text("10.0000--example", "supplement", 1)
    assert page["text"].startswith("Supporting information first page")
    assert page["page_count"] == 2
    _, rendered_count = app.render_pdf_page("10.0000--example", "supplement", 1)
    assert rendered_count == 2
    assert (
        app.search_pdf("10.0000--example", "supplement", "searchable")[0]["page"] == 2
    )
    assert (
        app.evidence_block("calibration", "10.0000--example", "supplement_p3_table_1")[
            "page"
        ]
        == 1
    )
    with fitz.open(stream=app.review_pdf("10.0000--example", "supplement")) as pdf:
        assert len(pdf) == 2
        assert pdf[0].get_text().startswith("Supporting information first page")
