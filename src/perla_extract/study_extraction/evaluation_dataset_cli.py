"""Aggregate already matched paper evaluations into dataset-level statistics."""

from __future__ import annotations

import json
from pathlib import Path

import click
from pydantic import ValidationError

from .artifacts import write_json_atomic
from .evaluation import EvaluationReport, aggregate_evaluations

REPORT = click.Path(
    path_type=Path, exists=True, dir_okay=False, readable=True, resolve_path=True
)
OUTPUT = click.Path(path_type=Path, dir_okay=False, resolve_path=True)


@click.command(context_settings={"show_default": True})
@click.option(
    "--report",
    "reports",
    type=REPORT,
    multiple=True,
    required=True,
    help="Repeat for every immutable per-paper evaluation.json.",
)
@click.option("--output", type=OUTPUT, default="dataset_evaluation.json")
@click.option("--bootstrap-samples", type=click.IntRange(min=0), default=2_000)
@click.option("--seed", type=int, default=0)
def main(
    reports: tuple[Path, ...], output: Path, bootstrap_samples: int, seed: int
) -> None:
    """Aggregate compatible reports with micro counts and paper bootstrap intervals."""

    validated: list[EvaluationReport] = []
    for path in reports:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            validated.append(EvaluationReport.model_validate(payload))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            raise click.ClickException(
                f"invalid evaluation report {path}: {exc}"
            ) from exc
    try:
        aggregate = aggregate_evaluations(
            validated, bootstrap_samples=bootstrap_samples, seed=seed
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    write_json_atomic(output, aggregate.model_dump(mode="json"))
    click.echo(str(output))


if __name__ == "__main__":
    main()
