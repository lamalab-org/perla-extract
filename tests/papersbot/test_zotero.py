from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from perla_extract.papersbot.bot import load_state, run_papersbot
from perla_extract.papersbot.cli import main
from perla_extract.papersbot.zotero import ZoteroClient


class FakeResponse:
    def __init__(
        self,
        payload=None,
        content: bytes = b"",
        headers=None,
        status_code: int = 200,
    ):
        self.payload = payload
        self.content = content
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def zotero_item(doi: str = "10.1234/zotero.1") -> dict:
    return {
        "key": "PARENT01",
        "data": {
            "key": "PARENT01",
            "itemType": "journalArticle",
            "title": "A stable perovskite solar cell",
            "abstractNote": "Photovoltaic device performance",
            "DOI": doi,
            "url": f"https://doi.org/{doi}",
            "date": "2025-03-04",
        },
    }


class ZoteroReadSession:
    def get(self, url, **kwargs):
        del kwargs
        if url.endswith("/groups/42/items/top"):
            return FakeResponse([zotero_item()], headers={"Total-Results": "1"})
        if url.endswith("/items/PARENT01/children"):
            return FakeResponse(
                [
                    {
                        "key": "PDFCHILD",
                        "data": {
                            "key": "PDFCHILD",
                            "itemType": "attachment",
                            "linkMode": "imported_file",
                            "contentType": "application/pdf",
                        },
                    }
                ]
            )
        if url.endswith("/items/PDFCHILD/file"):
            return FakeResponse(content=b"%PDF-1.7\nfrom zotero")
        raise AssertionError(url)


def test_group_items_and_stored_pdf_enter_the_normal_bot_pipeline(tmp_path: Path):
    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        rss_enabled=False,
        openalex_enabled=False,
        zotero_group_id="42",
        zotero_api_key="read-key",
        session=ZoteroReadSession(),
    )

    assert result.status == "complete"
    assert result.source_counts == {"zotero": 1}
    assert result.zotero is not None
    assert result.zotero.items_seen == 1
    assert result.zotero.pdfs_downloaded == 1
    assert result.downloaded_files[0].read_bytes().startswith(b"%PDF-")
    record = load_state(tmp_path / "state/state.json").papers["10.1234/zotero.1"]
    assert record.zotero_item_key == "PARENT01"
    assert record.zotero_attachment_key == "PDFCHILD"
    assert record.pdf_source == "zotero"
    assert record.pdf_access_basis == "member-supplied"
    assert record.pdf_sha256
    assert record.sources == ["zotero:groups/42"]
    assert "read-key" not in (tmp_path / "state/last_run.json").read_text()


class RedirectingAttachmentSession(ZoteroReadSession):
    def __init__(self):
        self.storage_request = None

    def get(self, url, **kwargs):
        if url.endswith("/items/PDFCHILD/file"):
            return FakeResponse(
                headers={"Location": "https://storage.example.test/file"},
                status_code=302,
            )
        if url == "https://storage.example.test/file":
            self.storage_request = kwargs
            return FakeResponse(content=b"%PDF-1.7\nredirected")
        return super().get(url, **kwargs)


def test_attachment_redirect_does_not_forward_the_api_key(tmp_path: Path):
    session = RedirectingAttachmentSession()
    client = ZoteroClient(session, group_id="42", api_key="secret-key")

    client.download_attachment("PDFCHILD", tmp_path / "paper.pdf")

    assert session.storage_request is not None
    assert "headers" not in session.storage_request


class ZoteroNoDoiSession(ZoteroReadSession):
    def get(self, url, **kwargs):
        if url.endswith("/groups/42/items/top"):
            item = zotero_item(doi="")
            item["data"]["url"] = ""
            return FakeResponse([item], headers={"Total-Results": "1"})
        return super().get(url, **kwargs)


def test_stored_group_pdf_does_not_require_a_doi(tmp_path: Path):
    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        rss_enabled=False,
        openalex_enabled=False,
        zotero_group_id="42",
        session=ZoteroNoDoiSession(),
    )

    assert result.status == "complete"
    assert result.outcome_counts == {"downloaded": 1}
    assert result.downloaded_files[0].name == "zotero-PARENT01.pdf"


class CuratedCollectionSession(ZoteroReadSession):
    def get(self, url, **kwargs):
        if url.endswith("/groups/42/collections/COLL/items/top"):
            item = zotero_item()
            item["data"]["title"] = "Review selected by the journal club"
            item["data"]["abstractNote"] = ""
            return FakeResponse([item], headers={"Total-Results": "1"})
        return super().get(url, **kwargs)


def test_curated_collection_bypasses_automatic_relevance_filters(tmp_path: Path):
    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        rss_enabled=False,
        openalex_enabled=False,
        zotero_group_id="42",
        zotero_collection_key="COLL",
        zotero_curated=True,
        session=CuratedCollectionSession(),
    )

    assert result.outcome_counts == {"downloaded": 1}
    record = load_state(tmp_path / "state/state.json").papers["10.1234/zotero.1"]
    assert record.zotero_curated is True


def test_curated_mode_requires_a_specific_collection(tmp_path: Path):
    with pytest.raises(ValueError, match="collection-key"):
        run_papersbot(
            tmp_path / "papers",
            state_dir=tmp_path / "state",
            rss_enabled=False,
            openalex_enabled=False,
            zotero_group_id="42",
            zotero_curated=True,
            session=ZoteroReadSession(),
        )


def test_cli_exposes_only_read_side_zotero_controls():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--zotero-group-id" in result.output
    assert "--zotero-collection-key" in result.output
    assert "--zotero-api-key" in result.output
    assert "--zotero-curated" in result.output
    assert "--zotero-save" not in result.output
    assert "--zotero-pdf-policy" not in result.output
