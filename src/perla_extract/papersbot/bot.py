"""Incremental multi-source paper discovery and PDF retrieval."""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from html import unescape
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote
from uuid import uuid4

from loguru import logger

from .acquisition import OpenAccessPdfSource, PdfSource, ZoteroPdfSource
from .models import (
    BotResult,
    BotRunConfiguration,
    BotState,
    DiscoveryFailure,
    PaperRecord,
    PaperRunOutcome,
    PdfAcquisitionFailure,
    SelectionPolicy,
    ZoteroRunStats,
)
from .openalex import fetch_topic_works
from .zotero import ZoteroClient

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
TERMINAL_STATUSES = {"downloaded", "excluded", "irrelevant"}
RETRYABLE_STATUSES = {"new", "error", "no_pdf", "missing_doi"}


def default_feeds_path() -> Path:
    """Return the maintained journal feed list shipped with the package."""

    return Path(str(files("perla_extract.papersbot").joinpath("feeds.txt")))


def default_selection_path() -> Path:
    """Return the replaceable relevance policy shipped with the package."""

    return Path(str(files("perla_extract.papersbot").joinpath("selection.json")))


def load_feeds(path: Path) -> list[str]:
    """Read a comment-friendly feed file while preserving its declared order."""

    return [
        line
        for raw_line in path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.partition("#")[0].strip())
    ]


def load_policy(path: Path) -> SelectionPolicy:
    """Load selection behavior from data rather than embedding domain rules in code."""

    return SelectionPolicy.model_validate_json(path.read_text(encoding="utf-8"))


def extract_doi(entry: Mapping[str, Any]) -> str | None:
    """Find a DOI in common feed fields, then fall back to all textual values."""

    preferred = (
        entry.get("prism_doi"),
        entry.get("dc_identifier"),
        entry.get("doi"),
        entry.get("link"),
        entry.get("id"),
        entry.get("summary"),
    )
    remaining = tuple(value for value in entry.values() if isinstance(value, str))
    for value in (*preferred, *remaining):
        if not isinstance(value, str):
            continue
        match = DOI_PATTERN.search(unescape(value))
        if match:
            return match.group(0).rstrip(".,;)").lower()
    return None


def _plain_text(value: str) -> str:
    """Remove feed markup so matching and saved state remain human-readable."""

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", unescape(value))).strip()


def _entry_record(
    entry: Mapping[str, Any], source: str, *, zotero_curated: bool = False
) -> PaperRecord:
    """Normalize metadata from a discovery source into one paper representation."""

    title = _plain_text(str(entry.get("title", "")))
    summary = _plain_text(str(entry.get("summary", entry.get("description", ""))))
    link = str(entry.get("link", ""))
    doi = extract_doi(entry)
    identifier = str(doi or entry.get("id") or entry.get("guid") or link or title)
    return PaperRecord(
        identifier=identifier,
        sources=[source],
        title=title,
        summary=summary,
        link=link,
        doi=doi,
        pdf_url=str(entry["pdf_url"]) if entry.get("pdf_url") else None,
        openalex_id=(str(entry["openalex_id"]) if entry.get("openalex_id") else None),
        topic_ids=list(entry.get("topic_ids") or []),
        publication_date=entry.get("publication_date"),
        zotero_item_key=(
            str(entry["zotero_item_key"]) if entry.get("zotero_item_key") else None
        ),
        zotero_curated=zotero_curated,
    )


def _merge_records(records: Iterable[PaperRecord]) -> list[PaperRecord]:
    """Collapse source overlap while retaining the richest metadata and provenance."""

    merged: dict[str, PaperRecord] = {}
    for record in records:
        current = merged.get(record.identifier)
        if current is None:
            merged[record.identifier] = record
            continue
        current.sources = list(dict.fromkeys([*current.sources, *record.sources]))
        current.topic_ids = list(dict.fromkeys([*current.topic_ids, *record.topic_ids]))
        if len(record.title) > len(current.title):
            current.title = record.title
        if len(record.summary) > len(current.summary):
            current.summary = record.summary
        current.link = current.link or record.link
        current.pdf_url = current.pdf_url or record.pdf_url
        current.openalex_id = current.openalex_id or record.openalex_id
        current.zotero_item_key = current.zotero_item_key or record.zotero_item_key
        current.zotero_attachment_key = (
            current.zotero_attachment_key or record.zotero_attachment_key
        )
        current.zotero_curated = current.zotero_curated or record.zotero_curated
        current.publication_date = current.publication_date or record.publication_date
    return list(merged.values())


