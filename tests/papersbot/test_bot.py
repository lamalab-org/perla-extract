from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from perla_extract.papersbot.bot import extract_doi, load_state, run_papersbot
from perla_extract.papersbot.models import SelectionPolicy


class FakeResponse:
    def __init__(self, *, payload=None, content: bytes = b""):
        self.payload = payload
        self.content = content

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


class FakeSession:
    def get(self, url, **kwargs):
        del kwargs
        if url == "https://example.test/feed":
            return FakeResponse(content=b"feed")
        if "crossref" in url:
            return FakeResponse(payload={"message": {"title": ["Solar cell"]}})
        if "openalex" in url:
            return FakeResponse(
                payload={"best_oa_location": {"pdf_url": "https://example.test/a.pdf"}}
            )
        if url == "https://example.test/a.pdf":
            return FakeResponse(content=b"%PDF-1.7\ncontent")
        raise AssertionError(url)


class FakeFeedParser:
    @staticmethod
    def parse(url):
        del url
        return SimpleNamespace(
            bozo=False,
            entries=[
                {
                    "id": "paper-1",
                    "title": "A stable perovskite absorber",
                    "summary": "DOI: 10.1234/example.1",
                }
            ],
        )


def test_selection_policy_is_grouped_and_title_exclusions_are_local():
    policy = SelectionPolicy(
        required_groups=[["perovskite"], ["solar cell", "photovoltaic"]],
        excluded_title_terms=["review"],
    )

    assert policy.accepts("A perovskite photovoltaic device")
    assert not policy.accepts("A perovskite LED")
    assert not policy.accepts("A space-efficient perovskite LED")
    assert policy.excludes("A review of perovskites")
    assert not policy.excludes("A preview of perovskite devices")


def test_extract_doi_uses_standard_pattern_across_feed_fields():
    assert extract_doi({"link": "https://doi.org/10.1000/ABC.123"}) == "10.1000/abc.123"
    assert extract_doi({"summary": "No identifier"}) is None


def test_run_is_incremental_and_writes_readable_state(tmp_path: Path):
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "required_groups": [["perovskite"], ["solar cell"]],
                "excluded_title_terms": [],
            }
        ),
        encoding="utf-8",
    )
    options = {
        "download_dir": tmp_path / "papers",
        "state_dir": tmp_path / "state",
        "feeds": ["https://example.test/feed"],
        "selection_file": selection,
        "session": FakeSession(),
        "feedparser_module": FakeFeedParser(),
    }

    first = run_papersbot(**options)
    second = run_papersbot(**options)

    assert first.pdfs_downloaded == 1
    assert first.downloaded_files[0].read_bytes().startswith(b"%PDF-")
    assert second.candidates_processed == 0
    state = load_state(tmp_path / "state" / "state.json")
    assert state.papers["paper-1"].status == "downloaded"
    assert state.papers["paper-1"].doi == "10.1234/example.1"
