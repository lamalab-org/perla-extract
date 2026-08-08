import json
from io import BytesIO
from pathlib import Path

import fitz

from review_workbench.review_collaboration import add_user
from review_workbench.server import ReviewApplication, make_handler


def test_import_paper_creates_corpus_and_review_records(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "data" / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "test").mkdir(parents=True)
    (ground_truth_dir / "dev").mkdir()
    app = ReviewApplication(pdf_dir, ground_truth_dir)
    truth = {"cells": [{"pce": {"value": 20.0, "unit": "%"}, "layers": []}]}

    result = app.import_paper(
        "test",
        "10.1234--example.1",
        b"%PDF-1.4\n%%EOF\n",
        json.dumps(truth).encode(),
    )

    assert result["cell_count"] == 1
    assert app.pdf_path(result["id"]).exists()
    assert app.paper_path("test", result["id"]).exists()
    assert app.list_papers("test")[0]["field_review"]["total"] == 2
    assert app.corpus_summary()["test"]["papers"] == 1
    assert app.corpus_summary()["dev"]["papers"] == 0


def test_review_ui_has_no_direct_html_injection_sinks():
    javascript = (
        Path(__file__).parents[1]
        / "review_app"
        / "app.js"
    ).read_text()

    assert ".innerHTML" not in javascript
    assert ".outerHTML" not in javascript
    assert "document.write" not in javascript
    assert "renderSafeHtml" in javascript


def test_pdf_search_uses_reliable_page_navigation():
    app_dir = Path(__file__).parents[1] / "review_app"
    javascript = (app_dir / "app.js").read_text()
    html = (app_dir / "index.html").read_text()

    assert 'id="pdf-page-image"' in html
    assert 'id="pdf-highlight"' in html
    assert "/api/pdf-page/" in javascript
    assert "result.bbox" in javascript
    assert "showPdfHighlight" in javascript


def test_pdf_search_returns_coordinates_and_page_renderer(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "dev").mkdir(parents=True)
    (ground_truth_dir / "test").mkdir()
    paper_id = "10.1234--coordinates"
    document = fitz.open()
    document.new_page().insert_text((72, 140), "The champion PCE was 21.4 percent.")
    document.new_page().insert_text((72, 300), "Average efficiency was 19.2 percent.")
    document.save(pdf_dir / f"{paper_id}.pdf")
    app = ReviewApplication(pdf_dir, ground_truth_dir)

    results = app.search_pdf(paper_id, "19.2")
    image, page_count = app.render_pdf_page(paper_id, 2)
    page_text, text_page_count = app.pdf_page_text(paper_id, 2)

    assert results[0]["page"] == 2
    assert 0 < results[0]["bbox"]["y"] < 1
    assert results[0]["bbox"]["width"] > 0
    assert image.startswith(b"\x89PNG")
    assert page_count == 2
    assert "Average efficiency" in page_text
    assert text_page_count == 2


def test_reviewer_progress_is_aggregated_by_split(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "dev").mkdir(parents=True)
    (ground_truth_dir / "test").mkdir()
    paper_id = "10.1234--progress"
    (ground_truth_dir / "test" / f"{paper_id}.json").write_text(
        json.dumps({"cells": [{"pce": {"value": 20.0, "unit": "%"}}]}),
        encoding="utf-8",
    )
    app = ReviewApplication(pdf_dir, ground_truth_dir)
    reviewer = add_user(ground_truth_dir, "Ada Reviewer")
    app.save_review_evidence(
        "test",
        paper_id,
        {
            "reviewer_id": reviewer["id"],
            "fields": {
                "/cells/0/pce/value": {"status": "verified", "page": 1}
            },
        },
    )

    progress = next(
        item
        for item in app.reviewer_progress_summary("test")
        if item["id"] == reviewer["id"]
    )

    assert progress["reviewed"] == 1
    assert progress["total"] == 2
    assert progress["papers_started"] == 1
    assert progress["papers_completed"] == 0
    assert progress["percent"] == 50.0


def test_review_ui_has_separate_figure_audit():
    app_dir = Path(__file__).parents[1] / "review_app"
    html = (app_dir / "index.html").read_text()
    javascript = (app_dir / "app.js").read_text()

    assert 'data-tab="figures"' in html
    assert 'id="figure-audit-form"' in html
    assert "schema_relevant_figures" in javascript
    assert "figure_only_schema_figures" in javascript


def test_search_and_issues_offer_explicit_pdf_jumps():
    app_dir = Path(__file__).parents[1] / "review_app"
    html = (app_dir / "index.html").read_text()
    javascript = (app_dir / "app.js").read_text()

    assert "Proposed uncertainty / provenance fields" in html
    assert "Jump · p." in javascript
    assert "Jump to evidence" in javascript
    assert "issue.source_text" in javascript
    assert "fact-value-relation" in javascript
    assert "fact-aggregation" in javascript
    assert '"mean", "Average / mean"' in javascript
    assert 'id="copy-pdf-quote"' in html
    assert 'id="copy-page-text"' in html
    assert "(p. ${state.pdfPage})" in javascript
    assert 'id="reviewer-progress"' in html
    assert 'id="next-pending"' in html
    assert "out_of_scope_tandem" in html


def test_authenticated_comment_ignores_client_supplied_author(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "dev").mkdir(parents=True)
    (ground_truth_dir / "test").mkdir()
    paper_id = "10.1234--example.1"
    (ground_truth_dir / "test" / f"{paper_id}.json").write_text(
        json.dumps({"cells": []}), encoding="utf-8"
    )
    app = ReviewApplication(pdf_dir, ground_truth_dir)

    class Authenticator:
        def authenticate(self, headers):
            return {
                "id": "user_123",
                "name": "Ada Reviewer",
                "email": "ada@example.org",
                "role": "reviewer",
            }

    handler_type = make_handler(app, Authenticator())
    handler = object.__new__(handler_type)
    body = json.dumps({"author_id": "attacker", "body": "Checked"}).encode()
    handler.path = f"/api/comments/test/{paper_id}"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = BytesIO(body)
    captured = {}
    handler.send_json = lambda payload, status=200: captured.update(payload=payload)
    handler.send_error = lambda status: captured.update(error=status)

    handler.do_POST()

    comment = captured["payload"]["comment"]

    assert comment["author_id"] == "user_123"
