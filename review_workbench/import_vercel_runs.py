#!/usr/bin/env python3
"""Import missing validated run directories into the deployed review dataset."""

from __future__ import annotations

import os
from pathlib import Path

import click
from loguru import logger

from perla_extract.study_extraction.cohort import CohortManifest
from perla_extract.study_extraction.revalidate import revalidate_run
from review_workbench.import_runs import _admissible, import_run


def _load_env(path: Path) -> None:
    """Load deployment secrets before importing the Vercel application module."""

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@click.command(context_settings={"show_default": True})
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
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
    "--env-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--split",
    type=click.Choice(["calibration", "dev", "test"]),
    default="dev",
)
@click.option("--reviewer-id", default="seed-import")
@click.option("--dry-run", is_flag=True)
def main(
    manifest_path: Path,
    runs_dir: Path,
    pdf_dir: Path,
    env_file: Path,
    split: str,
    reviewer_id: str,
    dry_run: bool,
) -> None:
    """Add only absent papers and leave every existing seed and revision untouched."""

    _load_env(env_file)
    from review_workbench.api.index import review_application

    manifest = CohortManifest.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    if manifest.split != split:
        raise click.ClickException(
            f"manifest split is {manifest.split!r}, not requested split {split!r}"
        )
    existing = set(review_application.store.storage.list_paper_ids(split))
    run_dirs = [runs_dir / paper.paper_id for paper in manifest.papers]
    missing = [path for path in run_dirs if path.name not in existing]
    logger.info(
        "Found {} run candidate(s); {} already exist and {} are missing",
        len(run_dirs),
        len(run_dirs) - len(missing),
        len(missing),
    )
    imported = 0
    failures: list[str] = []
    for run_dir in missing:
        try:
            revalidate_run(run_dir)
            _admissible(run_dir)
            if not dry_run:
                import_run(
                    review_application,
                    run_dir=run_dir,
                    pdf_dir=pdf_dir,
                    split=split,
                    reviewer_id=reviewer_id,
                )
            imported += 1
        except (OSError, ValueError, click.ClickException) as exc:
            failures.append(f"{run_dir.name}: {exc}")
            logger.error("Cannot import {}: {}", run_dir.name, exc)
    action = "Would import" if dry_run else "Imported"
    click.echo(
        f"{action} {imported} missing paper(s); preserved {len(existing)} existing "
        f"paper(s); {len(failures)} failed admission."
    )
    if failures:
        raise click.ClickException("; ".join(failures))


if __name__ == "__main__":
    main()
