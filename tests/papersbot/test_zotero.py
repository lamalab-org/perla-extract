from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest
from click.testing import CliRunner

from perla_extract.papersbot.bot import load_state, run_papersbot
from perla_extract.papersbot.cli import main
from perla_extract.papersbot.models import PaperRecord
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


def test_curated_input_cannot_also_be_the_bot_output(tmp_path: Path):
    with pytest.raises(ValueError, match="different Zotero collections"):
        run_papersbot(
            tmp_path / "papers",
            state_dir=tmp_path / "state",
            rss_enabled=False,
            openalex_enabled=False,
            zotero_group_id="42",
            zotero_collection_key="COLL",
            zotero_output_collection_key="COLL",
            zotero_curated=True,
            session=ZoteroReadSession(),
        )


class ZoteroWriteSession:
    def __init__(self):
        self.posts = []

    def get(self, url, **kwargs):
        if url.endswith("/groups/42/collections/COLL/items/top"):
            return FakeResponse([], headers={"Total-Results": "0"})
        if url.endswith("/groups/42/items/top"):
            assert kwargs["params"]["qmode"] == "everything"
            return FakeResponse([])
        if url.endswith("/items/new"):
            return FakeResponse(
                {
                    "itemType": "journalArticle",
                    "title": "",
                    "creators": [],
                    "abstractNote": "",
                    "date": "",
                    "DOI": "",
                    "url": "",
                    "collections": [],
                    "tags": [],
                    "relations": {},
                }
            )
        raise AssertionError(url)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return FakeResponse({"successful": {"0": {"key": "CREATED1"}}})


def test_writeback_uses_collection_and_never_persists_the_api_key():
    session = ZoteroWriteSession()
    client = ZoteroClient(
        session,
        group_id="42",
        collection_key="COLL",
        output_collection_key="OUT",
        api_key="secret-key",
    )
    entries, stats = client.fetch_items()
    record = PaperRecord(
        identifier="10.1234/new.1",
        doi="10.1234/new.1",
        title="New perovskite solar cell",
        summary="Device abstract",
        link="https://doi.org/10.1234/new.1",
        publication_date=date(2025, 1, 2),
    )

    key, created = client.save_item(record)

    assert entries == []
    assert stats.items_seen == 0
    assert (key, created) == ("CREATED1", True)
    _, request = session.posts[0]
    assert request["json"][0]["collections"] == ["OUT"]
    assert request["json"][0]["DOI"] == "10.1234/new.1"
    assert request["headers"]["Zotero-API-Key"] == "secret-key"
    assert "secret-key" not in str(request["json"])


class CollectionWithGroupDuplicateSession(ZoteroWriteSession):
    def get(self, url, **kwargs):
        if url.endswith("/groups/42/items/top"):
            assert kwargs["params"]["q"] == "10.1234/zotero.1"
            return FakeResponse([zotero_item()])
        return super().get(url, **kwargs)


def test_collection_writeback_checks_for_duplicates_across_the_group():
    session = CollectionWithGroupDuplicateSession()
    client = ZoteroClient(
        session,
        group_id="42",
        collection_key="COLL",
        api_key="secret-key",
    )
    client.fetch_items()

    key, created = client.save_item(
        PaperRecord(
            identifier="10.1234/zotero.1",
            doi="10.1234/zotero.1",
            title="Already present outside the collection",
        )
    )

    assert (key, created) == ("PARENT01", False)
    assert session.posts == []


def test_writeback_reuses_an_existing_doi_without_posting():
    session = ZoteroReadSession()
    client = ZoteroClient(session, group_id="42", api_key="secret-key")
    client.fetch_items()

    key, created = client.save_item(
        PaperRecord(
            identifier="10.1234/zotero.1",
            doi="10.1234/zotero.1",
            title="Already present",
        )
    )

    assert (key, created) == ("PARENT01", False)


class StatusSession:
    def __init__(self):
        self.patch_request = None

    def get(self, url, **kwargs):
        del kwargs
        assert url.endswith("/groups/42/items/PARENT01")
        return FakeResponse(
            {
                "key": "PARENT01",
                "version": 7,
                "data": {
                    "version": 7,
                    "tags": [
                        {"tag": "journal-club-favourite"},
                        {"tag": "perla:status:error"},
                        {"tag": "perla:extract"},
                    ],
                },
            }
        )

    def patch(self, url, **kwargs):
        self.patch_request = (url, kwargs)
        return FakeResponse(status_code=204)


