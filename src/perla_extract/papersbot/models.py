"""Persistent data and selection policy for PapersBot."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator


class SelectionPolicy(BaseModel):
    """Describe relevance as data so the bot is not tied to one coded heuristic.

    A paper must contain at least one term from every required group. Title-only
    exclusions remove publication types that should not enter device extraction.
    Projects can replace the packaged policy from the command line.
    """

    required_groups: list[list[str]]
    excluded_title_terms: list[str] = Field(default_factory=list)

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
        """Use the first group as a cheap gate before metadata API calls."""

        return any(self._contains(text, term) for term in self.required_groups[0])

    def accepts(self, text: str) -> bool:
        """Return whether text contains a term from every required group."""

        return all(
            any(self._contains(text, term) for term in group)
            for group in self.required_groups
        )


class PaperRecord(BaseModel):
    """Store one feed entry and the latest outcome of processing it."""

    identifier: str
    source_feed: str
    title: str = ""
    summary: str = ""
    link: str = ""
    doi: str | None = None
    status: str = "new"
    attempts: int = 0
    pdf_url: str | None = None
    downloaded_file: str | None = None
    error: str | None = None
    updated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds")
    )


class BotState(BaseModel):
    """Versioned JSON state that makes scheduled runs incremental and inspectable."""

    format_version: int = 1
    papers: dict[str, PaperRecord] = Field(default_factory=dict)


class BotResult(BaseModel):
    """Summary returned by a PapersBot run and printed by its CLI."""

    feeds_checked: int = 0
    feed_errors: int = 0
    entries_seen: int = 0
    candidates_processed: int = 0
    relevant_papers: int = 0
    pdfs_downloaded: int = 0
    downloaded_files: list[Path] = Field(default_factory=list)
