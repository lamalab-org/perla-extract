"""Bind a run's configuration, prediction and accounting to the same saved attempt.

These checks detect stale or accidentally mixed artifacts. They do not authenticate
an untrusted author who can rewrite both the artifacts and their manifest.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from .artifacts import write_json_exclusive
from .evaluation import RunEfficiency

REQUIRED_FILES = ("run_configuration.json", "report.json")
OPTIONAL_FILES = ("extraction.json", "document.json")
# Execution completion is independent of validation. Callers must still inspect
# the saved issues; citation warnings do not make a completed extraction partial.
COMPLETE_OUTPUT_STATUSES = frozenset({"complete", "complete_needs_review"})


def verify_source_hashes(reference: list[str], configuration: object) -> None:
    """Require the same main paper and supplements when reference hashes exist.

    Input hashes come from the sealed run configuration, not extracted metadata.
    Absence of reference hashes cannot establish compatibility; callers retain
    that provenance limitation in their reports.
    """

    if not isinstance(configuration, dict):
        raise ValueError("Run configuration must be an object")
    if reference and sorted(reference) != sorted(
        configuration.get("source_sha256", [])
    ):
        raise ValueError("Reference source hashes differ from the run sources")


class RunManifest(BaseModel):
    """Identify an attempt, including failed attempts that produced no prediction."""

    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1)
    files: dict[str, str]


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_run_manifest(output_dir: Path, *, run_id: str | None = None) -> RunManifest:
    """Call once after all run artifacts have been written, including failure reports."""

    missing = [name for name in REQUIRED_FILES if not (output_dir / name).is_file()]
    if missing:
        raise ValueError(f"Cannot seal run without {', '.join(missing)}")
    report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("Run report must be a JSON object")
    report_run_id = report.get("run_id")
    if run_id is not None and report_run_id is not None and run_id != report_run_id:
        raise ValueError("Report and manifest run IDs must agree")
    manifest = RunManifest(
        run_id=run_id or report_run_id or str(uuid4()),
        files={
            name: file_digest(output_dir / name)
            for name in REQUIRED_FILES + OPTIONAL_FILES
            if (output_dir / name).is_file()
        },
    )
    write_json_exclusive(output_dir / "run_manifest.json", manifest.model_dump())
    return manifest


def verify_run_manifest(
    output_dir: Path, *, accounting_only: bool = False
) -> tuple[RunManifest, str]:
    """Verify saved artifacts; accounting can survive missing or damaged predictions.

    The accounting-only view still binds the original configuration and report.
    Full verification also rejects a prediction added after an unsuccessful run.
    """

    path = output_dir / "run_manifest.json"
    try:
        manifest = RunManifest.model_validate_json(path.read_bytes())
        if not set(REQUIRED_FILES).issubset(manifest.files):
            raise ValueError("Run manifest must bind configuration and accounting")
        if set(manifest.files) - set(REQUIRED_FILES + OPTIONAL_FILES):
            raise ValueError("Run manifest contains unsupported artifact names")
        names = REQUIRED_FILES if accounting_only else REQUIRED_FILES + OPTIONAL_FILES
        for name in names:
            artifact = output_dir / name
            expected = manifest.files.get(name)
            if artifact.is_file() != (expected is not None):
                raise ValueError(f"Run artifact presence differs from manifest: {name}")
            if expected is not None and file_digest(artifact) != expected:
                raise ValueError(f"Run artifact differs from manifest: {name}")
        report = json.loads((output_dir / "report.json").read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            raise ValueError("Run report must be a JSON object")
        if report.get("run_id") is not None and report["run_id"] != manifest.run_id:
            raise ValueError("Report and manifest run IDs differ")
        return manifest, file_digest(path)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot verify run {output_dir}: {exc}") from exc


def load_run_efficiency(path: Path) -> RunEfficiency:
    """Read paid-work totals without depending on the existence of a scientific score."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        usage = payload["usage"]
        budget = payload.get("budget", {})
        if not isinstance(usage, dict) or not isinstance(budget, dict):
            raise ValueError("Run usage and budget must be JSON objects")
        return RunEfficiency(
            status=payload["status"],
            live_calls=usage["live_calls"],
            cache_hits=usage["cache_hits"],
            prompt_tokens=usage["prompt_tokens"],
            completion_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
            cost_usd=usage["cost"],
            cost_sources=usage.get("cost_sources", []),
            provider_requests=budget.get("provider_requests"),
            cost_tracking_complete=budget.get("cost_tracking_complete"),
            token_tracking_complete=usage.get("token_tracking_complete"),
            elapsed_seconds=payload["elapsed_seconds"],
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid run accounting in {path}: {exc}") from exc
