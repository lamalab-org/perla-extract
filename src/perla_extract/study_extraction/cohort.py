"""Run one frozen extraction configuration over a review cohort."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import click
from pydantic import BaseModel, ConfigDict, Field

from .artifacts import write_json_atomic
from .cli import extract_study
from .logging import configure_logging, logger
from .models import study_schema_sha256
from .workflow import prompt_sha256


class CohortPaper(BaseModel):
    """Identify one paper and whether it should receive an independent second review."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_id: str = Field(min_length=1, max_length=240)
    double_review: bool = False


class CohortManifest(BaseModel):
    """Freeze the scientific cohort and model settings before annotation begins."""

    model_config = ConfigDict(extra="forbid", strict=True)

    format_version: Literal[1]
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    split: Literal["calibration", "dev", "test"]
    model: str = Field(min_length=1, max_length=300)
    reasoning_effort: Literal["omit", "none", "minimal", "low", "medium", "high"] = (
        "omit"
    )
    parser: Literal["docling", "pymupdf"] = "docling"
    claim_recall_passes: int = Field(default=2, ge=1, le=3)
    max_model_calls_per_paper: int | None = Field(default=14, ge=1)
    max_cost_usd_per_paper: float | None = Field(default=None, gt=0)
    papers: list[CohortPaper] = Field(min_length=1)


def _load_manifest(path: Path) -> CohortManifest:
    """Validate cohort membership and reject ambiguous duplicate paper IDs."""

    manifest = CohortManifest.model_validate_json(path.read_text(encoding="utf-8"))
    identifiers = [paper.paper_id for paper in manifest.papers]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("cohort manifest contains duplicate paper IDs")
    return manifest


def _supplement_path(directory: Path, paper_id: str) -> Path | None:
    """Resolve the two documented SI filenames without guessing from other PDFs."""

    candidates = (
        directory / f"{paper_id}-SI.pdf",
        directory / f"{paper_id}.supplement.pdf",
    )
    return next((path for path in candidates if path.is_file()), None)


def _completed_run_matches(run_dir: Path, manifest: CohortManifest) -> bool:
    """Resume only when the existing seed used the current schema, prompt, and model."""

    try:
        report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        configuration = json.loads(
            (run_dir / "run_configuration.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return (
        report.get("status") in {"complete", "complete_needs_review"}
        and configuration.get("model") == manifest.model
        and configuration.get("parser") == manifest.parser
        and configuration.get("claim_recall_passes")
        == manifest.claim_recall_passes
        and configuration.get("reasoning_effort")
        == (None if manifest.reasoning_effort == "omit" else manifest.reasoning_effort)
        and configuration.get("max_model_calls")
        == manifest.max_model_calls_per_paper
        and configuration.get("max_cost_usd") == manifest.max_cost_usd_per_paper
        and configuration.get("schema_sha256") == study_schema_sha256()
        and configuration.get("prompt_sha256") == prompt_sha256()
    )


@click.command(context_settings={"show_default": True})
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--pdf-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--supplement-dir",
    type=click.Path(path_type=Path, exists=True, file_okay=False),
    required=True,
)
@click.option(
    "--output-dir", type=click.Path(path_type=Path, file_okay=False), required=True
)
@click.option("--env-file", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--rerun", is_flag=True, help="Regenerate matching completed runs.")
@click.option(
    "--limit",
    type=click.IntRange(min=1),
    help="Run only the first N pending papers for a controlled pilot.",
)
@click.option("--shard-count", type=click.IntRange(min=1), default=1)
@click.option("--shard-index", type=click.IntRange(min=0), default=0)
@click.option(
    "--log-level",
    type=click.Choice(("DEBUG", "INFO", "WARNING", "ERROR"), case_sensitive=False),
    default="INFO",
)
def main(
    manifest_path: Path,
    pdf_dir: Path,
    supplement_dir: Path,
    output_dir: Path,
    env_file: Path | None,
    rerun: bool,
    limit: int | None,
    shard_count: int,
    shard_index: int,
    log_level: str,
) -> None:
    """Generate review seeds while preserving failures and resumability."""

    configure_logging(level=log_level)
    try:
        manifest = _load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if shard_index >= shard_count:
        raise click.ClickException("shard-index must be smaller than shard-count")
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = manifest.papers[shard_index::shard_count]
    incomplete = [
        paper
        for paper in selected
        if rerun or not _completed_run_matches(output_dir / paper.paper_id, manifest)
    ]
    pending = incomplete
    if limit is not None:
        pending = pending[:limit]
    logger.info(
        "Cohort {} contains {} papers; {} require extraction",
        manifest.name,
        len(selected),
        len(pending),
    )
    audit_path = (
        output_dir / "cohort_run.json"
        if shard_count == 1
        else output_dir / f"cohort_run.shard-{shard_index + 1}-of-{shard_count}.json"
    )
    pending_ids = {paper.paper_id for paper in pending}
    incomplete_ids = {paper.paper_id for paper in incomplete}
    results: list[dict[str, object]] = [
        {
            "paper_id": paper.paper_id,
            "status": (
                "pending"
                if paper.paper_id in pending_ids
                else "deferred"
                if paper.paper_id in incomplete_ids
                else "already_complete"
            ),
        }
        for paper in selected
    ]

    def write_audit() -> None:
        """Persist the whole shard state, including skipped and unfinished papers."""

        write_json_atomic(
            audit_path,
            {
                "manifest": manifest.model_dump(mode="json"),
                "shard_count": shard_count,
                "shard_index": shard_index,
                "selected_paper_count": len(selected),
                "pending_paper_count": len(pending),
                "results": results,
            },
        )

    write_audit()
    for index, paper in enumerate(pending, start=1):
        result_index = next(
            index
            for index, item in enumerate(results)
            if item["paper_id"] == paper.paper_id
        )
        main_pdf = pdf_dir / f"{paper.paper_id}.pdf"
        supplement = _supplement_path(supplement_dir, paper.paper_id)
        if not main_pdf.is_file():
            results[result_index] = {
                "paper_id": paper.paper_id,
                "status": "failed",
                "error": f"missing main PDF: {main_pdf}",
            }
            logger.error("[{}/{}] {} has no main PDF", index, len(pending), paper.paper_id)
            write_audit()
            continue
        logger.info(
            "[{}/{}] Extracting {}{}",
            index,
            len(pending),
            paper.paper_id,
            " with SI" if supplement else " without separate SI",
        )
        try:
            report = extract_study(
                pdf=main_pdf,
                supplement=supplement,
                output_dir=output_dir / paper.paper_id,
                model=manifest.model,
                claim_model=manifest.model,
                enrichment_model=manifest.model,
                refinement_model=manifest.model,
                repair_model=manifest.model,
                reasoning_effort=manifest.reasoning_effort,
                claim_recall_passes=manifest.claim_recall_passes,
                parser=manifest.parser,
                max_model_calls=manifest.max_model_calls_per_paper,
                max_cost_usd=manifest.max_cost_usd_per_paper,
                env_file=env_file,
            )
            results[result_index] = {"paper_id": paper.paper_id, **report}
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("Extraction failed for {}", paper.paper_id)
            results[result_index] = {
                "paper_id": paper.paper_id,
                "status": "failed",
                "error": str(exc),
            }
        write_audit()
    failed = [item for item in results if item.get("status") == "failed"]
    click.echo(
        f"Processed {len(pending)} paper(s); {len(failed)} failed. "
        f"Audit: {audit_path}"
    )
    if failed:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
