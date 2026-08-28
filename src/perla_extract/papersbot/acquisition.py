"""Acquire PDFs through replaceable, provenance-reporting sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from loguru import logger

from .models import PaperRecord
from .zotero import ZoteroClient


@dataclass(frozen=True)
class AcquiredPdf:
    """Describe one successful acquisition without hiding why it was accessible."""

    url: str
    access_basis: str
    downloaded_now: bool
    attachment_key: str | None = None


class PdfSource(Protocol):
    """Retrieve a PDF for a normalized paper record when this source can serve it.

    Discovery and acquisition are separate concerns: an RSS item may ultimately be
    retrieved from Zotero, an open repository, or an institutionally authorized
    service. Implementations return ``None`` when they cannot serve a record and
    identify the access basis when they can. This keeps publisher- or deployment-
    specific access logic outside the discovery pipeline.
    """

    name: str

    def acquire(self, record: PaperRecord, destination: Path) -> AcquiredPdf | None:
        """Download or reuse one validated PDF, or return ``None`` if unavailable."""


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


def _download_pdf(session: Any, url: str, destination: Path, timeout: float) -> bool:
    """Stream a candidate PDF atomically and reject HTML saved with a PDF name."""

    if destination.exists():
        return False
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
        return True
    finally:
        temporary.unlink(missing_ok=True)


class ZoteroPdfSource:
    """Retrieve PDFs deliberately stored by members of a Zotero group library."""

    name = "zotero"

    def __init__(self, client: ZoteroClient) -> None:
        self.client = client

    def acquire(self, record: PaperRecord, destination: Path) -> AcquiredPdf | None:
        """Use the record's stored attachment without assuming that it has a DOI."""

        if not record.zotero_item_key:
            return None
        attachment_key = self.client.attachment_key(record.zotero_item_key)
        if not attachment_key:
            return None
        downloaded_now = not destination.exists()
        if downloaded_now:
            self.client.download_attachment(attachment_key, destination)
        return AcquiredPdf(
            url=(
                f"https://api.zotero.org/groups/{self.client.group_id}"
                f"/items/{attachment_key}/file"
            ),
            access_basis="member-supplied",
            downloaded_now=downloaded_now,
            attachment_key=attachment_key,
        )


class OpenAccessPdfSource:
    """Resolve and retrieve openly available PDFs without scraping landing pages."""

    name = "open-access"

    def __init__(
        self,
        session: Any,
        *,
        timeout: float,
        unpaywall_email: str | None = None,
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.unpaywall_email = unpaywall_email

    def acquire(self, record: PaperRecord, destination: Path) -> AcquiredPdf | None:
        """Resolve a DOI through Unpaywall or OpenAlex and validate the response."""

        if not record.doi:
            return None
        url = record.pdf_url or self._resolve(record.doi)
        if not url:
            return None
        downloaded_now = _download_pdf(
            self.session, url, destination, timeout=self.timeout
        )
        return AcquiredPdf(
            url=url,
            access_basis="open-access",
            downloaded_now=downloaded_now,
        )

    def _resolve(self, doi: str) -> str | None:
        """Return the strongest public PDF location reported for one DOI."""

        if self.unpaywall_email:
            try:
                payload = _request_json(
                    self.session,
                    f"https://api.unpaywall.org/v2/{quote(doi, safe='')}",
                    self.timeout,
                    params={"email": self.unpaywall_email},
                )
                location = payload.get("best_oa_location") or {}
                if isinstance(location, dict) and location.get("url_for_pdf"):
                    return str(location["url_for_pdf"])
            except Exception as exc:
                logger.debug("Unpaywall lookup failed for {}: {}", doi, exc)

        try:
            payload = _request_json(
                self.session,
                f"https://api.openalex.org/works/https://doi.org/"
                f"{quote(doi, safe='')}",
                self.timeout,
            )
            locations = [
                payload.get("best_oa_location"),
                payload.get("primary_location"),
            ]
            for location in locations:
                if isinstance(location, dict) and location.get("pdf_url"):
                    return str(location["pdf_url"])
        except Exception as exc:
            logger.debug("OpenAlex lookup failed for {}: {}", doi, exc)
        return None
