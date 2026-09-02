"""Vercel entry point backed by private Blob storage."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

MODULE_PATH = Path(__file__).resolve()
PROJECT_ROOT = (
    MODULE_PATH.parents[2]
    if MODULE_PATH.parent.parent.name == "review_workbench"
    else MODULE_PATH.parents[1]
)
sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]

from perla_extract.study_extraction.artifacts import write_json_atomic  # noqa: E402
from review_workbench.auth import clerk_key_allowed  # noqa: E402
from review_workbench.expert_comparison import (  # noqa: E402
    ComparisonReview,
    ComparisonSource,
    NativeUtilityReview,
    PairwisePreferenceReview,
)
from review_workbench.review_storage import (  # noqa: E402
    ReviewPaperSource,
    ReviewRevision,
    StaleRevisionError,
)
from review_workbench.server import ReviewApplication, make_handler  # noqa: E402


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    """Make a downloaded PDF visible only after all of its bytes are present."""

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class BlobStore:
    """Use the official client for writes and the REST endpoint for reads."""

    endpoint = "https://blob.vercel-storage.com"

    def __init__(self, token: str | None = None):
        self.token = token or os.environ.get("BLOB_READ_WRITE_TOKEN", "")
        if self.token:
            from vercel.blob import BlobClient

            self.client = BlobClient(token=self.token)
        else:
            self.client = None

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _request(self, url: str) -> bytes:
        request = Request(url, headers={"Authorization": f"Bearer {self.token}"})
        with urlopen(request, timeout=60) as response:
            return response.read()

    def list(self, prefix: str) -> list[dict]:
        """Return every Blob below a prefix, following Vercel's cursors."""

        if not self.configured:
            return []
        blobs: list[dict] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            query = {"prefix": prefix, "limit": 1000}
            if cursor:
                query["cursor"] = cursor
            page = json.loads(self._request(f"{self.endpoint}?{urlencode(query)}"))
            blobs.extend(page.get("blobs", []))
            next_cursor = page.get("cursor") if page.get("hasMore") else None
            if not next_cursor:
                return blobs
            if next_cursor in seen_cursors:
                raise RuntimeError("Vercel Blob returned a repeated pagination cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def find(self, pathname: str) -> dict | None:
        """Find an exact pathname through its containing Blob directory.

        Vercel Blob's list endpoint accepts directory prefixes but may return an
        empty result when the prefix is a complete filename. Listing the parent and
        comparing pathnames keeps reads consistent with the directory listing used
        to discover papers.
        """

        parent, separator, _ = pathname.rpartition("/")
        prefix = f"{parent}/" if separator else ""
        return next(
            (item for item in self.list(prefix) if item.get("pathname") == pathname),
            None,
        )

    def download(self, blob: dict) -> bytes:
        return self._request(str(blob["url"]))

    def put(
        self,
        pathname: str,
        body: bytes,
        content_type: str,
        *,
        overwrite: bool = True,
    ) -> dict:
        if self.client is None:
            raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")
        result = self.client.put(
            pathname,
            body,
            access="private",
            content_type=content_type,
            add_random_suffix=False,
            overwrite=overwrite,
            cache_control_max_age=60,
        )
        return {
            "pathname": result.pathname,
            "url": result.url,
            "downloadUrl": result.download_url,
        }


class BlobReviewStateStorage:
    """Use immutable Blob paths as a distributed compare-and-swap log."""

    source_prefix = "workbench/review-sources/"
    revision_prefix = "workbench/review-revisions/"

    def __init__(self, blob: BlobStore):
        self.blob = blob

    def _source_path(self, split: str, paper_id: str) -> str:
        return f"{self.source_prefix}{split}/{paper_id}.json"

    def _revision_prefix(self, split: str, paper_id: str) -> str:
        return f"{self.revision_prefix}{split}/{paper_id}/"

    def _revision_path(self, split: str, paper_id: str, revision: int) -> str:
        return f"{self._revision_prefix(split, paper_id)}{revision:08d}.json"

    @staticmethod
    def _body(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

    def _read(
        self, pathname: str, model: type[ReviewPaperSource] | type[ReviewRevision]
    ):
        item = self.blob.find(pathname)
        if item is None:
            raise FileNotFoundError(pathname)
        return model.model_validate_json(self.blob.download(item))

    def _put_exclusive(self, pathname: str, value: Any) -> None:
        try:
            self.blob.put(
                pathname,
                self._body(value),
                "application/json",
                overwrite=False,
            )
        except Exception as error:
            if self.blob.find(pathname) is not None:
                raise FileExistsError(pathname) from error
            raise

    def create(self, split: str, paper_id: str, source: ReviewPaperSource) -> None:
        try:
            self._put_exclusive(
                self._source_path(split, paper_id), source.model_dump(mode="json")
            )
        except FileExistsError as error:
            raise ValueError("paper already exists") from error

    def load_source(self, split: str, paper_id: str) -> ReviewPaperSource:
        return self._read(self._source_path(split, paper_id), ReviewPaperSource)

    def load_revision(self, split: str, paper_id: str) -> ReviewRevision:
        revisions = self.blob.list(self._revision_prefix(split, paper_id))
        if revisions:
            latest = max(revisions, key=lambda item: str(item.get("pathname", "")))
            return ReviewRevision.model_validate_json(self.blob.download(latest))
        return self.load_source(split, paper_id).initial_revision

    def compare_and_swap(
        self,
        split: str,
        paper_id: str,
        expected_revision: int,
        revision: ReviewRevision,
    ) -> None:
        current = self.load_revision(split, paper_id)
        if current.revision != expected_revision:
            raise StaleRevisionError(
                f"stale revision {expected_revision}; current revision is {current.revision}"
            )
        if revision.revision != expected_revision + 1:
            raise ValueError(
                "new revision must immediately follow the expected revision"
            )
        try:
            self._put_exclusive(
                self._revision_path(split, paper_id, revision.revision),
                revision.model_dump(mode="json"),
            )
        except FileExistsError as error:
            raise StaleRevisionError(
                f"stale revision {expected_revision}; revision {revision.revision} already exists"
            ) from error

    def list_paper_ids(self, split: str) -> list[str]:
        prefix = f"{self.source_prefix}{split}/"
        return sorted(
            Path(str(item["pathname"])).stem
            for item in self.blob.list(prefix)
            if str(item.get("pathname", "")).endswith(".json")
        )

    def list_paper_heads(self, split: str) -> list[tuple[str, int]]:
        """List paper IDs and current revisions without downloading study records.

        Source and revision pathnames already contain this information. Reading every
        full extraction merely to build the paper rail made startup proportional to
        the size of all studies, so revision directories are inspected concurrently
        and their JSON bodies stay untouched until a reviewer opens one paper.
        """

        paper_ids = self.list_paper_ids(split)

        def head(paper_id: str) -> tuple[str, int]:
            revisions = self.blob.list(self._revision_prefix(split, paper_id))
            numbers = [
                int(Path(str(item.get("pathname", ""))).stem)
                for item in revisions
                if Path(str(item.get("pathname", ""))).stem.isdigit()
            ]
            return paper_id, max(numbers, default=1)

        with ThreadPoolExecutor(max_workers=min(8, len(paper_ids) or 1)) as executor:
            return list(executor.map(head, paper_ids))


class BlobComparisonStorage:
    """Persist blinded comparison sources and reviewer drafts as immutable blobs."""

    source_prefix = "workbench/comparison-sources/"
    review_prefix = "workbench/comparison-reviews/"
    utility_prefix = "workbench/comparison-utility/"
    preference_prefix = "workbench/comparison-preferences/"

    def __init__(self, blob: BlobStore):
        self.blob = blob

    @staticmethod
    def _body(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()

    def _put_exclusive(self, pathname: str, value: Any) -> None:
        try:
            self.blob.put(
                pathname, self._body(value), "application/json", overwrite=False
            )
        except Exception as error:
            if self.blob.find(pathname) is not None:
                raise FileExistsError(pathname) from error
            raise

    def _source_path(self, comparison_id: str) -> str:
        return f"{self.source_prefix}{comparison_id}.json"

    def _review_prefix(self, comparison_id: str, reviewer_id: str) -> str:
        return f"{self.review_prefix}{comparison_id}/{reviewer_id}/"

    def create(self, source: ComparisonSource) -> None:
        try:
            self._put_exclusive(
                self._source_path(source.comparison_id), source.model_dump(mode="json")
            )
        except FileExistsError as error:
            raise ValueError("comparison already exists") from error

    def list_ids(self) -> list[str]:
        return sorted(
            Path(str(item["pathname"])).stem
            for item in self.blob.list(self.source_prefix)
            if str(item.get("pathname", "")).endswith(".json")
        )

    def load_source(self, comparison_id: str) -> ComparisonSource:
        item = self.blob.find(self._source_path(comparison_id))
        if item is None:
            raise FileNotFoundError(comparison_id)
        return ComparisonSource.model_validate_json(self.blob.download(item))

    def load_review(
        self, comparison_id: str, reviewer_id: str
    ) -> ComparisonReview | None:
        items = self.blob.list(self._review_prefix(comparison_id, reviewer_id))
        if not items:
            return None
        latest = max(items, key=lambda item: str(item.get("pathname", "")))
        return ComparisonReview.model_validate_json(self.blob.download(latest))

    def compare_and_swap(
        self, expected_revision: int, review: ComparisonReview
    ) -> None:
        current = self.load_review(review.comparison_id, review.reviewer_id)
        current_revision = current.revision if current else 0
        if (
            current_revision != expected_revision
            or review.revision != expected_revision + 1
        ):
            raise StaleRevisionError("comparison review changed in another session")
        pathname = (
            f"{self._review_prefix(review.comparison_id, review.reviewer_id)}"
            f"{review.revision:08d}.json"
        )
        try:
            self._put_exclusive(pathname, review.model_dump(mode="json"))
        except FileExistsError as error:
            raise StaleRevisionError(
                "comparison review changed in another session"
            ) from error

    def _utility_path(self, comparison_id: str, reviewer_id: str) -> str:
        return f"{self.utility_prefix}{comparison_id}/{reviewer_id}.json"

    def load_utility(
        self, comparison_id: str, reviewer_id: str
    ) -> NativeUtilityReview | None:
        item = self.blob.find(self._utility_path(comparison_id, reviewer_id))
        return (
            NativeUtilityReview.model_validate_json(self.blob.download(item))
            if item
            else None
        )

    def save_utility(self, review: NativeUtilityReview) -> None:
        try:
            self._put_exclusive(
                self._utility_path(review.comparison_id, review.reviewer_id),
                review.model_dump(mode="json"),
            )
        except FileExistsError as error:
            raise ValueError("native utility review is already submitted") from error

    def _preference_path(self, comparison_id: str, reviewer_id: str) -> str:
        return f"{self.preference_prefix}{comparison_id}/{reviewer_id}.json"

    def load_preference(
        self, comparison_id: str, reviewer_id: str
    ) -> PairwisePreferenceReview | None:
        item = self.blob.find(self._preference_path(comparison_id, reviewer_id))
        return (
            PairwisePreferenceReview.model_validate_json(self.blob.download(item))
            if item
            else None
        )

    def save_preference(self, review: PairwisePreferenceReview) -> None:
        try:
            self._put_exclusive(
                self._preference_path(review.comparison_id, review.reviewer_id),
                review.model_dump(mode="json"),
            )
        except FileExistsError as error:
            raise ValueError("pairwise preference is already submitted") from error


class VercelReviewApplication(ReviewApplication):
    """Adapt the filesystem-oriented review core to Vercel's ephemeral runtime.

    Review transitions commit immutable Blob revisions, so separate serverless
    instances cannot overwrite each other. PDFs remain separate blobs and are
    downloaded lazily.
    """

    users_pathname = "workbench/review-users.json"
    pdf_prefix = "papers/"
    review_pdf_prefix = "workbench/review-pdfs/"

    def __init__(self, blob: BlobStore, workspace: Path):
        self.blob = blob
        self.workspace = workspace
        self.remote_pdfs: dict[tuple[str | None, str, str], dict] = {}
        self._pdf_download_lock = threading.Lock()
        ground_truth, pdfs = workspace / "review_data", workspace / "pdfs"
        ground_truth.mkdir(parents=True, exist_ok=True)
        pdfs.mkdir(parents=True, exist_ok=True)
        super().__init__(
            pdfs,
            ground_truth,
            BlobReviewStateStorage(blob),
            BlobComparisonStorage(blob),
        )
        self._hydrate()

    def _hydrate(self) -> None:
        """Load small user metadata and index private PDFs for lazy download."""

        if not self.blob.configured:
            return
        with ThreadPoolExecutor(max_workers=3) as executor:
            users_request = executor.submit(self.blob.find, self.users_pathname)
            legacy_pdfs = executor.submit(self.blob.list, self.pdf_prefix)
            review_pdfs = executor.submit(self.blob.list, self.review_pdf_prefix)
            users_blob = users_request.result()
            legacy_pdf_items = legacy_pdfs.result()
            review_pdf_items = review_pdfs.result()
        if users_blob:
            write_json_atomic(
                self.ground_truth_dir / "users.json",
                json.loads(self.blob.download(users_blob)),
            )
        for blob in legacy_pdf_items:
            self._index_pdf(blob, split=None)
        for blob in review_pdf_items:
            pathname = str(blob.get("pathname", ""))
            relative = pathname.removeprefix(self.review_pdf_prefix)
            split, separator, _ = relative.partition("/")
            if separator and split in {"calibration", "dev", "test"}:
                self._index_pdf(blob, split=split)

    def list_papers(self, split: str) -> list[dict[str, Any]]:
        """Return a lightweight paper rail and defer full study reads until selection."""

        self.store.validate_identity(split, "10.0000--placeholder")
        return [
            {
                "id": paper_id,
                "revision": revision,
                "sources": self.available_sources(paper_id, split),
            }
            for paper_id, revision in self.store.storage.list_paper_heads(split)
        ]

    def _index_pdf(self, blob: dict, *, split: str | None) -> None:
        """Index one private PDF without conflating dataset generations."""

        name = Path(str(blob.get("pathname", ""))).name
        if not name.endswith(".pdf"):
            return
        if name.endswith(".supplement.pdf"):
            paper_id, source = name.removesuffix(".supplement.pdf"), "supplement"
        else:
            paper_id, source = name.removesuffix(".pdf"), "main"
        self.remote_pdfs[(split, paper_id, source)] = blob

    def _sync_users(self) -> None:
        """Persist the non-scientific reviewer directory separately from paper state."""

        path = self.ground_truth_dir / "users.json"
        if self.blob.configured and path.exists():
            self.blob.put(
                self.users_pathname,
                path.read_bytes(),
                "application/json",
            )

    def _pdf_cache_path(
        self, paper_id: str, source: str, split: str | None
    ) -> Path:
        """Keep split-scoped downloads separate inside an ephemeral runtime."""

        self.store.validate_identity(split or "dev", paper_id)
        if source not in {"main", "supplement"}:
            raise ValueError("source must be main or supplement")
        if split is None:
            return super().pdf_path(paper_id, source)
        directory = self.pdf_dir / split
        directory.mkdir(parents=True, exist_ok=True)
        suffix = ".supplement.pdf" if source == "supplement" else ".pdf"
        return directory / f"{paper_id}{suffix}"

    def ensure_pdf(
        self, paper_id: str, source: str, split: str | None = None
    ) -> Path:
        """Download the split-scoped source, falling back only to legacy storage."""

        exact_key = (split, paper_id, source)
        legacy_key = (None, paper_id, source)
        key = exact_key if exact_key in self.remote_pdfs else legacy_key
        path = self._pdf_cache_path(paper_id, source, key[0])
        if path.exists() and path.stat().st_size:
            return path
        blob = self.remote_pdfs.get(key)
        if not blob:
            raise FileNotFoundError(path)
        # The page image and text arrive as concurrent browser requests. Serialize
        # their first access so a large SI is downloaded once, never read halfway.
        with self._pdf_download_lock:
            if not path.exists() or not path.stat().st_size:
                _write_bytes_atomic(path, self.blob.download(blob))
        return path

    def pdf_path(
        self, paper_id: str, source: str = "main", split: str | None = None
    ) -> Path:
        if (split, paper_id, source) in getattr(self, "remote_pdfs", {}):
            return self.ensure_pdf(paper_id, source, split)
        if (None, paper_id, source) in getattr(self, "remote_pdfs", {}):
            return self.ensure_pdf(paper_id, source, None)
        return self._pdf_cache_path(paper_id, source, split)

    def available_sources(self, paper_id: str, split: str) -> list[str]:
        """Use the hydrated Blob index without downloading source documents."""

        return [
            source
            for source in ("main", "supplement")
            if (split, paper_id, source) in self.remote_pdfs
            or (None, paper_id, source) in self.remote_pdfs
        ]

    def _write_users(self, users: list[dict[str, str]]) -> None:
        """Persist remotely only when the shared application changed the directory."""

        super()._write_users(users)
        self._sync_users()

    def import_paper(
        self,
        split: str,
        paper_id: str,
        pdf_bytes: bytes,
        extraction_bytes: bytes,
        **kwargs,
    ):
        result = super().import_paper(
            split, paper_id, pdf_bytes, extraction_bytes, **kwargs
        )
        main = self.blob.put(
            f"{self.review_pdf_prefix}{split}/{paper_id}.pdf",
            pdf_bytes,
            "application/pdf",
            overwrite=False,
        )
        self.remote_pdfs[(split, paper_id, "main")] = main
        supplement = kwargs.get("supplement_bytes", b"")
        if supplement:
            item = self.blob.put(
                f"{self.review_pdf_prefix}{split}/{paper_id}.supplement.pdf",
                supplement,
                "application/pdf",
                overwrite=False,
            )
            self.remote_pdfs[(split, paper_id, "supplement")] = item
        return result


blob_store = BlobStore()
review_application = VercelReviewApplication(
    blob_store, Path("/tmp/perla-study-review")
)
authenticator = None
has_internal_accounts = bool(os.environ.get("REVIEW_INTERNAL_ACCOUNTS"))
clerk_publishable_key = os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
has_clerk = clerk_key_allowed(
    clerk_publishable_key,
    os.environ.get("VERCEL_ENV"),
)
if has_internal_accounts and has_clerk:
    from review_workbench.auth import InternalOrClerkAuthenticator

    authenticator = InternalOrClerkAuthenticator()
elif has_internal_accounts:
    from review_workbench.auth import InternalAuthenticator

    authenticator = InternalAuthenticator()
elif has_clerk:
    from review_workbench.auth import ClerkAuthenticator

    authenticator = ClerkAuthenticator()

BaseHandler = make_handler(review_application, authenticator)


class handler(BaseHandler):
    """Hydrate source PDFs before delegating requests to the shared HTTP handler.

    Only routes that read PDFs trigger the lazy download; all review behavior remains
    in ``ReviewApplication`` and its generated base handler.
    """

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if any(
            parsed.path.startswith(prefix)
            for prefix in (
                "/api/pdf/",
                "/api/pdf-page/",
                "/api/pdf-text/",
                "/api/search/",
            )
        ):
            paper_id = unquote(parsed.path.split("/", 3)[-1])
            query = parse_qs(parsed.query)
            source = query.get("source", ["main"])[0]
            split = query.get("split", [None])[0]
            try:
                review_application.ensure_pdf(paper_id, source, split)
            except FileNotFoundError:
                pass
        super().do_GET()
