from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from review_workbench.api.index import (
    BlobComparisonStorage,
    BlobReviewStateStorage,
    BlobStore,
    VercelReviewApplication,
)
from review_workbench.expert_comparison import (
    NativeUtilityReview,
    PairwisePreferenceReview,
    build_comparison_source,
)
from review_workbench.review_storage import (
    ReviewPaperSource,
    ReviewRevision,
    StaleRevisionError,
)


class MemoryBlobStore:
    """Model the overwrite contract of Vercel Blob without network access."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.hide_revision_lists = False
        self.downloads = 0

    def find(self, pathname: str) -> dict[str, str] | None:
        return {"pathname": pathname} if pathname in self.objects else None

    def list(self, prefix: str) -> list[dict[str, str]]:
        if self.hide_revision_lists and "review-revisions/" in prefix:
            return []
        return [
            {"pathname": pathname}
            for pathname in self.objects
            if pathname.startswith(prefix)
        ]

    def download(self, blob: dict[str, str]) -> bytes:
        self.downloads += 1
        return self.objects[blob["pathname"]]

    def put(
        self,
        pathname: str,
        body: bytes,
        content_type: str,
        *,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        del content_type
        if not overwrite and pathname in self.objects:
            raise RuntimeError("blob already exists")
        self.objects[pathname] = body
        return {"pathname": pathname}


class DirectoryOnlyBlobStore(BlobStore):
    """Reproduce Vercel's directory-only prefix lookup behavior."""

    def __init__(self, pathname: str):
        self.pathname = pathname
        self.prefixes: list[str] = []

    def list(self, prefix: str) -> list[dict[str, str]]:
        self.prefixes.append(prefix)
        return (
            [{"pathname": self.pathname}]
            if prefix == self.pathname.rpartition("/")[0] + "/"
            else []
        )


def test_blob_find_lists_the_parent_directory_before_matching_exact_path():
    pathname = "workbench/review-sources/calibration/example.json"
    blob = DirectoryOnlyBlobStore(pathname)

    assert blob.find(pathname) == {"pathname": pathname}
    assert blob.prefixes == ["workbench/review-sources/calibration/"]


def test_vercel_application_retains_and_lists_uploaded_workbooks():
    blob = MemoryBlobStore()
    app = object.__new__(VercelReviewApplication)
    app.blob = blob
    relative = Path(
        "dev/10.0000--example/"
        "00000002--event-1--reviewer-1--review.xlsx"
    )

    app._archive_workbook_submission(
        relative, b"xlsx", {"submission_id": "submission-1"}
    )
    app._record_workbook_outcome(relative, {"status": "accepted"})
    artifacts = app.uploaded_review_workbooks()

    assert artifacts[0]["data"] == b"xlsx"
    assert artifacts[0]["receipt"]["submission_id"] == "submission-1"
    assert artifacts[0]["outcome"]["status"] == "accepted"


class PagedBlobStore(BlobStore):
    """Serve two deterministic Blob pages without making network requests."""

    token = "test-token"

    def __init__(self, pathname: str):
        self.pathname = pathname
        self.requests: list[dict[str, list[str]]] = []

    def _request(self, url: str) -> bytes:
        query = parse_qs(urlparse(url).query)
        self.requests.append(query)
        if "cursor" not in query:
            return json.dumps(
                {
                    "blobs": [{"pathname": "workbench/review-sources/calibration/first.json"}],
                    "hasMore": True,
                    "cursor": "page-2",
                }
            ).encode()
        return json.dumps(
            {"blobs": [{"pathname": self.pathname}], "hasMore": False}
        ).encode()


def test_blob_list_follows_cursors_before_exact_path_matching():
    pathname = "workbench/review-sources/calibration/later.json"
    blob = PagedBlobStore(pathname)

    assert blob.find(pathname) == {"pathname": pathname}
    assert len(blob.requests) == 2
    assert blob.requests[0]["prefix"] == ["workbench/review-sources/calibration/"]
    assert blob.requests[1]["cursor"] == ["page-2"]


def test_vercel_source_inventory_does_not_download_pdfs():
    app = object.__new__(VercelReviewApplication)
    app.remote_pdfs = {
        ("calibration", "paper", "main"): {"pathname": "main.pdf"},
        (None, "paper", "supplement"): {"pathname": "supplement.pdf"},
    }

    assert app.available_sources("paper", "calibration") == [
        "main",
        "supplement",
    ]


class RecordingBlobStore:
    """Record user-directory writes without providing other Blob behavior."""

    configured = True

    def __init__(self):
        self.puts = 0

    def put(self, *args, **kwargs) -> None:
        del args, kwargs
        self.puts += 1


def test_vercel_syncs_user_directory_only_when_the_user_changes(tmp_path):
    app = object.__new__(VercelReviewApplication)
    app.ground_truth_dir = tmp_path
    app.blob = RecordingBlobStore()
    user = {"id": "ada", "name": "Ada", "email": "ada@example.org", "role": "reviewer"}

    app.ensure_authenticated_user(user)
    app.ensure_authenticated_user(user)

    assert app.blob.puts == 1


