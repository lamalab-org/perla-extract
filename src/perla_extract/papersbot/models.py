"""Persistent data and selection policy for PapersBot."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class OpenAlexPolicy(BaseModel):
    """Configure topic discovery and its incremental overlap.

    Topic IDs are kept in the policy file because they describe the literature
    domain, not the retrieval algorithm. Re-reading several days on each run makes
    delayed indexing harmless; DOI deduplication keeps that overlap inexpensive.
    """

    topic_ids: list[str]
    initial_lookback_days: int = Field(default=30, ge=1)
    overlap_days: int = Field(default=7, ge=0)

    @field_validator("topic_ids")
    @classmethod
    def require_topics(cls, topics: list[str]) -> list[str]:
        """Reject an enabled discovery block that cannot produce a query."""

        cleaned = list(
            dict.fromkeys(topic.strip() for topic in topics if topic.strip())
        )
        if not cleaned:
            raise ValueError("topic_ids must contain at least one OpenAlex topic")
        return cleaned


class SelectionPolicy(BaseModel):
    """Describe relevance as data so the bot is not tied to one coded heuristic.

    A paper must contain at least one term from every required group. Title-only
    exclusions remove publication types that should not enter device extraction.
    Projects can replace the packaged policy from the command line.
    """

    required_groups: list[list[str]]
    excluded_title_terms: list[str] = Field(default_factory=list)
    openalex: OpenAlexPolicy | None = None

    @staticmethod
    def _contains(text: str, term: str) -> bool:
        """Match configured terms at lexical boundaries to avoid substrings."""

        pattern = rf"(?<!\w){re.escape(term)}(?!\w)"
        return re.search(pattern, text, re.IGNORECASE) is not None

    @field_validator("required_groups")
    @classmethod
    def require_nonempty_groups(cls, groups: list[list[str]]) -> list[list[str]]:
        """Reject policies that would silently accept every paper."""

        if not groups or any(not group for group in groups):
            raise ValueError("required_groups must contain only non-empty groups")
        return groups

    def excludes(self, title: str) -> bool:
        """Apply publication-type exclusions only to the title.

        Applying words such as ``review`` to an abstract would reject research
        articles merely because they discuss prior work.
        """

        return any(self._contains(title, term) for term in self.excluded_title_terms)

    def is_candidate(self, text: str) -> bool:
        """Use any configured relevance signal as a cheap metadata-lookup gate.

        Requiring the first group made group order affect recall: a sparse feed entry
        mentioning only photovoltaics could be rejected before richer Crossref
        metadata supplied the missing material term.
        """

        return any(
            self._contains(text, term)
            for group in self.required_groups
            for term in group
        )

    def accepts(self, text: str) -> bool:
        """Return whether text contains a term from every required group."""

        return all(
            any(self._contains(text, term) for term in group)
            for group in self.required_groups
        )


class PaperRecord(BaseModel):
    """Store one discovered paper and the latest outcome of processing it.

    A DOI is the stable identity whenever it is available. ``sources`` retains all
    places that found the paper, which makes overlap among RSS, OpenAlex, and Zotero
    auditable without processing the same work twice.
    """

    identifier: str
    sources: list[str] = Field(default_factory=list)
    title: str = ""
    summary: str = ""
    link: str = ""
    doi: str | None = None
    openalex_id: str | None = None
    topic_ids: list[str] = Field(default_factory=list)
    zotero_item_key: str | None = None
    zotero_attachment_key: str | None = None
    zotero_curated: bool = False
    publication_date: date | None = None
    status: str = "new"
    attempts: int = 0
    pdf_url: str | None = None
    downloaded_file: str | None = None
    pdf_sha256: str | None = None
    pdf_source: str | None = None
    pdf_access_basis: str | None = None
    error: str | None = None
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds")
    )

    @model_validator(mode="before")
    @classmethod
    def read_legacy_feed_source(cls, value: object) -> object:
        """Load version-one state without keeping its feed-only name in new files."""

        if (
            isinstance(value, dict)
            and "source_feed" in value
            and "sources" not in value
        ):
            migrated = dict(value)
            source = migrated.pop("source_feed")
            migrated["sources"] = [source] if source else []
            return migrated
        return value


class BotState(BaseModel):
    """Versioned JSON state that makes scheduled runs incremental and inspectable."""

    format_version: int = 4
    papers: dict[str, PaperRecord] = Field(default_factory=dict)
    openalex_last_successful_date: date | None = None


class BotRunConfiguration(BaseModel):
    """Record the non-secret inputs needed to interpret one run's statistics."""

    feeds: list[str]
    selection_file: str
    selection_sha256: str
    max_attempts: int
    request_timeout: float
    unpaywall_enabled: bool
    pdf_sources: list[str] = Field(default_factory=list)
    rss_enabled: bool
    openalex_enabled: bool
    openalex_topic_ids: list[str] = Field(default_factory=list)
    openalex_start_date: date | None = None
    openalex_end_date: date | None = None
    zotero_enabled: bool = False
    zotero_group_id: str | None = None
    zotero_collection_key: str | None = None
    zotero_output_collection_key: str | None = None
    zotero_save_enabled: bool = False
    zotero_curated: bool = False
    zotero_pdf_policy: Literal["never", "research-group"] = "never"