def _merge_previous(state: BotState, record: PaperRecord) -> PaperRecord | None:
    """Find prior state by DOI and migrate older feed identifiers transparently."""

    previous_key = record.identifier if record.identifier in state.papers else None
    if previous_key is None and record.doi:
        previous_key = next(
            (key for key, item in state.papers.items() if item.doi == record.doi),
            None,
        )
    if previous_key is None:
        return None
    previous = state.papers[previous_key]
    record.sources = list(dict.fromkeys([*previous.sources, *record.sources]))
    record.topic_ids = list(dict.fromkeys([*previous.topic_ids, *record.topic_ids]))
    record.attempts = previous.attempts
    record.pdf_url = record.pdf_url or previous.pdf_url
    record.downloaded_file = previous.downloaded_file
    record.zotero_item_key = record.zotero_item_key or previous.zotero_item_key
    record.zotero_attachment_key = (
        record.zotero_attachment_key or previous.zotero_attachment_key
    )
    record.zotero_curated = record.zotero_curated or previous.zotero_curated
    record.pdf_sha256 = previous.pdf_sha256
    record.pdf_source = previous.pdf_source
    record.pdf_access_basis = previous.pdf_access_basis
    if previous_key != record.identifier:
        del state.papers[previous_key]
    return previous


def _pending_state_retries(
    state: BotState,
    discovered: Iterable[PaperRecord],
    *,
    max_attempts: int,
) -> list[PaperRecord]:
    """Replay unfinished transient-source records after discovery stops returning them.

    RSS entries eventually leave their feeds and OpenAlex advances its date checkpoint.
    Retrying only records seen again would therefore make ``max_attempts`` depend on an
    external source's retention window. Curated Zotero records are excluded because
    removing an item from the intake collection should also remove it from the queue.
    """

    discovered_ids = {record.identifier for record in discovered}
    discovered_dois = {record.doi for record in discovered if record.doi}
    pending: list[PaperRecord] = []
    for record in state.papers.values():
        already_discovered = record.identifier in discovered_ids or (
            record.doi is not None and record.doi in discovered_dois
        )
        if already_discovered or record.zotero_curated:
            continue
        stale_download = (
            record.status == "downloaded" and not _downloaded_file_is_current(record)
        )
        within_retry_budget = (
            record.status in RETRYABLE_STATUSES and record.attempts < max_attempts
        )
        if stale_download or within_retry_budget:
            pending.append(record.model_copy(deep=True))
    return pending


def _downloaded_file_is_current(record: PaperRecord) -> bool:
    """Treat a downloaded state as terminal only while its local bytes still match."""

    if not record.downloaded_file:
        return False
    path = Path(record.downloaded_file)
    if not path.is_file():
        return False
    try:
        digest = _validated_pdf_sha256(path)
    except (OSError, ValueError):
        return False
    return record.pdf_sha256 is None or digest == record.pdf_sha256


def _clear_stale_download(record: PaperRecord) -> None:
    """Remove a corrupt managed PDF and clear provenance before reacquisition."""

    if record.downloaded_file:
        path = Path(record.downloaded_file)
        if path.is_file():
            path.unlink()
    record.status = "new"
    record.downloaded_file = None
    record.pdf_sha256 = None
    record.pdf_source = None
    record.pdf_access_basis = None


def load_state(path: Path) -> BotState:
    """Load and migrate state if present; absence represents a first run."""

    if not path.exists():
        return BotState()
    state = BotState.model_validate_json(path.read_text(encoding="utf-8"))
    state.format_version = 4
    return state


