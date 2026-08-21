"""Read and write Zotero group libraries through the version-3 Web API."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from hashlib import md5, sha256
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4

from .models import PaperRecord, ZoteroRunStats

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
MANAGED_TAG_PREFIXES = (
    "perla:status:",
    "perla:source:",
    "perla:pdf:",
    "perla:access:",
)
MANAGED_EXACT_TAGS = {"perla:curated"}


def _doi(value: object) -> str | None:
    """Normalize the first DOI found in a Zotero field without trusting its prefix."""

    match = DOI_PATTERN.search(str(value or ""))
    return match.group(0).rstrip(".,;)").lower() if match else None


def _publication_date(value: object) -> date | None:
    """Keep only unambiguous full dates; partial bibliographic dates remain metadata."""

    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", str(value or ""))
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


class ZoteroClient:
    """Provide the small group-library boundary PapersBot actually needs.

    Discovery, attachment download, and item creation live here so the bot's
    selection and retry loop remains source-neutral. The API key is held only by this
    client and is never copied into paper state or run configuration.
    """

    api_root = "https://api.zotero.org"

    def __init__(
        self,
        session: Any,
        *,
        group_id: str,
        api_key: str | None = None,
        collection_key: str | None = None,
        output_collection_key: str | None = None,
        timeout: float = 30.0,
    ):
        cleaned_group = str(group_id).strip()
        if not cleaned_group.isdigit():
            raise ValueError("Zotero group ID must contain only digits")
        self.session = session
        self.group_id = cleaned_group
        self.api_key = (api_key or "").strip()
        self.collection_key = (collection_key or "").strip() or None
        self.output_collection_key = (output_collection_key or "").strip() or None
        self.timeout = timeout
        self._known_dois: dict[str, str] = {}
        self._attachments: dict[str, str | None] = {}
        self._item_template: dict[str, Any] | None = None
        self._attachment_template: dict[str, Any] | None = None
        self._private_upload_verified = False

    @property
    def source(self) -> str:
        """Return stable provenance that does not expose credentials."""

        suffix = f"/collections/{self.collection_key}" if self.collection_key else ""
        return f"zotero:groups/{self.group_id}{suffix}"

    def _headers(self) -> dict[str, str]:
        headers = {"Zotero-API-Version": "3"}
        if self.api_key:
            headers["Zotero-API-Key"] = self.api_key
        return headers

    def _items_url(self) -> str:
        prefix = f"{self.api_root}/groups/{self.group_id}"
        if self.collection_key:
            return f"{prefix}/collections/{quote(self.collection_key)}/items/top"
        return f"{prefix}/items/top"

    @staticmethod
    def _array(response: Any, *, endpoint: str) -> list[Mapping[str, Any]]:
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
            for item in page:
                entry = self._entry(item)
                if entry is None:
                    continue
                entries.append(entry)
                if entry["doi"]:
                    self._known_dois[entry["doi"]] = entry["zotero_item_key"]
            total = int(response.headers.get("Total-Results", len(page)))
            start += len(page)
            if not page or len(page) < limit or start >= total:
                break
        return entries, stats

    def _entry(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        data = item.get("data")
        if not isinstance(data, Mapping):
            return None
        if data.get("itemType") in {"attachment", "note", "annotation"}:
            return None
        item_key = str(item.get("key") or data.get("key") or "").strip()
        if not item_key:
            return None
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

    def attachment_key(self, item_key: str) -> str | None:
        """Resolve the first stored PDF child, caching negative lookups for the run."""

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
        key = next(
            (
                str(child.get("key") or child.get("data", {}).get("key"))
                for child in children
                if isinstance(child.get("data"), Mapping)
                and child["data"].get("itemType") == "attachment"
                and child["data"].get("contentType") == "application/pdf"
                and child["data"].get("linkMode") in {"imported_file", "imported_url"}
            ),
            None,
        )
        self._attachments[item_key] = key
        return key

    def require_private_file_writes(self) -> None:
        """Refuse research-copy upload unless Zotero reports a private group.

        Collection membership cannot narrow file visibility inside a group. Checking
        the group itself prevents a seemingly private intake collection from placing
        copyrighted research copies in a public library.
        """

        if self._private_upload_verified:
            return
        if not self.api_key:
            raise ValueError("ZOTERO_API_KEY is required for Zotero PDF upload")
        url = f"{self.api_root}/groups/{self.group_id}"
        response = self.session.get(
            url,
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") if isinstance(payload, Mapping) else None
        if not isinstance(data, Mapping):
            raise ValueError("Zotero returned invalid group metadata")
        if str(data.get("type") or "").strip().lower() != "private":
            raise ValueError("research-group PDF upload requires a private Zotero group")
        if str(data.get("fileEditing") or "").strip().lower() == "none":
            raise ValueError("Zotero file storage is disabled for this group")
        self._private_upload_verified = True

    def download_attachment(self, attachment_key: str, destination: Path) -> None:
        """Download a stored group PDF atomically and reject non-PDF responses.

        Redirects are followed explicitly without the API-key header. That keeps a
        private-library credential on Zotero's API host when file bytes are served
        from a separate storage host.
        """

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

    def save_item(self, record: PaperRecord) -> tuple[str, bool]:
        """Create one missing bibliographic item and return ``(key, created)``.

        DOI identity makes retries idempotent. Creation uses a write token as the
        final guard against a transport retry being processed twice by Zotero.
        """

        if record.zotero_item_key:
            return record.zotero_item_key, False
        if not record.doi:
            raise ValueError("A DOI is required to create a Zotero item safely")
        existing = self._known_dois.get(record.doi)
        if not existing:
            existing = self._find_item_by_doi(record.doi)
        if existing:
            return existing, False
        if not self.api_key:
            raise ValueError("ZOTERO_API_KEY is required for Zotero writes")
        template = self._journal_article_template()
        template.update(
            {
                "title": record.title,
                "abstractNote": record.summary,
                "DOI": record.doi,
                "url": record.link or f"https://doi.org/{record.doi}",
                "date": record.publication_date.isoformat()
                if record.publication_date
                else "",
                "collections": (
                    [self.output_collection_key]
                    if self.output_collection_key
                    else []
                ),
            }
        )
        headers = {
            **self._headers(),
            "Content-Type": "application/json",
            "Zotero-Write-Token": uuid4().hex,
        }
        url = f"{self.api_root}/groups/{self.group_id}/items"
        response = self.session.post(
            url,
            json=[template],
            headers=headers,
            timeout=self.timeout,
        )
        key = _created_item_key(response)
        self._known_dois[record.doi] = key
        return key, True

    def sync_status(self, record: PaperRecord) -> bool:
        """Replace only PERLA-owned tags while preserving every human edit.

        Zotero annotations, notes, collections, bibliographic fields, and ordinary
        tags are untouched. Optimistic version headers make a concurrent journal-club
        edit win instead of being silently overwritten.
        """

        if not record.zotero_item_key:
            raise ValueError("A Zotero item key is required for status synchronization")
        if not self.api_key:
            raise ValueError("ZOTERO_API_KEY is required for Zotero writes")
        item_url = (
            f"{self.api_root}/groups/{self.group_id}/items/"
            f"{quote(record.zotero_item_key)}"
        )
        for attempt in range(2):
            response = self.session.get(
                item_url,
                params={"format": "json"},
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, Mapping) else None
            if not isinstance(data, Mapping):
                raise ValueError("Zotero returned invalid item metadata")
            version = payload.get("version") or data.get("version")
            if version is None:
                raise ValueError("Zotero item response omitted its version")
            original_tags = [
                dict(tag)
                for tag in data.get("tags", [])
                if isinstance(tag, Mapping) and str(tag.get("tag") or "").strip()
            ]
            retained = [
                tag
                for tag in original_tags
                if not _managed_tag(str(tag["tag"]))
            ]
            desired = [*retained, *({"tag": tag} for tag in _status_tags(record))]
            if original_tags == desired:
                return False
            headers = {
                **self._headers(),
                "Content-Type": "application/json",
                "If-Unmodified-Since-Version": str(version),
            }
            updated = self.session.patch(
                item_url,
                json={"tags": desired},
                headers=headers,
                timeout=self.timeout,
            )
            if getattr(updated, "status_code", 200) != 412:
                updated.raise_for_status()
                return True
            if attempt == 1:
                updated.raise_for_status()
        return False

    def upload_pdf(
        self,
        record: PaperRecord,
        path: Path,
        *,
        access_basis: str,
        source_url: str | None,
    ) -> tuple[str, bool, str]:
        """Attach a local PDF through Zotero's atomic file-storage protocol.

        Existing PDF children are never replaced. An interrupted upload reuses its
        child attachment on the next run, while Zotero's MD5 authorization makes the
        file association idempotent.
        """

        if not record.zotero_item_key:
            raise ValueError("Save the Zotero bibliographic item before its PDF")
        self.require_private_file_writes()
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError(f"PDF does not exist: {resolved}")
        with resolved.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise ValueError(f"File is not a PDF: {resolved}")
        digest = _sha256_file(resolved)
        attachment = self._pdf_attachment(record.zotero_item_key)
        if attachment is None:
            attachment_key = self._create_pdf_attachment(
                record,
                resolved,
                access_basis=access_basis,
                source_url=source_url,
                sha256_digest=digest,
            )
        else:
            data = attachment.get("data")
            attachment_key = str(
                attachment.get("key")
                or (data.get("key") if isinstance(data, Mapping) else "")
                or ""
            ).strip()
            if not attachment_key:
                raise ValueError("Zotero PDF attachment omitted its item key")
            if isinstance(data, Mapping) and data.get("md5"):
                self._attachments[record.zotero_item_key] = attachment_key
                return attachment_key, False, digest
        self._upload_attachment_file(attachment_key, resolved)
        self._attachments[record.zotero_item_key] = attachment_key
        return attachment_key, True, digest

    def _pdf_attachment(self, item_key: str) -> Mapping[str, Any] | None:
        """Return the first stored PDF child, including incomplete uploads."""

        url = f"{self.api_root}/groups/{self.group_id}/items/{quote(item_key)}/children"
        response = self.session.get(
            url,
            params={"format": "json", "limit": 100},
            headers=self._headers(),
            timeout=self.timeout,
        )
        for child in self._array(response, endpoint=url):
            data = child.get("data")
            if (
                isinstance(data, Mapping)
                and data.get("itemType") == "attachment"
                and data.get("contentType") == "application/pdf"
                and data.get("linkMode") in {"imported_file", "imported_url"}
            ):
                return child
        return None

    def _create_pdf_attachment(
        self,
        record: PaperRecord,
        path: Path,
        *,
        access_basis: str,
        source_url: str | None,
        sha256_digest: str,
    ) -> str:
        """Create the child item before atomically associating its file bytes."""

        template = self._pdf_attachment_template()
        source_line = source_url or "not recorded"
        template.update(
            {
                "parentItem": record.zotero_item_key,
                "linkMode": "imported_file",
                "title": "Full Text PDF (PERLA)",
                "accessDate": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "url": source_url or "",
                "note": (
                    "<p>Added by PERLA PapersBot for internal scientific text and "
                    "data mining and verification.</p>"
                    f"<p>Access basis: {access_basis}<br>Source: {source_line}<br>"
                    f"SHA-256: {sha256_digest}</p>"
                ),
                "contentType": "application/pdf",
                "charset": "",
                "filename": path.name,
                "md5": None,
                "mtime": None,
            }
        )
        response = self.session.post(
            f"{self.api_root}/groups/{self.group_id}/items",
            json=[template],
            headers={
                **self._headers(),
                "Content-Type": "application/json",
                "Zotero-Write-Token": uuid4().hex,
            },
            timeout=self.timeout,
        )
        return _created_item_key(response)

    def _upload_attachment_file(self, attachment_key: str, path: Path) -> None:
        """Authorize, transfer, and register one full Zotero storage upload."""

        file_url = (
            f"{self.api_root}/groups/{self.group_id}/items/"
            f"{quote(attachment_key)}/file"
        )
        authorization = self.session.post(
            file_url,
            data={
                "md5": _md5_file(path),
                "filename": path.name,
                "filesize": str(path.stat().st_size),
                "mtime": str(int(path.stat().st_mtime * 1000)),
                "params": "1",
            },
            headers={**self._headers(), "If-None-Match": "*"},
            timeout=self.timeout,
        )
        authorization.raise_for_status()
        payload = authorization.json()
        if not isinstance(payload, Mapping):
            raise ValueError("Zotero returned invalid file-upload authorization")
        if payload.get("exists") == 1:
            return
        upload_url = str(payload.get("url") or "").strip()
        upload_key = str(payload.get("uploadKey") or "").strip()
        if not upload_url or not upload_key:
            raise ValueError("Zotero file-upload authorization is incomplete")
        params = payload.get("params")
        if params is not None:
            fields = _upload_fields(params)
            with path.open("rb") as handle:
                uploaded = self.session.post(
                    upload_url,
                    data=fields,
                    files={"file": (path.name, handle, "application/pdf")},
                    timeout=self.timeout,
                )
        else:
            content_type = str(payload.get("contentType") or "").strip()
            prefix = str(payload.get("prefix") or "").encode()
            suffix = str(payload.get("suffix") or "").encode()
            if not content_type or not prefix or not suffix:
                raise ValueError("Zotero file-upload body instructions are incomplete")
            uploaded = self.session.post(
                upload_url,
                data=prefix + path.read_bytes() + suffix,
                headers={"Content-Type": content_type},
                timeout=self.timeout,
            )
        uploaded.raise_for_status()
        registered = self.session.post(
            file_url,
            data={"upload": upload_key},
            headers={**self._headers(), "If-None-Match": "*"},
            timeout=self.timeout,
        )
        registered.raise_for_status()

    def _find_item_by_doi(self, doi: str) -> str | None:
        """Check the whole group before writing, even when ingestion is collection-scoped.

        A paper can already exist outside the configured collection. Searching the
        group before creation prevents a collection filter or stale local state from
        turning writeback into duplicate bibliographic records.
        """

        url = f"{self.api_root}/groups/{self.group_id}/items/top"
        response = self.session.get(
            url,
            params={"q": doi, "qmode": "everything", "limit": 100},
            headers=self._headers(),
            timeout=self.timeout,
        )
        for item in self._array(response, endpoint=url):
            entry = self._entry(item)
            if entry is not None and entry["doi"] == doi:
                key = str(entry["zotero_item_key"])
                self._known_dois[doi] = key
                return key
        return None

    def _journal_article_template(self) -> dict[str, Any]:
        """Cache Zotero's current editable fields instead of duplicating its schema."""

        if self._item_template is None:
            url = f"{self.api_root}/items/new"
            response = self.session.get(
                url,
                params={"itemType": "journalArticle"},
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Zotero returned an invalid journalArticle template")
            self._item_template = dict(payload)
        return dict(self._item_template)

    def _pdf_attachment_template(self) -> dict[str, Any]:
        """Cache Zotero's current imported-file fields instead of copying its schema."""

        if self._attachment_template is None:
            url = f"{self.api_root}/items/new"
            response = self.session.get(
                url,
                params={"itemType": "attachment", "linkMode": "imported_file"},
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, Mapping):
                raise ValueError("Zotero returned an invalid PDF attachment template")
            self._attachment_template = dict(payload)
        return dict(self._attachment_template)


def _created_item_key(response: Any) -> str:
    """Validate Zotero's batch-write response and return its first created key."""

    response.raise_for_status()
    payload = response.json()
    successful = payload.get("successful") if isinstance(payload, Mapping) else None
    saved = successful.get("0") if isinstance(successful, Mapping) else None
    key = str(saved.get("key") if isinstance(saved, Mapping) else "").strip()
    if not key:
        raise ValueError("Zotero did not return the created item key")
    return key


def _managed_tag(tag: str) -> bool:
    """Identify only tags owned by PERLA's status synchronizer."""

    return tag in MANAGED_EXACT_TAGS or tag.startswith(MANAGED_TAG_PREFIXES)


def _status_tags(record: PaperRecord) -> list[str]:
    """Describe machine state compactly without putting run logs in Zotero."""

    source_kinds = sorted({source.partition(":")[0] for source in record.sources})
    pdf_state = (
        "attached"
        if record.zotero_attachment_key
        else "local"
        if record.downloaded_file
        else "missing"
    )
    tags = [
        f"perla:status:{record.status}",
        *(f"perla:source:{source}" for source in source_kinds),
        f"perla:pdf:{pdf_state}",
    ]
    if record.zotero_curated:
        tags.append("perla:curated")
    if record.pdf_access_basis:
        tags.append(f"perla:access:{record.pdf_access_basis}")
    return tags


def _md5_file(path: Path) -> str:
    """Compute the MD5 required by Zotero's storage protocol, not for security."""

    digest = md5(usedforsecurity=False)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    """Create a durable provenance fingerprint for the uploaded research copy."""

    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _upload_fields(value: object) -> dict[str, str]:
    """Normalize either documented representation of Zotero's storage form fields."""

    if isinstance(value, Mapping):
        return {str(key): str(field) for key, field in value.items()}
    if isinstance(value, list):
        fields: dict[str, str] = {}
        for entry in value:
            if isinstance(entry, Mapping) and "name" in entry and "value" in entry:
                fields[str(entry["name"])] = str(entry["value"])
            elif isinstance(entry, (list, tuple)) and len(entry) == 2:
                fields[str(entry[0])] = str(entry[1])
            else:
                raise ValueError("Zotero returned invalid storage form fields")
        return fields
    raise ValueError("Zotero returned invalid storage form fields")
