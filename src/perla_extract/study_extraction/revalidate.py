"""Refresh local evidence checks without repeating expensive model calls."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import click

from .artifacts import write_json_atomic
from .models import EvidenceBlock, StudyExtraction
from .validation import validate_study


def _json(path: Path) -> object:
    """Read a required run artifact and retain its path in parse errors."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc


def revalidate_run(run_dir: Path) -> dict[str, object]:
    """Rebuild validation artifacts from the immutable extraction and document.

    Evidence policy can improve independently of model prompts. Revalidating cached
    output avoids both another paid call and the misleading implication that a newer
    local check produced the original extraction. Only derived validation counts and
    status are refreshed; model responses, usage, and scientific records stay intact.
    """

    extraction = StudyExtraction.model_validate(_json(run_dir / "extraction.json"))
    document = _json(run_dir / "document.json")
    raw_blocks = document.get("blocks") if isinstance(document, dict) else None
    if not isinstance(raw_blocks, list):
        raise ValueError(f"{run_dir / 'document.json'} has no evidence block list")
    blocks = [EvidenceBlock.model_validate(item) for item in raw_blocks]
    validation = validate_study(extraction, blocks)
    grounded_values = validation.pop("verified_values")
    report = _json(run_dir / "report.json")
    if not isinstance(report, dict):
        raise ValueError(f"{run_dir / 'report.json'} is not a JSON object")
    counts = validation["counts"]
    assert isinstance(counts, dict)
    report.update(counts)
    issues = validation["issues"]
    assert isinstance(issues, list)
    report["validation_issue_count"] = len(issues)
    if report.get("status") in {"complete", "complete_needs_review"}:
        coverage_path = run_dir / "claim_coverage_audit.json"
        coverage = _json(coverage_path) if coverage_path.is_file() else None
        coverage_complete = not isinstance(coverage, dict) or coverage.get(
            "status"
        ) == "complete"
        report["status"] = (
            "complete"
            if validation["status"] == "verified"
            and coverage_complete
            and report.get("enrichment_status") not in {"failed", "needs_review"}
            else "complete_needs_review"
        )
    write_json_atomic(run_dir / "grounded_values.json", grounded_values)
    write_json_atomic(run_dir / "validation.json", validation)
    write_json_atomic(run_dir / "report.json", report)
    return report


def revalidate_runs(run_dirs: Iterable[Path]) -> tuple[int, list[str]]:
    """Refresh every supplied run while preserving a complete failure list."""

    completed = 0
    failures: list[str] = []
    for run_dir in run_dirs:
        try:
            revalidate_run(run_dir)
            completed += 1
        except (OSError, ValueError) as exc:
            failures.append(f"{run_dir.name}: {exc}")
    return completed, failures


@click.command(context_settings={"show_default": True})
@click.option(
    "--runs-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
def main(runs_dir: Path) -> None:
    """Revalidate every completed extraction directory below RUNS_DIR."""

    candidates = sorted(
        path.parent for path in runs_dir.glob("*/extraction.json") if path.is_file()
    )
    completed, failures = revalidate_runs(candidates)
    click.echo(f"Revalidated {completed} run(s); {len(failures)} failed.")
    if failures:
        raise click.ClickException("; ".join(failures))


if __name__ == "__main__":
    main()
