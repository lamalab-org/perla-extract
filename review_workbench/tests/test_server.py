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
    relative = app._workbook_submission_relative_path(
        "dev",
        "10.0000--example",
        "2026-09-03T10:00:00+00:00",
        "submission-1",
        "reviewer-1",
        "reviewed workbook.xlsx",
    )
    body = b"exact xlsx bytes"
    receipt = {"submission_id": "submission-1", "status": "received"}

    app._archive_workbook_submission(relative, body, receipt)
    app._record_workbook_outcome(relative, {"status": "rejected"})
    artifacts = app.uploaded_review_workbooks()

    assert len(artifacts) == 1
    assert artifacts[0]["data"] == body
    assert artifacts[0]["receipt"] == receipt
    assert artifacts[0]["outcome"] == {"status": "rejected"}
    assert artifacts[0]["archive_path"].startswith("uploaded_workbooks/dev/")


def test_figure_proposals_are_served_one_paper_at_a_time(tmp_path):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    app.static_dir = tmp_path / "static"
    app.static_dir.mkdir()
    (app.static_dir / "figure-census-proposals.json").write_text(
        json.dumps({"papers": {"paper-a": {"panels": [{"figure_number": "1"}]}}})
    )

    assert app.figure_census_proposal("paper-a") == {
        "paper_id": "paper-a",
        "proposal": {"panels": [{"figure_number": "1"}]},
    }
    assert app.figure_census_proposal("missing") == {
        "paper_id": "missing",
        "proposal": None,
    }


def test_rejected_workbook_remains_archived(tmp_path, monkeypatch):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")

    def reject(*args, **kwargs):
        raise ValueError("invalid workbook")

    monkeypatch.setattr(app.store, "import_review_workbook", reject)
    with pytest.raises(ValueError, match="invalid workbook"):
        app.import_review_workbook(
            "dev",
            "10.0000--example",
            b"not really xlsx",
            "reviewer-1",
            filename="attempt.xlsx",
        )

    artifact = app.uploaded_review_workbooks()[0]
    assert artifact["data"] == b"not really xlsx"
    assert artifact["outcome"]["status"] == "rejected"
    assert artifact["outcome"]["message"] == "invalid workbook"


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


def test_figure_panel_preview_is_rendered_from_frozen_coordinates(
    tmp_path, monkeypatch
):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    app.static_dir = tmp_path / "static"
    app.static_dir.mkdir()
    (app.static_dir / "figure-census-proposals.json").write_text(
        json.dumps(
            {
                "papers": {
                    "paper-a": {
                        "panels": [
                            {
                                "proposal_panel_id": "panel-a",
                                "page": 1,
                                "figure_bbox_pdf": [0.0, 0.0, 300.0, 300.0],
                                "panel_bbox_normalized": [0, 0, 500, 1000],
                            }
                        ]
                    }
                }
            }
        )
    )
    monkeypatch.setattr(
        app, "review_pdf", lambda paper_id, source, split: pdf_bytes("Figure")
    )

    image = app.render_figure_panel("paper-a", "panel-a", "dev")

    assert image.startswith(b"\x89PNG\r\n\x1a\n")


def test_figure_panel_preview_rejects_a_different_pdf_revision(tmp_path, monkeypatch):
    app = ReviewApplication(tmp_path / "pdfs", tmp_path / "review")
    app.static_dir = tmp_path / "static"
    app.static_dir.mkdir()
    (app.static_dir / "figure-census-proposals.json").write_text(
        json.dumps(
            {
                "papers": {
                    "paper-a": {
                        "pdf_sha256": "0" * 64,
                        "panels": [
                            {
                                "proposal_panel_id": "panel-a",
                                "page": 1,
                                "figure_bbox_pdf": [0.0, 0.0, 300.0, 300.0],
                            }
                        ],
                    }
                }
            }
        )
    )
    monkeypatch.setattr(
        app, "review_pdf", lambda paper_id, source, split: pdf_bytes("Figure")
    )

    with pytest.raises(FileNotFoundError, match="does not match"):
        app.render_figure_panel("paper-a", "panel-a", "dev")


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
