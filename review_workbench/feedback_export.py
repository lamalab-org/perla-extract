"""Build an administrator download of reviewer-authored feedback.

The review state already keeps an immutable event stream.  This module packages that
stream together with its derived current state, rather than inventing another mutable
reporting database.  JSON preserves every detail; CSV makes routine inspection and
analysis convenient for people who do not want to write code.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from review_workbench.expert_comparison import ComparisonService
from review_workbench.study_review import StudyReviewStore

SPLITS = ("calibration", "dev", "test")
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _compact_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def _csv_bytes(rows: list[dict[str, Any]], fields: list[str]) -> bytes:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def _zip_member(archive: zipfile.ZipFile, name: str, body: bytes) -> None:
    """Write portable ZIP members with stable metadata and UTF-8 filenames."""

    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, body)


def _paper_feedback(
    store: StudyReviewStore, split: str, paper_id: str
) -> dict[str, Any]:
    """Keep human events and the current state derived from them side by side."""

    revision = store.storage.load_revision(split, paper_id)
    events = [event for event in revision.events if event["kind"] != "seed_imported"]
    summary = store.summary(revision.ground_truth, revision.events)
    undone_ids = {
        str(event.get("details", {}).get("undoes_event_id"))
        for event in revision.events
        if event.get("details", {}).get("undoes_event_id")
    }
    return {
        "split": split,
        "paper_id": paper_id,
        "current_revision": revision.revision,
        "reviewer_ids": sorted({str(event["reviewer_id"]) for event in events}),
        "current_review_state": {
            "completed_stages": summary["completed_stages"],
            "inventory_audits": summary["inventory_audits"],
            "record_decisions": summary["record_decisions"],
        },
        "undone_event_ids": sorted(undone_ids),
        "events": events,
    }


def _ground_truth_feedback(store: StudyReviewStore) -> list[dict[str, Any]]:
    identities = [
        (split, paper_id)
        for split in SPLITS
        for paper_id in store.storage.list_paper_ids(split)
    ]
    with ThreadPoolExecutor(max_workers=min(8, len(identities) or 1)) as executor:
        return list(
            executor.map(
                lambda identity: _paper_feedback(store, *identity), identities
            )
        )


def _comparison_feedback(service: ComparisonService) -> list[dict[str, Any]]:
    """Export responses without breaking the experiment's reveal boundary."""

    batches: list[dict[str, Any]] = []
    for comparison_id in service.storage.list_ids():
        source = service.storage.load_source(comparison_id)
        reviews = [
            review.model_dump(mode="json")
            for assignment in source.assignments
            if (
                review := service.storage.load_review(
                    comparison_id, assignment.reviewer_id
                )
            )
            is not None
        ]
        utilities = [
            review.model_dump(mode="json")
            for assignment in source.assignments
            if (
                review := service.storage.load_utility(
                    comparison_id, assignment.reviewer_id
                )
            )
            is not None
        ]
        preferences = [
            review.model_dump(mode="json")
            for assignment in source.assignments
            if (
                review := service.storage.load_preference(
                    comparison_id, assignment.reviewer_id
                )
            )
            is not None
        ]
        complete = len(preferences) == len(source.assignments)
        batch: dict[str, Any] = {
            "comparison_id": comparison_id,
            "paper_id": source.paper_id,
            "split": source.split,
            "title": source.title,
            "assigned_reviewer_count": len(source.assignments),
            "accuracy_review_count": len(reviews),
            "native_utility_review_count": len(utilities),
            "pairwise_preference_count": len(preferences),
            "comparison_complete": complete,
            "pairwise_rubrics": [
                rubric.model_dump(mode="json") for rubric in source.pairwise_rubrics
            ],
            "reviews": reviews,
            "native_utility_reviews": utilities,
            "pairwise_preference_reviews": preferences,
        }
        if complete:
            identified = service.export(comparison_id)
            batch["candidate_mapping"] = identified["candidate_mapping"]
            batch["summary_by_origin"] = identified["summary_by_origin"]
        batches.append(batch)
    return batches


