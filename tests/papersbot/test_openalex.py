from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from perla_extract.papersbot.bot import load_state, run_papersbot, save_state
from perla_extract.papersbot.models import BotState, PaperRecord
from perla_extract.papersbot.openalex import fetch_topic_works, reconstruct_abstract


class Response:
    def __init__(self, payload=None, content: bytes = b""):
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


def _work(doi: str = "10.1234/shared") -> dict:
    return {
        "id": "https://openalex.org/W1",
        "doi": f"https://doi.org/{doi}",
        "display_name": "A perovskite solar cell",
        "publication_date": "2026-08-17",
        "abstract_inverted_index": {"Stable": [2], "A": [0], "device": [1]},
        "topics": [{"id": "https://openalex.org/T10247"}],
        "best_oa_location": {"pdf_url": "https://example.test/paper.pdf"},
        "primary_location": {"landing_page_url": "https://example.test/article"},
    }


def _policy(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "required_groups": [["perovskite"], ["solar cell"]],
                "excluded_title_terms": ["review"],
                "openalex": {
                    "topic_ids": ["T10247"],
                    "initial_lookback_days": 30,
                    "overlap_days": 7,
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_reconstruct_abstract_uses_positions_not_mapping_order():
    assert reconstruct_abstract({"last": [2], "first": [0], "middle": [1]}) == (
        "first middle last"
    )
    assert reconstruct_abstract(None) == ""


def test_fetch_topic_works_follows_cursor_pages_and_records_api_cost():
    class Session:
        def __init__(self):
            self.cursors = []

        def get(self, url, **kwargs):
            assert url == "https://api.openalex.org/works"
            assert kwargs["params"]["per_page"] == 100
            assert "per-page" not in kwargs["params"]
            assert kwargs["headers"] == {"Authorization": "Bearer openalex-key"}
            cursor = kwargs["params"]["cursor"]
            self.cursors.append(cursor)
            if cursor == "*":
                return Response(
                    {
                        "results": [_work()],
                        "meta": {"count": 2, "next_cursor": "c2", "cost_usd": 0.001},
                    }
                )
            return Response(
                {
                    "results": [_work("10.1234/second")],
                    "meta": {"count": 2, "next_cursor": None, "cost_usd": 0.002},
                }
            )

    session = Session()
    entries, stats = fetch_topic_works(
        session,
        topic_ids=["T10247"],
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 18),
        timeout=10,
        email="contact@example.org",
        api_key="openalex-key",
    )

    assert session.cursors == ["*", "c2"]
    assert [entry["doi"] for entry in entries] == [
        "https://doi.org/10.1234/shared",
        "https://doi.org/10.1234/second",
    ]
    assert entries[0]["summary"] == "A device Stable"
    assert stats.pages == 2
    assert stats.works_seen == 2
    assert stats.reported_cost_usd == 0.003


def test_rss_and_openalex_share_doi_identity_and_processing(tmp_path: Path):
    class Session:
        def get(self, url, **kwargs):
            if url == "https://example.test/feed":
                return Response(content=b"feed")
            if url == "https://api.openalex.org/works":
                return Response(
                    {
                        "results": [_work()],
                        "meta": {"count": 1, "next_cursor": None, "cost_usd": 0.001},
                    }
                )
            if url == "https://example.test/paper.pdf":
                return Response(content=b"%PDF-1.7\ncontent")
            raise AssertionError((url, kwargs))

    class Parser:
        @staticmethod
        def parse(content):
            del content
            return SimpleNamespace(
                bozo=False,
                entries=[
                    {
                        "id": "publisher-id",
                        "title": "A perovskite solar cell",
                        "summary": "10.1234/shared",
                    }
                ],
            )

    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        selection_file=_policy(tmp_path / "selection.json"),
        session=Session(),
        feedparser_module=Parser(),
        openalex_start_date=date(2026, 8, 1),
        openalex_end_date=date(2026, 8, 18),
    )

    state = load_state(tmp_path / "state/state.json")
    record = state.papers["10.1234/shared"]
    assert result.entries_seen == 2
    assert result.unique_papers_seen == 1
    assert result.candidates_processed == 1
    assert result.source_counts == {"rss": 1, "openalex": 1}
    assert record.sources == [
        "rss:https://example.test/feed",
        "openalex:topics/T10247",
    ]
    assert state.openalex_last_successful_date == date(2026, 8, 18)
    assert result.openalex is not None and result.openalex.checkpoint_advanced


def test_failed_openalex_page_does_not_advance_checkpoint(tmp_path: Path):
    state_path = tmp_path / "state/state.json"
    save_state(
        state_path,
        BotState(openalex_last_successful_date=date(2026, 8, 10)),
    )

    class BrokenSession:
        def get(self, url, **kwargs):
            del url, kwargs
            raise OSError("OpenAlex unavailable")

    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        rss_enabled=False,
        selection_file=_policy(tmp_path / "selection.json"),
        session=BrokenSession(),
        openalex_end_date=date(2026, 8, 18),
    )

    assert result.status == "complete_with_errors"
    assert result.discovery_failures[0].source_kind == "openalex"
    assert load_state(state_path).openalex_last_successful_date == date(2026, 8, 10)


def test_legacy_feed_identifier_is_migrated_to_doi(tmp_path: Path):
    state_path = tmp_path / "state/state.json"
    downloaded_file = tmp_path / "shared.pdf"
    downloaded_file.write_bytes(b"%PDF-1.7\nlegacy")
    save_state(
        state_path,
        BotState(
            papers={
                "publisher-id": PaperRecord(
                    identifier="publisher-id",
                    sources=["rss:old"],
                    doi="10.1234/shared",
                    status="downloaded",
                    attempts=1,
                    downloaded_file=str(downloaded_file),
                    pdf_sha256=hashlib.sha256(downloaded_file.read_bytes()).hexdigest(),
                )
            }
        ),
    )

    class Session:
        def get(self, url, **kwargs):
            if url == "https://example.test/feed":
                return Response(content=b"feed")
            raise AssertionError((url, kwargs))

    class Parser:
        @staticmethod
        def parse(content):
            del content
            return SimpleNamespace(
                bozo=False,
                entries=[
                    {
                        "id": "new-id",
                        "title": "A perovskite solar cell",
                        "summary": "10.1234/shared",
                    }
                ],
            )

    result = run_papersbot(
        tmp_path / "papers",
        state_dir=tmp_path / "state",
        feeds=["https://example.test/feed"],
        selection_file=_policy(tmp_path / "selection.json"),
        session=Session(),
        feedparser_module=Parser(),
        openalex_enabled=False,
    )

    state = load_state(state_path)
    assert list(state.papers) == ["10.1234/shared"]
    assert result.skip_counts == {"terminal:downloaded": 1}
