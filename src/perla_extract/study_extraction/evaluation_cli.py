"""Command-line interface for reproducible rich-schema evaluation."""

from __future__ import annotations

from pathlib import Path

import click
from pydantic import ValidationError

from .artifacts import write_json_exclusive
from .evaluation import (
    EvaluationConfig,
    PredictionValidation,
    RunEfficiency,
    evaluate_study,
    evaluate_study_with_explanations,
)
from .models import EvidenceBlock, StudyExtraction
from .reference_io import load_reference
from .reference_io import load_study as _study
from .reference_io import read_json as _json
from .run_artifacts import (
    load_run_efficiency,
    verify_run_manifest,
    verify_source_hashes,
)
from .scoring_explanation import FactSelection
from .scoring_fields import IMPORTANT_FIELDS
from .validation import validate_study

INPUT = click.Path(path_type=Path, exists=True, readable=True, resolve_path=True)
OUTPUT = click.Path(path_type=Path, dir_okay=False, resolve_path=True)


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
        load_run_efficiency(report_path),
    )


@click.command(context_settings={"show_default": True})
@click.option(
    "--truth", type=INPUT, required=True, help="Frozen truth directory or JSON."
)
@click.option(
    "--prediction", type=INPUT, required=True, help="Extraction run directory or JSON."
)
@click.option("--output", type=OUTPUT, default="evaluation.json")
@click.option(
    "--explain-truth",
    multiple=True,
    help="Scored reference JSON path to explain; repeatable.",
)
@click.option(
    "--explain-prediction",
    multiple=True,
    help="Scored prediction JSON path to explain; repeatable.",
)
@click.option(
    "--explanation-output",
    type=OUTPUT,
    help="Write selected fact explanations to a separate JSON artifact.",
)
@click.option("--explanation-candidates", type=click.IntRange(1, 20), default=5)
@click.option(
    "--explain-view",
    type=click.Choice(["attributed", "recovery"]),
    default="attributed",
)
@click.option(
    "--minimum-record-similarity",
    type=click.FloatRange(0, 1),
    default=EvaluationConfig().minimum_record_similarity,
)
@click.option(
    "--numeric-relative-tolerance",
    type=click.FloatRange(min=0),
    default=EvaluationConfig().numeric_relative_tolerance,
    help="Relative numeric allowance after unit conversion; 0.01 means 1%. Use 1e-6 for strict comparison.",
)
@click.option(
    "--numeric-absolute-tolerance",
    type=click.FloatRange(min=0),
    default=EvaluationConfig().numeric_absolute_tolerance,
    help="Absolute allowance in base units; disabled by default because scales differ.",
)
@click.option(
    "--operation-aliases",
    type=INPUT,
    help="JSON operation-to-canonical-name mapping; replaces defaults.",
)
@click.option(
    "--descriptor-equivalences",
    type=INPUT,
    help="JSON list of reviewed cohort/specimen label equivalences, scoped to reference hashes.",
)
@click.option(
    "--fail-on-scoring-issues",
    is_flag=True,
    help="Save diagnostics, then fail if scientific attribution is ambiguous.",
)
def main(
    truth: Path,
    prediction: Path,
    output: Path,
    explain_truth: tuple[str, ...],
    explain_prediction: tuple[str, ...],
    explanation_output: Path | None,
    explanation_candidates: int,
    explain_view: str,
    minimum_record_similarity: float,
    numeric_relative_tolerance: float,
    numeric_absolute_tolerance: float,
    operation_aliases: Path | None,
    descriptor_equivalences: Path | None,
    fail_on_scoring_issues: bool,
) -> None:
    """Score one extraction without using an LLM or run-local identifiers."""

    if bool(explain_truth or explain_prediction) != bool(explanation_output):
        raise click.ClickException(
            "Provide fact paths and --explanation-output together"
        )
    if (
        explanation_output is not None
        and explanation_output.resolve() == output.resolve()
    ):
        raise click.ClickException(
            "Score and explanation outputs must be different files"
        )
    if explanation_output is not None and explanation_output.exists():
        raise click.ClickException(
            f"Cannot create {explanation_output}; choose a new explanation output path"
        )
    try:
        expected, benchmark = load_reference(truth)
        actual, prediction_validation, run_efficiency = _prediction(prediction)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        config = EvaluationConfig(
            minimum_record_similarity=minimum_record_similarity,
            numeric_relative_tolerance=numeric_relative_tolerance,
            numeric_absolute_tolerance=numeric_absolute_tolerance,
            **(
                {"operation_aliases": _json(operation_aliases)}
                if operation_aliases
                else {}
            ),
            **(
                {"descriptor_equivalences": _json(descriptor_equivalences)}
                if descriptor_equivalences
                else {}
            ),
        )
    except ValueError as exc:
        raise click.ClickException(f"invalid scoring configuration: {exc}") from exc
    try:
        manifest_digest = None
        run_id = None
        if prediction.is_dir():
            manifest, manifest_digest = verify_run_manifest(prediction)
            run_id = manifest.run_id
            if benchmark is not None:
                verify_source_hashes(
                    benchmark.source_sha256,
                    _json(prediction / "run_configuration.json"),
                )
        explanation = None
        if explanation_output is not None:
            report, explanation = evaluate_study_with_explanations(
                expected,
                actual,
                facts=[
                    FactSelection(side=side, path=path, view=explain_view)
                    for side, paths in (
                        ("truth", explain_truth),
                        ("prediction", explain_prediction),
                    )
                    for path in paths
                ],
                max_candidates=explanation_candidates,
                benchmark=benchmark,
                prediction_validation=prediction_validation,
                run_efficiency=run_efficiency,
                run_manifest_sha256=manifest_digest,
                run_id=run_id,
                config=config,
            )
        else:
            report = evaluate_study(
                expected,
                actual,
                benchmark=benchmark,
                prediction_validation=prediction_validation,
                run_efficiency=run_efficiency,
                run_manifest_sha256=manifest_digest,
                run_id=run_id,
                config=config,
            )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    artifacts = [(output, report.model_dump(mode="json"))]
    if explanation_output is not None and explanation is not None:
        artifacts.append((explanation_output, explanation.model_dump(mode="json")))
    for destination, payload in artifacts:
        try:
            write_json_exclusive(destination, payload)
        except OSError as exc:
            raise click.ClickException(
                f"Cannot create {destination}; choose a new output path: {exc}"
            ) from exc
        click.echo(str(destination))
    click.echo("Field  TP  FP  FN  Precision  Recall  F1")
    for key, label in IMPORTANT_FIELDS.items():
        score = report.core_facts.fields.get(key)
        if score is None or not (score.predicted or score.truth):
            continue
        rates = "  ".join(
            f"{v:.1%}" if v is not None else "—"
            for v in (score.precision, score.recall, score.f1)
        )
        click.echo(
            f"{label}  {score.matched}  {score.predicted - score.matched}  {score.truth - score.matched}  {rates}"
        )
    for warning in report.input_warnings:
        click.echo(f"Input warning: {warning}", err=True)
    if fail_on_scoring_issues and report.core_facts.issues:
        raise click.ClickException(
            f"{len(report.core_facts.issues)} scoring issue(s) require review; diagnostics saved to {output}"
        )


if __name__ == "__main__":
    main()
