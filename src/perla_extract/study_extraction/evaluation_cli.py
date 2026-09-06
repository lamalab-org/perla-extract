"""Command-line interface for reproducible rich-schema evaluation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import click
from pydantic import ValidationError

from .artifacts import write_json_atomic
from .evaluation import (
    BenchmarkProvenance,
    EvaluationConfig,
    PredictionValidation,
    RunEfficiency,
    evaluate_study,
)
from .models import EvidenceBlock, StudyExtraction, study_schema_sha256
from .validation import validate_study

INPUT = click.Path(path_type=Path, exists=True, readable=True, resolve_path=True)
OUTPUT = click.Path(path_type=Path, dir_okay=False, resolve_path=True)
SUPPORTED_GROUND_TRUTH_FORMAT_VERSION = 2


def _json(path: Path) -> object:
    """Load one UTF-8 JSON artifact and keep its path in parse errors."""

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise click.ClickException(f"cannot read {path}: {exc}") from exc


def _canonical_digest(value: object) -> str:
    """Reproduce the ground-truth exporter's content hash."""

    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _study(payload: object, path: Path) -> StudyExtraction:
    """Convert schema failures into concise CLI errors that retain the source path."""

    try:
        return StudyExtraction.model_validate(payload)
    except ValidationError as exc:
        raise click.ClickException(f"invalid StudyExtraction in {path}: {exc}") from exc


def _truth(
    path: Path,
) -> tuple[StudyExtraction, list[str], BenchmarkProvenance | None]:
    """Load a truth file or verify a complete frozen benchmark directory."""

    if path.is_file():
        return _study(_json(path), path), [], None
    truth_path = path / "ground_truth.json"
    manifest_path = path / "manifest.json"
    if not truth_path.is_file() or not manifest_path.is_file():
        raise click.ClickException(
            f"{path} must contain ground_truth.json and manifest.json"
        )
    payload = _json(truth_path)
    manifest = _json(manifest_path)
    if not isinstance(manifest, dict):
        raise click.ClickException(f"{manifest_path} is not a JSON object")
    if manifest.get("artifact_format_version") != SUPPORTED_GROUND_TRUTH_FORMAT_VERSION:
        raise click.ClickException(
            "ground-truth artifact format is unsupported; regenerate or migrate it"
        )
    expected_schema = manifest.get("study_schema_sha256")
    if expected_schema != study_schema_sha256():
        raise click.ClickException(
            "ground-truth schema hash differs from the installed evaluator"
        )
    files = manifest.get("files")
    expected_truth = files.get("ground_truth.json") if isinstance(files, dict) else None
    if expected_truth != _canonical_digest(payload):
        raise click.ClickException("ground_truth.json does not match its manifest hash")
    review = manifest.get("review")
    if not isinstance(review, dict):
        raise click.ClickException("manifest review must be a JSON object")
    uncertain = review.get("uncertain_record_keys")
    if not isinstance(uncertain, list) or not all(
        isinstance(item, str) for item in uncertain
    ):
        raise click.ClickException("manifest uncertainty mask is invalid")
    paper_id = manifest.get("paper_id")
    split = manifest.get("split")
    source_manifest = manifest.get("source_manifest")
    if not isinstance(paper_id, str) or not isinstance(split, str):
        raise click.ClickException("manifest paper_id and split must be strings")
    if not isinstance(source_manifest, dict):
        raise click.ClickException("manifest source_manifest must be a JSON object")
    source_hashes = source_manifest.get("source_sha256", [])
    if not isinstance(source_hashes, list) or not all(
        isinstance(item, str) for item in source_hashes
    ):
        raise click.ClickException(
            "source_manifest source_sha256 must be a string list"
        )
    return (
        _study(payload, truth_path),
        uncertain,
        BenchmarkProvenance(
            paper_id=paper_id,
            split=split,
            ground_truth_sha256=expected_truth,
            source_manifest_sha256=_canonical_digest(source_manifest),
            source_sha256=source_hashes,
        ),
    )


