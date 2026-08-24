from __future__ import annotations

from typing import Any

import pytest

from review_workbench.api.index import (
    BlobReviewStateStorage,
    BlobStore,
    VercelReviewApplication,
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
