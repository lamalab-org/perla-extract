from __future__ import annotations

import csv
import json
from io import BytesIO, StringIO
from zipfile import ZipFile

from review_workbench.expert_comparison import (
    ComparisonService,
    LocalComparisonStorage,
)
from review_workbench.feedback_export import build_feedback_archive
from review_workbench.study_review import (
    FigurePanelCensus,
    InventoryAuditRequest,
    MainTextFigureCensus,
    MutationRequest,
    StudyReviewStore,
)


def test_feedback_download_preserves_history_and_is_easy_to_inspect(
    tmp_path, empty_study, document_payload
):
    store = StudyReviewStore(tmp_path / "review")
    store.import_seed(
        "dev",
        "10.0000--example",
        empty_study,
        document=document_payload,
        manifest={"model": "frontier"},
        reviewer_id="importer",
    )
    store.mutate(
        "dev",
        "10.0000--example",
        MutationRequest(
            action="replace",
            path="/unresolved_notes/0",
            value="Checked against the paper",
            evidence=[{"block_id": "main_p1_text_1", "quote": "champion device"}],
            note="Resolved during review",
            base_revision=1,
        ),
        "reviewer-1",
    )
    comparisons = ComparisonService(LocalComparisonStorage(tmp_path / "comparisons"))

    archive_path = tmp_path / "feedback.zip"
    workbook = b"exact reviewer workbook bytes"
    archive_path.write_bytes(
        build_feedback_archive(
            store,
            comparisons,
            [
                {
                    "split": "dev",
                    "paper_id": "10.0000--example",
                    "sha256": "a" * 64,
                    "receipt": {
                        "submission_id": "submission-1",
                        "reviewer_id": "reviewer-1",
                    },
                    "outcome": {"status": "rejected"},
                    "archive_path": (
                        "uploaded_workbooks/dev/10.0000--example/"
                        "00000002--event-1--reviewer-1--review.xlsx"
                    ),
                    "data": workbook,
                }
            ],
            {"format_version": 1, "papers": {}},
        )
    )
    with ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "feedback.json",
            "review_events.csv",
            "figure_panels.csv",
            "figure_census_proposals.json",
            "comparison_reviews.csv",
            (
                "uploaded_workbooks/dev/10.0000--example/"
                "00000002--event-1--reviewer-1--review.xlsx"
            ),
        }
        assert (
            archive.read(
                "uploaded_workbooks/dev/10.0000--example/"
                "00000002--event-1--reviewer-1--review.xlsx"
            )
            == workbook
        )
        snapshot = json.loads(archive.read("feedback.json"))
        proposals = json.loads(archive.read("figure_census_proposals.json"))
        rows = list(
            csv.DictReader(
                StringIO(archive.read("review_events.csv").decode("utf-8-sig"))
            )
        )
        comparison_rows = list(
            csv.DictReader(
                StringIO(archive.read("comparison_reviews.csv").decode("utf-8-sig"))
            )
        )
        figure_rows = list(
            csv.DictReader(
                StringIO(archive.read("figure_panels.csv").decode("utf-8-sig"))
            )
        )

    assert proposals == {"format_version": 1, "papers": {}}
    assert snapshot["figure_census_proposals"] == proposals
    assert snapshot["counts"] == {
        "comparison_batches": 0,
        "comparison_responses": 0,
        "papers_with_feedback": 1,
        "review_events": 1,
        "reviewers": 1,
        "uploaded_review_workbooks": 1,
        "figure_panels": 0,
    }
    assert snapshot["figure_census_summary"] == {
        "panel_count": 0,
        "papers_with_panel_census": 0,
        "reviewer_paper_censuses": 0,
        "schema_relevant_panels": 0,
        "figure_only_records": 0,
        "figure_only_atomic_values": 0,
        "panels_by_class": {},
        "panels_by_data_presentation": {},
        "panels_by_extraction_feasibility": {},
    }
    assert snapshot["uploaded_review_workbooks"][0]["receipt"] == {
        "submission_id": "submission-1",
        "reviewer_id": "reviewer-1",
    }
    assert snapshot["uploaded_review_workbooks"][0]["outcome"]["status"] == ("rejected")
    assert "data" not in snapshot["uploaded_review_workbooks"][0]
    assert snapshot["ground_truth_reviews"][0]["events"][0]["kind"] == "mutation"
    assert rows[0]["reviewer_id"] == "reviewer-1"
    assert rows[0]["before_json"] == '"Initial model note"'
    assert rows[0]["after_json"] == '"Checked against the paper"'
    assert comparison_rows == []
    assert figure_rows == []


def test_feedback_download_flattens_only_current_subfigure_census(
    tmp_path, empty_study, document_payload
):
    store = StudyReviewStore(tmp_path / "review")
    store.import_seed(
        "dev",
        "10.0000--example",
        empty_study,
        document=document_payload,
        manifest={},
        reviewer_id="importer",
    )
    store.inventory_audit(
        "dev",
        "10.0000--example",
        InventoryAuditRequest(
            base_revision=1,
            review_scope_sources=["main"],
            expected_counts={},
            main_text_figure_census=MainTextFigureCensus(
                panels=[
                    FigurePanelCensus(
                        figure_number="2",
                        panel_label="a",
                        page=4,
                        figure_class="stability",
                        description="Normalized efficiency over time.",
                        x_axis_label="Time (h)",
                        y_axis_label="Normalized PCE (%)",
                        data_presentation="mixed",
                        extraction_feasibility="partly_straightforward",
                        schema_relevant=True,
                        figure_only_records=1,
                        figure_only_atomic_values=2,
                    )
                ]
            ),
        ),
        "reviewer-1",
    )

    archive = build_feedback_archive(
        store,
        ComparisonService(LocalComparisonStorage(tmp_path / "comparisons")),
    )
    with ZipFile(BytesIO(archive)) as bundle:
        snapshot = json.loads(bundle.read("feedback.json"))
        rows = list(
            csv.DictReader(
                StringIO(bundle.read("figure_panels.csv").decode("utf-8-sig"))
            )
        )

    assert rows[0]["figure_class"] == "stability"
    assert rows[0]["description"] == "Normalized efficiency over time."
    assert snapshot["figure_census_summary"] == {
        "panel_count": 1,
        "papers_with_panel_census": 1,
        "reviewer_paper_censuses": 1,
        "schema_relevant_panels": 1,
        "figure_only_records": 1,
        "figure_only_atomic_values": 2,
        "panels_by_class": {"stability": 1},
        "panels_by_data_presentation": {"mixed": 1},
        "panels_by_extraction_feasibility": {"partly_straightforward": 1},
    }
