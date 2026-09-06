"""Serve the study-extraction ground-truth workbench locally or on Vercel."""

from __future__ import annotations

import email.policy
import hashlib
import json
import mimetypes
import re
import sys
import uuid
from datetime import datetime, timezone
from email.parser import BytesParser
from functools import lru_cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import parse_qs, unquote, urlparse

import click
import fitz
from loguru import logger
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from perla_extract.study_extraction.artifacts import (  # noqa: E402
    write_bytes_exclusive,
    write_json_atomic,
    write_json_exclusive,
)
from perla_extract.study_extraction.enrichment import EnrichmentAudit  # noqa: E402
from perla_extract.study_extraction.models import StudyExtraction  # noqa: E402
from review_workbench.expert_comparison import (  # noqa: E402
    ComparisonService,
    ComparisonStorage,
    LocalComparisonStorage,
    build_comparison_source,
)
from review_workbench.feedback_export import build_feedback_archive  # noqa: E402
from review_workbench.ground_truth_export import (  # noqa: E402
    build_ground_truth_export,
    ground_truth_zip,
)
from review_workbench.review_storage import (  # noqa: E402
    ReviewStateStorage,
    StaleRevisionError,
)
from review_workbench.study_review import (  # noqa: E402
    InventoryAuditRequest,
    MutationRequest,
    RecordDecisionRequest,
    RecordMergeRequest,
    RecordReclassificationRequest,
    ReviewerResetRequest,
    StageRequest,
    StudyReviewStore,
    UndoMutationRequest,
)

REVISION_CONFLICT_RESPONSE = {
    "code": "review_revision_conflict",
    "error": (
        "This paper changed in another review session. Load the latest saved version, "
        "review your change again, and then save it."
    ),
}


def _same_pdf_page(left: fitz.Page, right: fitz.Page) -> bool:
    """Compare duplicated pages conservatively without relying on a publisher layout.

    Text-rich pages must have identical normalized text. Image-only pages use a
    low-resolution grayscale rendering, which lets concatenated scanned papers be
    recognized without guessing where their supporting information begins.
    """

    left_text = re.sub(r"\s+", "", left.get_text()).casefold()
    right_text = re.sub(r"\s+", "", right.get_text()).casefold()
    if left_text or right_text:
        return left_text == right_text
    matrix = fitz.Matrix(0.5, 0.5)
    left_image = left.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
    right_image = right.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY, alpha=False)
    return (
        left_image.width,
        left_image.height,
        hashlib.sha256(left_image.samples).digest(),
    ) == (
        right_image.width,
        right_image.height,
        hashlib.sha256(right_image.samples).digest(),
    )


