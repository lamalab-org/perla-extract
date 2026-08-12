import json
from io import BytesIO
from pathlib import Path

import fitz

from review_workbench.review_collaboration import add_issue, add_user, load_issues
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
    styles = (app_dir / "styles.css").read_text()

    assert 'id="pdf-page-image"' in html
    assert 'id="pdf-highlight"' in html
    assert 'id="pdf-text-layer"' in html
    assert "/api/pdf-page/" in javascript
    assert "result.bbox" in javascript
    assert "showPdfHighlight" in javascript
    assert "renderPdfTextLayer" in javascript
    assert "window.getSelection()" in javascript
    assert "user-select: text" in styles


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
    lines = app.pdf_page_text_lines(paper_id, 2)
    assert any("Average efficiency" in line["text"] for line in lines)
    assert all(0 <= line["bbox"]["x"] <= 1 for line in lines)


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
    assert "unlinked_device_statistic_figures" in javascript
    assert 'id="quantity-category"' in html
    assert "candidate_groups" in javascript


def test_review_ui_previews_proposed_ground_truth_before_saving():
    app_dir = Path(__file__).parents[1] / "review_app"
    html = (app_dir / "index.html").read_text()
    javascript = (app_dir / "app.js").read_text()

    assert 'data-tab="revision"' in html
    assert 'id="revision-list"' in html
    assert "/api/proposed-ground-truth/" in javascript
    assert "change.before" in javascript
    assert "change.after" in javascript
    assert "state.truthDraft" in javascript
    assert 'data-tab="gaps"' not in html
    assert 'id="open-quantity-scanner"' in html
    assert 'id="select-all-changes"' in html
    assert 'id="accept-proposal"' in html
    assert 'id="reject-proposal"' in html
    assert 'id="defer-proposal"' in html
    assert 'id="use-pdf-selection"' in html
    assert 'id="propose-schema-change"' in html
    assert 'id="download-ground-truth"' in html
    assert "Ground-truth correction or schema change?" in html
    assert "Download corrected ground truth" in html
    assert "How to apply a proposal" in html
    assert "Accept and apply" in html
    assert "Reject — keep current truth" in html
    assert "Needs more investigation" in html
    assert "selectedRevisionChanges" in javascript
    assert "Review and apply proposed change" in javascript
    assert "This paper has no applicable proposed change yet" in javascript
    assert "pendingProposalEdit" in javascript
    assert "edited_ground_truth" in javascript
    assert 'link.download = `${state.selected}.json`' in javascript
    assert "/preview" in javascript
    assert "/decision" in javascript
    assert "atomic_group_key" in javascript


def test_proposed_ground_truth_endpoint_returns_preview(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "dev").mkdir(parents=True)
    (ground_truth_dir / "test").mkdir()
    paper_id = "10.1234--revision"
    (ground_truth_dir / "test" / f"{paper_id}.json").write_text(
        json.dumps({"cells": []}), encoding="utf-8"
    )
    handler_type = make_handler(ReviewApplication(pdf_dir, ground_truth_dir))
    handler = object.__new__(handler_type)
    handler.path = f"/api/proposed-ground-truth/test/{paper_id}"
    handler.headers = {}
    captured = {}
    handler.send_json = lambda payload, status=200: captured.update(payload=payload)
    handler.send_error = lambda status: captured.update(error=status)

    handler.do_GET()

    assert captured["payload"]["current_ground_truth"] == {"cells": []}
    assert captured["payload"]["proposed_ground_truth"] == {"cells": []}
    assert captured["payload"]["changes"] == []


def test_accepting_atomic_proposal_validates_saves_and_records_decision(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "dev").mkdir(parents=True)
    (ground_truth_dir / "test").mkdir()
    paper_id = "10.1234--decision"
    (ground_truth_dir / "test" / f"{paper_id}.json").write_text(
        json.dumps({"cells": []}), encoding="utf-8"
    )
    reviewer = add_user(ground_truth_dir, "Ada")
    issue = add_issue(
        ground_truth_dir, "test", paper_id, reviewer["id"], "missing_cell",
        "Add the explicitly reported device.",
        proposed_patch=[
            {"op": "test", "path": "/cells", "value": []},
            {"op": "add", "path": "/cells/-", "value": {}},
        ],
        source_page=3, source_text="The treated device was fabricated.",
        source_type="main_text", device_identity="Treated device",
        measurement_identity="Complete device record",
        linkage_rationale="The paragraph describes this one device.",
        counterevidence="Control device was checked separately.",
        scope_notes="Eligible main text and single-junction device.",
        atomic_groups=[{"id": "device", "label": "Complete device", "operation_indexes": [0, 1]}],
    )
    app = ReviewApplication(pdf_dir, ground_truth_dir)

    result = app.decide_proposal_changes(
        "test", paper_id,
        {
            "action": "accept", "change_ids": [f"{issue['id']}:1"],
            "note": "Verified and clarified.",
            "edited_ground_truth": {"cells": [{"number_devices": 3}]},
        },
        reviewer["id"],
    )

    assert app.load_ground_truth("test", paper_id) == {"cells": [{"number_devices": 3}]}
    saved_issue = load_issues(ground_truth_dir, "test", paper_id)[0]
    assert saved_issue["status"] == "resolved"
    assert saved_issue["accepted_change_ids"] == [f"{issue['id']}:1"]
    assert saved_issue["proposal_decisions"][0]["note"] == "Verified and clarified."
    assert saved_issue["proposal_decisions"][0]["edited_before_accepting"] is True
    assert result["action"] == "accept"


def test_accepting_a_patch_without_evidence_packet_is_blocked(tmp_path):
    pdf_dir = tmp_path / "pdfs"
    ground_truth_dir = tmp_path / "ground_truth"
    pdf_dir.mkdir()
    (ground_truth_dir / "dev").mkdir(parents=True)
    (ground_truth_dir / "test").mkdir()
    paper_id = "10.1234--weak-proposal"
    (ground_truth_dir / "test" / f"{paper_id}.json").write_text(
        json.dumps({"cells": []}), encoding="utf-8"
    )
    reviewer = add_user(ground_truth_dir, "Ada")
    issue = add_issue(
        ground_truth_dir, "test", paper_id, reviewer["id"], "missing_cell",
        "Plausible but uncited.",
        proposed_patch=[
            {"op": "test", "path": "/cells", "value": []},
            {"op": "add", "path": "/cells/-", "value": {}},
        ],
    )
    app = ReviewApplication(pdf_dir, ground_truth_dir)

    try:
        app.decide_proposal_changes(
            "test", paper_id,
            {"action": "accept", "change_ids": [f"{issue['id']}:1"]},
            reviewer["id"],
        )
    except ValueError as error:
        assert "evidence-readiness gate" in str(error)
    else:
        raise AssertionError("Weak proposal was accepted")

    assert app.load_ground_truth("test", paper_id) == {"cells": []}


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
    assert "jumpToFactEvidence(fact)" in javascript
    assert 'data-use-figure-audit' in javascript
    assert 'id="issue-proposed-patch"' in html
    assert "issue.proposed_patch" in javascript
    assert 'value="ready">Ready proposal decisions' in html
    assert 'value="issues">All open findings/proposals' in html
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