class DiscoveryFailure(BaseModel):
    """Preserve a source failure that would otherwise exist only in console output."""

    source_kind: Literal["rss", "openalex", "zotero"]
    source: str
    error: str


class PdfAcquisitionFailure(BaseModel):
    """Preserve one failed retrieval attempt even when a later source succeeds."""

    source: str
    identifier: str
    doi: str | None = None
    error: str


class OpenAlexRunStats(BaseModel):
    """Record enough query detail to audit coverage and API use after a run."""

    start_date: date
    end_date: date
    topic_ids: list[str]
    pages: int = 0
    works_seen: int = 0
    reported_results: int | None = None
    reported_cost_usd: float = 0.0
    checkpoint_advanced: bool = False


class ZoteroRunStats(BaseModel):
    """Account for group-library reads, attachment downloads, and opt-in writes."""

    group_id: str
    collection_key: str | None = None
    pages: int = 0
    items_seen: int = 0
    pdfs_downloaded: int = 0
    items_created: int = 0
    items_existing: int = 0
    items_updated: int = 0
    pdfs_uploaded: int = 0
    pdfs_existing: int = 0
    errors: int = 0


class PaperRunOutcome(BaseModel):
    """Describe how one discovered paper contributed—or did not contribute—to a run."""

    identifier: str
    doi: str | None = None
    status: str
    disposition: Literal["evaluated", "skipped_terminal", "skipped_max_attempts"]
    attempt: int
    error: str | None = None


class BotResult(BaseModel):
    """Versioned, retrievable account of one PapersBot invocation."""

    format_version: int = 4
    run_id: str
    status: Literal["running", "complete", "complete_with_errors", "failed"] = "running"
    started_at: str
    finished_at: str | None = None
    configuration: BotRunConfiguration
    feeds_checked: int = 0
    discovery_errors: int = 0
    entries_seen: int = 0
    unique_papers_seen: int = 0
    source_counts: dict[str, int] = Field(default_factory=dict)
    candidates_processed: int = 0
    relevant_papers: int = 0
    pdfs_downloaded: int = 0
    retries_attempted: int = 0
    retry_counts: dict[str, int] = Field(default_factory=dict)
    outcome_counts: dict[str, int] = Field(default_factory=dict)
    skip_counts: dict[str, int] = Field(default_factory=dict)
    discovery_failures: list[DiscoveryFailure] = Field(default_factory=list)
    acquisition_failures: list[PdfAcquisitionFailure] = Field(default_factory=list)
    openalex: OpenAlexRunStats | None = None
    zotero: ZoteroRunStats | None = None
    outcomes: list[PaperRunOutcome] = Field(default_factory=list)
    downloaded_files: list[Path] = Field(default_factory=list)
    error: str | None = None
