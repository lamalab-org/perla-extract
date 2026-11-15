"""Run a paired extraction experiment from one editable TOML file.

Validate all inputs first, freeze the intended runs, then call the existing
extractor, scorer and comparator. Each attempt has its own response cache. Failed
attempts stay in the roster, so they cannot disappear from the final comparison.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click
from pydantic import Field

from .artifacts import write_json_exclusive
from .cli import _load_env
from .comparison import ARMS, _settings, summarize
from .comparison_study import ComparisonStudy, FactorContrast, same_setting
from .configuration import ExtractionConfig, parse_settings, resolve_config, tomllib
from .evaluation_cli import main as evaluate
from .experiment_summary import write_summary
from .figure_data import export_tables
from .logging import configure_logging
from .models import StrictModel
from .reference_io import load_reference
from .run_artifacts import file_digest
from .workflow import run_extraction

_MANAGED = {
    "pdf",
    "supplement",
    "output_dir",
    "document_cache_dir",
    "model_cache_dir",
    "dry_run",
    "refresh_document_cache",
}


class Paper(StrictModel):
    pdf: str
    reference: str
    supplement: str | None = None


class ExperimentSettings(StrictModel):
    output: str
    repeats: int = Field(default=1, ge=1)
    order_seed: int = 0
    extraction: dict[str, Any] = Field(default_factory=dict)
    stages: dict[str, dict[str, Any]] = Field(default_factory=dict)
    tracing: dict[str, Any] = Field(default_factory=dict)
    control: dict[str, Any]
    treatment: dict[str, Any]
    papers: list[Paper] = Field(min_length=1)


@dataclass(frozen=True)
class Attempt:
    paper_id: str
    repeat: int
    arm: str
    reference: Path
    config: ExtractionConfig


@dataclass(frozen=True)
class Experiment:
    source: Path
    output: Path
    study: ComparisonStudy
    attempts: list[Attempt]
    inputs: dict[Path, str]
    order_seed: int


def _path(directory: Path, value: str) -> Path:
    if not value.strip():
        raise ValueError("Input and output paths must not be empty")
    return (directory / value).resolve()


def _arm_settings(settings: ExperimentSettings, arm: str, directory: Path) -> dict:
    overrides = dict(getattr(settings, arm))
    arm_stages = overrides.pop("stages", {})
    if not isinstance(arm_stages, dict):
        raise ValueError(f"{arm}.stages must contain stage tables")
    options = settings.extraction | overrides
    if forbidden := _MANAGED & options.keys():
        raise ValueError(
            f"Put sources in [[papers]]; run paths and caches are automatic: {', '.join(sorted(forbidden))}"
        )
    stage_names = set(settings.stages) | set(arm_stages)
    stages = {}
    for stage in stage_names:
        override = arm_stages.get(stage, {})
        if not isinstance(override, dict):
            raise ValueError(f"{arm}.stages.{stage} must be a table")
        # Selecting a model supplies a complete deployment, including transport.
        # Limit-only interventions keep the shared connection.
        stages[stage] = (
            {} if "model" in override else dict(settings.stages.get(stage, {}))
        ) | override
    return parse_settings(
        {"extraction": options, "stages": stages, "tracing": settings.tracing},
        directory,
    )


def prepare(path: Path) -> Experiment:
    """Validate every paper and both arm configurations before any paid request."""
    path = path.resolve()
    settings = ExperimentSettings.model_validate(
        tomllib.loads(path.read_text(encoding="utf-8"))
    )
    output = _path(path.parent, settings.output)
    if output.exists():
        raise ValueError(
            f"Output already exists: {output}; choose a new output in {path.name}"
        )
    methods = {arm: _arm_settings(settings, arm, path.parent) for arm in ARMS}
    inputs = {path: file_digest(path)}
    attempts: list[Attempt] = []
    paper_ids: list[str] = []
    splits: set[str] = set()
    seen_sources: set[str] = set()
    for index, paper in enumerate(settings.papers, 1):
        pdf = _path(path.parent, paper.pdf)
        supplement = _path(path.parent, paper.supplement) if paper.supplement else None
        reference = _path(path.parent, paper.reference)
        _, benchmark = load_reference(reference)
        if benchmark is None or not benchmark.source_sha256:
            raise ValueError(
                f"{reference}: use a finalized reference directory with source PDF hashes"
            )
        if benchmark.paper_id in paper_ids:
            raise ValueError(f"Duplicate reference paper: {benchmark.paper_id}")
        paper_ids.append(benchmark.paper_id)
        splits.add(benchmark.split)
        missing = [
            f"  {role}: {source}"
            for role, source in (("pdf", pdf), ("supplement", supplement))
            if source is not None and not source.is_file()
        ]
        if missing:
            raise ValueError(
                f"{benchmark.paper_id}: source files missing for reference {reference}:\n"
                + "\n".join(missing)
                + "\nSet this [[papers]] block's paths to the original main PDF and SI. "
                "The reference directory contains reviewed answers, not source PDFs."
            )
        hashes = [
            file_digest(source) for source in (pdf, supplement) if source is not None
        ]
        if sorted(hashes) != sorted(benchmark.source_sha256):
            raise ValueError(
                f"{benchmark.paper_id}: PDF/SI bytes do not match the reference sources. "
                f"Reference: {reference}; pdf: {pdf}; supplement: {supplement or 'none'}. "
                "Use the original source files recorded by this reference, including its SI."
            )
        if seen_sources.intersection(hashes):
            raise ValueError(
                f"{benchmark.paper_id}: source PDF reused by another paper"
            )
        seen_sources.update(hashes)
        for source in (
            pdf,
            supplement,
            reference / "manifest.json",
            reference / "ground_truth.json",
        ):
            if source is not None:
                inputs[source] = file_digest(source)
        for repeat in range(1, settings.repeats + 1):
            for arm in ARMS:
                run = output / "runs" / f"paper-{index:03d}" / f"{arm}-{repeat}"
                config = resolve_config(
                    **methods[arm],
                    pdf=pdf,
                    supplement=supplement,
                    output_dir=run,
                    document_cache_dir=output / "cache" / "documents",
                    model_cache_dir=run / "model-cache",
                )
                attempts.append(
                    Attempt(benchmark.paper_id, repeat, arm, reference, config)
                )
    if len(splits) > 1:
        raise ValueError(
            "Use one reference split per experiment; keep development and test papers separate"
        )
    left, right = (
        _settings({"scientific_settings": attempts[index].config.scientific_settings()})
        for index in (0, 1)
    )
    factors = {
        name: FactorContrast(control=left.get(name), treatment=right.get(name))
        for name in left.keys() | right.keys()
        if not same_setting(left.get(name), right.get(name))
    }
    if not factors:
        raise ValueError(
            "Control and treatment are identical; change a setting in [treatment]"
        )
    if "parser" in factors:
        raise ValueError(
            "Use the same parser in both arms; paired extraction comparisons require identical source evidence"
        )
    study = ComparisonStudy(
        paper_ids=paper_ids, repeats=settings.repeats, factors=factors
    )
    return Experiment(path, output, study, attempts, inputs, settings.order_seed)


def _unchanged(experiment: Experiment, attempt: Attempt | None = None) -> None:
    paths = (
        experiment.inputs.keys()
        if attempt is None
        else {
            experiment.source,
            attempt.config.pdf,
            attempt.reference / "manifest.json",
            attempt.reference / "ground_truth.json",
            *([attempt.config.supplement] if attempt.config.supplement else []),
        }
    )
    for path in paths:
        digest = experiment.inputs[path]
        if file_digest(path) != digest:
            raise ValueError(f"Input changed after the experiment was checked: {path}")


def execute(experiment: Experiment) -> dict:
    """Freeze the roster before execution and retain incomplete experiments on disk."""
    _unchanged(experiment)
    # Use the same optional credential discovery as the individual extraction CLI.
    environment = Path(".env.local")
    _load_env(environment if environment.is_file() else None)
    output = experiment.output
    output.mkdir(parents=True, exist_ok=False)
    (output / "experiment.toml").write_bytes(experiment.source.read_bytes())
    study_path = output / "study.json"
    write_json_exclusive(study_path, experiment.study.model_dump(mode="json"))
    roster = output / "runs.csv"
    with roster.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["paper_id", "arm", "repeat", "run_dir"])
        for attempt in experiment.attempts:
            writer.writerow(
                [
                    attempt.paper_id,
                    attempt.arm,
                    attempt.repeat,
                    str(attempt.config.output_dir.relative_to(output)),
                ]
            )
    ordered = list(experiment.attempts)
    random.Random(experiment.order_seed).shuffle(ordered)
    write_json_exclusive(
        output / "inputs.json",
        {
            "sha256": {str(path): digest for path, digest in experiment.inputs.items()},
            "order_seed": experiment.order_seed,
            "execution_order": [
                str(attempt.config.output_dir.relative_to(output))
                for attempt in ordered
            ],
        },
    )
    errors = []
    stopped = None
    try:
        for index, attempt in enumerate(ordered, 1):
            click.echo(
                f"[{index}/{len(ordered)}] {attempt.paper_id} · {attempt.arm} · repeat {attempt.repeat}"
            )
            # A changed source or method stops the campaign rather than mixing methods.
            _unchanged(experiment, attempt)
            try:
                report = run_extraction(attempt.config)
                click.echo(f"  Extraction status: {report['status']}")
                if report["status"] in {"failed", "interrupted"}:
                    raise ValueError(
                        f"Extraction {report['status']}; inspect {attempt.config.output_dir / 'report.json'}"
                    )
                evaluate.main(
                    args=[
                        "--truth",
                        str(attempt.reference),
                        "--prediction",
                        str(attempt.config.output_dir),
                        "--output",
                        str(attempt.config.output_dir / "evaluation.json"),
                        "--fail-on-scoring-issues",
                    ],
                    standalone_mode=False,
                )
            except (OSError, RuntimeError, ValueError, click.ClickException) as exc:
                errors.append(
                    {
                        "run_dir": str(attempt.config.output_dir.relative_to(output)),
                        "error": str(exc),
                    }
                )
                click.echo(f"Needs attention: {exc}", err=True)
        _unchanged(experiment)
    except BaseException as exc:
        stopped = str(exc) or type(exc).__name__
        errors.append({"run_dir": "", "error": stopped})
        raise
    finally:
        write_json_exclusive(output / "errors.json", errors)
        result = summarize(roster, study=study_path)
        if stopped:
            issue = f"Experiment stopped: {stopped}"
            result["issues"].append(issue)
            result["quality_issues"].append(issue)
            result.update(
                quality=None,
                quality_status="needs_attention",
                recorded_cost_saving_fraction=None,
            )
        write_json_exclusive(output / "comparison.json", result)
        export_tables(result, output / "figure-data")
        write_summary(result, output / "summary.md")
    return result


def _show_input_mapping(experiment: Experiment) -> None:
    """Show each source pair and its scoring reference once, before model calls."""
    click.echo("Paper/SI/reference mapping (source fingerprints verified):")
    for attempt in experiment.attempts:
        if attempt.arm != "control" or attempt.repeat != 1:
            continue
        click.echo(f"  {attempt.paper_id}")
        for label, path in (
            ("Paper", attempt.config.pdf),
            ("SI", attempt.config.supplement),
            ("Ground truth", attempt.reference),
        ):
            shown = (
                path.relative_to(experiment.source.parent)
                if path is not None and path.is_relative_to(experiment.source.parent)
                else path
            )
            click.echo(f"    {label}: {shown if shown is not None else 'none'}")
        _, benchmark = load_reference(attempt.reference)
        if benchmark is None or not benchmark.coverage.complete():
            click.echo("    Scoring: diagnostic reference agreement; coverage/context review is incomplete.")
        else:
            click.echo("    Scoring: reference scope reviewed.")


@click.command()
@click.argument(
    "experiment_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option(
    "--check",
    is_flag=True,
    help="Verify and show paper/SI/reference matches without parsing PDFs, creating output or calling a model.",
)
def main(experiment_file: Path, check: bool) -> None:
    """Extract, score and compare both arms of EXPERIMENT_FILE."""
    configure_logging(level="INFO", json_output=False)
    try:
        experiment = prepare(experiment_file)
        _show_input_mapping(experiment)
        click.echo(
            f"{len(experiment.study.paper_ids)} paper(s) × 2 arms × {experiment.study.repeats} repeat(s) = {len(experiment.attempts)} runs"
        )
        for name, contrast in sorted(experiment.study.factors.items()):
            click.echo(
                f"  {name}: {json.dumps(contrast.control)} → {json.dumps(contrast.treatment)}"
            )
        click.echo(f"Output: {experiment.output}")
        if check:
            click.echo(
                "Inputs and settings valid. No model calls or output files created."
            )
            return
        result = execute(experiment)
        if result["quality"] is not None:
            delta = result["quality"]["mean_paired_fact_f1_delta"]
            click.echo(
                f"Mean paired attributed fact-F1 change (treatment − control): {delta:+.4f}"
            )
        click.echo(f"Read first: {experiment.output / 'summary.md'}")
        click.echo(f"Comparison: {experiment.output / 'comparison.json'}")
        click.echo(f"Figure data: {experiment.output / 'figure-data'}")
        if result["issues"]:
            raise ValueError(
                "Experiment needs attention: " + "; ".join(result["issues"][:3])
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
