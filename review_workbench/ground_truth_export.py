"""Freeze an adjudicated review revision into deterministic benchmark artifacts."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    StudyExtraction,
    study_schema_sha256,
)
from perla_extract.study_extraction.validation import validate_study
from review_workbench.study_review import ReviewEvent, StudyReviewStore

GROUND_TRUTH_FORMAT_VERSION = 3
GROUND_TRUTH_FILENAMES = (
    "ground_truth.json",
    "seed_extraction.json",
    "review_events.json",
    "manifest.json",
)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    """Serialize JSON consistently so hashes and repeated exports are reproducible."""

    options: dict[str, Any] = {"ensure_ascii": False, "sort_keys": True}
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode()


def _sha256(value: object) -> str:
    """Identify JSON content independently of whitespace and dictionary ordering."""

    return hashlib.sha256(_json_bytes(value)).hexdigest()


class GroundTruthReview(BaseModel):
    """Freeze the human decisions that define the benchmark label boundary."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_count: int = Field(ge=1)
    reviewers: list[str]
    adjudicators: list[str] = Field(min_length=1)
    completed_stages: dict[str, list[str]]
    uncertain_record_keys: list[str]


class GroundTruthValidation(BaseModel):
    """Record the deterministic gate applied to the final reviewed revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    status: str
    counts: dict[str, Any]
    issue_count: int = Field(ge=0)


class GroundTruthManifest(BaseModel):
    """Describe exactly which reviewed revision and inputs a benchmark item contains."""

    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_format_version: int = Field(ge=1)
    study_schema_version: int = Field(ge=1)
    study_schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    paper_id: str
    split: str
    revision: int = Field(ge=1)
    evidence_version: int = Field(ge=1)
    evidence_document_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    frozen_at: str
    source_manifest: dict[str, Any]
    review: GroundTruthReview
    validation: GroundTruthValidation
    files: dict[str, str]


class GroundTruthExport(BaseModel):
    """Hold the four files needed to review or reproduce one benchmark item."""

    model_config = ConfigDict(extra="forbid", strict=True)

    ground_truth: StudyExtraction
    seed_extraction: StudyExtraction
    review_events: list[ReviewEvent]
    manifest: GroundTruthManifest

    def files(self) -> dict[str, object]:
        """Return stable public filenames without leaking mutable workbench storage."""

        return {
            "ground_truth.json": self.ground_truth.model_dump(mode="json"),
            "seed_extraction.json": self.seed_extraction.model_dump(mode="json"),
            "review_events.json": [
                event.model_dump(mode="json") for event in self.review_events
            ],
            "manifest.json": self.manifest.model_dump(mode="json"),
        }


def _evidence_blocks(document: object) -> list[EvidenceBlock]:
    """Validate the imported parser document before using it as a citation source."""

    if document is None:
        raise ValueError("ground-truth export requires an imported document.json")
    raw_blocks = document.get("blocks") if isinstance(document, dict) else document
    if not isinstance(raw_blocks, list):
        raise ValueError("document.json must contain a list of evidence blocks")
    return [EvidenceBlock.model_validate(block) for block in raw_blocks]


def build_ground_truth_export(
    store: StudyReviewStore, split: str, paper_id: str
) -> GroundTruthExport:
    """Compile one final review revision and refuse incomplete or ungrounded truth.

    Requiring adjudication to be the latest event makes the frozen revision explicit:
    any subsequent correction must be adjudicated again. Deterministic validation then
    ensures every citation and reported raw value still resolves in the supplied paper
    evidence before the item can enter a benchmark PR.
    """

    store.validate_identity(split, paper_id)
    source = store.storage.load_source(split, paper_id)
    revision = store.storage.load_revision(split, paper_id)
    document = store.load_document(split, paper_id)
    events = [ReviewEvent.model_validate(event) for event in revision.events]
    final_event = events[-1]
    if not (
        final_event.kind == "stage_complete"
        and final_event.details.get("stage") == "adjudication"
    ):
        raise ValueError("latest review revision must complete adjudication")

    truth = StudyExtraction.model_validate(revision.ground_truth)
    seed = StudyExtraction.model_validate(source.seed_extraction)
    validation = validate_study(truth, _evidence_blocks(document))
    issues = validation.get("issues", [])
    if validation.get("status") != "verified":
        reasons = "; ".join(
            f"{issue.get('path')}: {issue.get('reason')}"
            for issue in issues[:5]
            if isinstance(issue, dict)
        )
        raise ValueError(f"ground truth has unresolved evidence issues: {reasons}")

    summary = store.summary(revision.ground_truth, revision.events)
    adjudicator = final_event.reviewer_id
    decisions = summary["record_decisions"].get(adjudicator, {})
    accepted = {"verified", "uncertain"}
    if (
        sum(decision in accepted for decision in decisions.values())
        != summary["record_count"]
    ):
        raise ValueError("adjudicator must review every current record before export")

    ground_truth = truth.model_dump(mode="json")
    seed_extraction = seed.model_dump(mode="json")
    review_events = [event.model_dump(mode="json") for event in events]
    file_hashes = {
        "ground_truth.json": _sha256(ground_truth),
        "seed_extraction.json": _sha256(seed_extraction),
        "review_events.json": _sha256(review_events),
    }
    reviewers = sorted({event.reviewer_id for event in events})
    uncertain_record_keys = sorted(
        record_key
        for record_key, decision in decisions.items()
        if decision == "uncertain"
    )
    manifest = GroundTruthManifest(
        artifact_format_version=GROUND_TRUTH_FORMAT_VERSION,
        study_schema_version=STUDY_SCHEMA_VERSION,
        study_schema_sha256=study_schema_sha256(),
        paper_id=paper_id,
        split=split,
        revision=revision.revision,
        evidence_version=revision.evidence_version,
        evidence_document_sha256=_sha256(document),
        frozen_at=final_event.timestamp,
        source_manifest=source.manifest,
        review={
            "event_count": len(events),
            "reviewers": reviewers,
            "adjudicators": [adjudicator],
            "completed_stages": summary["completed_stages"],
            "uncertain_record_keys": uncertain_record_keys,
        },
        validation={
            "status": validation["status"],
            "counts": validation["counts"],
            "issue_count": len(issues),
        },
        files=file_hashes,
    )
    return GroundTruthExport(
        ground_truth=truth,
        seed_extraction=seed,
        review_events=events,
        manifest=manifest,
    )


def write_ground_truth_export(export: GroundTruthExport, output_root: Path) -> Path:
    """Publish all four files as one immutable directory or leave existing data alone.

    A complete paper directory is assembled beside its destination and renamed into
    place. An identical prior export is a no-op; any difference is a conflict that must
    be reviewed explicitly instead of being hidden behind an overwrite flag.
    """

    target = output_root.resolve() / export.manifest.split / export.manifest.paper_id
    files = export.files()
    if target.exists():
        if not target.is_dir():
            raise ValueError(f"ground-truth export target is not a directory: {target}")
        existing_names = sorted(path.name for path in target.iterdir())
        if existing_names != sorted(GROUND_TRUTH_FILENAMES):
            raise ValueError(
                f"existing export is incomplete or has extra files: {target}"
            )
        if all(
            json.loads((target / name).read_text(encoding="utf-8")) == value
            for name, value in files.items()
        ):
            return target
        raise ValueError(
            f"refusing to overwrite a different ground-truth export: {target}"
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.", dir=target.parent))
    try:
        for name, value in files.items():
            write_json_atomic(temporary / name, value)
        try:
            os.rename(temporary, target)
        except FileExistsError:
            raise ValueError(
                f"another export created the target concurrently: {target}"
            )
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def ground_truth_zip(export: GroundTruthExport) -> bytes:
    """Create a byte-for-byte stable browser download of the PR-ready files."""

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in sorted(export.files().items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, _json_bytes(value, pretty=True))
    return output.getvalue()
