from __future__ import annotations

from typing import Any

import pytest

from review_workbench.api.index import BlobReviewStateStorage, BlobStore
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
