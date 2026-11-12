"""Run one frozen extraction configuration over a review cohort."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import click
from pydantic import BaseModel, ConfigDict, Field

from .artifacts import write_json_atomic
from .cli import extract_study
from .configuration import (
    ExtractionConfig,
    default,
    implementation_sha256,
    load_settings,
    resolve_config,
    runtime_versions,
)
from .logging import configure_logging, logger
from .models import (
    EvidenceBlock,
    StudyExtraction,
    study_schema_prose_sha256,
    study_schema_sha256,
)
from .run_artifacts import verify_run_manifest
from .source import _parser_dependency_versions, _parser_implementation_sha256
from .workflow import prompt_sha256


class CohortPaper(BaseModel):
    """Identify one paper and whether it should receive an independent second review."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_id: str = Field(min_length=1, max_length=240)
    double_review: bool = False


class CohortExclusion(BaseModel):
    """Retain why a considered paper is outside the scientific cohort."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_id: str = Field(min_length=1, max_length=240)
    reason: str = Field(min_length=1, max_length=500)


class CohortManifest(BaseModel):
    """Freeze the scientific cohort and model settings before annotation begins."""

    model_config = ConfigDict(extra="forbid", strict=True)

    format_version: Literal[1]
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=1000)
    split: Literal["calibration", "dev", "test"]
    model: str | None = Field(default=None, min_length=1, max_length=300)
    reasoning_effort: Literal["omit", "none", "minimal", "low", "medium", "high"] = (
        "omit"
    )
    parser: Literal["docling", "pymupdf"] = default("parser")
    claim_recall_passes: int = Field(default=default("claim_recall_passes"), ge=1, le=3)
    max_model_calls_per_paper: int | None = Field(default=None, ge=1)
    max_cost_usd_per_paper: float | None = Field(default=None, gt=0)
    papers: list[CohortPaper] = Field(min_length=1)
    exclusions: list[CohortExclusion] = Field(default_factory=list)


def _load_manifest(path: Path) -> CohortManifest:
    """Validate cohort membership and reject ambiguous duplicate paper IDs."""

    manifest = CohortManifest.model_validate_json(path.read_text(encoding="utf-8"))
    identifiers = [paper.paper_id for paper in manifest.papers]
    if any(name in {".", ".."} or "/" in name or "\\" in name for name in identifiers):
        raise ValueError("paper IDs must be single filename components")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("cohort manifest contains duplicate paper IDs")
    excluded = [paper.paper_id for paper in manifest.exclusions]
    if len(excluded) != len(set(excluded)):
        raise ValueError("cohort manifest contains duplicate exclusions")
    overlap = sorted(set(identifiers) & set(excluded))
    if overlap:
        raise ValueError(
            f"cohort papers also appear as exclusions: {', '.join(overlap)}"
        )
    return manifest


def _supplement_path(directory: Path, paper_id: str) -> Path | None:
    """Resolve the two documented SI filenames without guessing from other PDFs."""

    candidates = (
        directory / f"{paper_id}-SI.pdf",
        directory / f"{paper_id}.supplement.pdf",
    )
    return next((path for path in candidates if path.is_file()), None)


def _paper_config(
    manifest: CohortManifest,
    pdf: Path,
    supplement: Path | None,
    run_dir: Path,
    config_file: Path | None = None,
) -> ExtractionConfig:
    """Apply the shared TOML configuration to each paper's fixed input roles."""
    settings: dict[str, Any] = {
        "model": manifest.model or default("model"),
        "parser": manifest.parser,
        "claim_recall_passes": manifest.claim_recall_passes,
        "reasoning_effort": manifest.reasoning_effort,
        "max_model_calls": manifest.max_model_calls_per_paper,
        "max_cost_usd": manifest.max_cost_usd_per_paper,
    }
    settings.update(load_settings(config_file))
    settings.update(pdf=pdf, supplement=supplement, output_dir=run_dir)
    return resolve_config(**settings)


