#!/usr/bin/env python3
"""Import or explicitly refresh validated runs in the deployed review dataset."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import click
from loguru import logger

from perla_extract.study_extraction.cohort import CohortManifest
from perla_extract.study_extraction.models import StudyExtraction
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


def _sha256(path: Path) -> str:
    """Record the exact local artifact promoted by an administrative refresh."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _refresh_run(
    application: Any,
    run_dir: Path,
    split: str,
    reviewer_id: str,
    *,
    apply: bool = True,
) -> bool:
    """Promote a run and its matching parser document as one audited revision."""

    extraction_path = run_dir / "extraction.json"
    document_path = run_dir / "document.json"
    extraction_payload = json.loads(extraction_path.read_text(encoding="utf-8"))
    if isinstance(extraction_payload, dict) and isinstance(
        extraction_payload.get("extraction"), dict
    ):
        extraction_payload = extraction_payload["extraction"]
    extraction = StudyExtraction.model_validate(extraction_payload)
    document = json.loads(document_path.read_text(encoding="utf-8"))
    current = application.store.storage.load_revision(split, run_dir.name)
    if (
        extraction.model_dump(mode="json") == current.ground_truth
        and document == application.store.load_document(split, run_dir.name)
    ):
        return False
    if not apply:
        return True
    application.store.refresh_ground_truth(
        split,
        run_dir.name,
        extraction,
        document=document,
        base_revision=current.revision,
        reviewer_id=reviewer_id,
        reason="Promote a newer validated extraction and its evidence document.",
        provenance={
            "batch": run_dir.parent.name,
            "extraction_sha256": _sha256(extraction_path),
            "document_sha256": _sha256(document_path),
            "validation_sha256": _sha256(run_dir / "validation.json"),
            "run_configuration_sha256": _sha256(
                run_dir / "run_configuration.json"
            ),
        },
    )
    return True


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
@click.option(
    "--refresh-existing",
    is_flag=True,
    help="Append validated replacements for included papers that already exist.",
)
@click.option("--dry-run", is_flag=True)
def main(
    manifest_path: Path,
    runs_dir: Path,
    pdf_dir: Path,
    env_file: Path,
    split: str,
    reviewer_id: str,
    refresh_existing: bool,
    dry_run: bool,
) -> None:
    """Add absent papers and optionally append audited replacement revisions."""

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
    refreshed = 0
    unchanged = 0
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
    if refresh_existing:
        for run_dir in (path for path in run_dirs if path.name in existing):
            try:
                revalidate_run(run_dir)
                _admissible(run_dir)
                if _refresh_run(
                    review_application,
                    run_dir,
                    split,
                    reviewer_id,
                    apply=not dry_run,
                ):
                    refreshed += 1
                else:
                    unchanged += 1
            except (OSError, ValueError, click.ClickException) as exc:
                failures.append(f"{run_dir.name}: {exc}")
                logger.error("Cannot refresh {}: {}", run_dir.name, exc)
    action = "Would import" if dry_run else "Imported"
    refresh_action = "would refresh" if dry_run else "refreshed"
    click.echo(
        f"{action} {imported} missing paper(s); {refresh_action} {refreshed} existing "
        f"paper(s); {unchanged} already current; {len(failures)} failed admission."
    )
    if failures:
        raise click.ClickException("; ".join(failures))


if __name__ == "__main__":
    main()
