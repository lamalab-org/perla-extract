from __future__ import annotations

import hashlib
import json

import fitz

from review_workbench.server import ReviewApplication


def pdf_bytes(text: str) -> bytes:
    document = fitz.open()
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
