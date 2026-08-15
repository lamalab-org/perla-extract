"""Vercel entry point backed by private Blob storage."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

MODULE_PATH = Path(__file__).resolve()
PROJECT_ROOT = MODULE_PATH.parents[2] if MODULE_PATH.parent.parent.name == "review_workbench" else MODULE_PATH.parents[1]
sys.path[:0] = [str(PROJECT_ROOT), str(PROJECT_ROOT / "src")]

from review_workbench.server import ReviewApplication, make_handler  # noqa: E402


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
        if not self.configured:
            return []
        return json.loads(self._request(f"{self.endpoint}?prefix={quote(prefix)}&limit=1000")).get("blobs", [])

    def find(self, pathname: str) -> dict | None:
        return next((item for item in self.list(pathname) if item.get("pathname") == pathname), None)

    def download(self, blob: dict) -> bytes:
        return self._request(str(blob["url"]))

    def put(self, pathname: str, body: bytes, content_type: str) -> dict:
        if self.client is None:
            raise RuntimeError("BLOB_READ_WRITE_TOKEN is not configured")
        result = self.client.put(
            pathname, body, access="private", content_type=content_type,
            add_random_suffix=False, overwrite=True, cache_control_max_age=60,
        )
        return {"pathname": result.pathname, "url": result.url, "downloadUrl": result.download_url}


class VercelReviewApplication(ReviewApplication):
    """Mirror mutable JSON locally and synchronize after every accepted decision."""

    state_pathname = "workbench/study-review-state.json"
    pdf_prefix = "papers/"

    def __init__(self, blob: BlobStore, workspace: Path):
        self.blob = blob
        self.workspace = workspace
        self._write_lock = threading.RLock()
        self.remote_pdfs: dict[tuple[str, str], dict] = {}
        ground_truth, pdfs = workspace / "review_data", workspace / "pdfs"
        ground_truth.mkdir(parents=True, exist_ok=True)
        pdfs.mkdir(parents=True, exist_ok=True)
        super().__init__(pdfs, ground_truth)
        self._hydrate()

    def _hydrate(self) -> None:
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
                target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        for blob in self.blob.list(self.pdf_prefix):
            name = Path(str(blob.get("pathname", ""))).name
            if not name.endswith(".pdf"):
                continue
            if name.endswith(".supplement.pdf"):
                paper_id, source = name.removesuffix(".supplement.pdf"), "supplement"
            else:
                paper_id, source = name.removesuffix(".pdf"), "main"
            self.remote_pdfs[(paper_id, source)] = blob

    def _sync(self) -> None:
        if not self.blob.configured:
            return
        with self._write_lock:
            files = {
                str(path.relative_to(self.ground_truth_dir)): json.loads(path.read_text(encoding="utf-8"))
                for path in sorted(self.ground_truth_dir.rglob("*.json"))
            }
            body = json.dumps({"schema_version": 2, "json_files": files}, ensure_ascii=False, separators=(",", ":")).encode()
            self.blob.put(self.state_pathname, body, "application/json")

    def ensure_pdf(self, paper_id: str, source: str) -> Path:
        path = super().pdf_path(paper_id, source)
        if path.exists() and path.stat().st_size:
            return path
        blob = self.remote_pdfs.get((paper_id, source))
        if not blob:
            raise FileNotFoundError(path)
        path.write_bytes(self.blob.download(blob))
        return path

    def pdf_path(self, paper_id: str, source: str = "main") -> Path:
        path = super().pdf_path(paper_id, source)
        if (paper_id, source) in getattr(self, "remote_pdfs", {}):
            return self.ensure_pdf(paper_id, source)
        return path

    def ensure_authenticated_user(self, user: dict[str, str]) -> dict[str, str]:
        result = super().ensure_authenticated_user(user)
        self._sync()
        return result

    def add_reviewer(self, payload: object) -> dict[str, str]:
        result = super().add_reviewer(payload)
        self._sync()
        return result

    def import_paper(self, split: str, paper_id: str, pdf_bytes: bytes, extraction_bytes: bytes, **kwargs):
        result = super().import_paper(split, paper_id, pdf_bytes, extraction_bytes, **kwargs)
        main = self.blob.put(f"{self.pdf_prefix}{paper_id}.pdf", pdf_bytes, "application/pdf")
        self.remote_pdfs[(paper_id, "main")] = main
        supplement = kwargs.get("supplement_bytes", b"")
        if supplement:
            item = self.blob.put(f"{self.pdf_prefix}{paper_id}.supplement.pdf", supplement, "application/pdf")
            self.remote_pdfs[(paper_id, "supplement")] = item
        self._sync()
        return result

    def mutate(self, *args, **kwargs):
        result = super().mutate(*args, **kwargs)
        self._sync()
        return result

    def inventory_audit(self, *args, **kwargs):
        result = super().inventory_audit(*args, **kwargs)
        self._sync()
        return result

    def decide_record(self, *args, **kwargs):
        result = super().decide_record(*args, **kwargs)
        self._sync()
        return result

    def complete_stage(self, *args, **kwargs):
        result = super().complete_stage(*args, **kwargs)
        self._sync()
        return result


blob_store = BlobStore()
review_application = VercelReviewApplication(blob_store, Path("/tmp/perla-study-review"))
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
        if any(parsed.path.startswith(prefix) for prefix in ("/api/pdf/", "/api/pdf-page/", "/api/pdf-text/", "/api/search/")):
            paper_id = unquote(parsed.path.split("/", 3)[-1])
            source = (dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item).get("source") or "main")
            try:
                review_application.ensure_pdf(paper_id, source)
            except FileNotFoundError:
                pass
        super().do_GET()