def _event_rows(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for paper in papers:
        undone = set(paper["undone_event_ids"])
        for event in paper["events"]:
            rows.append(
                {
                    "split": paper["split"],
                    "paper_id": paper["paper_id"],
                    "current_paper_revision": paper["current_revision"],
                    "event_revision": event["revision"],
                    "event_id": event["event_id"],
                    "timestamp": event["timestamp"],
                    "reviewer_id": event["reviewer_id"],
                    "kind": event["kind"],
                    "is_undone": event["event_id"] in undone,
                    "action": event.get("action") or "",
                    "path": event.get("path") or "",
                    "note": event.get("note") or "",
                    "before_json": _compact_json(event.get("before")),
                    "after_json": _compact_json(event.get("after")),
                    "evidence_json": _compact_json(event.get("evidence", [])),
                    "details_json": _compact_json(event.get("details", {})),
                }
            )
    return rows


def _comparison_rows(batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for batch in batches:
        mapping = batch.get("candidate_mapping", {})
        utilities = {
            item["reviewer_id"]: item for item in batch["native_utility_reviews"]
        }
        preferences = {
            item["reviewer_id"]: item
            for item in batch["pairwise_preference_reviews"]
        }
        for review in batch["reviews"]:
            verdicts = Counter(item["verdict"] for item in review["judgments"])
            utility = utilities.get(review["reviewer_id"], {})
            preference = preferences.get(review["reviewer_id"], {})
            rows.append(
                {
                    "comparison_id": batch["comparison_id"],
                    "paper_id": batch["paper_id"],
                    "split": batch["split"],
                    "reviewer_id": review["reviewer_id"],
                    "blind_label": review["blind_label"],
                    "origin_if_revealed": mapping.get(review["blind_label"], ""),
                    "final": review["final"],
                    "revision": review["revision"],
                    "started_at": review["started_at"],
                    "saved_at": review["saved_at"],
                    "submitted_at": review.get("submitted_at") or "",
                    "active_seconds": review["active_seconds"],
                    "confidence": review.get("confidence") or "",
                    "judgment_count": len(review["judgments"]),
                    "correct": verdicts["correct"],
                    "incorrect": verdicts["incorrect"],
                    "unsupported": verdicts["unsupported"],
                    "cannot_determine": verdicts["cannot_determine"],
                    "missing_fact_count": len(review["missing_facts"]),
                    "extra_records": review["extra_records"],
                    "missing_records": review["missing_records"],
                    "wrong_links": review["wrong_links"],
                    "notes": review["notes"],
                    "judgments_json": _compact_json(review["judgments"]),
                    "missing_facts_json": _compact_json(review["missing_facts"]),
                    "native_utility_submitted": bool(utility),
                    "native_ratings_json": _compact_json(utility.get("ratings")),
                    "suitable_as_curation_start": utility.get(
                        "suitable_as_curation_start", ""
                    ),
                    "native_notes": utility.get("notes", ""),
                    "pairwise_preference_submitted": bool(preference),
                    "pairwise_preferences_json": _compact_json(
                        preference.get("preferences")
                    ),
                    "pairwise_confidence": preference.get("confidence", ""),
                    "pairwise_rationale": preference.get("rationale", ""),
                }
            )
    return rows


def build_feedback_archive(
    store: StudyReviewStore,
    comparisons: ComparisonService,
    uploaded_workbooks: list[dict[str, Any]] | None = None,
) -> bytes:
    """Create one self-describing download of all reviewer-authored responses."""

    papers = _ground_truth_feedback(store)
    workbook_artifacts = uploaded_workbooks or []
    workbook_metadata = [
        {key: value for key, value in artifact.items() if key != "data"}
        for artifact in workbook_artifacts
    ]
    comparison_batches = _comparison_feedback(comparisons)
    event_rows = _event_rows(papers)
    comparison_rows = _comparison_rows(comparison_batches)
    snapshot = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "ground_truth_reviews": papers,
        "extractor_comparisons": comparison_batches,
        "uploaded_review_workbooks": workbook_metadata,
        "counts": {
            "papers_with_feedback": sum(bool(item["events"]) for item in papers),
            "review_events": len(event_rows),
            "reviewers": len(
                {
                    row["reviewer_id"]
                    for row in [*event_rows, *comparison_rows]
                }
            ),
            "comparison_batches": len(comparison_batches),
            "comparison_responses": len(comparison_rows),
            "uploaded_review_workbooks": len(workbook_artifacts),
        },
    }
    readme = """PERLA reviewer feedback export

feedback.json is the lossless export. It contains every reviewer-authored event and
the current review state derived from those immutable events.

review_events.csv is one row per saved ground-truth review action. is_undone marks an
edit that a later undo reversed. Reset events remain in history; feedback.json shows
which decisions, census answers, and completion stages are current.

comparison_reviews.csv is one row per started extractor-comparison response. It keeps
the independent accuracy review, native-utility ratings, and rubric-level A/B
preferences together. Drafts and final responses are distinguished by the final
column. Candidate origins remain blank while a comparison batch is incomplete,
preserving the blinded protocol.

Each comparison batch in feedback.json also contains the exact frozen rubrics shown
to its reviewers, including the question, minimum acceptable bar, and preference
rule. This supports reproducible analysis if a later study revises the wording.

Reviewer identifiers are stable application IDs, not passwords or session tokens.
The archive contains no PDFs and no authentication configuration.

uploaded_workbooks/ contains the exact XLSX bytes retained from successful workbook
imports. feedback.json records their paper, reviewer, revision, original filename,
and SHA-256 digest. Workbooks uploaded before this archival feature was deployed are
not recoverable as files; their accepted edits remain in review_events.csv and JSON.
"""
    event_fields = list(event_rows[0]) if event_rows else [
        "split", "paper_id", "current_paper_revision", "event_revision",
        "event_id", "timestamp", "reviewer_id", "kind", "is_undone", "action",
        "path", "note", "before_json", "after_json", "evidence_json", "details_json",
    ]
    comparison_fields = list(comparison_rows[0]) if comparison_rows else [
        "comparison_id", "paper_id", "split", "reviewer_id", "blind_label",
        "origin_if_revealed", "final", "revision", "started_at", "saved_at",
        "submitted_at", "active_seconds", "confidence", "judgment_count", "correct",
        "incorrect", "unsupported", "cannot_determine", "missing_fact_count",
        "extra_records", "missing_records", "wrong_links", "notes", "judgments_json",
        "missing_facts_json", "native_utility_submitted", "native_ratings_json",
        "suitable_as_curation_start", "native_notes",
        "pairwise_preference_submitted", "pairwise_preferences_json",
        "pairwise_confidence", "pairwise_rationale",
    ]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        _zip_member(archive, "README.txt", readme.encode())
        _zip_member(archive, "feedback.json", _json_bytes(snapshot))
        _zip_member(
            archive, "review_events.csv", _csv_bytes(event_rows, event_fields)
        )
        _zip_member(
            archive,
            "comparison_reviews.csv",
            _csv_bytes(comparison_rows, comparison_fields),
        )
        for artifact in workbook_artifacts:
            _zip_member(archive, artifact["archive_path"], artifact["data"])
    return stream.getvalue()
