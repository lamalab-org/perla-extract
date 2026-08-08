#!/usr/bin/env python3
"""Local PDF/ground-truth review workbench.

Run from the repository root:

    .venv/bin/python review_workbench/server.py \
      --pdf-dir /Users/kevinmaikjablonka/Downloads/test_eval_pdfs
"""

from __future__ import annotations

import argparse
import email.policy
import json
import mimetypes
import re
import sys
from email.parser import BytesParser
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import fitz


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from perla_extract.ground_truth import (  # noqa: E402
    exclusion_reasons,
    load_review_metadata,
    paper_metadata,
    review_metadata_path,
)
from review_workbench.review_evidence import (  # noqa: E402
    disagreement_paths,
    fact_suggestions,
    flatten_facts,
    load_evidence,
    quantity_mentions,
    review_progress,
    reviewer_entry,
    save_evidence,
)
from review_workbench.review_collaboration import (  # noqa: E402
    add_comment,
    add_issue,
    add_user,
    load_comments,
    load_figure_audits,
    load_issues,
    load_users,
    resolve_issue,
    save_figure_audit,
    upsert_authenticated_user,
)


PAPER_ID = re.compile(r"^[A-Za-z0-9.-]+--[A-Za-z0-9._-]+$")


class ReviewApplication:
    def __init__(self, pdf_dir: Path, ground_truth_dir: Path):
        self.pdf_dir = pdf_dir.resolve()
        self.ground_truth_dir = ground_truth_dir.resolve()
        self.static_dir = REPO_ROOT / "review_workbench" / "review_app"
        self.extractions_dir = self.ground_truth_dir.parent / "extractions"

    def validate_paper_id(self, paper_id: str) -> str:
        if not PAPER_ID.fullmatch(paper_id):
            raise ValueError("Invalid paper identifier")
        return paper_id

    def truth_dir(self, split: str) -> Path:
        if split not in {"dev", "test"}:
            raise ValueError("split must be 'dev' or 'test'")
        return self.ground_truth_dir / split

    def paper_path(self, split: str, paper_id: str) -> Path:
        return self.truth_dir(split) / f"{self.validate_paper_id(paper_id)}.json"

    def pdf_path(self, paper_id: str) -> Path:
        return self.pdf_dir / f"{self.validate_paper_id(paper_id)}.pdf"

    def extraction_sources(self) -> list[str]:
        if not self.extractions_dir.exists():
            return []
        return sorted(
            str(path.relative_to(self.extractions_dir))
            for path in self.extractions_dir.iterdir()
            if path.is_dir() and path.name != "humans"
        )

    def list_papers(self, split: str) -> list[dict]:
        truth_dir = self.truth_dir(split)
        manifest = load_review_metadata(truth_dir)
        papers = []
        for path in sorted(truth_dir.glob("*.json")):
            with path.open(encoding="utf-8") as stream:
                truth = json.load(stream)
            metadata = paper_metadata(manifest, path.stem)
            evidence = load_evidence(
                self.ground_truth_dir, split, path.stem, truth
            )
            papers.append(
                {
                    "id": path.stem,
                    "cell_count": len(truth.get("cells") or []),
                    "pdf_exists": self.pdf_path(path.stem).exists(),
                    "metadata": metadata,
                    "exclusion_reasons": exclusion_reasons(metadata),
                    "field_review": review_progress(evidence),
                    "open_issues": sum(
                        issue["status"] == "open"
                        for issue in load_issues(
                            self.ground_truth_dir, split, path.stem
                        )
                    ),
                }
            )
        return papers

    def corpus_summary(self) -> dict:
        return {
            split: {
                "papers": len(list(self.truth_dir(split).glob("*.json"))),
                "open_issues": sum(
                    issue["status"] == "open"
                    for path in self.truth_dir(split).glob("*.json")
                    for issue in load_issues(
                        self.ground_truth_dir, split, path.stem
                    )
                ),
            }
            for split in ("dev", "test")
        }

    def reviewer_progress_summary(self, split: str) -> list[dict]:
        """Aggregate per-reviewer field progress across one dataset split."""
        users = load_users(self.ground_truth_dir)
        summaries = {
            user["id"]: {
                **user,
                "total": 0,
                "reviewed": 0,
                "verified": 0,
                "incorrect": 0,
                "not_in_paper": 0,
                "needs_followup": 0,
                "pending": 0,
                "papers_started": 0,
                "papers_completed": 0,
            }
            for user in users
        }
        for truth_path in sorted(self.truth_dir(split).glob("*.json")):
            truth = self.load_ground_truth(split, truth_path.stem)
            evidence = load_evidence(
                self.ground_truth_dir, split, truth_path.stem, truth
            )
            for reviewer_id, summary in summaries.items():
                progress = review_progress(evidence, reviewer_id)
                for key in (
                    "total",
                    "reviewed",
                    "verified",
                    "incorrect",
                    "not_in_paper",
                    "needs_followup",
                    "pending",
                ):
                    summary[key] += progress[key]
                if progress["reviewed"]:
                    summary["papers_started"] += 1
                if progress["total"] and progress["reviewed"] == progress["total"]:
                    summary["papers_completed"] += 1
        paper_count = len(list(self.truth_dir(split).glob("*.json")))
        return [
            {
                **summary,
                "paper_count": paper_count,
                "percent": round(
                    100 * summary["reviewed"] / summary["total"], 1
                )
                if summary["total"]
                else 0,
            }
            for summary in summaries.values()
        ]

    def load_ground_truth(self, split: str, paper_id: str) -> dict:
        truth_path = self.paper_path(split, paper_id)
        if not truth_path.exists():
            raise FileNotFoundError(truth_path)
        with truth_path.open(encoding="utf-8") as stream:
            return json.load(stream)

    def get_paper(self, split: str, paper_id: str, source: str | None) -> dict:
        truth = self.load_ground_truth(split, paper_id)
        manifest = load_review_metadata(self.truth_dir(split))
        extraction = None
        if source:
            source_path = (self.extractions_dir / source).resolve()
            if self.extractions_dir not in source_path.parents:
                raise ValueError("Invalid extraction source")
            extraction_path = source_path / f"{paper_id}.json"
            if extraction_path.exists():
                with extraction_path.open(encoding="utf-8") as stream:
                    extraction = json.load(stream)
        return {
            "id": paper_id,
            "ground_truth": truth,
            "extraction": extraction,
            "metadata": paper_metadata(manifest, paper_id),
        }

    def get_review(
        self, split: str, paper_id: str, reviewer_id: str = "reviewer"
    ) -> dict:
        truth = self.load_ground_truth(split, paper_id)
        evidence = load_evidence(
            self.ground_truth_dir, split, paper_id, truth
        )
        facts = flatten_facts(truth)
        path = self.pdf_path(paper_id)
        suggestions = {}
        if path.exists():
            pages = self.pdf_pages(paper_id, path.stat().st_mtime_ns)
            suggestions = fact_suggestions(pages, facts)
        disagreements = set(disagreement_paths(evidence))
        enriched = []
        for fact in facts:
            enriched.append(
                {
                    **fact,
                    "evidence": reviewer_entry(
                        evidence["fields"][fact["path"]], reviewer_id
                    ),
                    "reviews": evidence["fields"][fact["path"]].get(
                        "reviews", {}
                    ),
                    "disagreement": fact["path"] in disagreements,
                    "suggestion": suggestions.get(fact["path"]),
                }
            )
        return {
            "paper_id": paper_id,
            "facts": enriched,
            "progress": review_progress(evidence, reviewer_id),
            "overall_progress": review_progress(evidence),
            "disagreement_paths": sorted(disagreements),
            "ground_truth_sha256": evidence["ground_truth_sha256"],
        }

    def get_quantities(self, split: str, paper_id: str) -> dict:
        truth = self.load_ground_truth(split, paper_id)
        path = self.pdf_path(paper_id)
        if not path.exists():
            raise FileNotFoundError(path)
        pages = self.pdf_pages(paper_id, path.stat().st_mtime_ns)
        mentions = quantity_mentions(pages, flatten_facts(truth))
        unmapped = [mention for mention in mentions if not mention["mapped_paths"]]
        return {
            "mentions": mentions,
            "unmapped": unmapped,
            "total": len(mentions),
            "unmapped_count": len(unmapped),
        }

    def save_review_evidence(
        self, split: str, paper_id: str, payload: object
    ) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Evidence must be an object")
        truth = self.load_ground_truth(split, paper_id)
        reviewer_id = str(payload.get("reviewer_id", ""))
        if reviewer_id not in {user["id"] for user in load_users(self.ground_truth_dir)}:
            raise ValueError("Unknown reviewer")
        current = load_evidence(
            self.ground_truth_dir, split, paper_id, truth
        )
        submitted_fields = payload.get("fields", {})
        if not isinstance(submitted_fields, dict):
            raise ValueError("Evidence fields must be an object")
        for path, field in current["fields"].items():
            if path in submitted_fields:
                field.setdefault("reviews", {})[reviewer_id] = submitted_fields[path]
        evidence = save_evidence(
            self.ground_truth_dir, split, paper_id, truth, current
        )
        return {
            "evidence": evidence,
            "progress": review_progress(evidence, reviewer_id),
            "overall_progress": review_progress(evidence),
            "disagreement_paths": disagreement_paths(evidence),
        }

    def users(self) -> list[dict[str, str]]:
        return load_users(self.ground_truth_dir)

    def add_reviewer(self, payload: object) -> dict[str, str]:
        if not isinstance(payload, dict):
            raise ValueError("Reviewer payload must be an object")
        return add_user(self.ground_truth_dir, str(payload.get("name", "")))

    def ensure_authenticated_user(self, user: dict[str, str]) -> dict[str, str]:
        return upsert_authenticated_user(self.ground_truth_dir, user)

    def comments(self, split: str, paper_id: str) -> list[dict]:
        self.paper_path(split, paper_id)
        return load_comments(self.ground_truth_dir, split, paper_id)

    def add_review_comment(
        self, split: str, paper_id: str, payload: object
    ) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Comment payload must be an object")
        if not self.paper_path(split, paper_id).exists():
            raise FileNotFoundError(self.paper_path(split, paper_id))
        return add_comment(
            self.ground_truth_dir,
            split,
            paper_id,
            str(payload.get("author_id", "")),
            str(payload.get("body", "")),
            str(payload["field_path"]) if payload.get("field_path") else None,
        )

    def issues(self, split: str, paper_id: str) -> list[dict]:
        if not self.paper_path(split, paper_id).exists():
            raise FileNotFoundError(self.paper_path(split, paper_id))
        return load_issues(self.ground_truth_dir, split, paper_id)

    def figure_audits(self, split: str, paper_id: str) -> dict[str, dict]:
        if not self.paper_path(split, paper_id).exists():
            raise FileNotFoundError(self.paper_path(split, paper_id))
        return load_figure_audits(self.ground_truth_dir, split, paper_id)

    def save_paper_figure_audit(
        self, split: str, paper_id: str, payload: object
    ) -> dict:
        if not self.paper_path(split, paper_id).exists():
            raise FileNotFoundError(self.paper_path(split, paper_id))
        if not isinstance(payload, dict):
            raise ValueError("Figure audit must be an object")
        return save_figure_audit(
            self.ground_truth_dir,
            split,
            paper_id,
            str(payload.get("reviewer_id", "")),
            payload,
        )

    def add_missing_issue(
        self, split: str, paper_id: str, payload: object
    ) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Issue payload must be an object")
        if not self.paper_path(split, paper_id).exists():
            raise FileNotFoundError(self.paper_path(split, paper_id))
        return add_issue(
            self.ground_truth_dir,
            split,
            paper_id,
            str(payload.get("reporter_id", "")),
            str(payload.get("type", "other")),
            str(payload.get("description", "")),
            cell_index=payload.get("cell_index"),
            field_path=str(payload["field_path"]) if payload.get("field_path") else None,
            suggested_value=str(payload.get("suggested_value", "")),
            source_page=payload.get("source_page"),
            source_text=str(payload.get("source_text", "")),
            value_relation=str(payload.get("value_relation", "unspecified")),
            aggregation=str(payload.get("aggregation", "unspecified")),
            measurement_context=str(
                payload.get("measurement_context", "unspecified")
            ),
            uncertainty=str(payload.get("uncertainty", "")),
            proposed_patch=payload.get("proposed_patch"),
        )

    def resolve_missing_issue(
        self, split: str, paper_id: str, issue_id: str, payload: object
    ) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Resolution payload must be an object")
        return resolve_issue(
            self.ground_truth_dir,
            split,
            paper_id,
            issue_id,
            str(payload.get("reviewer_id", "")),
            str(payload.get("resolution", "")),
        )

    def import_paper(
        self,
        split: str,
        paper_id: str,
        pdf_bytes: bytes,
        ground_truth_bytes: bytes,
    ) -> dict:
        self.validate_paper_id(paper_id)
        truth_path = self.paper_path(split, paper_id)
        pdf_path = self.pdf_path(paper_id)
        if truth_path.exists() or pdf_path.exists():
            raise ValueError("A paper with this identifier already exists")
        if not pdf_bytes.startswith(b"%PDF"):
            raise ValueError("Uploaded paper is not a PDF")
        try:
            truth = json.loads(ground_truth_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Ground truth is not valid JSON") from error
        if not isinstance(truth, dict) or not isinstance(truth.get("cells"), list):
            raise ValueError("Ground truth must contain a cells list")
        truth_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(pdf_bytes)
        truth_path.write_text(
            json.dumps(truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        manifest = load_review_metadata(self.truth_dir(split))
        manifest["papers"][paper_id] = {
            "article_type": "unknown",
            "tandem_scope": "unknown",
            "review_status": "pending",
            "notes": "Imported through the review workbench.",
        }
        review_metadata_path(self.truth_dir(split)).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        evidence = load_evidence(
            self.ground_truth_dir, split, paper_id, truth
        )
        save_evidence(
            self.ground_truth_dir, split, paper_id, truth, evidence
        )
        return {"id": paper_id, "split": split, "cell_count": len(truth["cells"])}

    def seed_all_evidence(self) -> int:
        count = 0
        for split in ("dev", "test"):
            for path in sorted(self.truth_dir(split).glob("*.json")):
                truth = self.load_ground_truth(split, path.stem)
                current = load_evidence(
                    self.ground_truth_dir, split, path.stem, truth
                )
                save_evidence(
                    self.ground_truth_dir, split, path.stem, truth, current
                )
                count += 1
        return count

    def save_ground_truth(self, split: str, paper_id: str, payload: object) -> None:
        if not isinstance(payload, dict) or not isinstance(payload.get("cells"), list):
            raise ValueError("Ground truth must be an object containing a 'cells' list")
        path = self.paper_path(split, paper_id)
        if not path.exists():
            raise FileNotFoundError(path)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def save_metadata(self, split: str, paper_id: str, payload: object) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Metadata must be an object")
        allowed = {
            "article_type": {"unknown", "research", "review", "perspective", "news_and_views", "other"},
            "tandem_scope": {"unknown", "none", "mentions_only", "contains_tandem_devices", "tandem_only"},
            "review_status": {"pending", "reviewed"},
        }
        for key, choices in allowed.items():
            if payload.get(key) not in choices:
                raise ValueError(f"Invalid {key}")
        cleaned = {key: payload[key] for key in allowed}
        cleaned["notes"] = str(payload.get("notes", ""))
        truth_dir = self.truth_dir(split)
        manifest = load_review_metadata(truth_dir)
        manifest["papers"][self.validate_paper_id(paper_id)] = cleaned
        path = review_metadata_path(truth_dir)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return cleaned

    @lru_cache(maxsize=64)
    def pdf_pages(self, paper_id: str, modified_ns: int) -> tuple[str, ...]:
        del modified_ns
        path = self.pdf_path(paper_id)
        with fitz.open(path) as document:
            return tuple(page.get_text() for page in document)

    def render_pdf_page(
        self, paper_id: str, page_number: int, scale: float = 1.5
    ) -> tuple[bytes, int]:
        """Render one PDF page for deterministic in-app navigation."""
        path = self.pdf_path(paper_id)
        if not path.exists():
            raise FileNotFoundError(path)
        if not 0.75 <= scale <= 3:
            raise ValueError("PDF page scale must be between 0.75 and 3")
        with fitz.open(path) as document:
            page_count = len(document)
            if not 1 <= page_number <= page_count:
                raise ValueError(
                    f"PDF page must be between 1 and {page_count}"
                )
            page = document[page_number - 1]
            pixmap = page.get_pixmap(
                matrix=fitz.Matrix(scale, scale), alpha=False
            )
            return pixmap.tobytes("png"), page_count

    def pdf_page_text(self, paper_id: str, page_number: int) -> tuple[str, int]:
        path = self.pdf_path(paper_id)
        if not path.exists():
            raise FileNotFoundError(path)
        pages = self.pdf_pages(paper_id, path.stat().st_mtime_ns)
        if not 1 <= page_number <= len(pages):
            raise ValueError(f"PDF page must be between 1 and {len(pages)}")
        return pages[page_number - 1], len(pages)

    def pdf_page_text_lines(self, paper_id: str, page_number: int) -> list[dict]:
        """Return positioned text lines for a selectable PDF text layer."""
        path = self.pdf_path(paper_id)
        if not path.exists():
            raise FileNotFoundError(path)
        with fitz.open(path) as document:
            if not 1 <= page_number <= len(document):
                raise ValueError(
                    f"PDF page must be between 1 and {len(document)}"
                )
            page = document[page_number - 1]
            page_rect = page.rect
            lines = []
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(span.get("text", "") for span in spans)
                    if not text.strip():
                        continue
                    x0, y0, x1, y1 = line["bbox"]
                    lines.append(
                        {
                            "text": text,
                            "bbox": {
                                "x": x0 / page_rect.width,
                                "y": y0 / page_rect.height,
                                "width": (x1 - x0) / page_rect.width,
                                "height": (y1 - y0) / page_rect.height,
                            },
                            "font_size": max(
                                (span.get("size", 0) for span in spans),
                                default=0,
                            )
                            / page_rect.height,
                        }
                    )
            return lines

    def search_pdf(self, paper_id: str, query: str) -> list[dict]:
        path = self.pdf_path(paper_id)
        if not path.exists():
            raise FileNotFoundError(path)
        query = query.strip()
        if len(query) < 2 and not re.fullmatch(r"\d", query):
            return []
        results = []
        with fitz.open(path) as document:
            for page_number, page in enumerate(document, 1):
                page_rect = page.rect
                text = page.get_text()
                normalized = re.sub(r"\s+", " ", text).strip()
                pattern = re.compile(re.escape(query), re.IGNORECASE)
                text_matches = list(pattern.finditer(normalized))[:5]
                rectangles = page.search_for(query)[:5]
                count = max(len(text_matches), len(rectangles))
                for index in range(count):
                    match = text_matches[index] if index < len(text_matches) else None
                    rectangle = rectangles[index] if index < len(rectangles) else None
                    if match:
                        start = max(0, match.start() - 100)
                        end = min(len(normalized), match.end() + 150)
                        snippet = normalized[start:end]
                        match_start = match.start() - start
                        match_end = match.end() - start
                    else:
                        snippet = query
                        match_start = 0
                        match_end = len(query)
                    bbox = None
                    if rectangle and page_rect.width and page_rect.height:
                        bbox = {
                            "x": rectangle.x0 / page_rect.width,
                            "y": rectangle.y0 / page_rect.height,
                            "width": rectangle.width / page_rect.width,
                            "height": rectangle.height / page_rect.height,
                        }
                    results.append(
                        {
                            "page": page_number,
                            "snippet": snippet,
                            "match_start": match_start,
                            "match_end": match_end,
                            "bbox": bbox,
                        }
                    )
                    if len(results) >= 50:
                        return results
        return results


def make_handler(application: ReviewApplication, authenticator=None):
    class Handler(BaseHTTPRequestHandler):
        def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path: Path, content_type: str | None = None):
            if not path.exists() or not path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(
            self, body: bytes, content_type: str, headers: dict[str, str] | None = None
        ):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length))

        def read_multipart(self) -> dict[str, bytes | str]:
            length = int(self.headers.get("Content-Length", "0"))
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("Expected multipart form data")
            body = self.rfile.read(length)
            message = BytesParser(policy=email.policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                + body
            )
            result: dict[str, bytes | str] = {}
            for part in message.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                value = part.get_payload(decode=True) or b""
                result[name] = value if part.get_filename() else value.decode("utf-8")
            return result

        def current_user(self, *, require_admin: bool = False) -> dict[str, str]:
            if authenticator is None:
                return {
                    "id": "reviewer",
                    "name": "Reviewer",
                    "email": "",
                    "role": "admin",
                }
            if not hasattr(self, "_authenticated_user"):
                self._authenticated_user = authenticator.authenticate(self.headers)
                application.ensure_authenticated_user(self._authenticated_user)
            user = self._authenticated_user
            if require_admin and user.get("role") != "admin":
                from review_workbench.auth import AuthenticationError

                raise AuthenticationError("Administrator access is required", 403)
            return user

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            query = parse_qs(parsed.query)
            try:
                if parsed.path == "/api/auth/config":
                    config = (
                        authenticator.public_config()
                        if authenticator is not None
                        else {"enabled": False, "mode": "local", "publishable_key": "", "frontend_api": ""}
                    )
                    self.send_json(config)
                    return
                if parsed.path == "/api/session":
                    self.send_json({"user": self.current_user()})
                    return
                if parsed.path.startswith("/api/"):
                    user = self.current_user()
                if parsed.path == "/api/papers":
                    split = query.get("split", ["test"])[0]
                    self.send_json({"papers": application.list_papers(split), "sources": application.extraction_sources()})
                    return
                if parsed.path == "/api/users":
                    self.send_json({"users": application.users()})
                    return
                if parsed.path == "/api/corpus-summary":
                    self.send_json(application.corpus_summary())
                    return
                if parsed.path == "/api/reviewer-progress":
                    split = query.get("split", ["test"])[0]
                    self.send_json(
                        {
                            "reviewers": application.reviewer_progress_summary(
                                split
                            )
                        }
                    )
                    return
                if parsed.path.startswith("/api/paper/"):
                    paper_id = unquote(parsed.path.removeprefix("/api/paper/"))
                    split = query.get("split", ["test"])[0]
                    source = query.get("source", [None])[0]
                    self.send_json(application.get_paper(split, paper_id, source))
                    return
                if parsed.path.startswith("/api/review/"):
                    parts = [
                        unquote(part)
                        for part in parsed.path.strip("/").split("/")
                    ]
                    if len(parts) != 4:
                        raise ValueError("Expected /api/review/<split>/<paper-id>")
                    self.send_json(
                        application.get_review(parts[2], parts[3], user["id"])
                    )
                    return
                if parsed.path.startswith("/api/comments/"):
                    parts = [
                        unquote(part)
                        for part in parsed.path.strip("/").split("/")
                    ]
                    if len(parts) != 4:
                        raise ValueError("Expected /api/comments/<split>/<paper-id>")
                    self.send_json(
                        {"comments": application.comments(parts[2], parts[3])}
                    )
                    return
                if parsed.path.startswith("/api/issues/"):
                    parts = [
                        unquote(part)
                        for part in parsed.path.strip("/").split("/")
                    ]
                    if len(parts) != 4:
                        raise ValueError("Expected /api/issues/<split>/<paper-id>")
                    self.send_json(
                        {"issues": application.issues(parts[2], parts[3])}
                    )
                    return
                if parsed.path.startswith("/api/figure-audits/"):
                    parts = [
                        unquote(part)
                        for part in parsed.path.strip("/").split("/")
                    ]
                    if len(parts) != 4:
                        raise ValueError(
                            "Expected /api/figure-audits/<split>/<paper-id>"
                        )
                    self.send_json(
                        {"audits": application.figure_audits(parts[2], parts[3])}
                    )
                    return
                if parsed.path.startswith("/api/quantities/"):
                    paper_id = unquote(
                        parsed.path.removeprefix("/api/quantities/")
                    )
                    split = query.get("split", ["test"])[0]
                    self.send_json(application.get_quantities(split, paper_id))
                    return
                if parsed.path.startswith("/api/search/"):
                    paper_id = unquote(parsed.path.removeprefix("/api/search/"))
                    self.send_json({"results": application.search_pdf(paper_id, query.get("q", [""])[0])})
                    return
                if parsed.path.startswith("/api/pdf-page/"):
                    paper_id = unquote(
                        parsed.path.removeprefix("/api/pdf-page/")
                    )
                    page_number = int(query.get("page", ["1"])[0])
                    scale = float(query.get("scale", ["1.5"])[0])
                    body, page_count = application.render_pdf_page(
                        paper_id, page_number, scale
                    )
                    self.send_bytes(
                        body,
                        "image/png",
                        {"X-PDF-Pages": str(page_count)},
                    )
                    return
                if parsed.path.startswith("/api/pdf-text/"):
                    paper_id = unquote(
                        parsed.path.removeprefix("/api/pdf-text/")
                    )
                    page_number = int(query.get("page", ["1"])[0])
                    page_text, page_count = application.pdf_page_text(
                        paper_id, page_number
                    )
                    self.send_json(
                        {
                            "text": page_text,
                            "page_count": page_count,
                            "lines": application.pdf_page_text_lines(
                                paper_id, page_number
                            ),
                        }
                    )
                    return
                if parsed.path.startswith("/api/pdf/"):
                    paper_id = unquote(parsed.path.removeprefix("/api/pdf/"))
                    self.send_file(application.pdf_path(paper_id), "application/pdf")
                    return
                asset = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                if asset not in {"index.html", "app.js", "styles.css"}:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                self.send_file(application.static_dir / asset)
            except FileNotFoundError as error:
                self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except PermissionError as error:
                self.send_json(
                    {"error": str(error)},
                    HTTPStatus(getattr(error, "status", HTTPStatus.FORBIDDEN)),
                )

        def do_PUT(self):  # noqa: N802
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
            try:
                user = self.current_user()
                payload = self.read_json()
                if len(parts) == 4 and parts[:2] == ["api", "ground-truth"]:
                    application.save_ground_truth(parts[2], parts[3], payload)
                    self.send_json({"saved": True})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "metadata"]:
                    metadata = application.save_metadata(parts[2], parts[3], payload)
                    self.send_json({"saved": True, "metadata": metadata, "exclusion_reasons": exclusion_reasons(metadata)})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "evidence"]:
                    if not isinstance(payload, dict):
                        raise ValueError("Evidence payload must be an object")
                    payload["reviewer_id"] = user["id"]
                    result = application.save_review_evidence(
                        parts[2], parts[3], payload
                    )
                    self.send_json({"saved": True, **result})
                    return
                if len(parts) == 4 and parts[:2] == ["api", "figure-audits"]:
                    if not isinstance(payload, dict):
                        raise ValueError("Figure audit must be an object")
                    payload["reviewer_id"] = user["id"]
                    audit = application.save_paper_figure_audit(
                        parts[2], parts[3], payload
                    )
                    self.send_json({"saved": True, "audit": audit})
                    return
                if len(parts) == 5 and parts[:2] == ["api", "issues"]:
                    if not isinstance(payload, dict):
                        raise ValueError("Issue payload must be an object")
                    payload["reviewer_id"] = user["id"]
                    issue = application.resolve_missing_issue(
                        parts[2], parts[3], parts[4], payload
                    )
                    self.send_json({"saved": True, "issue": issue})
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except FileNotFoundError as error:
                self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except PermissionError as error:
                self.send_json(
                    {"error": str(error)},
                    HTTPStatus(getattr(error, "status", HTTPStatus.FORBIDDEN)),
                )

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
            try:
                if parsed.path == "/api/auth/login":
                    if authenticator is None or not hasattr(authenticator, "login"):
                        raise ValueError("Password login is not enabled")
                    payload = self.read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("Login payload must be an object")
                    token, user = authenticator.login(
                        str(payload.get("email", "")),
                        str(payload.get("password", "")),
                    )
                    application.ensure_authenticated_user(user)
                    self.send_json({"token": token, "user": user})
                    return
                user = self.current_user()
                if parsed.path == "/api/users":
                    self.current_user(require_admin=True)
                    user = application.add_reviewer(self.read_json())
                    self.send_json({"user": user}, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/papers/import":
                    self.current_user(require_admin=True)
                    form = self.read_multipart()
                    result = application.import_paper(
                        str(form.get("split", "test")),
                        str(form.get("paper_id", "")),
                        form.get("pdf", b"") if isinstance(form.get("pdf"), bytes) else b"",
                        form.get("ground_truth", b"")
                        if isinstance(form.get("ground_truth"), bytes)
                        else b"",
                    )
                    self.send_json({"paper": result}, HTTPStatus.CREATED)
                    return
                if len(parts) == 4 and parts[:2] == ["api", "comments"]:
                    payload = self.read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("Comment payload must be an object")
                    payload["author_id"] = user["id"]
                    comment = application.add_review_comment(parts[2], parts[3], payload)
                    self.send_json({"comment": comment}, HTTPStatus.CREATED)
                    return
                if len(parts) == 4 and parts[:2] == ["api", "issues"]:
                    payload = self.read_json()
                    if not isinstance(payload, dict):
                        raise ValueError("Issue payload must be an object")
                    payload["reporter_id"] = user["id"]
                    issue = application.add_missing_issue(parts[2], parts[3], payload)
                    self.send_json({"issue": issue}, HTTPStatus.CREATED)
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except FileNotFoundError as error:
                self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except PermissionError as error:
                self.send_json(
                    {"error": str(error)},
                    HTTPStatus(getattr(error, "status", HTTPStatus.FORBIDDEN)),
                )

        def log_message(self, fmt, *args):
            print(f"{self.client_address[0]} - {fmt % args}")

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument(
        "--ground-truth-dir",
        type=Path,
        default=REPO_ROOT / "src/perla_extract/data/ground_truth",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--seed-evidence",
        action="store_true",
        help="Create or reconcile field-review records before starting",
    )
    args = parser.parse_args()
    if not args.pdf_dir.is_dir():
        parser.error(f"PDF directory does not exist: {args.pdf_dir}")
    application = ReviewApplication(args.pdf_dir, args.ground_truth_dir)
    if args.seed_evidence:
        print(f"Seeded {application.seed_all_evidence()} evidence records")
    server = ThreadingHTTPServer((args.host, args.port), make_handler(application))
    print(f"Ground-truth review app: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