def test_status_sync_replaces_only_managed_tags():
    session = StatusSession()
    client = ZoteroClient(session, group_id="42", api_key="secret-key")
    record = PaperRecord(
        identifier="10.1234/zotero.1",
        doi="10.1234/zotero.1",
        title="Paper",
        sources=["rss:https://example.test", "zotero:groups/42"],
        zotero_item_key="PARENT01",
        zotero_curated=True,
        status="downloaded",
        downloaded_file="paper.pdf",
    )

    assert client.sync_status(record) is True

    _, request = session.patch_request
    tags = [tag["tag"] for tag in request["json"]["tags"]]
    assert "journal-club-favourite" in tags
    assert "perla:extract" in tags
    assert "perla:status:error" not in tags
    assert "perla:status:downloaded" in tags
    assert "perla:curated" in tags
    assert request["headers"]["If-Unmodified-Since-Version"] == "7"
    assert set(request["json"]) == {"tags"}


class IrrelevantMirrorSession:
    def __init__(self):
        self.tags = None

    def get(self, url, **kwargs):
        if url.endswith("/groups/42/items/top"):
            return FakeResponse(
                [
                    {
                        "key": "OFFTOPIC",
                        "data": {
                            "key": "OFFTOPIC",
                            "itemType": "journalArticle",
                            "title": "An unrelated catalyst",
                            "abstractNote": "",
                            "DOI": "10.1234/offtopic.1",
                            "url": "https://doi.org/10.1234/offtopic.1",
                            "date": "2025-03-04",
                        },
                    }
                ],
                headers={"Total-Results": "1"},
            )
        if url.endswith("/groups/42/items/OFFTOPIC"):
            return FakeResponse(
                {
                    "key": "OFFTOPIC",
                    "version": 2,
                    "data": {"version": 2, "tags": [{"tag": "journal-club"}]},
                }
            )
        raise AssertionError((url, kwargs))

    def patch(self, url, **kwargs):
        assert url.endswith("/groups/42/items/OFFTOPIC")
        self.tags = kwargs["json"]["tags"]
        return FakeResponse(status_code=204)


def test_writeback_mirrors_rejected_discoveries_for_debugging(tmp_path: Path):
    session = IrrelevantMirrorSession()

    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        rss_enabled=False,
        openalex_enabled=False,
        zotero_group_id="42",
        zotero_api_key="secret-key",
        zotero_save=True,
        session=session,
    )

    assert result.outcome_counts == {"irrelevant": 1}
    assert result.zotero.items_existing == 1
    assert result.zotero.items_updated == 1
    assert {tag["tag"] for tag in session.tags} >= {
        "journal-club",
        "perla:status:irrelevant",
    }


class UploadSession:
    def __init__(self, *, group_type="Private"):
        self.group_type = group_type
        self.posts = []
        self.storage_request = None
        self.uploaded = False

    def get(self, url, **kwargs):
        if url.endswith("/groups/42"):
            return FakeResponse(
                {"data": {"type": self.group_type, "fileEditing": "members"}}
            )
        if url.endswith("/items/PARENT01/children"):
            children = []
            if self.uploaded:
                children = [
                    {
                        "key": "ATTACH01",
                        "data": {
                            "key": "ATTACH01",
                            "itemType": "attachment",
                            "linkMode": "imported_file",
                            "contentType": "application/pdf",
                            "md5": "server-md5",
                        },
                    }
                ]
            return FakeResponse(children)
        if url.endswith("/items/new"):
            assert kwargs["params"] == {
                "itemType": "attachment",
                "linkMode": "imported_file",
            }
            return FakeResponse(
                {
                    "itemType": "attachment",
                    "linkMode": "imported_file",
                    "title": "",
                    "accessDate": "",
                    "url": "",
                    "note": "",
                    "tags": [],
                    "relations": {},
                    "contentType": "",
                    "charset": "",
                    "filename": "",
                    "md5": None,
                    "mtime": None,
                }
            )
        raise AssertionError(url)

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if url.endswith("/groups/42/items"):
            return FakeResponse({"successful": {"0": {"key": "ATTACH01"}}})
        if url.endswith("/items/ATTACH01/file") and "md5" in kwargs.get("data", {}):
            return FakeResponse(
                {
                    "url": "https://storage.example.test/upload",
                    "params": {"key": "stored-object", "policy": "signed"},
                    "uploadKey": "UPLOAD01",
                }
            )
        if url == "https://storage.example.test/upload":
            self.storage_request = kwargs
            return FakeResponse(status_code=201)
        if url.endswith("/items/ATTACH01/file") and "upload" in kwargs.get("data", {}):
            self.uploaded = True
            return FakeResponse(status_code=204)
        raise AssertionError(url)


