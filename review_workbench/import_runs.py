#!/usr/bin/env python3
"""Import validated extraction-run directories as immutable review seeds."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

from review_workbench.server import ReviewApplication  # noqa: E402


def _read(path: Path, *, required: bool = True) -> bytes | None:
    """Read an import artifact while making missing required provenance explicit."""

    if path.is_file():
        return path.read_bytes()
    if required:
        raise click.ClickException(f"required artifact is missing: {path}")
    return None


def _admissible(run_dir: Path) -> None:
    """Reject a run that has not passed the evidence checks required for review."""

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    validation = json.loads(
        (run_dir / "validation.json").read_text(encoding="utf-8")
    )
    if report.get("status") not in {"complete", "complete_needs_review"}:
        raise click.ClickException(
            f"{run_dir.name}: extraction status is {report.get('status')!r}"
        )
    if validation.get("status") != "verified" or validation.get("issues"):
        raise click.ClickException(
            f"{run_dir.name}: unresolved evidence-validation issues"
        )


def import_run(
    app: ReviewApplication,
    *,
    run_dir: Path,
    pdf_dir: Path,
    split: str,
    reviewer_id: str,
) -> None:
    """Create one review item without replacing an existing immutable seed."""

    _admissible(run_dir)
    paper_id = run_dir.name
    main = pdf_dir / f"{paper_id}.pdf"
    supplement = pdf_dir / f"{paper_id}.supplement.pdf"
    app.import_paper(
        split,
        paper_id,
        _read(main),
        _read(run_dir / "extraction.json"),
        supplement_bytes=_read(supplement, required=False),
        document_bytes=_read(run_dir / "document.json"),
        configuration_bytes=_read(run_dir / "run_configuration.json"),
        coverage_bytes=_read(run_dir / "coverage_audit.json", required=False),
        refinement_bytes=_read(run_dir / "refinement_audit.json", required=False),
        repair_bytes=_read(run_dir / "targeted_repair.json", required=False),
        enrichment_bytes=_read(run_dir / "enrichment.json", required=False),
        reviewer_id=reviewer_id,
    )
    logger.info("Imported {} into {}", paper_id, split)


@click.command()
@click.option(
    "--runs-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--pdf-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--review-data",
    type=click.Path(path_type=Path, file_okay=False),
    required=True,
)
@click.option(
    "--split",
    type=click.Choice(["calibration", "dev", "test"]),
    default="calibration",
    show_default=True,
)
@click.option("--reviewer-id", default="seed-import", show_default=True)
def main(
    runs_dir: Path,
    pdf_dir: Path,
    review_data: Path,
    split: str,
    reviewer_id: str,
) -> None:
    """Import every immediate run directory after validating its final artifacts."""

    run_dirs = sorted(path for path in runs_dir.iterdir() if path.is_dir())
    if not run_dirs:
        raise click.ClickException(f"no extraction runs found under {runs_dir}")
    app = ReviewApplication(pdf_dir, review_data)
    for run_dir in run_dirs:
        import_run(
            app,
            run_dir=run_dir,
            pdf_dir=pdf_dir,
            split=split,
            reviewer_id=reviewer_id,
        )
    click.echo(f"Imported {len(run_dirs)} {split} seed(s) into {review_data}")


if __name__ == "__main__":
    main()
