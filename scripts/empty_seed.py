#!/usr/bin/env python3
"""Open parsed papers in the review workbench with an empty draft, for annotation from zero.

Takes the folders written by ``perla-extract --dry-run`` (parse only, no model call)
and imports each one into the workbench with an empty ``StudyExtraction``: the paper
DOI is filled from the folder name, every record list is empty. The annotator then
builds all records from the paper with "Add missing record".

Nothing is written into the parse folders and no model-run files are faked: the
import goes through the workbench's own validation (``ReviewApplication.import_paper``)
and the run configuration records ``annotation_seed: empty``. A paper that is
already in the workbench is skipped, never replaced.

From the repository root:

  uv run python scripts/empty_seed.py annotation/seeds \\
    --pdf-dir papers/test --review-data review_data/annotator-A --split test
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

from perla_extract.study_extraction.models import StudyExtraction  # noqa: E402
from review_workbench.server import ReviewApplication  # noqa: E402

READER_ID = "empty-seed"


def empty_extraction(paper_id: str) -> dict:
    """An empty, schema-valid study; only the DOI (from the paper ID) is known."""

    study = StudyExtraction(
        paper={"title": None, "doi": paper_id.replace("--", "/")},
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    return study.model_dump(mode="json")


def source_pdf(configured: str | None, pdf_dir: Path, fallback: str) -> Path | None:
    """The PDF recorded by the parse, or the same file name in --pdf-dir."""

    if configured is None:
        return None
    original = Path(configured)
    for candidate in (original, pdf_dir / original.name, pdf_dir / fallback):
        if candidate.is_file():
            return candidate
    raise click.ClickException(f"PDF not found: {configured} (also looked in {pdf_dir})")


def check_parse(run_dir: Path) -> dict:
    """Accept only a finished parse-only run; refuse model runs and broken parses."""

    for name in ("document.json", "run_configuration.json", "report.json"):
        if not (run_dir / name).is_file():
            raise click.ClickException(
                f"{run_dir.name}: {name} is missing; did the --dry-run parse finish?"
            )
    if (run_dir / "extraction.json").exists():
        raise click.ClickException(
            f"{run_dir.name}: contains a model extraction; use review_workbench/import_runs.py"
        )
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    if report.get("status") != "dry_run":
        raise click.ClickException(
            f"{run_dir.name}: parse status is {report.get('status')!r}, expected 'dry_run'"
        )
    document = json.loads((run_dir / "document.json").read_text(encoding="utf-8"))
    if not document.get("blocks"):
        raise click.ClickException(f"{run_dir.name}: the parsed document has no text blocks")
    return json.loads((run_dir / "run_configuration.json").read_text(encoding="utf-8"))


@click.command()
@click.argument("seeds_dir", type=click.Path(path_type=Path, exists=True, file_okay=False))
@click.option("--pdf-dir", type=click.Path(path_type=Path, exists=True, file_okay=False), required=True)
@click.option("--review-data", type=click.Path(path_type=Path, file_okay=False), required=True)
@click.option("--split", type=click.Choice(["calibration", "dev", "test"]), default="test", show_default=True)
def main(seeds_dir: Path, pdf_dir: Path, review_data: Path, split: str) -> None:
    """Import every parse folder in SEEDS_DIR with an empty draft."""

    run_dirs = sorted(path for path in seeds_dir.iterdir() if path.is_dir())
    if not run_dirs:
        raise click.ClickException(f"no parse folders under {seeds_dir}")
    app = ReviewApplication(pdf_dir, review_data)
    imported = skipped = 0
    for run_dir in run_dirs:
        paper_id = run_dir.name
        if (review_data / "seeds" / split / f"{paper_id}.json").exists():
            click.echo(f"skip {paper_id}: already in {review_data} ({split})")
            skipped += 1
            continue
        configuration = check_parse(run_dir)
        main_pdf = source_pdf(configuration.get("pdf"), pdf_dir, f"{paper_id}.pdf")
        if main_pdf is None:
            raise click.ClickException(f"{paper_id}: the parse recorded no main PDF")
        supplement = source_pdf(configuration.get("supplement"), pdf_dir, f"{paper_id}-SI.pdf")
        configuration["annotation_seed"] = {"kind": "empty", "created_by": "scripts/empty_seed.py"}
        app.import_paper(
            split,
            paper_id,
            main_pdf.read_bytes(),
            json.dumps(empty_extraction(paper_id)).encode(),
            supplement_bytes=supplement.read_bytes() if supplement else b"",
            document_bytes=(run_dir / "document.json").read_bytes(),
            configuration_bytes=json.dumps(configuration).encode(),
            reviewer_id=READER_ID,
        )
        click.echo(f"imported {paper_id} ({'main + SI' if supplement else 'main only'})")
        imported += 1
    click.echo(f"{imported} imported, {skipped} skipped, into {review_data} ({split})")


if __name__ == "__main__":
    main()
