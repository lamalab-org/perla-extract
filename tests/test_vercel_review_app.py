import json

import pytest

from review_workbench.api.index import BlobStore, VercelReviewApplication


class MemoryBlobStore(BlobStore):
    def __init__(self):
        self.token = "test"
        self.objects = {}

    def list(self, prefix):
        return [
            {"pathname": path, "url": path}
            for path in self.objects
            if path.startswith(prefix)
        ]

    def download(self, blob):
        return self.objects[blob["pathname"]]

    def put(self, pathname, body, content_type):
        self.objects[pathname] = body
        return {"pathname": pathname, "url": pathname, "contentType": content_type}


def test_vercel_adapter_persists_reviewers_and_imported_pdfs(tmp_path):
    blob = MemoryBlobStore()
    app = VercelReviewApplication(blob, tmp_path / "first")
    user = app.add_reviewer({"name": "Ada Reviewer"})
    truth = {"cells": [{"pce": {"unit": "%", "value": 20.0}, "layers": []}]}
    app.import_paper(
        "dev",
        "10.1234--example.1",
        b"%PDF-1.4\n%%EOF\n",
        json.dumps(truth).encode(),
    )

    restored = VercelReviewApplication(blob, tmp_path / "second")

    assert user in restored.users()
    assert restored.load_ground_truth("dev", "10.1234--example.1") == truth
    assert restored.ensure_pdf("10.1234--example.1").read_bytes().startswith(b"%PDF")


def test_unconfigured_blob_store_hydrates_locally_and_rejects_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    blob = BlobStore(token="")
    app = VercelReviewApplication(blob, tmp_path / "unconfigured")

    assert not blob.configured
    assert blob.list("papers/") == []
    assert app.users() == [{"id": "reviewer", "name": "Reviewer"}]
    with pytest.raises(RuntimeError, match="BLOB_READ_WRITE_TOKEN"):
        app._sync_state()
