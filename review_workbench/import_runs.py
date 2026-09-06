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


def _read(path: Path | None, *, required: bool = True) -> bytes | None:
    """Read an import artifact while making missing required provenance explicit."""

    if path is not None and path.is_file():
        return path.read_bytes()
    if required:
        raise click.ClickException(f"required artifact is missing: {path}")
    return None


def _required(path: Path | None) -> bytes:
    """Read a mandatory artifact and expose a non-optional type to callers."""

    value = _read(path)
    assert value is not None
    return value


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


def _source_path(
    configuration: dict[str, object],
    key: str,
    *,
    paper_id: str,
    pdf_dir: Path,
) -> Path | None:
    """Find a source PDF after an extraction run has been moved or shared.

    ``run_configuration.json`` records the original source path. Review batches are
    often copied to another machine, so the import directory may contain only the
    original basename or the historical paper-ID filename. Trying those explicit
    alternatives preserves provenance without requiring one rigid directory layout.
    """

    configured = configuration.get(key)
    if configured is None:
        return None
    original = Path(str(configured))
    conventional = (
        f"{paper_id}.pdf" if key == "pdf" else f"{paper_id}.supplement.pdf"
    )
    candidates = (original, pdf_dir / original.name, pdf_dir / conventional)
    return next((path for path in candidates if path.is_file()), candidates[-1])


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
    configuration_path = run_dir / "run_configuration.json"
    configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
    main = _source_path(
        configuration, "pdf", paper_id=paper_id, pdf_dir=pdf_dir
    )
    supplement = _source_path(
        configuration, "supplement", paper_id=paper_id, pdf_dir=pdf_dir
    )
    if main is None:
        raise click.ClickException(f"{paper_id}: run configuration has no main PDF")
    app.import_paper(
        split,
        paper_id,
        _required(main),
        _required(run_dir / "extraction.json"),
        supplement_bytes=_read(supplement, required=False) or b"",
        document_bytes=_required(run_dir / "document.json"),
        configuration_bytes=_required(configuration_path),
        coverage_bytes=(
            _read(run_dir / "claim_coverage_audit.json", required=False) or b""
        ),
        refinement_bytes=(
            _read(run_dir / "refinement_audit.json", required=False) or b""
        ),
        repair_bytes=_read(run_dir / "targeted_repair.json", required=False) or b"",
        enrichment_bytes=_read(run_dir / "enrichment.json", required=False) or b"",
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
    default="dev",
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