class JournalClubRoundTripSession(UploadSession):
    """Emulate Zotero plus one open repository for the complete bot round trip."""

    def __init__(self):
        super().__init__()
        self.tags = None

    def get(self, url, **kwargs):
        if url.endswith("/groups/42/collections/INBOX/items/top"):
            return FakeResponse([zotero_item()], headers={"Total-Results": "1"})
        if url.startswith("https://api.openalex.org/works/"):
            return FakeResponse(
                {
                    "best_oa_location": {
                        "pdf_url": "https://repository.example.test/paper.pdf"
                    }
                }
            )
        if url == "https://repository.example.test/paper.pdf":
            return FakeResponse(content=b"%PDF-1.7\nopen repository copy")
        if url.endswith("/groups/42/items/PARENT01"):
            return FakeResponse(
                {
                    "key": "PARENT01",
                    "version": 8,
                    "data": {
                        "version": 8,
                        "tags": [{"tag": "journal-club-pick"}],
                    },
                }
            )
        return super().get(url, **kwargs)

    def patch(self, url, **kwargs):
        assert url.endswith("/groups/42/items/PARENT01")
        self.tags = kwargs["json"]["tags"]
        return FakeResponse(status_code=204)


def test_curated_item_round_trip_downloads_uploads_and_syncs_status(tmp_path: Path):
    session = JournalClubRoundTripSession()

    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        rss_enabled=False,
        openalex_enabled=False,
        zotero_group_id="42",
        zotero_collection_key="INBOX",
        zotero_api_key="secret-key",
        zotero_curated=True,
        zotero_save=True,
        zotero_pdf_policy="research-group",
        session=session,
    )

    assert result.status == "complete"
    assert result.outcome_counts == {"downloaded": 1}
    assert result.pdfs_downloaded == 1
    assert result.zotero is not None
    assert result.zotero.items_existing == 1
    assert result.zotero.items_updated == 1
    assert result.zotero.pdfs_uploaded == 1
    assert {tag["tag"] for tag in session.tags} >= {
        "journal-club-pick",
        "perla:curated",
        "perla:status:downloaded",
        "perla:pdf:attached",
        "perla:access:open-access",
    }
    record = load_state(tmp_path / "state/state.json").papers["10.1234/zotero.1"]
    assert record.zotero_attachment_key == "ATTACH01"
    assert record.pdf_sha256 == sha256(
        result.downloaded_files[0].read_bytes()
    ).hexdigest()


def test_pdf_upload_is_private_idempotent_and_records_provenance(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\nresearch copy")
    session = UploadSession()
    client = ZoteroClient(session, group_id="42", api_key="secret-key")
    record = PaperRecord(
        identifier="10.1234/zotero.1",
        doi="10.1234/zotero.1",
        title="Paper",
        zotero_item_key="PARENT01",
    )

    key, uploaded, digest = client.upload_pdf(
        record,
        pdf,
        access_basis="open-access",
        source_url="https://repository.example.test/paper.pdf",
    )
    second_key, second_upload, _ = client.upload_pdf(
        record,
        pdf,
        access_basis="open-access",
        source_url="https://repository.example.test/paper.pdf",
    )

    assert (key, uploaded) == ("ATTACH01", True)
    assert (second_key, second_upload) == ("ATTACH01", False)
    assert digest == sha256(pdf.read_bytes()).hexdigest()
    attachment_payload = session.posts[0][1]["json"][0]
    assert digest in attachment_payload["note"]
    assert "open-access" in attachment_payload["note"]
    assert session.storage_request["data"]["policy"] == "signed"
    assert "headers" not in session.storage_request


def test_research_pdf_upload_refuses_a_public_group(tmp_path: Path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.7\nresearch copy")
    client = ZoteroClient(
        UploadSession(group_type="PublicClosed"),
        group_id="42",
        api_key="secret-key",
    )

    with pytest.raises(ValueError, match="private Zotero group"):
        client.upload_pdf(
            PaperRecord(
                identifier="10.1234/zotero.1",
                doi="10.1234/zotero.1",
                title="Paper",
                zotero_item_key="PARENT01",
            ),
            pdf,
            access_basis="research-group",
            source_url=None,
        )


def test_cli_exposes_read_and_explicit_write_controls():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "--zotero-group-id" in result.output
    assert "--zotero-collection-key" in result.output
    assert "--zotero-output-collection-key" in result.output
    assert "--zotero-save / --no-zotero-save" in result.output
    assert "--zotero-curated / --no-zotero-curated" in result.output
    assert "--zotero-pdf-policy [never|research-group]" in result.output
