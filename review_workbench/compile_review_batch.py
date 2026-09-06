"""Compile offline expert workbooks into a conservative adjudication batch.

The command does not call reviewer prose ground truth and never silently applies a
spreadsheet correction. It archives the exact workbook, validates it against the seed
that generated it, and marks only unqualified affirmative decisions as provisionally
verified. Everything else stays in an explicit adjudication queue.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.evidence import source_contains_text
from perla_extract.study_extraction.models import EvidenceBlock, StudyExtraction
from perla_extract.study_extraction.validation import validate_study
from review_workbench.spreadsheet_review import (
    WorkbookChange,
    read_review_workbook,
    read_review_workbook_feedback,
    read_review_workbook_metadata,
)
from review_workbench.study_review import RECORD_IDENTIFIERS, RECORD_LABELS

INPUT_FILE = click.Path(
    path_type=Path, exists=True, dir_okay=False, readable=True, resolve_path=True
)
INPUT_DIR = click.Path(
    path_type=Path, exists=True, file_okay=False, readable=True, resolve_path=True
)
OUTPUT_DIR = click.Path(path_type=Path, file_okay=False, resolve_path=True)
DRAFT_FORMAT_VERSION = 1


def _json(path: Path) -> object:
    """Read a JSON artifact while retaining its path in parse failures."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def _sha256(data: bytes) -> str:
    """Identify archived source bytes without depending on their filename."""

    return hashlib.sha256(data).hexdigest()


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    """Archive an exact workbook without exposing a partially written file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _run_directory(paper_id: str, roots: tuple[Path, ...]) -> Path:
    """Resolve one paper to exactly one extraction run instead of guessing recency."""

    candidates = sorted(
        {
            path.parent.resolve()
            for root in roots
            for path in root.glob(f"**/{paper_id}/extraction.json")
        }
    )
    if len(candidates) != 1:
        rendered = ", ".join(str(path) for path in candidates) or "none"
        raise ValueError(
            f"expected one extraction run for {paper_id}, found {len(candidates)}: "
            f"{rendered}"
        )
    return candidates[0]


def _plain_affirmation(note: str) -> bool:
    """Recognize only a deliberately tiny, language-neutral-enough acceptance token."""

    normalized = re.sub(r"[^a-z]+", "", note.casefold())
    return normalized == "ok"


def _record_keys(study: StudyExtraction) -> list[tuple[str, str]]:
    """List stable review keys in schema order for complete coverage accounting."""

    return [
        (collection, str(getattr(record, identifier)))
        for collection, identifier in RECORD_IDENTIFIERS.items()
        for record in getattr(study, collection)
    ]


def _load_review_compatible_study(
    payload: object,
) -> tuple[StudyExtraction, list[dict[str, str]]]:
    """Apply only lossless relationship migrations needed to read an older seed.

    A retained identifier already asserts the relationship. Updating an inconsistent
    legacy ``link_status`` to describe that same identifier changes no scientific
    content and remains visible in the draft manifest.
    """

    try:
        return StudyExtraction.model_validate(payload), []
    except ValidationError:
        if not isinstance(payload, dict):
            raise
    migrated = json.loads(json.dumps(payload))
    changes = []
    for test in migrated.get("stability_tests", []):
        if not isinstance(test, dict) or test.get("link_status") not in {
            "stability_specimen_only",
            "not_reported",
        }:
            continue
        previous = str(test.get("link_status"))
        replacement = (
            "explicit_device_link"
            if test.get("device_id")
            else "explicit_family_link"
            if test.get("family_id")
            else previous
        )
        if replacement != previous:
            test["link_status"] = replacement
            changes.append(
                {
                    "record_id": str(test.get("test_id", "")),
                    "field": "link_status",
                    "before": previous,
                    "after": replacement,
                    "reason": "legacy status contradicted an already present identifier",
                }
            )
    return StudyExtraction.model_validate(migrated), changes


def _change_assessment(change: WorkbookChange) -> dict[str, Any]:
    """Flag source-verbatim edits that do not occur in their supplied evidence."""

    requires_verbatim = change.path.endswith(("/raw_value", "/material_form_raw"))
    supported = None
    if requires_verbatim and isinstance(change.value, str):
        supported = any(
            source_contains_text(item.get("quote", ""), change.value)
            for item in change.evidence
        )
    return {
        "collection": change.collection,
        "record_id": change.record_id,
        "path": change.path,
        "proposed_value": change.value,
        "note": change.note,
        "evidence": list(change.evidence),
        "verbatim_evidence_status": (
            "supported"
            if supported is True
            else "conflict"
            if supported is False
            else "not_applicable"
        ),
        "status": "needs_adjudication",
    }


def compile_workbook(
    workbook_path: Path,
    run_roots: tuple[Path, ...],
    output_root: Path,
) -> dict[str, Any]:
    """Compile one workbook and matching model run into an auditable draft."""

    workbook_data = workbook_path.read_bytes()
    metadata = read_review_workbook_metadata(workbook_data)
    run_dir = _run_directory(metadata.paper_id, run_roots)
    extraction_path = run_dir / "extraction.json"
    document_path = run_dir / "document.json"
    study_payload = _json(extraction_path)
    document_payload = _json(document_path)
    study, compatibility_migrations = _load_review_compatible_study(study_payload)
    raw_blocks = (
        document_payload.get("blocks") if isinstance(document_payload, dict) else None
    )
    if not isinstance(raw_blocks, list):
        raise ValueError(f"{document_path} does not contain evidence blocks")
    blocks = [EvidenceBlock.model_validate(block) for block in raw_blocks]
    try:
        review = read_review_workbook(
            workbook_data,
            truth=study.model_dump(mode="json"),
            identifiers=RECORD_IDENTIFIERS,
            labels=RECORD_LABELS,
            paper_id=metadata.paper_id,
            split=metadata.split,
            revision=metadata.base_revision,
            schema_sha256=metadata.schema_sha256,
        )
        workbook_match = "exact_seed"
    except ValueError as error:
        if "older paper revision" not in str(error) and "older layout" not in str(
            error
        ):
            raise
        review = read_review_workbook_feedback(
            workbook_data, paper_id=metadata.paper_id, split=metadata.split
        )
        workbook_match = "older_seed_feedback_only"
    decisions = {(item.collection, item.record_id): item for item in review.decisions}
    known_record_keys = set(_record_keys(study))
    unmatched_decisions = [
        {
            "record_key": f"{item.collection}:{item.record_id}",
            "collection": item.collection,
            "record_id": item.record_id,
            "review_decision": item.decision,
            "reviewer_note": item.note,
            "status": "needs_record_mapping",
        }
        for item in review.decisions
        if (item.collection, item.record_id) not in known_record_keys
    ]
    assessments = []
    for collection, record_id in _record_keys(study):
        decision = decisions.get((collection, record_id))
        high_confidence = bool(
            decision
            and decision.decision == "verified"
            and _plain_affirmation(decision.note)
        )
        reason = (
            "accepted_without_caveat"
            if high_confidence
            else "not_reviewed"
            if decision is None
            else "qualified_acceptance"
            if decision.decision == "verified"
            else "reviewer_uncertain"
            if decision.decision == "uncertain"
            else "correction_required"
        )
        assessments.append(
            {
                "record_key": f"{collection}:{record_id}",
                "collection": collection,
                "record_id": record_id,
                "review_decision": decision.decision if decision else "not_reviewed",
                "reviewer_note": decision.note if decision else "",
                "provisional_status": (
                    "verified" if high_confidence else "needs_adjudication"
                ),
                "adjudication_reason": reason,
                "priority": (
                    "low"
                    if reason == "qualified_acceptance"
                    else "none"
                    if high_confidence
                    else "high"
                    if reason in {"not_reviewed", "correction_required"}
                    else "medium"
                ),
            }
        )
    corrections = [_change_assessment(change) for change in review.changes]
    paper_output = output_root / metadata.paper_id
    validation = validate_study(study, blocks)
    validation.pop("verified_values", None)
    feedback = {
        "paper_id": metadata.paper_id,
        "split": metadata.split,
        "record_assessments": assessments,
        "correction_proposals": corrections,
        "unmatched_workbook_decisions": unmatched_decisions,
        "workbook_comments": [item.__dict__ for item in review.comments],
    }
    write_json_atomic(
        paper_output / "provisional_ground_truth.json",
        study.model_dump(mode="json"),
    )
    write_json_atomic(paper_output / "adjudication.json", feedback)
    _write_bytes_atomic(paper_output / "reviewer_workbook.xlsx", workbook_data)
    manifest = {
        "draft_format_version": DRAFT_FORMAT_VERSION,
        "status": "provisional_needs_adjudication",
        "paper_id": metadata.paper_id,
        "split": metadata.split,
        "source_run": str(run_dir),
        "workbook_match": workbook_match,
        "compatibility_migrations": compatibility_migrations,
        "workbook_filename": workbook_path.name,
        "workbook_sha256": _sha256(workbook_data),
        "extraction_sha256": _sha256(extraction_path.read_bytes()),
        "document_sha256": _sha256(document_path.read_bytes()),
        "record_counts": dict(
            Counter(item["provisional_status"] for item in assessments)
        ),
        "adjudication_reason_counts": dict(
            Counter(item["adjudication_reason"] for item in assessments)
        ),
        "correction_proposal_count": len(corrections),
        "unmatched_workbook_decision_count": len(unmatched_decisions),
        "correction_evidence_conflicts": sum(
            item["verbatim_evidence_status"] == "conflict" for item in corrections
        ),
        "seed_validation": validation,
    }
    write_json_atomic(paper_output / "manifest.json", manifest)
    return manifest


@click.command(context_settings={"show_default": True})
@click.option("--workbook", "workbooks", type=INPUT_FILE, multiple=True, required=True)
@click.option("--run-root", "run_roots", type=INPUT_DIR, multiple=True, required=True)
@click.option("--output-dir", type=OUTPUT_DIR, required=True)
def main(
    workbooks: tuple[Path, ...], run_roots: tuple[Path, ...], output_dir: Path
) -> None:
    """Prepare a high-confidence subset and an explicit queue for final adjudication."""

    try:
        manifests = [
            compile_workbook(workbook, run_roots, output_dir) for workbook in workbooks
        ]
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    write_json_atomic(
        output_dir / "batch_summary.json",
        {
            "draft_format_version": DRAFT_FORMAT_VERSION,
            "paper_count": len(manifests),
            "verified_records": sum(
                item["record_counts"].get("verified", 0) for item in manifests
            ),
            "records_needing_adjudication": sum(
                item["record_counts"].get("needs_adjudication", 0) for item in manifests
            ),
            "correction_proposals": sum(
                item["correction_proposal_count"] for item in manifests
            ),
            "unmatched_workbook_decisions": sum(
                item["unmatched_workbook_decision_count"] for item in manifests
            ),
            "correction_evidence_conflicts": sum(
                item["correction_evidence_conflicts"] for item in manifests
            ),
            "papers": manifests,
        },
    )
    click.echo(str(output_dir))


if __name__ == "__main__":
    main()
