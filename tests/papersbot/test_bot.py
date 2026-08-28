from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from perla_extract.papersbot.acquisition import AcquiredPdf
from perla_extract.papersbot.bot import (
    _default_http_session,
    extract_doi,
    load_state,
    run_papersbot,
)
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
            return FakeResponse(
                payload={"message": {"title": ["Perovskite solar cell"]}}
            )
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
    assert policy.is_candidate("A perovskite absorber")
    assert policy.is_candidate("A photovoltaic device")
    assert not policy.is_candidate("An unrelated catalyst")


def test_extract_doi_uses_standard_pattern_across_feed_fields():
    assert extract_doi({"link": "https://doi.org/10.1000/ABC.123"}) == "10.1000/abc.123"
    assert extract_doi({"summary": "No identifier"}) is None


def test_default_http_session_retries_only_safe_transient_failures():
    session = _default_http_session(2)
    retries = session.get_adapter("https://").max_retries

    assert retries.total == 2
    assert retries.allowed_methods == frozenset({"GET"})
    assert 429 in retries.status_forcelist
    assert retries.respect_retry_after_header is True


def test_load_state_migrates_the_feed_only_source_field(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "papers": {
                    "old-id": {
                        "identifier": "old-id",
                        "source_feed": "https://example.test/feed",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    state = load_state(path)

    assert state.format_version == 5
    assert state.papers["old-id"].sources == ["https://example.test/feed"]


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
    assert first.status == "complete"
    assert first.outcome_counts == {"downloaded": 1}
    assert second.skip_counts == {"terminal:downloaded": 1}
    assert second.outcomes[0].status == "downloaded"
    assert second.outcomes[0].disposition == "skipped_terminal"
    state = load_state(tmp_path / "state" / "state.json")
    assert state.papers["10.1234/example.1"].status == "downloaded"
    assert state.papers["10.1234/example.1"].doi == "10.1234/example.1"
    run_files = sorted((tmp_path / "state" / "runs").glob("*.json"))
    assert len(run_files) == 2
    last_run = json.loads((tmp_path / "state" / "last_run.json").read_text())
    assert last_run["run_id"] == second.run_id
    assert last_run["finished_at"] is not None
    assert last_run["configuration"]["selection_sha256"]


def test_sparse_feed_entry_uses_metadata_before_irrelevant_decision(tmp_path: Path):
    """Do not make the order of policy groups a hidden source of false negatives."""

    class SparseFeedParser:
        @staticmethod
        def parse(url):
            del url
            return SimpleNamespace(
                bozo=False,
                entries=[
                    {
                        "id": "paper-2",
                        "title": "A photovoltaic device",
                        "summary": "DOI: 10.1234/example.2",
                    }
                ],
            )

    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "required_groups": [["perovskite"], ["photovoltaic"]],
                "excluded_title_terms": [],
            }
        ),
        encoding="utf-8",
    )

    result = run_papersbot(
        download_dir=tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        selection_file=selection,
        session=FakeSession(),
        feedparser_module=SparseFeedParser(),
    )

    assert result.pdfs_downloaded == 1
    assert (
        load_state(tmp_path / "state/state.json").papers["10.1234/example.2"].status
        == "downloaded"
    )


def test_custom_pdf_source_is_independent_of_doi_and_records_access_basis(
    tmp_path: Path,
):
    class DoiFreeFeedParser:
        @staticmethod
        def parse(url):
            del url
            return SimpleNamespace(
                bozo=False,
                entries=[
                    {
                        "id": "licensed-paper",
                        "title": "A perovskite solar cell",
                    }
                ],
            )

    class InstitutionalSource:
        name = "institutional-library"

        @staticmethod
        def acquire(record, destination):
            assert record.identifier == "licensed-paper"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"%PDF-1.7\nlicensed copy")
            return AcquiredPdf(
                url="https://library.example.test/licensed-paper.pdf",
                access_basis="institutional-license",
                downloaded_now=True,
            )

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

    result = run_papersbot(
        download_dir=tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        selection_file=selection,
        session=FakeSession(),
        feedparser_module=DoiFreeFeedParser(),
        pdf_sources=[InstitutionalSource()],
    )

    record = load_state(tmp_path / "state/state.json").papers["licensed-paper"]
    assert result.status == "complete"
    assert result.configuration.pdf_sources == ["institutional-library"]
    assert record.status == "downloaded"
    assert record.pdf_source == "institutional-library"
    assert record.pdf_access_basis == "institutional-license"


