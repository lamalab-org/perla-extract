"""Incremental feed discovery and open-access PDF retrieval."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import unescape
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote

from loguru import logger

from .models import BotResult, BotState, PaperRecord, SelectionPolicy

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
TERMINAL_STATUSES = {"downloaded", "excluded", "irrelevant", "missing_doi"}


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


def _entry_record(entry: Mapping[str, Any], feed: str) -> PaperRecord:
    """Normalize the unstable field conventions used by RSS and Atom publishers."""

    title = _plain_text(str(entry.get("title", "")))
    summary = _plain_text(str(entry.get("summary", entry.get("description", ""))))
    link = str(entry.get("link", ""))
    doi = extract_doi(entry)
    identifier = str(entry.get("id") or entry.get("guid") or doi or link or title)
    return PaperRecord(
        identifier=identifier,
        source_feed=feed,
        title=title,
        summary=summary,
        link=link,
        doi=doi,
    )


def load_state(path: Path) -> BotState:
    """Load state if present; an absent file represents a first run."""

    if not path.exists():
        return BotState()
    return BotState.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(path: Path, state: BotState) -> None:
    """Replace state atomically so interruption cannot leave truncated JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _request_json(session: Any, url: str, timeout: float, **kwargs: Any) -> dict[str, Any]:
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


def _open_pdf_url(
    session: Any, doi: str, timeout: float, unpaywall_email: str | None
) -> str | None:
    """Resolve an open PDF using public APIs without scraping publisher pages."""

    if unpaywall_email:
        try:
            payload = _request_json(
                session,
                f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                timeout,
                params={"email": unpaywall_email},
            )
            location = payload.get("best_oa_location") or {}
            if isinstance(location, dict) and location.get("url_for_pdf"):
                return str(location["url_for_pdf"])
        except Exception as exc:
            logger.debug("Unpaywall lookup failed for {}: {}", doi, exc)

    try:
        payload = _request_json(
            session,
            f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}",
            timeout,
        )
        locations = [payload.get("best_oa_location"), payload.get("primary_location")]
        for location in locations:
            if not isinstance(location, dict):
                continue
            url = location.get("pdf_url")
            if url:
                return str(url)
    except Exception as exc:
        logger.debug("OpenAlex lookup failed for {}: {}", doi, exc)
    return None


def _download_pdf(session: Any, url: str, destination: Path, timeout: float) -> None:
    """Stream a candidate PDF and reject HTML error pages saved with a PDF name."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(url, stream=True, timeout=timeout) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)
        with temporary.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError(f"Downloaded content is not a PDF: {url}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


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
    max_attempts: int = 4,
    request_timeout: float = 30.0,
    session: Any | None = None,
    feedparser_module: Any | None = None,
) -> BotResult:
    """Discover, select, and download papers while recording inspectable state.

    Feed parsing and HTTP clients are injectable to keep the orchestration testable.
    Runtime imports allow the extraction package to remain usable when the optional
    ``papersbot`` dependencies are not installed.
    """

    if session is None or feedparser_module is None:
        try:
            import feedparser as installed_feedparser
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "PapersBot dependencies are missing; install perla-extract[papersbot]"
            ) from exc
        session = session or requests.Session()
        feedparser_module = feedparser_module or installed_feedparser

    output_path = Path(download_dir)
    state_path = Path(state_dir) / "state.json"
    state = load_state(state_path)
    policy = load_policy(
        Path(selection_file) if selection_file else default_selection_path()
    )
    feed_urls = list(feeds) if feeds else load_feeds(
        Path(feeds_file) if feeds_file else default_feeds_path()
    )
    if not feed_urls:
        raise ValueError("No feed URLs were configured")

    result = BotResult()
    for feed_number, feed_url in enumerate(feed_urls, start=1):
        logger.info("Checking feed {}/{}: {}", feed_number, len(feed_urls), feed_url)
        result.feeds_checked += 1
        try:
            entries = _feed_entries(
                session, feedparser_module, feed_url, request_timeout
            )
        except Exception as exc:
            result.feed_errors += 1
            logger.warning("Could not read feed {}: {}", feed_url, exc)
            continue

        for entry in entries:
            result.entries_seen += 1
            record = _entry_record(entry, feed_url)
            previous = state.papers.get(record.identifier)
            if previous and (
                previous.status in TERMINAL_STATUSES
                or previous.attempts >= max_attempts
            ):
                continue
            if previous:
                record.attempts = previous.attempts
            if policy.excludes(record.title):
                record.status = "excluded"
                state.papers[record.identifier] = record
                save_state(state_path, state)
                continue

            feed_text = f"{record.title} {record.summary}"
            if not policy.is_candidate(feed_text):
                record.status = "irrelevant"
                state.papers[record.identifier] = record
                save_state(state_path, state)
                continue
            if not record.doi:
                record.status = "missing_doi"
                state.papers[record.identifier] = record
                save_state(state_path, state)
                continue

            result.candidates_processed += 1
            record.attempts += 1
            logger.info("Processing candidate {}", record.doi)
            try:
                combined_text = feed_text
                if not policy.accepts(combined_text):
                    combined_text += " " + _crossref_text(
                        session, record.doi, request_timeout
                    )
                if not policy.accepts(combined_text):
                    record.status = "irrelevant"
                else:
                    result.relevant_papers += 1
                    record.pdf_url = _open_pdf_url(
                        session, record.doi, request_timeout, unpaywall_email
                    )
                    if not record.pdf_url:
                        record.status = "no_pdf"
                    else:
                        destination = output_path / _safe_pdf_name(record.doi)
                        if not destination.exists():
                            _download_pdf(
                                session, record.pdf_url, destination, request_timeout
                            )
                            result.pdfs_downloaded += 1
                        record.downloaded_file = str(destination.resolve())
                        record.status = "downloaded"
                        result.downloaded_files.append(destination.resolve())
                record.error = None
            except Exception as exc:
                record.status = "error"
                record.error = str(exc)
                logger.warning("Candidate {} failed: {}", record.doi, exc)
            record.updated_at = datetime.now(UTC).isoformat(timespec="seconds")
            state.papers[record.identifier] = record
            save_state(state_path, state)

    return result
