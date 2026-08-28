"""Read Zotero group libraries through the version-3 Web API."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from .models import ZoteroRunStats

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)


@dataclass(frozen=True)
class ZoteroPdfAttachment:
    """Retain the human-facing metadata of one stored PDF child."""

    key: str
    label: str = ""
    filename: str = ""


def _doi(value: object) -> str | None:
    """Normalize the first DOI found in a Zotero field without trusting its prefix."""

    match = DOI_PATTERN.search(str(value or ""))
    return match.group(0).rstrip(".,;)").lower() if match else None


def _publication_date(value: object) -> date | None:
    """Keep only unambiguous full dates; partial dates remain bibliographic text."""

    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", str(value or ""))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


class ZoteroClient:
    """Expose only the read operations required by curated literature intake.

    The API key remains inside this boundary and is never copied into paper state,
    run configuration, or logs. Writeback is intentionally absent from the initial
    integration so a cron deployment can use a group-scoped read-only key.
    """

    api_root = "https://api.zotero.org"

    def __init__(
        self,
        session: Any,
        *,
        group_id: str,
        api_key: str | None = None,
        collection_key: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        cleaned_group = str(group_id).strip()
        if not cleaned_group.isdigit():
            raise ValueError("Zotero group ID must contain only digits")
        self.session = session
        self.group_id = cleaned_group
        self.api_key = (api_key or "").strip()
        self.collection_key = (collection_key or "").strip() or None
        self.timeout = timeout
        self._attachments: dict[str, list[ZoteroPdfAttachment]] = {}

    @property
    def source(self) -> str:
        """Return stable provenance that does not expose credentials."""

        suffix = f"/collections/{self.collection_key}" if self.collection_key else ""
        return f"zotero:groups/{self.group_id}{suffix}"

    def _headers(self) -> dict[str, str]:
        """Authenticate only when a member-restricted library requires it."""

        headers = {"Zotero-API-Version": "3"}
        if self.api_key:
            headers["Zotero-API-Key"] = self.api_key
        return headers

    def _items_url(self) -> str:
        """Address either the configured intake collection or the whole group."""

        prefix = f"{self.api_root}/groups/{self.group_id}"
        if self.collection_key:
            return f"{prefix}/collections/{quote(self.collection_key)}/items/top"
        return f"{prefix}/items/top"

    @staticmethod
    def _array(response: Any, *, endpoint: str) -> list[Mapping[str, Any]]:
        """Validate Zotero list responses before the pipeline trusts their shape."""

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"Zotero returned a non-list response from {endpoint}")
        return [item for item in payload if isinstance(item, Mapping)]

    def fetch_items(self) -> tuple[list[dict[str, Any]], ZoteroRunStats]:
        """Read every top-level item and normalize it for DOI-first bot merging."""

        url = self._items_url()
        start = 0
        limit = 100
        entries: list[dict[str, Any]] = []
        stats = ZoteroRunStats(
            group_id=self.group_id,
            collection_key=self.collection_key,
        )
        while True:
            response = self.session.get(
                url,
                params={"format": "json", "limit": limit, "start": start},
                headers=self._headers(),
                timeout=self.timeout,
            )
            page = self._array(response, endpoint=url)
            stats.pages += 1
            stats.items_seen += len(page)
            entries.extend(
                entry for item in page if (entry := self._entry(item)) is not None
            )
            total = int(response.headers.get("Total-Results", len(page)))
            start += len(page)
            if not page or len(page) < limit or start >= total:
                break
        return entries, stats

    def _entry(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        """Ignore child objects and retain the metadata shared by all sources."""

        data = item.get("data")
        if not isinstance(data, Mapping):
            return None
        item_type = data.get("itemType")
        if item_type in {"note", "annotation"}:
            return None
        item_key = str(item.get("key") or data.get("key") or "").strip()
        if not item_key:
            return None
        if item_type == "attachment":
            if (
                data.get("parentItem")
                or data.get("contentType") != "application/pdf"
                or data.get("linkMode") not in {"imported_file", "imported_url"}
            ):
                return None
            return {
                "id": f"zotero:{self.group_id}:{item_key}",
                "doi": _doi(data.get("url")),
                "title": str(data.get("title") or data.get("filename") or ""),
                "summary": "",
                "link": str(data.get("url") or ""),
                "publication_date": None,
                "zotero_item_key": item_key,
                "zotero_attachment_key": item_key,
            }
        doi = _doi(data.get("DOI")) or _doi(data.get("extra")) or _doi(data.get("url"))
        return {
            "id": f"zotero:{self.group_id}:{item_key}",
            "doi": doi,
            "title": str(data.get("title") or ""),
            "summary": str(data.get("abstractNote") or ""),
            "link": str(data.get("url") or (f"https://doi.org/{doi}" if doi else "")),
            "publication_date": _publication_date(data.get("date")),
            "zotero_item_key": item_key,
        }

    @staticmethod
    def _pdf_attachment(item: Mapping[str, Any]) -> ZoteroPdfAttachment | None:
        """Convert a stored PDF item while preserving its original label and name."""

        data = item.get("data")
        if (
            not isinstance(data, Mapping)
            or data.get("itemType") != "attachment"
            or data.get("contentType") != "application/pdf"
            or data.get("linkMode") not in {"imported_file", "imported_url"}
        ):
            return None
        key = str(item.get("key") or data.get("key") or "").strip()
        if not key:
            return None
        return ZoteroPdfAttachment(
            key=key,
            label=str(data.get("title") or ""),
            filename=str(data.get("filename") or ""),
        )

    def direct_pdf_attachment(
        self, attachment_key: str, *, label: str = ""
    ) -> list[ZoteroPdfAttachment]:
        """Describe a top-level PDF already identified during item discovery."""

        return [ZoteroPdfAttachment(key=attachment_key, label=label)]

    def pdf_attachments(self, item_key: str) -> list[ZoteroPdfAttachment]:
        """Resolve every stored PDF child, caching the complete list for the run."""

        if item_key in self._attachments:
            return self._attachments[item_key]
        url = f"{self.api_root}/groups/{self.group_id}/items/{quote(item_key)}/children"
        response = self.session.get(
            url,
            params={"format": "json", "limit": 100},
            headers=self._headers(),
            timeout=self.timeout,
        )
        children = self._array(response, endpoint=url)
        attachments = [
            attachment
            for child in children
            if (attachment := self._pdf_attachment(child)) is not None
        ]
        self._attachments[item_key] = attachments
        return attachments

    def download_attachment(self, attachment_key: str, destination: Path) -> None:
        """Download a stored group PDF without leaking the key on redirects."""

        url = f"{self.api_root}/groups/{self.group_id}/items/{quote(attachment_key)}/file"
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            response = self.session.get(
                url,
                stream=True,
                headers=self._headers(),
                timeout=self.timeout,
                allow_redirects=False,
            )
            location = response.headers.get("Location")
            if 300 <= getattr(response, "status_code", 200) < 400 and location:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
                response = self.session.get(
                    location,
                    stream=True,
                    timeout=self.timeout,
                )
            with response:
                response.raise_for_status()
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            handle.write(chunk)
            with temporary.open("rb") as handle:
                if handle.read(5) != b"%PDF-":
                    raise ValueError("Zotero attachment is not a PDF")
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)