def _write_model(path: Path, model: BotState | BotResult) -> None:
    """Replace a bot artifact only after its complete JSON has reached disk."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def save_state(path: Path, state: BotState) -> None:
    """Checkpoint the latest paper states so later runs can resume incrementally."""

    _write_model(path, state)


def _save_run(state_dir: Path, result: BotResult) -> None:
    """Checkpoint both an immutable run name and a convenient latest-run view."""

    _write_model(state_dir / "runs" / f"{result.run_id}.json", result)
    _write_model(state_dir / "last_run.json", result)


def _increment(counts: dict[str, int], key: str) -> None:
    """Increment a named statistic without requiring a fixed status vocabulary."""

    counts[key] = counts.get(key, 0) + 1


def _record_outcome(
    result: BotResult,
    record: PaperRecord,
    disposition: str,
) -> None:
    """Add one auditable entry outcome and update its corresponding aggregate."""

    if disposition == "evaluated":
        _increment(result.outcome_counts, record.status)
    else:
        reason = disposition.removeprefix("skipped_")
        _increment(result.skip_counts, f"{reason}:{record.status}")
    result.outcomes.append(
        PaperRunOutcome(
            identifier=record.identifier,
            doi=record.doi,
            status=record.status,
            disposition=disposition,
            attempt=record.attempts,
            error=record.error,
        )
    )
    logger.bind(
        event="papersbot.paper_outcome",
        identifier=record.identifier,
        doi=record.doi,
        status=record.status,
        disposition=disposition,
        attempt=record.attempts,
    ).debug(
        "Paper outcome: status={} disposition={} attempt={} identifier={}",
        record.status,
        disposition,
        record.attempts,
        record.identifier,
    )


def _finalize_record(
    state: BotState,
    state_path: Path,
    result: BotResult,
    record: PaperRecord,
    disposition: str,
) -> None:
    """Persist one completed decision to durable state and its run ledger."""

    record.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state.papers[record.identifier] = record
    save_state(state_path, state)
    _record_outcome(result, record, disposition)


def _acquire_pdf(
    sources: Iterable[PdfSource],
    result: BotResult,
    record: PaperRecord,
    destination: Path,
) -> bool:
    """Try configured sources in order and preserve every failed attempt."""

    for source in sources:
        source_name = source.name.strip()
        acquired = None
        existed_before = destination.exists()
        try:
            acquired = source.acquire(record, destination)
            if acquired is None:
                continue
            digest = _validated_pdf_sha256(destination)
        except Exception as exc:
            if acquired is not None or (not existed_before and destination.exists()):
                destination.unlink(missing_ok=True)
            result.acquisition_failures.append(
                PdfAcquisitionFailure(
                    source=source_name,
                    identifier=record.identifier,
                    doi=record.doi,
                    error=str(exc),
                )
            )
            if source_name == "zotero" and result.zotero is not None:
                result.zotero.errors += 1
            logger.bind(
                event="papersbot.pdf_source_failed",
                pdf_source=source_name,
                identifier=record.identifier,
                doi=record.doi,
            ).warning(
                "PDF source {} failed for {}: {}",
                source_name,
                record.doi or record.identifier,
                exc,
            )
            continue
        if acquired.downloaded_now:
            result.pdfs_downloaded += 1
            if source_name == "zotero" and result.zotero is not None:
                result.zotero.pdfs_downloaded += 1
        record.pdf_url = acquired.url
        record.pdf_source = source_name
        record.pdf_access_basis = acquired.access_basis
        record.pdf_sha256 = digest
        record.zotero_attachment_key = (
            acquired.attachment_key or record.zotero_attachment_key
        )
        record.downloaded_file = str(destination.resolve())
        record.status = "downloaded"
        result.downloaded_files.append(destination.resolve())
        return True
    return False


def _validated_pdf_sha256(path: Path) -> str:
    """Validate a source's file and fingerprint the exact bytes kept in state."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if handle.read(5) != b"%PDF-":
            raise ValueError(f"PDF source did not produce a valid PDF: {path}")
        handle.seek(0)
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_json(
    session: Any, url: str, timeout: float, **kwargs: Any
) -> dict[str, Any]:
    """Make metadata failures explicit and consistent across public services."""

    response = session.get(url, timeout=timeout, **kwargs)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected an object response from {url}")
    return payload


