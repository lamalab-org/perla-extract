"""Retrieve topic-selected works from OpenAlex as ordinary paper metadata."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from .models import OpenAlexRunStats

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
SELECTED_FIELDS = (
    "id,doi,display_name,publication_date,abstract_inverted_index,topics,"
    "best_oa_location,primary_location"
)


def reconstruct_abstract(index: object) -> str:
    """Turn OpenAlex's word-position index back into readable abstract text.

    OpenAlex omits the original abstract for licensing reasons and exposes word
    positions instead. Reconstructing it here lets every later selection step work
    with the same plain-text input regardless of discovery source.
    """

    if not isinstance(index, Mapping):
        return ""
    positioned: dict[int, str] = {}
    for word, positions in index.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and position >= 0:
                positioned.setdefault(position, word)
    return " ".join(positioned[position] for position in sorted(positioned))


def _location_url(work: Mapping[str, Any], field: str, key: str) -> str:
    """Read one URL from an OpenAlex location without assuming it is present."""

    location = work.get(field)
    if not isinstance(location, Mapping):
        return ""
    return str(location.get(key) or "")


def _entry(work: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one OpenAlex work to the source-neutral fields used by PapersBot."""

    topics = work.get("topics")
    topic_ids = []
    if isinstance(topics, list):
        topic_ids = [
            str(topic["id"]).rsplit("/", 1)[-1]
            for topic in topics
            if isinstance(topic, Mapping) and topic.get("id")
        ]
    return {
        "id": work.get("id"),
        "doi": work.get("doi"),
        "title": work.get("display_name") or "",
        "summary": reconstruct_abstract(work.get("abstract_inverted_index")),
        "link": _location_url(work, "primary_location", "landing_page_url")
        or str(work.get("doi") or ""),
        "pdf_url": _location_url(work, "best_oa_location", "pdf_url") or None,
        "publication_date": work.get("publication_date"),
        "topic_ids": topic_ids,
        "openalex_id": work.get("id"),
    }


def fetch_topic_works(
    session: Any,
    *,
    topic_ids: list[str],
    start_date: date,
    end_date: date,
    timeout: float,
    email: str | None = None,
    api_key: str | None = None,
    per_page: int = 100,
) -> tuple[list[dict[str, Any]], OpenAlexRunStats]:
    """Fetch every cursor page for a topic/date query and report its coverage.

    The function returns data only after the complete cursor traversal succeeds.
    Callers can therefore advance their checkpoint atomically and keep the previous
    one when a later page fails.
    """

    filters = ",".join(
        (
            f"topics.id:{'|'.join(topic_ids)}",
            "type:article",
            f"from_publication_date:{start_date.isoformat()}",
            f"to_publication_date:{end_date.isoformat()}",
        )
    )
    cursor: str | None = "*"
    entries: list[dict[str, Any]] = []
    stats = OpenAlexRunStats(
        start_date=start_date,
        end_date=end_date,
        topic_ids=topic_ids,
    )
    while cursor:
        params: dict[str, object] = {
            "filter": filters,
            "select": SELECTED_FIELDS,
            "per_page": per_page,
            "cursor": cursor,
        }
        if email:
            params["mailto"] = email
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        response = session.get(
            OPENALEX_WORKS_URL,
            params=params,
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, Mapping):
            raise ValueError("OpenAlex returned a non-object response")
        results = payload.get("results")
        meta = payload.get("meta")
        if not isinstance(results, list) or not isinstance(meta, Mapping):
            raise ValueError(
                "OpenAlex response is missing results or pagination metadata"
            )
        entries.extend(_entry(work) for work in results if isinstance(work, Mapping))
        stats.pages += 1
        stats.works_seen += len(results)
        if isinstance(meta.get("count"), int):
            stats.reported_results = meta["count"]
        if isinstance(meta.get("cost_usd"), (int, float)):
            stats.reported_cost_usd += float(meta["cost_usd"])
        next_cursor = meta.get("next_cursor")
        cursor = str(next_cursor) if next_cursor else None
    return entries, stats