def _completed_run_matches(
    run_dir: Path,
    manifest: CohortManifest,
    pdf: Path | None = None,
    supplement: Path | None = None,
    config_file: Path | None = None,
) -> bool:
    """Reuse only sealed, valid outputs for the current sources and effective method."""
    if pdf is None or not pdf.is_file():
        return False
    try:
        verify_run_manifest(run_dir)
        report = json.loads((run_dir / "report.json").read_text())
        configuration = json.loads((run_dir / "run_configuration.json").read_text())
        StudyExtraction.model_validate_json((run_dir / "extraction.json").read_bytes())
        document = json.loads((run_dir / "document.json").read_text())
        for block in document["blocks"]:
            EvidenceBlock.model_validate(block)
        wanted = _paper_config(manifest, pdf, supplement, run_dir, config_file)
        sources = {"main": hashlib.sha256(pdf.read_bytes()).hexdigest()}
        if supplement is not None:
            sources["supplement"] = hashlib.sha256(supplement.read_bytes()).hexdigest()
        parsing = [event for event in report["source_parsing"] if "source" in event]
        recorded = {event["source"]: event["source_sha256"] for event in parsing}
        parser_versions = _parser_dependency_versions(wanted.parser)
        return (
            report.get("status") in {"complete", "complete_needs_review"}
            and configuration.get("scientific_settings") == wanted.scientific_settings()
            and configuration.get("schema_sha256") == study_schema_sha256()
            and configuration.get("prompt_sha256") == prompt_sha256()
            and configuration.get("schema_prose_sha256") == study_schema_prose_sha256()
            and configuration.get("pipeline_implementation_sha256")
            == implementation_sha256()
            and configuration.get("runtime_versions") == runtime_versions()
            and len(parsing) == len(sources)
            and sources == recorded
            and all(
                event.get("dependency_versions") == parser_versions
                and event.get("parser_implementation_sha256")
                == _parser_implementation_sha256()
                for event in parsing
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _matching_attempt(
    paper_dir: Path,
    manifest: CohortManifest,
    pdf: Path,
    supplement: Path | None,
    config_file: Path | None,
) -> Path | None:
    candidates = sorted(paper_dir.glob("attempt-*"), reverse=True) + [paper_dir]
    return next(
        (
            path
            for path in candidates
            if _completed_run_matches(path, manifest, pdf, supplement, config_file)
        ),
        None,
    )


def _new_attempt(paper_dir: Path) -> Path:
    """Allocate a fresh location, retaining every prior failed or completed attempt."""
    if not paper_dir.exists() or not any(paper_dir.iterdir()):
        return paper_dir
    number = 1
    while (paper_dir / f"attempt-{number:04d}").exists():
        number += 1
    return paper_dir / f"attempt-{number:04d}"


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
@click.option(
    "--config",
    "config_file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Shared extraction TOML; overrides legacy manifest settings.",
)
@click.option("--env-file", type=click.Path(path_type=Path, dir_okay=False))
@click.option(
    "--rerun",
    is_flag=True,
    help="Create new attempts even for matching completed runs.",
)
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
    config_file: Path | None,
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
        load_settings(config_file)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if shard_index >= shard_count:
        raise click.ClickException("shard-index must be smaller than shard-count")
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = manifest.papers[shard_index::shard_count]
    resumed = {
        paper.paper_id: _matching_attempt(
            output_dir / paper.paper_id,
            manifest,
            pdf_dir / f"{paper.paper_id}.pdf",
            _supplement_path(supplement_dir, paper.paper_id),
            config_file,
        )
        for paper in selected
    }
    incomplete = [
        paper for paper in selected if rerun or resumed[paper.paper_id] is None
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
    invocation_id = uuid4().hex
    history_path = output_dir / "cohort_runs" / f"{invocation_id}.json"
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

    for result in results:
        if result["status"] == "already_complete":
            result["run_dir"] = str(resumed[str(result["paper_id"])])

    def write_audit() -> None:
        """Persist the whole shard state, including skipped and unfinished papers."""

        for result in results:
            paper_dir = output_dir / str(result["paper_id"])
            candidates = [paper_dir] + sorted(paper_dir.glob("attempt-*"))
            result["attempt_dirs"] = [
                str(path) for path in candidates if (path / "report.json").is_file()
            ]
        payload = {
            "invocation_id": invocation_id,
            "manifest": manifest.model_dump(mode="json"),
            "shard_count": shard_count,
            "shard_index": shard_index,
            "selected_paper_count": len(selected),
            "pending_paper_count": len(pending),
            "results": results,
        }
        write_json_atomic(history_path, payload)
        write_json_atomic(audit_path, payload)

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
            logger.error(
                "[{}/{}] {} has no main PDF", index, len(pending), paper.paper_id
            )
            write_audit()
            continue
        logger.info(
            "[{}/{}] Extracting {}{}",
            index,
            len(pending),
            paper.paper_id,
            " with SI" if supplement else " without separate SI",
        )
        run_dir = _new_attempt(output_dir / paper.paper_id)
        try:
            config = _paper_config(manifest, main_pdf, supplement, run_dir, config_file)
            report = extract_study(
                **{item.name: getattr(config, item.name) for item in fields(config)},
                env_file=env_file,
            )
            results[result_index] = {
                "paper_id": paper.paper_id,
                "run_dir": str(run_dir),
                **report,
            }
        except (OSError, RuntimeError, ValueError) as exc:
            logger.exception("Extraction failed for {}", paper.paper_id)
            results[result_index] = {
                "paper_id": paper.paper_id,
                "status": "failed",
                "run_dir": str(run_dir),
                "error": str(exc),
            }
        write_audit()
    failed = [item for item in results if item.get("status") == "failed"]
    click.echo(
        f"Processed {len(pending)} paper(s); {len(failed)} failed. Audit: {audit_path}"
    )
    if failed:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":
    main()