def test_pdf_source_failure_is_recorded_before_fallback_succeeds(tmp_path: Path):
    class BrokenSource:
        name = "broken"

        @staticmethod
        def acquire(record, destination):
            del record
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"partial download")
            raise OSError("resolver unavailable")

    class FallbackSource:
        name = "fallback"

        @staticmethod
        def acquire(record, destination):
            del record
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b"%PDF-1.7\nfallback")
            return AcquiredPdf(
                url="https://repository.example.test/fallback.pdf",
                access_basis="repository",
                downloaded_now=True,
            )

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

    result = run_papersbot(
        download_dir=tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        selection_file=selection,
        session=FakeSession(),
        feedparser_module=FakeFeedParser(),
        pdf_sources=[BrokenSource(), FallbackSource()],
    )

    assert result.status == "complete_with_errors"
    assert result.outcome_counts == {"downloaded": 1}
    assert result.acquisition_failures[0].source == "broken"
    assert result.acquisition_failures[0].error == "resolver unavailable"


def test_retryable_state_is_replayed_after_feed_entry_disappears(tmp_path: Path):
    class EmptyFeedParser:
        @staticmethod
        def parse(content):
            del content
            return SimpleNamespace(bozo=False, entries=[])

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
    state_path = tmp_path / "state/state.json"
    from perla_extract.papersbot.bot import save_state
    from perla_extract.papersbot.models import BotState, PaperRecord

    save_state(
        state_path,
        BotState(
            papers={
                "10.1234/example.1": PaperRecord(
                    identifier="10.1234/example.1",
                    sources=["rss:https://example.test/feed"],
                    doi="10.1234/example.1",
                    title="A perovskite solar cell",
                    status="no_pdf",
                    attempts=1,
                )
            }
        ),
    )

    result = run_papersbot(
        download_dir=tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        selection_file=selection,
        openalex_enabled=False,
        session=FakeSession(),
        feedparser_module=EmptyFeedParser(),
    )

    assert result.state_retries_scheduled == 1
    assert result.retry_counts == {"no_pdf": 1}
    assert result.outcome_counts == {"downloaded": 1}


def test_missing_terminal_pdf_is_reacquired(tmp_path: Path):
    class EmptyFeedParser:
        @staticmethod
        def parse(content):
            del content
            return SimpleNamespace(bozo=False, entries=[])

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
        "openalex_enabled": False,
        "session": FakeSession(),
        "feedparser_module": FakeFeedParser(),
    }

    first = run_papersbot(**options)
    first.downloaded_files[0].unlink()
    options["feedparser_module"] = EmptyFeedParser()
    second = run_papersbot(**options)

    assert second.state_retries_scheduled == 1
    assert second.outcome_counts == {"downloaded": 1}
    assert second.pdfs_downloaded == 1
    assert second.downloaded_files[0].is_file()


def test_run_report_attributes_retries_to_the_previous_status(tmp_path: Path):
    class NoPdfSession(FakeSession):
        def get(self, url, **kwargs):
            if "openalex" in url:
                return FakeResponse(payload={"best_oa_location": None})
            return super().get(url, **kwargs)

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
        "feedparser_module": FakeFeedParser(),
    }

    first = run_papersbot(**options, session=NoPdfSession())
    second = run_papersbot(**options, session=FakeSession())

    assert first.outcome_counts == {"no_pdf": 1}
    assert second.retries_attempted == 1
    assert second.retry_counts == {"no_pdf": 1}
    assert second.outcomes[0].attempt == 2


def test_feed_failures_are_preserved_in_the_run_ledger(tmp_path: Path):
    class BrokenSession:
        def get(self, url, **kwargs):
            del url, kwargs
            raise OSError("feed unavailable")

    result = run_papersbot(
        download_dir=tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        openalex_enabled=False,
        session=BrokenSession(),
        feedparser_module=FakeFeedParser(),
    )

    assert result.status == "complete_with_errors"
    assert result.discovery_errors == 1
    assert result.discovery_failures[0].error == "feed unavailable"
    saved = json.loads((tmp_path / "state" / "last_run.json").read_text())
    assert saved["status"] == "complete_with_errors"
    assert saved["discovery_failures"][0]["source"] == "https://example.test/feed"