def revision(number: int, note: str) -> ReviewRevision:
    return ReviewRevision(
        revision=number,
        ground_truth={"note": note},
        events=[{"revision": index} for index in range(1, number + 1)],
    )


def test_blob_revision_paths_are_compare_and_swap_commits():
    blob = MemoryBlobStore()
    first = BlobReviewStateStorage(blob)  # type: ignore[arg-type]
    second = BlobReviewStateStorage(blob)  # type: ignore[arg-type]
    first.create(
        "dev",
        "10.0000--example",
        ReviewPaperSource(
            seed_extraction={"note": "seed"},
            manifest={},
            initial_revision=revision(1, "seed"),
        ),
    )

    first.compare_and_swap("dev", "10.0000--example", 1, revision(2, "ada"))
    blob.hide_revision_lists = True
    with pytest.raises(StaleRevisionError, match="revision 2 already exists"):
        second.compare_and_swap("dev", "10.0000--example", 1, revision(2, "grace"))

    blob.hide_revision_lists = False
    stored = second.load_revision("dev", "10.0000--example")
    assert stored.revision == 2
    assert stored.ground_truth == {"note": "ada"}
    assert len(blob.objects) == 2


def test_blob_paper_heads_do_not_download_full_studies():
    blob = MemoryBlobStore()
    storage = BlobReviewStateStorage(blob)  # type: ignore[arg-type]
    blob.objects.update(
        {
            "workbench/review-sources/calibration/paper-a.json": b"large source",
            "workbench/review-sources/calibration/paper-b.json": b"large source",
            "workbench/review-revisions/calibration/paper-a/00000002.json": b"large revision",
            "workbench/review-revisions/calibration/paper-a/00000007.json": b"large revision",
        }
    )

    assert storage.list_paper_heads("calibration") == [
        ("paper-a", 7),
        ("paper-b", 1),
    ]
    assert blob.downloads == 0


def test_blob_comparison_storage_keeps_sources_reviews_and_utility_separate():
    blob = MemoryBlobStore()
    storage = BlobComparisonStorage(blob)  # type: ignore[arg-type]
    source = build_comparison_source(
        comparison_id="comparison-1",
        paper_id="paper-1",
        title="Paper",
        split="dev",
        historical={"cells": []},
        extracted={
            "paper": {"title": "Paper", "doi": "10.0000/example"},
            "device_families": [],
            "individual_devices": [],
            "performance_observations": [],
            "population_statistics": [],
            "stability_tests": [],
            "unresolved_notes": [],
        },
        reviewer_ids=["ada"],
        randomization_seed="secret",
    )
    storage.create(source)
    assignment = source.assignments[0]
    utility = NativeUtilityReview(
        comparison_id=source.comparison_id,
        reviewer_id=assignment.reviewer_id,
        blind_label=assignment.blind_label,
        candidate_sha256=source.candidates[assignment.blind_label].native_sha256,
        submitted_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        ratings={
            "chemical_detail": 4,
            "relationships": 4,
            "verification_ease": 3,
            "nomad_usefulness": 5,
        },
        suitable_as_curation_start="yes",
    )
    storage.save_utility(utility)
    preference = PairwisePreferenceReview(
        comparison_id=source.comparison_id,
        reviewer_id=assignment.reviewer_id,
        candidate_hashes={
            label: candidate.native_sha256
            for label, candidate in source.candidates.items()
        },
        submitted_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        preferences={
            "factual_correctness": "tie",
            "coverage_completeness": "B",
            "chemical_detail": "B",
            "record_relationships": "B",
            "evidence_traceability": "A",
            "nomad_readiness": "B",
            "curation_effort": "B",
            "overall_preference": "B",
        },
        confidence=4,
    )
    storage.save_preference(preference)

    assert storage.list_ids() == ["comparison-1"]
    assert storage.load_source("comparison-1").source_hashes == {}
    assert storage.load_utility("comparison-1", "ada") == utility
    assert storage.load_preference("comparison-1", "ada") == preference
    assert any(
        path.startswith("workbench/comparison-utility/") for path in blob.objects
    )
    assert any(
        path.startswith("workbench/comparison-preferences/") for path in blob.objects
    )


def test_blob_current_revision_does_not_download_immutable_source_again():
    blob = MemoryBlobStore()
    storage = BlobReviewStateStorage(blob)  # type: ignore[arg-type]
    storage.create(
        "dev",
        "10.0000--example",
        ReviewPaperSource(
            seed_extraction={"note": "seed"},
            manifest={},
            initial_revision=revision(1, "seed"),
        ),
    )
    blob.put(
        "workbench/review-revisions/dev/10.0000--example/00000002.json",
        revision(2, "reviewed").model_dump_json().encode(),
        "application/json",
    )

    loaded = storage.load_revision("dev", "10.0000--example")

    assert loaded.revision == 2
    assert blob.downloads == 1
