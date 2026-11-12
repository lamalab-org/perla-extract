"""Read the current reference format and verify its immutable content identifiers.

This module is shared by scoring, release audits and experiment tools. It raises
ValueError with artifact paths; user interfaces decide how to display those errors.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from pydantic import ValidationError

from .models import StudyExtraction, compatible_study_schema

if TYPE_CHECKING:
    from .evaluation import BenchmarkProvenance

GROUND_TRUTH_FORMAT_VERSION: Final[Literal[4]] = 4


def read_json(path: Path) -> object:
    """Load one UTF-8 JSON artifact and keep its path in parse errors."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def reference_content_digest(value: object) -> str:
    """Reproduce the reference export hash for loaders, audits and experiment plans.

    The trailing newline is part of this public serialization contract. Do not
    substitute the scorer's parsed-study hash or a file-byte hash for this digest.
    """

    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_study(payload: object, path: Path) -> StudyExtraction:
    """Convert schema failures into concise CLI errors that retain the source path."""

    try:
        return StudyExtraction.model_validate(payload)
    except ValidationError as exc:
        raise ValueError(f"invalid StudyExtraction in {path}: {exc}") from exc


def load_reference(
    path: Path,
) -> tuple[StudyExtraction, BenchmarkProvenance | None]:
    """Load truth and verify the schema/content identifiers needed for scoring.

    This gate binds results to declared inputs; it does not authenticate expert
    judgments or revalidate the archived reference evidence. Those checks belong
    to adjudication and export. Bare JSON deliberately has no release provenance.
    """

    if path.is_file():
        return load_study(read_json(path), path), None
    truth_path = path / "ground_truth.json"
    manifest_path = path / "manifest.json"
    if not truth_path.is_file() or not manifest_path.is_file():
        raise ValueError(f"{path} must contain ground_truth.json and manifest.json")
    payload = read_json(truth_path)
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise ValueError(f"{manifest_path} is not a JSON object")
    if manifest.get("artifact_format_version") != GROUND_TRUTH_FORMAT_VERSION:
        raise ValueError(
            "ground-truth artifact format is unsupported; regenerate or migrate it"
        )
    evidence_version = manifest.get("evidence_version")
    evidence_digest = manifest.get("evidence_document_sha256")
    if (
        type(evidence_version) is not int
        or evidence_version < 1
        or not isinstance(evidence_digest, str)
        or not re.fullmatch(r"[0-9a-f]{64}", evidence_digest)
    ):
        raise ValueError(
            "format-4 reference requires an evidence version and document hash"
        )
    expected_schema = manifest.get("study_schema_sha256")
    if not compatible_study_schema(expected_schema):
        raise ValueError(
            "ground-truth schema hash differs from the installed evaluator"
        )
    files = manifest.get("files")
    expected_truth = files.get("ground_truth.json") if isinstance(files, dict) else None
    if expected_truth != reference_content_digest(payload):
        raise ValueError("ground_truth.json does not match its manifest hash")
    review = manifest.get("review")
    if not isinstance(review, dict):
        raise ValueError("manifest review must be a JSON object")
    uncertain = review.get("uncertain_record_keys")
    if not isinstance(uncertain, list) or not all(
        isinstance(item, str) for item in uncertain
    ):
        raise ValueError("manifest uncertain_record_keys must be a string list")
    if uncertain:
        raise ValueError(
            "reference contains unresolved records; complete adjudication and export "
            "a finalized reference before scoring"
        )
    # Public manifests redact reviewer IDs but retain stage names. Private
    # exports retain the stage-to-reviewer mapping. Neither is authentication;
    # both must at least declare the final gate that format 4 promises.
    stages = review.get("completed_stage_names", review.get("completed_stages"))
    validation = manifest.get("validation")
    if (
        not isinstance(stages, (dict, list))
        or "adjudication" not in stages
        or (isinstance(stages, dict) and not stages["adjudication"])
        or not isinstance(validation, dict)
        or validation.get("status") != "verified"
        or type(validation.get("issue_count")) is not int
        or validation["issue_count"] != 0
    ):
        raise ValueError(
            "format-4 reference requires completed adjudication and verified "
            "validation with zero issues; export the finalized revision again"
        )
    paper_id = manifest.get("paper_id")
    split = manifest.get("split")
    source_manifest = manifest.get("source_manifest")
    if not isinstance(paper_id, str) or not isinstance(split, str):
        raise ValueError("manifest paper_id and split must be strings")
    if not isinstance(source_manifest, dict):
        raise ValueError("manifest source_manifest must be a JSON object")
    source_hashes = source_manifest.get("source_sha256", [])
    if not isinstance(source_hashes, list) or not all(
        isinstance(item, str) for item in source_hashes
    ):
        raise ValueError("source_manifest source_sha256 must be a string list")
    from .evaluation import BenchmarkProvenance

    return (
        load_study(payload, truth_path),
        BenchmarkProvenance(
            paper_id=paper_id,
            split=split,
            ground_truth_sha256=expected_truth,
            source_manifest_sha256=reference_content_digest(source_manifest),
            source_sha256=source_hashes,
            evidence_version=evidence_version,
            evidence_document_sha256=evidence_digest,
        ),
    )