def _run_efficiency(path: Path) -> RunEfficiency:
    """Validate measured token, cost, request, and latency totals from report.json."""

    payload = _json(path)
    if not isinstance(payload, dict):
        raise click.ClickException(f"{path} is not a JSON object")
    usage = payload.get("usage")
    budget = payload.get("budget")
    if not isinstance(usage, dict):
        raise click.ClickException(f"{path} has no usage object")
    budget = budget if isinstance(budget, dict) else {}
    try:
        return RunEfficiency(
            status=payload.get("status"),
            live_calls=usage.get("live_calls"),
            cache_hits=usage.get("cache_hits"),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            cost_usd=usage.get("cost"),
            provider_requests=budget.get("provider_requests"),
            cost_tracking_complete=budget.get("cost_tracking_complete"),
            elapsed_seconds=payload.get("elapsed_seconds"),
        )
    except ValidationError as exc:
        raise click.ClickException(f"invalid run accounting in {path}: {exc}") from exc


def _prediction(
    path: Path,
) -> tuple[StudyExtraction, PredictionValidation | None, RunEfficiency | None]:
    """Load a prediction and validate its evidence when a complete run is supplied."""

    candidate = path / "extraction.json" if path.is_dir() else path
    if not candidate.is_file():
        raise click.ClickException(f"missing prediction artifact: {candidate}")
    study = _study(_json(candidate), candidate)
    if path.is_file():
        return study, None, None
    document_path = path / "document.json"
    report_path = path / "report.json"
    if not document_path.is_file():
        raise click.ClickException(
            f"prediction run is missing evidence artifact: {document_path}"
        )
    if not report_path.is_file():
        raise click.ClickException(
            f"prediction run is missing accounting artifact: {report_path}"
        )
    document = _json(document_path)
    raw_blocks = document.get("blocks") if isinstance(document, dict) else None
    if not isinstance(raw_blocks, list):
        raise click.ClickException(f"{document_path} has no evidence block list")
    try:
        blocks = [EvidenceBlock.model_validate(block) for block in raw_blocks]
    except ValidationError as exc:
        raise click.ClickException(
            f"invalid evidence blocks in {document_path}: {exc}"
        ) from exc
    validation = validate_study(study, blocks)
    validation.pop("verified_values", None)
    return (
        study,
        PredictionValidation.model_validate(validation),
        _run_efficiency(report_path),
    )


@click.command(context_settings={"show_default": True})
@click.option(
    "--truth", type=INPUT, required=True, help="Frozen truth directory or JSON."
)
@click.option(
    "--prediction", type=INPUT, required=True, help="Extraction run directory or JSON."
)
@click.option("--output", type=OUTPUT, default="evaluation.json")
@click.option("--minimum-record-similarity", type=click.FloatRange(0, 1), default=0.35)
@click.option(
    "--numeric-relative-tolerance", type=click.FloatRange(min=0), default=0.01
)
@click.option(
    "--numeric-absolute-tolerance", type=click.FloatRange(min=0), default=1e-9
)
def main(
    truth: Path,
    prediction: Path,
    output: Path,
    minimum_record_similarity: float,
    numeric_relative_tolerance: float,
    numeric_absolute_tolerance: float,
) -> None:
    """Score one extraction without using an LLM or run-local identifiers."""

    expected, uncertain, benchmark = _truth(truth)
    actual, prediction_validation, run_efficiency = _prediction(prediction)
    report = evaluate_study(
        expected,
        actual,
        ignored_truth_record_keys=uncertain,
        benchmark=benchmark,
        prediction_validation=prediction_validation,
        run_efficiency=run_efficiency,
        config=EvaluationConfig(
            minimum_record_similarity=minimum_record_similarity,
            numeric_relative_tolerance=numeric_relative_tolerance,
            numeric_absolute_tolerance=numeric_absolute_tolerance,
        ),
    )
    write_json_atomic(output, report.model_dump(mode="json"))
    click.echo(str(output))


if __name__ == "__main__":
    main()
