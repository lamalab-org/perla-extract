from __future__ import annotations

import csv
import json
from io import StringIO
from zipfile import ZipFile

from review_workbench.expert_comparison import (
    ComparisonService,
    LocalComparisonStorage,
)
from review_workbench.feedback_export import build_feedback_archive
from review_workbench.study_review import MutationRequest, StudyReviewStore


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
            evidence=[
                {"block_id": "main_p1_text_1", "quote": "champion device"}
            ],
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
        )
    )
    with ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {
            "README.txt",
            "feedback.json",
            "review_events.csv",
            "comparison_reviews.csv",
            (
                "uploaded_workbooks/dev/10.0000--example/"
                "00000002--event-1--reviewer-1--review.xlsx"
            ),
        }
        assert archive.read(
            "uploaded_workbooks/dev/10.0000--example/"
            "00000002--event-1--reviewer-1--review.xlsx"
        ) == workbook
        snapshot = json.loads(archive.read("feedback.json"))
        rows = list(
            csv.DictReader(
                StringIO(archive.read("review_events.csv").decode("utf-8-sig"))
            )
        )
        comparison_rows = list(
            csv.DictReader(
                StringIO(
                    archive.read("comparison_reviews.csv").decode("utf-8-sig")
                )
            )
        )

    assert snapshot["counts"] == {
        "comparison_batches": 0,
        "comparison_responses": 0,
        "papers_with_feedback": 1,
        "review_events": 1,
        "reviewers": 1,
        "uploaded_review_workbooks": 1,
    }
    assert snapshot["uploaded_review_workbooks"][0]["receipt"] == {
        "submission_id": "submission-1",
        "reviewer_id": "reviewer-1",
    }
    assert snapshot["uploaded_review_workbooks"][0]["outcome"]["status"] == (
        "rejected"
    )
    assert "data" not in snapshot["uploaded_review_workbooks"][0]
    assert snapshot["ground_truth_reviews"][0]["events"][0]["kind"] == "mutation"
    assert rows[0]["reviewer_id"] == "reviewer-1"
    assert rows[0]["before_json"] == '"Initial model note"'
    assert rows[0]["after_json"] == '"Checked against the paper"'
    assert comparison_rows == []
