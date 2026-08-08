"""Vercel entry point for the collaborative ground-truth review workbench.

The repository remains the immutable seed dataset. Mutable JSON review state and
PDFs live in a private Vercel Blob store and are mirrored into /tmp for the
existing review application.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen


MODULE_PATH = Path(__file__).resolve()
PROJECT_ROOT = (
    MODULE_PATH.parents[2]
    if MODULE_PATH.parent.parent.name == "review_workbench"
    else MODULE_PATH.parents[1]
)
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from review_workbench.server import ReviewApplication, make_handler  # noqa: E402


class BlobStore:
    """Small standard-library client for the Vercel Blob REST API."""

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

    def _request(
        self, url: str, *, method: str = "GET", data: bytes | None = None,
        headers: dict[str, str] | None = None
    ) -> bytes:
        request_headers = {"Authorization": f"Bearer {self.token}"}
        request_headers.update(headers or {})
        request = Request(url, data=data, method=method, headers=request_headers)
        with urlopen(request, timeout=60) as response:
            return response.read()

    def list(self, prefix: str) -> list[dict]:
        if not self.configured:
            return []
        url = f"{self.endpoint}?prefix={quote(prefix)}&limit=1000"
        payload = json.loads(self._request(url))
        return payload.get("blobs", [])

    def find(self, pathname: str) -> dict | None:
        return next(
            (blob for blob in self.list(pathname) if blob.get("pathname") == pathname),
            None,
        )

    def download(self, blob: dict) -> bytes:
        return self._request(str(blob["url"]))

    def put(self, pathname: str, body: bytes, content_type: str) -> dict:
        if self.client is None:
            raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")
        result = self.client.put(
            pathname,
            body,
            access="private",
            content_type=content_type,
            add_random_suffix=False,
            overwrite=True,
            cache_control_max_age=60,
        )
        return {
            "pathname": result.pathname,
            "url": result.url,
            "downloadUrl": result.download_url,
            "contentType": result.content_type,
        }


class VercelReviewApplication(ReviewApplication):
    """Blob-backed deployment adapter around the local review application."""

    state_pathname = "workbench/state.json"
    pdf_prefix = "papers/"

    def __init__(self, blob: BlobStore, workspace: Path):
        self.blob = blob
        self.workspace = workspace
        self._write_lock = threading.RLock()
        self.remote_pdfs: dict[str, dict] = {}
        ground_truth = workspace / "ground_truth"
        pdfs = workspace / "pdfs"
        self._seed_workspace(ground_truth, pdfs)
        super().__init__(pdfs, ground_truth)
        self._hydrate_remote_state()

    def _seed_workspace(self, ground_truth: Path, pdfs: Path) -> None:
        if not ground_truth.exists():
            shutil.copytree(
                PROJECT_ROOT / "src" / "perla_extract" / "data" / "ground_truth",
                ground_truth,
            )
        pdfs.mkdir(parents=True, exist_ok=True)

    def _hydrate_remote_state(self) -> None:
        if not self.blob.configured:
            return
        state_blob = self.blob.find(self.state_pathname)
        if state_blob:
            state = json.loads(self.blob.download(state_blob))
            for relative, payload in state.get("json_files", {}).items():
                target = (self.ground_truth_dir / relative).resolve()
                if self.ground_truth_dir not in target.parents:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
        for blob in self.blob.list(self.pdf_prefix):
            pathname = str(blob.get("pathname", ""))
            if not pathname.startswith(self.pdf_prefix) or not pathname.endswith(".pdf"):
                continue
            paper_id = Path(pathname).stem
            self.remote_pdfs[paper_id] = blob
            marker = self.pdf_dir / f"{paper_id}.pdf"
            if not marker.exists():
                marker.touch()

    def _state_payload(self) -> bytes:
        files = {}
        for path in sorted(self.ground_truth_dir.rglob("*.json")):
            files[str(path.relative_to(self.ground_truth_dir))] = json.loads(
                path.read_text(encoding="utf-8")
            )
        return json.dumps(
            {"schema_version": 1, "json_files": files},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()

    def _sync_state(self) -> None:
        with self._write_lock:
            self.blob.put(self.state_pathname, self._state_payload(), "application/json")

    def ensure_pdf(self, paper_id: str) -> Path:
        path = super().pdf_path(paper_id)
        if path.exists() and path.stat().st_size:
            return path
        blob = self.remote_pdfs.get(paper_id)
        if not blob:
            raise FileNotFoundError(path)
        path.write_bytes(self.blob.download(blob))
        return path

    def get_review(self, split: str, paper_id: str, reviewer_id: str = "reviewer") -> dict:
        self.ensure_pdf(paper_id)
        return super().get_review(split, paper_id, reviewer_id)

    def get_quantities(self, split: str, paper_id: str) -> dict:
        self.ensure_pdf(paper_id)
        return super().get_quantities(split, paper_id)

    def search_pdf(self, paper_id: str, query: str) -> list[dict]:
        self.ensure_pdf(paper_id)
        return super().search_pdf(paper_id, query)

    def render_pdf_page(
        self, paper_id: str, page_number: int, scale: float = 1.5
    ) -> tuple[bytes, int]:
        self.ensure_pdf(paper_id)
        return super().render_pdf_page(paper_id, page_number, scale)

    def pdf_page_text(self, paper_id: str, page_number: int) -> tuple[str, int]:
        self.ensure_pdf(paper_id)
        return super().pdf_page_text(paper_id, page_number)

    def pdf_page_text_lines(self, paper_id: str, page_number: int) -> list[dict]:
        self.ensure_pdf(paper_id)
        return super().pdf_page_text_lines(paper_id, page_number)

    def add_reviewer(self, payload: object) -> dict[str, str]:
        result = super().add_reviewer(payload)
        self._sync_state()
        return result

    def ensure_authenticated_user(self, user: dict[str, str]) -> dict[str, str]:
        current = next(
            (record for record in self.users() if record.get("id") == user.get("id")),
            None,
        )
        if current == user:
            return current
        result = super().ensure_authenticated_user(user)
        self._sync_state()
        return result

    def save_review_evidence(self, split: str, paper_id: str, payload: object) -> dict:
        result = super().save_review_evidence(split, paper_id, payload)
        self._sync_state()
        return result

    def save_paper_figure_audit(
        self, split: str, paper_id: str, payload: object
    ) -> dict:
        result = super().save_paper_figure_audit(split, paper_id, payload)
        self._sync_state()
        return result

    def add_review_comment(self, split: str, paper_id: str, payload: object) -> dict:
        result = super().add_review_comment(split, paper_id, payload)
        self._sync_state()
        return result

    def add_missing_issue(self, split: str, paper_id: str, payload: object) -> dict:
        result = super().add_missing_issue(split, paper_id, payload)
        self._sync_state()
        return result

    def resolve_missing_issue(
        self, split: str, paper_id: str, issue_id: str, payload: object
    ) -> dict:
        result = super().resolve_missing_issue(split, paper_id, issue_id, payload)
        self._sync_state()
        return result

    def save_ground_truth(self, split: str, paper_id: str, payload: object) -> None:
        super().save_ground_truth(split, paper_id, payload)
        self._sync_state()

    def save_metadata(self, split: str, paper_id: str, payload: object) -> dict:
        result = super().save_metadata(split, paper_id, payload)
        self._sync_state()
        return result

    def import_paper(
        self,
        split: str,
        paper_id: str,
        pdf_bytes: bytes,
        ground_truth_bytes: bytes,
    ) -> dict:
        result = super().import_paper(
            split, paper_id, pdf_bytes, ground_truth_bytes
        )
        blob = self.blob.put(
            f"{self.pdf_prefix}{paper_id}.pdf", pdf_bytes, "application/pdf"
        )
        self.remote_pdfs[paper_id] = blob
        self._sync_state()
        return result


blob_store = BlobStore()
review_application = VercelReviewApplication(
    blob_store, Path("/tmp/perla-review-workbench")
)
authenticator = None
if os.environ.get("REVIEW_INTERNAL_ACCOUNTS"):
    from review_workbench.auth import InternalAuthenticator

    authenticator = InternalAuthenticator()
elif os.environ.get("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"):
    from review_workbench.auth import ClerkAuthenticator

    authenticator = ClerkAuthenticator()
BaseHandler = make_handler(review_application, authenticator)


class handler(BaseHandler):
    """Vercel Python runtime handler."""

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        pdf_prefix = next(
            (
                prefix
                for prefix in ("/api/pdf/", "/api/pdf-page/")
                if parsed.path.startswith(prefix)
            ),
            None,
        )
        if pdf_prefix:
            paper_id = unquote(parsed.path.removeprefix(pdf_prefix))
            try:
                review_application.ensure_pdf(paper_id)
            except FileNotFoundError:
                pass
        super().do_GET()
