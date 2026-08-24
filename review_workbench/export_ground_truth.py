#!/usr/bin/env python3
"""Export an adjudicated workbench revision for a benchmark data PR."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from loguru import logger

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO_ROOT), str(REPO_ROOT / "src")]

from review_workbench.ground_truth_export import (  # noqa: E402
    build_ground_truth_export,
    write_ground_truth_export,
)
from review_workbench.study_review import StudyReviewStore  # noqa: E402


@click.command()
@click.option(
    "--review-data",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("review_data"),
    show_default=True,
)
@click.option(
    "--output-root",
    type=click.Path(path_type=Path, file_okay=False),
    default=Path("data/study_extraction/ground_truth/v1"),
    show_default=True,
)
@click.option("--split", type=click.Choice(["calibration", "dev", "test"]), required=True)
@click.option("--paper-id", required=True)
def main(review_data: Path, output_root: Path, split: str, paper_id: str) -> None:
    """Freeze one adjudicated paper into the tracked benchmark directory."""

    logger.info("Validating adjudicated revision for {}", paper_id)
    try:
        export = build_ground_truth_export(
            StudyReviewStore(review_data), split, paper_id
        )
        target = write_ground_truth_export(export, output_root)
    except (FileNotFoundError, ValueError) as error:
        raise click.ClickException(str(error)) from error
    logger.info("Ground truth is ready for review at {}", target)
    click.echo(target)


if __name__ == "__main__":
    main()