class ReviewApplication:
    """Keep HTTP concerns outside the review-state and scientific-validation logic.

    Both the local server and Vercel adapter use this boundary, so request payloads,
    PDF handling, and UI conveniences cannot create a second review implementation.
    """

    def __init__(
        self,
        pdf_dir: Path,
        ground_truth_dir: Path,
        review_storage: ReviewStateStorage | None = None,
        comparison_storage: ComparisonStorage | None = None,
    ):
        self.pdf_dir = pdf_dir.resolve()
        self.pdf_dir.mkdir(parents=True, exist_ok=True)
        self.store = StudyReviewStore(ground_truth_dir, review_storage)
        self.ground_truth_dir = self.store.root
        self.uploaded_workbook_dir = self.ground_truth_dir / "uploaded_workbooks"
        self.comparisons = ComparisonService(
            comparison_storage or LocalComparisonStorage(self.ground_truth_dir)
        )
        self.static_dir = REPO_ROOT / "review_workbench" / "review_app"

    @lru_cache(maxsize=1)
    def figure_census_proposals(self) -> dict[str, Any]:
        """Load immutable model suggestions once and expose papers independently.

        Reviewers need only the proposal for the open paper. Keeping the bundled
        artifact as the source of truth preserves reproducibility while avoiding a
        megabyte-scale browser download before record review can begin.
        """

        path = self.static_dir / "figure-census-proposals.json"
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        papers = payload.get("papers", {})
        return papers if isinstance(papers, dict) else {}

    def figure_census_proposal(self, paper_id: str) -> dict[str, Any]:
        """Return one paper's optional proposal without treating it as review data."""

        proposal = self.figure_census_proposals().get(paper_id)
        return {"paper_id": paper_id, "proposal": proposal}

    def render_figure_panel(
        self, paper_id: str, proposal_panel_id: str, split: str
    ) -> bytes:
        """Render the proposed panel directly from its reviewed source PDF.

        Coordinates are resolved from the frozen proposal rather than accepted from
        the browser. This keeps the preview reproducible and prevents arbitrary crop
        requests from becoming an unbounded rendering interface.
        """

        proposal = self.figure_census_proposals().get(paper_id) or {}
        panel = next(
            (
                item
                for item in proposal.get("panels", [])
                if item.get("proposal_panel_id") == proposal_panel_id
            ),
            None,
        )
        if panel is None:
            raise FileNotFoundError("figure-panel proposal not found")
        page_number = panel.get("page")
        figure_bbox = panel.get("figure_bbox_pdf")
        panel_bbox = panel.get("panel_bbox_normalized")
        if not isinstance(page_number, int) or not (
            isinstance(figure_bbox, list)
            and len(figure_bbox) == 4
            and all(isinstance(value, (int, float)) for value in figure_bbox)
        ):
            raise FileNotFoundError("figure-panel preview is unavailable")

        pdf_bytes = self.review_pdf(paper_id, "main", split)
        expected_hash = proposal.get("pdf_sha256")
        if expected_hash and hashlib.sha256(pdf_bytes).hexdigest() != expected_hash:
            raise FileNotFoundError("figure-panel source PDF does not match the proposal")

        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            if not 1 <= page_number <= len(pdf):
                raise FileNotFoundError("figure-panel page is unavailable")
            page = pdf[page_number - 1]
            figure = fitz.Rect(*figure_bbox) & page.rect
            clip = figure
            if (
                isinstance(panel_bbox, list)
                and len(panel_bbox) == 4
                and all(isinstance(value, (int, float)) for value in panel_bbox)
            ):
                x0, y0, x1, y1 = panel_bbox
                if 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 1000:
                    clip = fitz.Rect(
                        figure.x0 + figure.width * x0 / 1000,
                        figure.y0 + figure.height * y0 / 1000,
                        figure.x0 + figure.width * x1 / 1000,
                        figure.y0 + figure.height * y1 / 1000,
                    ) & page.rect
            if clip.is_empty or clip.is_infinite:
                raise FileNotFoundError("figure-panel crop is invalid")
            return page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=clip, alpha=False).tobytes("png")

    def create_comparison(self, payload: object) -> dict[str, Any]:
        """Freeze a blinded experiment from one legacy and one rich extraction."""

        if not isinstance(payload, dict):
            raise ValueError("comparison must be a JSON object")
        required = (
            "comparison_id",
            "paper_id",
            "title",
            "historical",
            "extracted",
            "reviewer_ids",
            "randomization_seed",
        )
        missing = [key for key in required if key not in payload]
        if missing:
            raise ValueError(f"missing comparison fields: {', '.join(missing)}")
        paper_id = str(payload["paper_id"])
        split = str(payload.get("split", "dev"))
        if split not in {"calibration", "dev", "test"}:
            raise ValueError("split must be calibration, dev, or test")
        source_hashes = {
            name: hashlib.sha256(self.review_pdf(paper_id, name, split)).hexdigest()
            for name in self.available_sources(paper_id, split)
        }
        if "main" not in source_hashes:
            raise ValueError(
                "the comparison paper must have a main PDF in the review app"
            )
        source = build_comparison_source(
            comparison_id=str(payload["comparison_id"]),
            paper_id=paper_id,
            title=str(payload["title"]),
            split=cast(Literal["calibration", "dev", "test"], split),
            historical=payload["historical"],
            extracted=payload["extracted"],
            reviewer_ids=[str(item) for item in payload["reviewer_ids"]],
            randomization_seed=str(payload["randomization_seed"]),
            source_hashes=source_hashes,
        )
        self.comparisons.storage.create(source)
        return {
            "comparison_id": source.comparison_id,
            "paper_id": source.paper_id,
            "assigned_reviewers": len(source.assignments),
            "candidate_hashes": sorted(
                candidate.common_sha256 for candidate in source.candidates.values()
            ),
        }

    def reviewer_feedback_archive(self) -> bytes:
        """Package all user feedback for an authenticated administrator."""

        proposal_path = self.static_dir / "figure-census-proposals.json"
        proposals = (
            json.loads(proposal_path.read_text(encoding="utf-8"))
            if proposal_path.exists()
            else None
        )
        return build_feedback_archive(
            self.store,
            self.comparisons,
            self.uploaded_review_workbooks(),
            proposals,
        )

    @staticmethod
    def _workbook_submission_relative_path(
        split: str,
        paper_id: str,
        received_at: str,
        submission_id: str,
        reviewer_id: str,
        filename: str,
    ) -> Path:
        """Give every received upload a unique readable name without trusting input."""

        safe_reviewer = re.sub(r"[^A-Za-z0-9._-]+", "_", reviewer_id)[:80]
        safe_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)[:160]
        if not safe_filename.lower().endswith(".xlsx"):
            safe_filename += ".xlsx"
        timestamp = re.sub(r"[^0-9TZ]", "", received_at)
        name = f"{timestamp}--{submission_id}--{safe_reviewer}--{safe_filename}"
        return Path(split) / paper_id / name

    def _archive_workbook_submission(
        self, relative: Path, data: bytes, receipt: dict[str, Any]
    ) -> None:
        """Persist exact bytes and their immutable receipt before validation starts."""

        path = self.uploaded_workbook_dir / relative
        write_bytes_exclusive(path, data)
        write_json_exclusive(path.with_suffix(".received.json"), receipt)

    def _record_workbook_outcome(self, relative: Path, outcome: dict[str, Any]) -> None:
        """Append validation outcome without mutating the original upload receipt."""

        write_json_exclusive(
            (self.uploaded_workbook_dir / relative).with_suffix(".outcome.json"),
            outcome,
        )

    @staticmethod
    def _uploaded_workbook_artifact(
        relative: Path,
        data: bytes,
        receipt: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Describe retained bytes and both immutable audit records for export."""

        return {
            "split": relative.parts[0],
            "paper_id": relative.parts[1],
            "sha256": hashlib.sha256(data).hexdigest(),
            "archive_path": (Path("uploaded_workbooks") / relative).as_posix(),
            "receipt": receipt,
            "outcome": outcome,
            "data": data,
        }

    def uploaded_review_workbooks(self) -> list[dict[str, Any]]:
        """Return exact retained workbook uploads for an administrator export."""

        if not self.uploaded_workbook_dir.exists():
            return []
        artifacts = []
        for path in sorted(self.uploaded_workbook_dir.rglob("*.xlsx")):
            relative = path.relative_to(self.uploaded_workbook_dir)
            received_path = path.with_suffix(".received.json")
            outcome_path = path.with_suffix(".outcome.json")
            artifacts.append(
                self._uploaded_workbook_artifact(
                    relative,
                    path.read_bytes(),
                    json.loads(received_path.read_text())
                    if received_path.exists()
                    else None,
                    json.loads(outcome_path.read_text())
                    if outcome_path.exists()
                    else None,
                )
            )
        return artifacts

    def pdf_path(
        self, paper_id: str, source: str = "main", split: str | None = None
    ) -> Path:
        """Resolve a source PDF; local storage remains flat for CLI compatibility."""

        del split
        self.store.validate_identity("dev", paper_id)
        if source == "main":
            return self.pdf_dir / f"{paper_id}.pdf"
        if source == "supplement":
            return self.pdf_dir / f"{paper_id}.supplement.pdf"
        raise ValueError("source must be main or supplement")

    def list_papers(self, split: str) -> list[dict[str, Any]]:
        papers = self.store.list_papers(split)
        for paper in papers:
            paper["sources"] = self.available_sources(paper["id"], split)
        return papers

    def get_paper(self, split: str, paper_id: str) -> dict[str, Any]:
        return self._with_sources(self.store.load_bundle(split, paper_id))

    def available_sources(self, paper_id: str, split: str) -> list[str]:
        """Report available documents without imposing a storage implementation.

        Local review uses file existence. Remote adapters can override this boundary
        to consult an object index without downloading every PDF while listing papers.
        """

        return [
            source
            for source in ("main", "supplement")
            if self.pdf_path(paper_id, source, split).exists()
        ]

    def _with_sources(self, bundle: dict[str, Any]) -> dict[str, Any]:
        """Attach available source documents to every bundle returned to the UI."""

        paper_id = str(bundle["paper_id"])
        bundle["sources"] = self.available_sources(paper_id, str(bundle["split"]))
        return bundle

    def users(self) -> list[dict[str, str]]:
        path = self.ground_truth_dir / "users.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

    def ensure_authenticated_user(self, user: dict[str, str]) -> dict[str, str]:
        users = self.users()
        existing = next((item for item in users if item["id"] == user["id"]), None)
        if existing == user:
            return existing
        if existing:
            users[users.index(existing)] = user
        else:
            users.append(user)
        self._write_users(users)
        return user

    def add_reviewer(self, payload: object) -> dict[str, str]:
        if not isinstance(payload, dict) or not str(payload.get("name", "")).strip():
            raise ValueError("reviewer name is required")
        name = str(payload["name"]).strip()
        identifier = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        user = {"id": identifier, "name": name, "email": "", "role": "reviewer"}
        return self.ensure_authenticated_user(user)

    def _write_users(self, users: list[dict[str, str]]) -> None:
        write_json_atomic(self.ground_truth_dir / "users.json", users)

    @staticmethod
    def _decode_json(
        data: bytes | None, label: str, *, required: bool = True
    ) -> object | None:
        if not data:
            if required:
                raise ValueError(f"{label} is required")
            return None
        try:
            return json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"{label} is not valid JSON") from error

    def import_paper(
        self,
        split: str,
        paper_id: str,
        pdf_bytes: bytes,
        extraction_bytes: bytes,
        *,
        supplement_bytes: bytes = b"",
        document_bytes: bytes = b"",
        configuration_bytes: bytes = b"",
        coverage_bytes: bytes = b"",
        refinement_bytes: bytes = b"",
        repair_bytes: bytes = b"",
        enrichment_bytes: bytes = b"",
        reviewer_id: str,
    ) -> dict[str, Any]:
        """Validate an uploaded artifact set before creating a review workspace.

        The rich extraction is validated by ``StudyReviewStore``; source PDFs,
        evidence blocks, and run configuration remain separate so reviewers can audit
        both scientific claims and the extraction conditions that produced them.
        """

        if not pdf_bytes.startswith(b"%PDF"):
            raise ValueError("main paper is not a PDF")
        if supplement_bytes and not supplement_bytes.startswith(b"%PDF"):
            raise ValueError("supplement is not a PDF")
        extraction = self._decode_json(extraction_bytes, "extraction.json")
        document = self._decode_json(document_bytes, "document.json", required=False)
        configuration = self._decode_json(
            configuration_bytes, "run_configuration.json", required=False
        )
        coverage = self._decode_json(
            coverage_bytes, "coverage_audit.json", required=False
        )
        refinement = self._decode_json(
            refinement_bytes, "refinement_audit.json", required=False
        )
        repair = self._decode_json(repair_bytes, "targeted_repair.json", required=False)
        enrichment_payload = self._decode_json(
            enrichment_bytes, "enrichment.json", required=False
        )
        enrichment = (
            EnrichmentAudit.model_validate(enrichment_payload).model_dump(mode="json")
            if enrichment_payload is not None
            else None
        )
        bundle = self.store.import_seed(
            split,
            paper_id,
            extraction,
            document=document,
            manifest={
                "extraction_configuration": configuration or {},
                "quality_artifacts": {
                    "coverage_audit": coverage,
                    "refinement_audit": refinement,
                    "targeted_repair": repair,
                    "enrichment": enrichment,
                },
                "main_pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "supplement_pdf_sha256": (
                    hashlib.sha256(supplement_bytes).hexdigest()
                    if supplement_bytes
                    else None
                ),
            },
            reviewer_id=reviewer_id,
        )
        main_path = self.pdf_path(paper_id, "main", split)
        main_path.write_bytes(pdf_bytes)
        if supplement_bytes:
            self.pdf_path(paper_id, "supplement", split).write_bytes(supplement_bytes)
        return self._with_sources(bundle)

    def mutate(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        return self._with_sources(
            self.store.mutate(
                split, paper_id, MutationRequest.model_validate(payload), reviewer_id
            )
        )

    def merge_records(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        """Apply a validated duplicate merge through the shared review store."""

        return self._with_sources(
            self.store.merge_records(
                split,
                paper_id,
                RecordMergeRequest.model_validate(payload),
                reviewer_id,
            )
        )

    def reclassify_record(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        """Apply a validated record-type correction through the shared store."""

        return self._with_sources(
            self.store.reclassify_record(
                split,
                paper_id,
                RecordReclassificationRequest.model_validate(payload),
                reviewer_id,
            )
        )

    def undo_mutation(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        """Validate an undo request before reversing one attributable correction."""

        return self._with_sources(
            self.store.undo_mutation(
                split,
                paper_id,
                UndoMutationRequest.model_validate(payload),
                reviewer_id,
            )
        )

    def inventory_audit(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        return self._with_sources(
            self.store.inventory_audit(
                split,
                paper_id,
                InventoryAuditRequest.model_validate(payload),
                reviewer_id,
            )
        )

    def decide_record(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        """Validate an HTTP decision payload before invoking digest-bound review logic."""

        return self._with_sources(
            self.store.decide_record(
                split,
                paper_id,
                RecordDecisionRequest.model_validate(payload),
                reviewer_id,
            )
        )

    def complete_stage(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        return self._with_sources(
            self.store.complete_stage(
                split, paper_id, StageRequest.model_validate(payload), reviewer_id
            )
        )

    def reviewer_progress(self, split: str, reviewer_id: str) -> dict[str, Any]:
        """Return the authenticated reviewer's saved activity across one split."""

        return self.store.reviewer_progress(split, reviewer_id)

    def reset_reviewer_state(
        self, split: str, paper_id: str, payload: object, reviewer_id: str
    ) -> dict[str, Any]:
        """Clear current reviewer markers while retaining the append-only history."""

        return self._with_sources(
            self.store.reset_reviewer_state(
                split,
                paper_id,
                ReviewerResetRequest.model_validate(payload),
                reviewer_id,
            )
        )

    def review_workbook(
        self,
        split: str,
        paper_id: str,
        reviewer_id: str,
        *,
        device_id: str | None = None,
    ) -> bytes:
        """Build an offline form from the current reviewer-visible revision."""

        return self.store.review_workbook(
            split, paper_id, reviewer_id, device_id=device_id
        )

    def import_review_workbook(
        self,
        split: str,
        paper_id: str,
        data: bytes,
        reviewer_id: str,
        *,
        filename: str,
    ) -> dict[str, Any]:
        """Archive every bounded upload before attempting its review transition."""

        self.store.validate_identity(split, paper_id)
        if not data or len(data) > 15 * 1024 * 1024:
            raise ValueError(
                "review workbook must be a non-empty file smaller than 15 MiB"
            )
        received_at = datetime.now(timezone.utc).isoformat()
        submission_id = str(uuid.uuid4())
        safe_filename = Path(filename).name[:240] or "review.xlsx"
        relative = self._workbook_submission_relative_path(
            split,
            paper_id,
            received_at,
            submission_id,
            reviewer_id,
            safe_filename,
        )
        receipt = {
            "submission_id": submission_id,
            "received_at": received_at,
            "split": split,
            "paper_id": paper_id,
            "reviewer_id": reviewer_id,
            "original_filename": safe_filename,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data),
        }
        try:
            self._archive_workbook_submission(relative, data, receipt)
        except Exception as error:
            raise RuntimeError(
                "The workbook could not be archived, so no review changes were "
                "processed. Please keep your local copy and try again."
            ) from error
        try:
            bundle = self.store.import_review_workbook(
                split,
                paper_id,
                data,
                reviewer_id,
                filename=safe_filename,
            )
        except Exception as error:
            try:
                self._record_workbook_outcome(
                    relative,
                    {
                        "status": "rejected",
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                        "error_type": type(error).__name__,
                        "message": str(error)[:2000],
                    },
                )
            except Exception:
                logger.exception(
                    "Workbook bytes and receipt were archived, but the rejected "
                    "validation outcome could not be recorded"
                )
            raise
        event = bundle["events"][-1]
        outcome = {
            "status": "accepted",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "revision": int(bundle["revision"]),
            "event_id": str(event["event_id"]),
            "workbook_import_mode": event.get("details", {}).get(
                "workbook_import_mode"
            ),
        }
        try:
            self._record_workbook_outcome(relative, outcome)
            bundle["workbook_outcome_recorded"] = True
        except Exception:
            logger.exception(
                "Workbook and review were saved, but the accepted outcome sidecar "
                "could not be recorded"
            )
            bundle["workbook_outcome_recorded"] = False
        bundle["workbook_archived"] = True
        bundle["workbook_submission_id"] = submission_id
        return self._with_sources(bundle)

    def evidence_blocks(
        self, split: str, paper_id: str, query: str = ""
    ) -> list[dict[str, Any]]:
        """Return a bounded source-block search for evidence selection in the UI."""

        payload = self.store.load_document(split, paper_id)
        if payload is None:
            return []
        blocks = payload.get("blocks", []) if isinstance(payload, dict) else payload
        supplement_offset = self.source_page_offset(paper_id, "supplement", split)
        blocks = [
            self._evidence_block_for_view(
                split, paper_id, block, supplement_offset=supplement_offset
            )
            for block in blocks
        ]
        query = query.strip().lower()
        if query:
            blocks = [
                block
                for block in blocks
                if query in str(block.get("text", "")).lower()
                or query in str(block.get("block_id", "")).lower()
            ]
        return blocks[:100]

    def evidence_block(
        self, split: str, paper_id: str, block_id: str
    ) -> dict[str, Any]:
        """Resolve one citation directly so navigation never depends on search."""

        payload = self.store.load_document(split, paper_id)
        blocks = (
            payload.get("blocks", []) if isinstance(payload, dict) else payload or []
        )
        block = next(
            (
                candidate
                for candidate in blocks
                if isinstance(candidate, dict) and candidate.get("block_id") == block_id
            ),
            None,
        )
        if block is None:
            raise FileNotFoundError(f"evidence block {block_id} is unavailable")
        return self._evidence_block_for_view(split, paper_id, block)

    def _evidence_block_for_view(
        self,
        split: str,
        paper_id: str,
        block: dict[str, Any],
        *,
        supplement_offset: int | None = None,
    ) -> dict[str, Any]:
        """Translate raw concatenated-PDF pages into the pages reviewers see."""

        result = dict(block)
        if result.get("source") != "supplement":
            return result
        offset = (
            supplement_offset
            if supplement_offset is not None
            else self.source_page_offset(paper_id, "supplement", split)
        )
        page = result.get("page")
        if not offset or not isinstance(page, int):
            return result
        if page <= offset:
            result.update(source="main", page=page)
        else:
            result["page"] = page - offset
        return result

    @staticmethod
    def study_schema() -> dict[str, Any]:
        """Expose the authoritative schema for generic missing-record drafts."""

        return StudyExtraction.model_json_schema()

    def ground_truth_archive(self, split: str, paper_id: str) -> bytes:
        """Build the adjudicated, citation-validated bundle used in data PRs."""

        return ground_truth_zip(build_ground_truth_export(self.store, split, paper_id))

    @lru_cache(maxsize=128)
    def _pages(
        self,
        paper_id: str,
        source: str,
        modified_ns: int,
        split: str | None = None,
    ) -> tuple[str, ...]:
        del modified_ns
        with fitz.open(self.pdf_path(paper_id, source, split)) as document:
            return tuple(page.get_text() for page in document)

    @lru_cache(maxsize=128)
    def _duplicated_main_prefix(
        self,
        paper_id: str,
        split: str | None,
        main_modified_ns: int,
        supplement_modified_ns: int,
    ) -> int:
        """Return a full duplicated main-paper prefix, or zero when uncertain.

        Some acquisition pipelines save ``main + SI`` as the supplement. We hide
        a prefix only when every main-paper page matches in order and additional
        pages remain, avoiding journal-specific headings and page-count guesses.
        File timestamps keep the cached decision tied to the exact local copies.
        """

        del main_modified_ns, supplement_modified_ns
        with (
            fitz.open(self.pdf_path(paper_id, "main", split)) as main,
            fitz.open(self.pdf_path(paper_id, "supplement", split)) as supplement,
        ):
            if not main or len(supplement) <= len(main):
                return 0
            return (
                len(main)
                if all(
                    _same_pdf_page(main[index], supplement[index])
                    for index in range(len(main))
                )
                else 0
            )

    def source_page_offset(
        self, paper_id: str, source: str, split: str | None = None
    ) -> int:
        """Locate the first logical page of a source without mutating its PDF."""

        if source != "supplement":
            return 0
        main = self.pdf_path(paper_id, "main", split)
        supplement = self.pdf_path(paper_id, "supplement", split)
        if not main.exists() or not supplement.exists():
            return 0
        return self._duplicated_main_prefix(
            paper_id,
            split,
            main.stat().st_mtime_ns,
            supplement.stat().st_mtime_ns,
        )

    def render_pdf_page(
        self,
        paper_id: str,
        source: str,
        page_number: int,
        scale: float = 1.5,
        split: str | None = None,
    ) -> tuple[bytes, int]:
        path = self.pdf_path(paper_id, source, split)
        if not path.exists():
            raise FileNotFoundError(path)
        if not 0.75 <= scale <= 3:
            raise ValueError("scale must be between 0.75 and 3")
        offset = self.source_page_offset(paper_id, source, split)
        with fitz.open(path) as document:
            page_count = len(document) - offset
            if not 1 <= page_number <= page_count:
                raise ValueError(f"page must be between 1 and {page_count}")
            pixmap = document[page_number + offset - 1].get_pixmap(
                matrix=fitz.Matrix(scale, scale), alpha=False
            )
            return pixmap.tobytes("png"), page_count

    def pdf_page_text(
        self,
        paper_id: str,
        source: str,
        page_number: int,
        split: str | None = None,
    ) -> dict[str, Any]:
        path = self.pdf_path(paper_id, source, split)
        if not path.exists():
            raise FileNotFoundError(path)
        offset = self.source_page_offset(paper_id, source, split)
        with fitz.open(path) as document:
            page_count = len(document) - offset
            if not 1 <= page_number <= page_count:
                raise ValueError(f"page must be between 1 and {page_count}")
            page = document[page_number + offset - 1]
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
                        }
                    )
            return {
                "text": page.get_text(),
                "page_count": page_count,
                "lines": lines,
            }

    def search_pdf(
        self,
        paper_id: str,
        source: str,
        query: str,
        split: str | None = None,
    ) -> list[dict[str, Any]]:
        path = self.pdf_path(paper_id, source, split)
        if not path.exists():
            raise FileNotFoundError(path)
        query = query.strip()
        if len(query) < 2:
            return []
        results = []
        offset = self.source_page_offset(paper_id, source, split)
        with fitz.open(path) as document:
            for physical_page in range(offset, len(document)):
                page_number = physical_page - offset + 1
                page = document[physical_page]
                text = re.sub(r"\s+", " ", page.get_text()).strip()
                for match in list(re.finditer(re.escape(query), text, re.IGNORECASE))[
                    :5
                ]:
                    start, end = (
                        max(0, match.start() - 100),
                        min(len(text), match.end() + 160),
                    )
                    results.append({"page": page_number, "snippet": text[start:end]})
                    if len(results) == 50:
                        return results
        return results

    def review_pdf(self, paper_id: str, source: str, split: str | None = None) -> bytes:
        """Return the logical source document used by the on-screen reviewer."""

        path = self.pdf_path(paper_id, source, split)
        if not path.exists():
            raise FileNotFoundError(path)
        offset = self.source_page_offset(paper_id, source, split)
        if not offset:
            return path.read_bytes()
        with fitz.open(path) as source_document:
            output = fitz.open()
            try:
                output.insert_pdf(
                    source_document,
                    from_page=offset,
                    to_page=len(source_document) - 1,
                )
                return output.tobytes(garbage=3, deflate=True)
            finally:
                output.close()


