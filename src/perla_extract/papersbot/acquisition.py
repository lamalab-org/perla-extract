"""Acquire PDFs through replaceable, provenance-reporting sources."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
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
    path: Path | None = None
    attachment_key: str | None = None
    label: str = ""
    filename: str = ""
    role: Literal["article", "supporting_information", "unknown"] = "unknown"


class PdfSource(Protocol):
    """Retrieve a PDF for a normalized paper record when this source can serve it.

    Discovery and acquisition are separate concerns: an RSS item may ultimately be
    retrieved from Zotero, an open repository, or an institutionally authorized
    service. Implementations return ``None`` when they cannot serve a record and
    identify the access basis when they can. This keeps publisher- or deployment-
    specific access logic outside the discovery pipeline.
    """

    name: str

    def acquire(
        self, record: PaperRecord, destination: Path
    ) -> AcquiredPdf | list[AcquiredPdf] | None:
        """Download one or more PDFs, or return ``None`` when none are available."""


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

    def acquire(self, record: PaperRecord, destination: Path) -> list[AcquiredPdf] | None:
        """Use every stored PDF attachment without assuming that one is the SI."""

        if not record.zotero_item_key:
            return None
        if record.zotero_attachment_key == record.zotero_item_key:
            attachments = self.client.direct_pdf_attachment(
                record.zotero_attachment_key,
                label=record.title,
            )
        else:
            attachments = self.client.pdf_attachments(record.zotero_item_key)
        if not attachments:
            return None
        acquired: list[AcquiredPdf] = []
        try:
            for index, attachment in enumerate(attachments):
                path = (
                    destination
                    if index == 0
                    else destination.with_name(
                        f"{destination.stem}--zotero-{attachment.key.lower()}.pdf"
                    )
                )
                downloaded_now = not path.exists()
                if downloaded_now:
                    self.client.download_attachment(attachment.key, path)
                acquired.append(
                    AcquiredPdf(
                        url=(
                            f"https://api.zotero.org/groups/{self.client.group_id}"
                            f"/items/{attachment.key}/file"
                        ),
                        access_basis="member-supplied",
                        downloaded_now=downloaded_now,
                        path=path,
                        attachment_key=attachment.key,
                        label=attachment.label,
                        filename=attachment.filename,
                    )
                )
        except Exception:
            for item in acquired:
                if item.downloaded_now and item.path is not None:
                    item.path.unlink(missing_ok=True)
            raise
        return acquired


class OpenAccessPdfSource:
    """Resolve and retrieve openly available PDFs without scraping landing pages."""

    name = "open-access"

    def __init__(
        self,
        session: Any,
        *,
        timeout: float,
        unpaywall_email: str | None = None,
        openalex_api_key: str | None = None,
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.unpaywall_email = unpaywall_email
        self.openalex_api_key = (openalex_api_key or "").strip() or None

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
            path=destination,
            role="article",
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
                headers=(
                    {"Authorization": f"Bearer {self.openalex_api_key}"}
                    if self.openalex_api_key
                    else None
                ),
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
