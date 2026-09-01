"""Persistent data and selection policy for PapersBot."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PAPERSBOT_FORMAT_VERSION = 5
PaperStatus: TypeAlias = Literal[
    "new",
    "downloaded",
    "excluded",
    "irrelevant",
    "error",
    "no_pdf",
    "missing_doi",
]
PaperDisposition: TypeAlias = Literal[
    "evaluated",
    "skipped_terminal",
    "skipped_max_attempts",
]


def _unique_nonempty(values: list[str]) -> list[str]:
    """Normalize user-authored policy lists before they become matching rules."""

    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class OpenAlexPolicy(BaseModel):
    """Configure topic discovery and its incremental overlap.

    Topic IDs are kept in the policy file because they describe the literature
    domain, not the retrieval algorithm. Re-reading several days on each run makes
    delayed indexing harmless; DOI deduplication keeps that overlap inexpensive.
    """

    model_config = ConfigDict(extra="forbid")

    topic_ids: list[str]
    initial_lookback_days: int = Field(default=30, ge=1)
    overlap_days: int = Field(default=7, ge=0)

    @field_validator("topic_ids")
    @classmethod
    def require_topics(cls, topics: list[str]) -> list[str]:
        """Reject an enabled discovery block that cannot produce a query."""

        cleaned = _unique_nonempty(topics)
        if not cleaned:
            raise ValueError("topic_ids must contain at least one OpenAlex topic")
        return cleaned


class SelectionPolicy(BaseModel):
    """Describe relevance as data so the bot is not tied to one coded heuristic.

    A paper must contain at least one term from every required group. Title-only
    exclusions remove publication types that should not enter device extraction.
    Projects can replace the packaged policy from the command line.
    """

    model_config = ConfigDict(extra="forbid")

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
        """Reject empty match rules that could silently accept every paper."""

        cleaned = [_unique_nonempty(group) for group in groups]
        if not cleaned or any(not group for group in cleaned):
            raise ValueError("required_groups must contain only non-empty groups")
        return cleaned

    @field_validator("excluded_title_terms")
    @classmethod
    def normalize_exclusions(cls, terms: list[str]) -> list[str]:
        """Discard blank exclusions because an empty pattern matches every title."""

        return _unique_nonempty(terms)

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


class PaperDocument(BaseModel):
    """Describe one locally retained PDF without guessing its scientific role."""

    source_identifier: str | None = None
    label: str = ""
    filename: str = ""
    role: Literal["article", "supporting_information", "unknown"] = "unknown"
    source_url: str
    local_file: str
    sha256: str
    pdf_source: str
    access_basis: str


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
    zotero_attachment_filename: str | None = None
    zotero_curated: bool = False
    publication_date: date | None = None
    status: PaperStatus = "new"
    attempts: int = 0
    pdf_url: str | None = None
    downloaded_file: str | None = None
    pdf_sha256: str | None = None
    pdf_source: str | None = None
    pdf_access_basis: str | None = None
    documents: list[PaperDocument] = Field(default_factory=list)
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

    format_version: int = PAPERSBOT_FORMAT_VERSION
    papers: dict[str, PaperRecord] = Field(default_factory=dict)
    openalex_last_successful_date: date | None = None


class BotRunConfiguration(BaseModel):
    """Record the non-secret inputs needed to interpret one run's statistics."""

    feeds: list[str]
    selection_file: str
    selection_sha256: str
    max_attempts: int
    request_timeout: float
    request_retries: int
    unpaywall_enabled: bool
    pdf_sources: list[str] = Field(default_factory=list)
    rss_enabled: bool
    openalex_enabled: bool
    openalex_authenticated: bool = False
    openalex_topic_ids: list[str] = Field(default_factory=list)
    openalex_start_date: date | None = None
    openalex_end_date: date | None = None
    zotero_enabled: bool = False
    zotero_group_id: str | None = None
    zotero_collection_key: str | None = None
    zotero_curated: bool = False


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
    """Account for group-library reads and stored attachment downloads."""

    group_id: str
    collection_key: str | None = None
    pages: int = 0
    items_seen: int = 0
    attachment_pages: int = 0
    attachments_seen: int = 0
    pdfs_downloaded: int = 0
    errors: int = 0


class PaperRunOutcome(BaseModel):
    """Describe how one discovered paper contributed—or did not contribute—to a run."""

    identifier: str
    doi: str | None = None
    status: PaperStatus
    disposition: PaperDisposition
    attempt: int
    error: str | None = None


class BotResult(BaseModel):
    """Versioned, retrievable account of one PapersBot invocation."""

    format_version: int = PAPERSBOT_FORMAT_VERSION
    run_id: str
    status: Literal["running", "complete", "complete_with_errors", "failed"] = "running"
    started_at: str
    finished_at: str | None = None
    configuration: BotRunConfiguration
    feeds_checked: int = 0
    discovery_errors: int = 0
    entries_seen: int = 0
    unique_papers_seen: int = 0
    state_retries_scheduled: int = 0
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