def make_handler(application: ReviewApplication, authenticator=None):
    """Build an HTTP handler while keeping authentication optional for local use."""

    class Handler(BaseHTTPRequestHandler):
        def send_json(
            self,
            payload: object,
            status: HTTPStatus = HTTPStatus.OK,
            headers: dict[str, str] | None = None,
        ):
            body = json.dumps(payload, ensure_ascii=False).encode()
            response_headers = headers or {}
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in response_headers:
                self.send_header("Cache-Control", "no-store")
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path: Path, content_type: str | None = None):
            if not path.is_file():
                raise FileNotFoundError(path)
            body = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header(
                "Content-Type",
                content_type
                or mimetypes.guess_type(path.name)[0]
                or "application/octet-stream",
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def send_bytes(
            self, body: bytes, content_type: str, headers: dict[str, str] | None = None
        ):
            response_headers = headers or {}
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if "Cache-Control" not in response_headers:
                self.send_header("Cache-Control", "no-store")
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def read_json(self) -> object:
            return json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            )

        def read_multipart(self) -> dict[str, bytes | str]:
            length = int(self.headers.get("Content-Length", "0"))
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("expected multipart form data")
            message = BytesParser(policy=email.policy.default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                + self.rfile.read(length)
            )
            result: dict[str, bytes | str] = {}
            for part in message.iter_parts():
                raw_name = part.get_param("name", header="content-disposition")
                if isinstance(raw_name, str) and raw_name:
                    payload = part.get_payload(decode=True)
                    value = payload if isinstance(payload, bytes) else b""
                    result[raw_name] = value if part.get_filename() else value.decode()
            return result

        def current_user(self, require_admin: bool = False) -> dict[str, str]:
            if authenticator is None:
                return {
                    "id": "local-reviewer",
                    "name": "Local reviewer",
                    "email": "",
                    "role": "admin",
                }
            if not hasattr(self, "_review_user"):
                self._review_user = authenticator.authenticate(self.headers)
                application.ensure_authenticated_user(self._review_user)
            if require_admin and self._review_user.get("role") != "admin":
                raise PermissionError("administrator access is required")
            return self._review_user

        @staticmethod
        def route_parts(path: str) -> list[str]:
            return [unquote(part) for part in path.strip("/").split("/")]

        def do_GET(self):  # noqa: N802
            parsed, query = urlparse(self.path), parse_qs(urlparse(self.path).query)
            try:
                if parsed.path == "/api/auth/config":
                    self.send_json(
                        authenticator.public_config()
                        if authenticator
                        else {"enabled": False, "mode": "local"}
                    )
                    return
                if parsed.path == "/api/session":
                    self.send_json({"user": self.current_user()})
                    return
                if parsed.path.startswith("/api/"):
                    self.current_user()
                if parsed.path == "/api/papers":
                    split = query.get("split", ["calibration"])[0]
                    self.send_json({"papers": application.list_papers(split)})
                    return
                if parsed.path == "/api/users":
                    self.send_json({"users": application.users()})
                    return
                if parsed.path == "/api/study-schema":
                    self.send_json(application.study_schema())
                    return
                if parsed.path == "/api/comparisons":
                    user = self.current_user()
                    self.send_json(
                        {
                            "comparisons": application.comparisons.list_for(
                                user["id"],
                                include_unassigned=user.get("role") == "admin",
                            )
                        }
                    )
                    return
                parts = self.route_parts(parsed.path)
                if parts[:2] == ["api", "comparisons"] and len(parts) == 3:
                    user = self.current_user()
                    self.send_json(application.comparisons.open(parts[2], user["id"]))
                    return
                if parts[:2] == ["api", "native-comparisons"] and len(parts) == 3:
                    user = self.current_user()
                    self.send_json(
                        application.comparisons.open_native(parts[2], user["id"])
                    )
                    return
                if parts[:2] == ["api", "pairwise-comparisons"] and len(parts) == 3:
                    user = self.current_user()
                    self.send_json(
                        application.comparisons.open_pairwise(parts[2], user["id"])
                    )
                    return
                if parts[:2] == ["api", "comparison-reveal"] and len(parts) == 3:
                    self.current_user(require_admin=True)
                    self.send_json(application.comparisons.reveal(parts[2]))
                    return
                if parts[:2] == ["api", "comparison-export"] and len(parts) == 3:
                    self.current_user(require_admin=True)
                    self.send_json(application.comparisons.export(parts[2]))
                    return
                if parts[:2] == ["api", "reviewer-progress"] and len(parts) == 3:
                    user = self.current_user()
                    self.send_json(application.reviewer_progress(parts[2], user["id"]))
                    return
                if parsed.path == "/api/reviewer-feedback-export":
                    self.current_user(require_admin=True)
                    self.send_bytes(
                        application.reviewer_feedback_archive(),
                        "application/zip",
                        {
                            "Content-Disposition": (
                                'attachment; filename="perla-reviewer-feedback.zip"'
                            )
                        },
                    )
                    return
                if parts[:2] == ["api", "paper"] and len(parts) == 4:
                    self.send_json(application.get_paper(parts[2], parts[3]))
                    return
                if parts[:2] == ["api", "figure-census-proposal"] and len(parts) == 3:
                    self.send_json(
                        application.figure_census_proposal(parts[2]),
                        headers={"Cache-Control": "private, max-age=300"},
                    )
                    return
                if parts[:2] == ["api", "figure-panel-image"] and len(parts) == 4:
                    self.send_bytes(
                        application.render_figure_panel(
                            parts[2], parts[3], query.get("split", ["dev"])[0]
                        ),
                        "image/png",
                        {"Cache-Control": "private, max-age=3600, immutable"},
                    )
                    return
                if parts[:2] == ["api", "evidence"] and len(parts) == 4:
                    self.send_json(
                        {
                            "blocks": application.evidence_blocks(
                                parts[2], parts[3], query.get("q", [""])[0]
                            )
                        }
                    )
                    return
                if parts[:2] == ["api", "evidence-block"] and len(parts) == 5:
                    self.send_json(
                        application.evidence_block(parts[2], parts[3], parts[4])
                    )
                    return
                if parts[:2] == ["api", "ground-truth-export"] and len(parts) == 4:
                    self.current_user(require_admin=True)
                    paper_id = parts[3]
                    self.send_bytes(
                        application.ground_truth_archive(parts[2], paper_id),
                        "application/zip",
                        {
                            "Content-Disposition": (
                                f'attachment; filename="{paper_id}.ground-truth.zip"'
                            )
                        },
                    )
                    return
                if parts[:2] == ["api", "review-workbook"] and len(parts) == 4:
                    user = self.current_user()
                    paper_id = parts[3]
                    device_id = query.get("device", [None])[0]
                    safe_device = re.sub(r"[^A-Za-z0-9._-]+", "-", device_id or "")
                    scope = f".{safe_device}" if safe_device else ""
                    self.send_bytes(
                        application.review_workbook(
                            parts[2],
                            paper_id,
                            user["id"],
                            device_id=device_id,
                        ),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        {
                            "Content-Disposition": (
                                f'attachment; filename="{paper_id}{scope}.review.xlsx"'
                            )
                        },
                    )
                    return
                if len(parts) == 3 and parts[:2] in (
                    ["api", "pdf-page"],
                    ["api", "pdf-text"],
                    ["api", "search"],
                    ["api", "pdf"],
                ):
                    paper_id = parts[2]
                    source = query.get("source", ["main"])[0]
                    split = query.get("split", [None])[0]
                    if parts[1] == "pdf-page":
                        body, count = application.render_pdf_page(
                            paper_id,
                            source,
                            int(query.get("page", ["1"])[0]),
                            float(query.get("scale", ["1.5"])[0]),
                            split,
                        )
                        self.send_bytes(
                            body,
                            "image/png",
                            {
                                "X-PDF-Pages": str(count),
                                "Cache-Control": "private, max-age=3600, immutable",
                            },
                        )
                    elif parts[1] == "pdf-text":
                        self.send_json(
                            application.pdf_page_text(
                                paper_id,
                                source,
                                int(query.get("page", ["1"])[0]),
                                split,
                            ),
                            headers={
                                "Cache-Control": "private, max-age=3600, immutable"
                            },
                        )
                    elif parts[1] == "search":
                        self.send_json(
                            {
                                "results": application.search_pdf(
                                    paper_id,
                                    source,
                                    query.get("q", [""])[0],
                                    split,
                                )
                            }
                        )
                    else:
                        self.send_bytes(
                            application.review_pdf(paper_id, source, split),
                            "application/pdf",
                        )
                    return
                asset = "index.html" if parsed.path == "/" else parsed.path.lstrip("/")
                if asset not in {
                    "index.html",
                    "app.js",
                    "styles.css",
                    "comparison.html",
                    "comparison.js",
                    "comparison.css",
                    "figure-census-proposals.json",
                }:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if asset == "figure-census-proposals.json":
                    self.send_bytes(
                        (application.static_dir / asset).read_bytes(),
                        "application/json",
                        {"Cache-Control": "public, max-age=300"},
                    )
                    return
                self.send_file(application.static_dir / asset)
            except FileNotFoundError as error:
                self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            except StaleRevisionError as error:
                logger.warning("Review revision conflict: {}", error)
                self.send_json(REVISION_CONFLICT_RESPONSE, HTTPStatus.CONFLICT)
            except (ValueError, ValidationError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except PermissionError as error:
                self.send_json(
                    {"error": str(error)},
                    HTTPStatus(getattr(error, "status", HTTPStatus.FORBIDDEN)),
                )

        def do_POST(self):  # noqa: N802
            parsed = urlparse(self.path)
            parts = self.route_parts(parsed.path)
            try:
                if parsed.path == "/api/auth/login":
                    if authenticator is None or not hasattr(authenticator, "login"):
                        raise ValueError("password login is not enabled")
                    payload = self.read_json()
                    token, user = authenticator.login(
                        str(payload.get("email", "")), str(payload.get("password", ""))
                    )
                    application.ensure_authenticated_user(user)
                    self.send_json({"token": token, "user": user})
                    return
                user = self.current_user()
                if parsed.path == "/api/users":
                    self.current_user(require_admin=True)
                    self.send_json(
                        {"user": application.add_reviewer(self.read_json())},
                        HTTPStatus.CREATED,
                    )
                    return
                if parsed.path == "/api/papers/import":
                    self.current_user(require_admin=True)
                    form = self.read_multipart()

                    def binary(name: str) -> bytes:
                        value = form.get(name, b"")
                        return value if isinstance(value, bytes) else b""

                    bundle = application.import_paper(
                        str(form.get("split", "calibration")),
                        str(form.get("paper_id", "")),
                        binary("pdf"),
                        binary("extraction"),
                        supplement_bytes=binary("supplement"),
                        document_bytes=binary("document"),
                        configuration_bytes=binary("run_configuration"),
                        coverage_bytes=binary("coverage_audit"),
                        refinement_bytes=binary("refinement_audit"),
                        repair_bytes=binary("targeted_repair"),
                        enrichment_bytes=binary("enrichment"),
                        reviewer_id=user["id"],
                    )
                    self.send_json(bundle, HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/comparisons":
                    self.current_user(require_admin=True)
                    self.send_json(
                        application.create_comparison(self.read_json()),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 3 and parts[:2] == ["api", "comparison-reviews"]:
                    self.send_json(
                        application.comparisons.save(
                            parts[2], user["id"], self.read_json()
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 3 and parts[:2] == ["api", "native-utility-reviews"]:
                    self.send_json(
                        application.comparisons.save_native(
                            parts[2], user["id"], self.read_json()
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 3 and parts[:2] == [
                    "api",
                    "pairwise-preference-reviews",
                ]:
                    self.send_json(
                        application.comparisons.save_pairwise(
                            parts[2], user["id"], self.read_json()
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "mutations"]:
                    self.send_json(
                        application.mutate(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "record-merges"]:
                    self.send_json(
                        application.merge_records(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == [
                    "api",
                    "record-reclassifications",
                ]:
                    self.send_json(
                        application.reclassify_record(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "mutation-undos"]:
                    self.send_json(
                        application.undo_mutation(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "review-workbook"]:
                    form = self.read_multipart()
                    workbook = form.get("workbook", b"")
                    if not isinstance(workbook, bytes):
                        raise ValueError("review workbook is required")
                    self.send_json(
                        application.import_review_workbook(
                            parts[2],
                            parts[3],
                            workbook,
                            user["id"],
                            filename=str(form.get("filename", "review.xlsx")),
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "inventory-audits"]:
                    self.send_json(
                        application.inventory_audit(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "record-decisions"]:
                    self.send_json(
                        application.decide_record(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "reviewer-resets"]:
                    self.send_json(
                        application.reset_reviewer_state(
                            parts[2], parts[3], self.read_json(), user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                if len(parts) == 4 and parts[:2] == ["api", "stages"]:
                    payload = self.read_json()
                    if (
                        isinstance(payload, dict)
                        and payload.get("stage") == "adjudication"
                    ):
                        self.current_user(require_admin=True)
                    self.send_json(
                        application.complete_stage(
                            parts[2], parts[3], payload, user["id"]
                        ),
                        HTTPStatus.CREATED,
                    )
                    return
                self.send_error(HTTPStatus.NOT_FOUND)
            except FileNotFoundError as error:
                self.send_json({"error": str(error)}, HTTPStatus.NOT_FOUND)
            except StaleRevisionError as error:
                logger.warning("Review revision conflict: {}", error)
                self.send_json(REVISION_CONFLICT_RESPONSE, HTTPStatus.CONFLICT)
            except (ValueError, ValidationError, json.JSONDecodeError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
            except PermissionError as error:
                self.send_json(
                    {"error": str(error)},
                    HTTPStatus(getattr(error, "status", HTTPStatus.FORBIDDEN)),
                )

        def log_message(self, fmt: str, *args):
            logger.info("{} - {}", self.client_address[0], fmt % args)

    return Handler


@click.command()
@click.option(
    "--pdf-dir", type=click.Path(path_type=Path, file_okay=False), required=True
)
@click.option(
    "--ground-truth-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("review_data"),
    show_default=True,
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8765, type=int, show_default=True)
def main(pdf_dir: Path, ground_truth_dir: Path, host: str, port: int) -> None:
    """Run the collaborative study ground-truth workbench."""

    application = ReviewApplication(pdf_dir, ground_truth_dir)
    server = ThreadingHTTPServer((host, port), make_handler(application))
    logger.info("Ground-truth workbench: http://{}:{}", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping ground-truth workbench")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