def _crossref_text(session: Any, doi: str, timeout: float) -> str:
    """Enrich sparse feed entries only when the cheap relevance gate passed."""

    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    work = _request_json(session, url, timeout).get("message", {})
    if not isinstance(work, dict):
        return ""
    title = " ".join(str(item) for item in work.get("title", []))
    abstract = str(work.get("abstract", ""))
    return _plain_text(f"{title} {abstract}")


def _safe_pdf_name(doi: str) -> str:
    """Map a DOI deterministically to a portable filename."""

    return re.sub(r"[^A-Za-z0-9._-]+", "--", doi).strip(".-") + ".pdf"


def _feed_entries(
    session: Any, feedparser_module: Any, feed_url: str, timeout: float
) -> Iterable[Mapping[str, Any]]:
    """Parse one feed and surface failures that otherwise look like empty feeds."""

    response = session.get(feed_url, timeout=timeout)
    response.raise_for_status()
    parsed = feedparser_module.parse(response.content)
    entries = getattr(parsed, "entries", [])
    if getattr(parsed, "bozo", False) and not entries:
        raise RuntimeError(str(getattr(parsed, "bozo_exception", "feed parse failed")))
    return entries


def run_papersbot(
    download_dir: str | Path = "downloaded_papers",
    *,
    state_dir: str | Path = ".papersbot-state",
    feeds: Iterable[str] | None = None,
    feeds_file: str | Path | None = None,
    selection_file: str | Path | None = None,
    unpaywall_email: str | None = None,
    pdf_sources: Iterable[PdfSource] | None = None,
    openalex_email: str | None = None,
    rss_enabled: bool = True,
    openalex_enabled: bool = True,
    openalex_start_date: date | None = None,
    openalex_end_date: date | None = None,
    zotero_group_id: str | None = None,
    zotero_api_key: str | None = None,
    zotero_collection_key: str | None = None,
    zotero_curated: bool = False,
    max_attempts: int = 4,
    request_timeout: float = 30.0,
    session: Any | None = None,
    feedparser_module: Any | None = None,
) -> BotResult:
    """Discover, select, and download papers while recording inspectable state.

    RSS provides low-latency journal updates, OpenAlex topics recover papers missed
    by feeds, and Zotero can supply a journal-club queue with stored PDFs. Every
    source becomes the same DOI-keyed record before processing. A curated Zotero
    collection changes only the relevance decision: human selection is accepted as
    approval instead of being second-guessed by the keyword policy. HTTP and feed
    clients remain injectable for deterministic tests. PDF sources are ordered and
    replaceable, so an authorized institutional retriever can be added without
    changing discovery or selection. Zotero access is read-only: configuring the
    group can never change its library.
    """

    if session is None:
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "PapersBot dependencies are missing; install perla-extract[papersbot]"
            ) from exc
        session = requests.Session()
    if rss_enabled and feedparser_module is None:
        try:
            import feedparser as installed_feedparser
        except ImportError as exc:
            raise RuntimeError(
                "RSS discovery requires the papersbot optional dependencies"
            ) from exc
        feedparser_module = installed_feedparser

    zotero_client = (
        ZoteroClient(
            session,
            group_id=zotero_group_id,
            api_key=zotero_api_key,
            collection_key=zotero_collection_key,
            timeout=request_timeout,
        )
        if zotero_group_id
        else None
    )
    if zotero_curated and not zotero_collection_key:
        raise ValueError("--zotero-curated requires --zotero-collection-key")

    configured_pdf_sources = (
        list(pdf_sources)
        if pdf_sources is not None
        else [
            *([ZoteroPdfSource(zotero_client)] if zotero_client else []),
            OpenAccessPdfSource(
                session,
                timeout=request_timeout,
                unpaywall_email=unpaywall_email,
            ),
        ]
    )
    pdf_source_names = [source.name.strip() for source in configured_pdf_sources]
    if any(not name for name in pdf_source_names):
        raise ValueError("Every PDF source must have a non-empty name")
    if len(pdf_source_names) != len(set(pdf_source_names)):
        raise ValueError("PDF source names must be unique within one run")

    output_path = Path(download_dir)
    state_directory = Path(state_dir)
    state_path = state_directory / "state.json"
    state = load_state(state_path)
    policy_path = Path(selection_file) if selection_file else default_selection_path()
    policy = load_policy(policy_path)
    feed_urls = []
    if rss_enabled:
        configured_feeds = (
            list(feeds)
            if feeds
            else load_feeds(Path(feeds_file) if feeds_file else default_feeds_path())
        )
        feed_urls = list(dict.fromkeys(configured_feeds))
        if not feed_urls:
            raise ValueError(
                "RSS discovery is enabled but no feed URLs were configured"
            )
    openalex_policy = policy.openalex if openalex_enabled else None
    if not feed_urls and openalex_policy is None and zotero_client is None:
        raise ValueError("Enable RSS, OpenAlex, or a Zotero group library")

    end_date = openalex_end_date or datetime.now(timezone.utc).date()
    start_date = openalex_start_date
    if openalex_policy and start_date is None:
        start_date = (
            state.openalex_last_successful_date
            - timedelta(days=openalex_policy.overlap_days)
            if state.openalex_last_successful_date
            else end_date - timedelta(days=openalex_policy.initial_lookback_days)
        )
    if start_date and start_date > end_date:
        raise ValueError("OpenAlex start date must not be after its end date")

    started = datetime.now(timezone.utc)
    run_id = f"{started.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex[:8]}"
    result = BotResult(
        run_id=run_id,
        started_at=started.isoformat(timespec="seconds"),
        configuration=BotRunConfiguration(
            feeds=feed_urls,
            selection_file=str(policy_path.resolve()),
            selection_sha256=hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            max_attempts=max_attempts,
            request_timeout=request_timeout,
            unpaywall_enabled=bool(unpaywall_email),
            pdf_sources=pdf_source_names,
            rss_enabled=rss_enabled,
            openalex_enabled=openalex_policy is not None,
            openalex_topic_ids=(openalex_policy.topic_ids if openalex_policy else []),
            openalex_start_date=start_date if openalex_policy else None,
            openalex_end_date=end_date if openalex_policy else None,
            zotero_enabled=zotero_client is not None,
            zotero_group_id=zotero_client.group_id if zotero_client else None,
            zotero_collection_key=(zotero_client.collection_key if zotero_client else None),
            zotero_curated=zotero_curated,
        ),
        zotero=(
            ZoteroRunStats(
                group_id=zotero_client.group_id,
                collection_key=zotero_client.collection_key,
            )
            if zotero_client
            else None
        ),
    )
    _save_run(state_directory, result)
    logger.bind(
        event="papersbot.run_started",
        run_id=run_id,
        feed_count=len(feed_urls),
        openalex_enabled=openalex_policy is not None,
        zotero_enabled=zotero_client is not None,
        pdf_sources=pdf_source_names,
    ).info(
        "PapersBot run {} started with {} feed(s); OpenAlex={}; Zotero={}; PDFs={}",
        run_id,
        len(feed_urls),
        "enabled" if openalex_policy else "disabled",
        "enabled" if zotero_client else "disabled",
        ",".join(pdf_source_names) or "disabled",
    )
    openalex_query_succeeded = False
    try:
        discovered: list[PaperRecord] = []
        for feed_number, feed_url in enumerate(feed_urls, start=1):
            logger.bind(
                event="papersbot.feed_started",
                run_id=run_id,
                feed_url=feed_url,
                feed_number=feed_number,
            ).info("Checking feed {}/{}: {}", feed_number, len(feed_urls), feed_url)
            result.feeds_checked += 1
            try:
                entries = _feed_entries(
                    session, feedparser_module, feed_url, request_timeout
                )
            except Exception as exc:
                result.discovery_errors += 1
                result.discovery_failures.append(
                    DiscoveryFailure(source_kind="rss", source=feed_url, error=str(exc))
                )
                logger.bind(
                    event="papersbot.feed_failed",
                    run_id=run_id,
                    feed_url=feed_url,
                ).warning("Could not read feed {}: {}", feed_url, exc)
                _save_run(state_directory, result)
                continue

            for entry in entries:
                result.entries_seen += 1
                _increment(result.source_counts, "rss")
                discovered.append(_entry_record(entry, f"rss:{feed_url}"))
            _save_run(state_directory, result)

        if openalex_policy and start_date is not None:
            logger.bind(
                event="papersbot.openalex_started",
                run_id=run_id,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                topic_ids=openalex_policy.topic_ids,
            ).info(
                "Querying OpenAlex topics from {} through {}",
                start_date,
                end_date,
            )
            try:
                entries, result.openalex = fetch_topic_works(
                    session,
                    topic_ids=openalex_policy.topic_ids,
                    start_date=start_date,
                    end_date=end_date,
                    timeout=request_timeout,
                    email=openalex_email,
                )
                source = f"openalex:topics/{'|'.join(openalex_policy.topic_ids)}"
                for entry in entries:
                    result.entries_seen += 1
                    _increment(result.source_counts, "openalex")
                    discovered.append(_entry_record(entry, source))
                openalex_query_succeeded = True
                logger.bind(
                    event="papersbot.openalex_finished",
                    run_id=run_id,
                    pages=result.openalex.pages,
                    works_seen=result.openalex.works_seen,
                    reported_cost_usd=result.openalex.reported_cost_usd,
                ).info(
                    "OpenAlex returned {} work(s) across {} page(s)",
                    result.openalex.works_seen,
                    result.openalex.pages,
                )
            except Exception as exc:
                result.discovery_errors += 1
                result.discovery_failures.append(
                    DiscoveryFailure(
                        source_kind="openalex",
                        source="|".join(openalex_policy.topic_ids),
                        error=str(exc),
                    )
                )
                logger.bind(event="papersbot.openalex_failed", run_id=run_id).warning(
                    "OpenAlex topic query failed: {}", exc
                )

        if zotero_client is not None:
            logger.bind(
                event="papersbot.zotero_started",
                run_id=run_id,
                group_id=zotero_client.group_id,
                collection_key=zotero_client.collection_key,
            ).info("Reading Zotero group {}", zotero_client.group_id)
            try:
                entries, zotero_stats = zotero_client.fetch_items()
                result.zotero = zotero_stats
                for entry in entries:
                    result.entries_seen += 1
                    _increment(result.source_counts, "zotero")
                    discovered.append(
                        _entry_record(
                            entry,
                            zotero_client.source,
                            zotero_curated=zotero_curated,
                        )
                    )
                logger.bind(
                    event="papersbot.zotero_finished",
                    run_id=run_id,
                    items_seen=zotero_stats.items_seen,
                    pages=zotero_stats.pages,
                ).info(
                    "Zotero returned {} bibliographic item(s) across {} page(s)",
                    len(entries),
                    zotero_stats.pages,
                )
            except Exception as exc:
                result.discovery_errors += 1
                if result.zotero is not None:
                    result.zotero.errors += 1
                result.discovery_failures.append(
                    DiscoveryFailure(
                        source_kind="zotero",
                        source=zotero_client.source,
                        error=str(exc),
                    )
                )
                logger.bind(event="papersbot.zotero_failed", run_id=run_id).warning(
                    "Zotero group discovery failed: {}", exc
                )

        unique_records = _merge_records(discovered)
        result.unique_papers_seen = len(unique_records)
        state_retries = _pending_state_retries(
            state,
            unique_records,
            max_attempts=max_attempts,
        )
        result.state_retries_scheduled = len(state_retries)
        logger.bind(
            event="papersbot.discovery_finished",
            run_id=run_id,
            entries_seen=result.entries_seen,
            unique_papers_seen=result.unique_papers_seen,
            source_counts=result.source_counts,
        ).info(
            "Discovery produced {} unique paper(s) from {} source record(s)",
            result.unique_papers_seen,
            result.entries_seen,
        )
        for record in [*unique_records, *state_retries]:
            previous = _merge_previous(state, record)
            newly_curated = bool(
                record.zotero_curated
                and previous is not None
                and not previous.zotero_curated
            )
            stale_download = bool(
                previous
                and previous.status == "downloaded"
                and not _downloaded_file_is_current(record)
            )
            if stale_download:
                logger.bind(
                    event="papersbot.download_reopened",
                    identifier=record.identifier,
                    downloaded_file=record.downloaded_file,
                ).warning(
                    "Reopening downloaded paper because its local PDF is missing or changed: {}",
                    record.identifier,
                )
                _clear_stale_download(record)
            if (
                previous
                and previous.status in TERMINAL_STATUSES
                and not newly_curated
                and not stale_download
            ):
                record.status = previous.status
                record.error = previous.error
                _finalize_record(
                    state,
                    state_path,
                    result,
                    record,
                    "skipped_terminal",
                )
                continue
            if (
                previous
                and previous.attempts >= max_attempts
                and not newly_curated
                and not stale_download
            ):
                record.status = previous.status
                record.error = previous.error
                _finalize_record(
                    state,
                    state_path,
                    result,
                    record,
                    "skipped_max_attempts",
                )
                continue
            if not record.zotero_curated and policy.excludes(record.title):
                record.status = "excluded"
                _finalize_record(
                    state,
                    state_path,
                    result,
                    record,
                    "evaluated",
                )
                continue

            discovered_text = f"{record.title} {record.summary}"
            if not record.zotero_curated and not policy.is_candidate(discovered_text):
                record.status = "irrelevant"
                _finalize_record(
                    state,
                    state_path,
                    result,
                    record,
                    "evaluated",
                )
                continue
            result.candidates_processed += 1
            if record.attempts:
                result.retries_attempted += 1
                _increment(
                    result.retry_counts,
                    previous.status if previous is not None else "unknown",
                )
            record.attempts += 1
            logger.bind(
                event="papersbot.candidate_started",
                run_id=run_id,
                doi=record.doi,
                attempt=record.attempts,
                sources=record.sources,
            ).info(
                "Processing candidate {} (attempt {})",
                record.doi or record.zotero_item_key,
                record.attempts,
            )
            try:
                combined_text = discovered_text
                if (
                    not record.zotero_curated
                    and not policy.accepts(combined_text)
                    and record.doi
                ):
                    combined_text += " " + _crossref_text(
                        session, record.doi, request_timeout
                    )
                if not record.zotero_curated and not policy.accepts(combined_text):
                    record.status = "irrelevant"
                else:
                    result.relevant_papers += 1
                    file_identity = (
                        record.doi
                        or (
                            f"zotero-{record.zotero_item_key}"
                            if record.zotero_item_key
                            else f"record-{record.identifier}"
                        )
                    )
                    destination = output_path / _safe_pdf_name(file_identity)
                    acquired = _acquire_pdf(
                        configured_pdf_sources, result, record, destination
                    )
                    if not acquired:
                        record.status = "no_pdf" if record.doi else "missing_doi"
                record.error = None
            except Exception as exc:
                record.status = "error"
                record.error = str(exc)
                logger.bind(
                    event="papersbot.candidate_failed",
                    run_id=run_id,
                    doi=record.doi,
                    attempt=record.attempts,
                ).warning("Candidate {} failed: {}", record.doi, exc)
            _finalize_record(
                state,
                state_path,
                result,
                record,
                "evaluated",
            )

        if openalex_query_succeeded and result.openalex is not None:
            state.openalex_last_successful_date = end_date
            result.openalex.checkpoint_advanced = True
            save_state(state_path, state)
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        raise
    else:
        result.status = (
            "complete_with_errors"
            if result.discovery_errors
            or result.outcome_counts.get("error", 0)
            or result.acquisition_failures
            or (result.zotero is not None and result.zotero.errors)
            else "complete"
        )
    finally:
        result.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _save_run(state_directory, result)
        logger.bind(
            event="papersbot.run_finished",
            run_id=run_id,
            status=result.status,
            outcome_counts=result.outcome_counts,
            skip_counts=result.skip_counts,
            retries_attempted=result.retries_attempted,
            retry_counts=result.retry_counts,
            discovery_errors=result.discovery_errors,
        ).info(
            "PapersBot run {} finished with status={} outcomes={} skips={} retries={} discovery_errors={}",
            run_id,
            result.status,
            result.outcome_counts,
            result.skip_counts,
            result.retries_attempted,
            result.discovery_errors,
        )

    return result
