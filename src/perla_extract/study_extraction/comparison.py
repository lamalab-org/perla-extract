"""Compare paired runs listed in a CSV, retaining failures and independent costs.

The roster has paper_id, arm (control/treatment), repeat and run_dir columns. An
optional role distinguishes paired observations from accounting-only failed or
abandoned attempts. Paths are relative to the CSV. Run ordinary extraction and
scoring commands first, then summarize their sealed artifacts here.
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from statistics import mean

import click

from .artifacts import write_json_exclusive
from .evaluation import EvaluationReport, _study_digest, aggregate_evaluations
from .figure_data import export_tables
from .models import StudyExtraction
from .run_artifacts import file_digest, load_run_efficiency, verify_run_manifest

ARMS = ("control", "treatment")
_RUNTIME_FIELDS = {
    "pdf",
    "supplement",
    "output_dir",
    "document_cache_dir",
    "model_cache_dir",
    "configuration_sha256",
    "source_sha256",
    "evidence_spans_sha256",
    "effective_mode",
    "scientific_settings",
    "effective_models",
    "effective_deployments",
}


def paired_interval(deltas: list[float], samples: int = 2000, seed: int = 0) -> dict:
    """Bootstrap paper means, conditional on the observed within-paper repetitions."""

    if samples < 0 or not deltas:
        raise ValueError("Provide paper differences and a nonnegative bootstrap count")
    result = dict(
        paper_count=len(deltas),
        mean=mean(deltas),
        ci95_lower=None,
        ci95_upper=None,
        method="paired-paper-percentile",
        samples=samples,
        seed=seed,
        status="disabled" if samples == 0 else "insufficient_papers",
    )
    if len(deltas) > 1 and samples:
        rng = random.Random(seed)
        draws = sorted(mean(rng.choices(deltas, k=len(deltas))) for _ in range(samples))
        result.update(
            ci95_lower=draws[int(0.025 * (samples - 1))],
            ci95_upper=draws[int(0.975 * (samples - 1))],
            status="available",
        )
    return result


def _flatten(value: dict, prefix: str = "") -> dict:
    result = {}
    for name, item in value.items():
        key = f"{prefix}.{name}" if prefix else name
        if isinstance(item, dict):
            result.update(_flatten(item, key))
        else:
            result[key] = item
    return result


def _settings(configuration: dict) -> dict:
    source = configuration.get("scientific_settings", configuration)
    if not isinstance(source, dict):
        raise ValueError("scientific_settings must be an object")
    settings = {
        key: value for key, value in source.items() if key not in _RUNTIME_FIELDS
    }
    for name in (
        "schema_sha256",
        "schema_prose_sha256",
        "prompt_sha256",
        "parser_dependencies",
        "parser_implementation_sha256",
        "pipeline_implementation_sha256",
        "runtime_versions",
    ):
        if name in configuration:
            settings[name] = configuration[name]
    return _flatten(settings)


def read_roster(path: Path) -> list[dict]:
    """Require paired quality observations and retain separately listed extra spend.

    Accounting-only rows may omit repeat, but must belong to a paper/arm represented
    in the paired roster. They never fill a missing pair or become a quality sample.
    """

    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        required = {"paper_id", "arm", "repeat", "run_dir"}
        if set(columns) not in (required, required | {"role"}) or len(columns) != len(
            set(columns)
        ):
            raise ValueError(
                "runs.csv requires paper_id,arm,repeat,run_dir columns and optional role"
            )
        rows: list[dict] = list(reader)
    keys, paths = set(), set()
    for row in rows:
        if None in row:
            raise ValueError("CSV row has more values than its header")
        if not row["paper_id"] or row["arm"] not in ARMS:
            raise ValueError("Each row needs a paper_id and control or treatment arm")
        row["role"] = row.get("role") or "paired"
        if row["role"] not in {"paired", "accounting_only"}:
            raise ValueError("role must be paired or accounting_only")
        if row["role"] == "accounting_only" and row["repeat"] in {None, ""}:
            row["repeat"] = None
        else:
            try:
                row["repeat"] = int(row["repeat"])
            except (ValueError, TypeError) as exc:
                raise ValueError("paired repeat must be a positive integer") from exc
            if row["repeat"] < 1:
                raise ValueError("repeat must be a positive integer when supplied")
        if not row["run_dir"]:
            raise ValueError("Each row needs a run_dir")
        directory = Path(row["run_dir"])
        row["run_dir"] = str((path.parent / directory).resolve())
        key = (row["paper_id"], row["repeat"], row["arm"])
        if (row["role"] == "paired" and key in keys) or row["run_dir"] in paths:
            raise ValueError("Duplicate paper/repetition/arm or reused run directory")
        if row["role"] == "paired":
            keys.add(key)
        paths.add(row["run_dir"])
    if not keys or any(
        (paper, repeat, arm) not in keys for paper, repeat, _ in keys for arm in ARMS
    ):
        raise ValueError(
            "List both arms for every paired paper/repetition; accounting_only cannot fill a missing pair"
        )
    paired_members = {(paper, arm) for paper, _, arm in keys}
    if any((row["paper_id"], row["arm"]) not in paired_members for row in rows):
        raise ValueError(
            "Every accounting_only paper and arm must appear in the paired roster"
        )
    return rows


def summarize(
    roster: Path,
    *,
    factors: tuple[str, ...] = (),
    bootstrap_samples: int = 2000,
    seed: int = 0,
) -> dict:
    """Verify run bindings, then compute paired quality and separately available spend."""

    if bootstrap_samples < 0:
        raise ValueError("bootstrap samples cannot be negative")
    rows = read_roster(roster)
    scores, configurations = {}, {}
    for row in rows:
        key = (row["paper_id"], row["repeat"], row["arm"])
        directory = Path(row["run_dir"])
        row.update(
            issues=[],
            accounting_issues=[],
            quality_available=False,
            quality_applicable=row["role"] == "paired",
            cost_available=False,
        )
        try:
            manifest, digest = verify_run_manifest(directory, accounting_only=True)
            row.update(run_id=manifest.run_id, run_manifest_sha256=digest)
            config = json.loads((directory / "run_configuration.json").read_text())
            if not isinstance(config, dict):
                raise ValueError("Run configuration must be an object")
            if row["quality_applicable"]:
                configurations[key] = config
            row["settings"] = _settings(config)
            run_accounting = load_run_efficiency(directory / "report.json")
            row["accounting"] = run_accounting.model_dump()
            row["cost_available"] = run_accounting.cost_tracking_complete is True
        except (OSError, ValueError) as exc:
            row["accounting_issues"].append(str(exc))
            row["issues"].append(str(exc))
            continue
        if not row["quality_applicable"]:
            continue
        try:
            verify_run_manifest(directory)
            path = directory / "evaluation.json"
            report = EvaluationReport.model_validate_json(path.read_bytes())
            if report.run_id != manifest.run_id or report.run_manifest_sha256 != digest:
                raise ValueError(
                    "Score is not bound to this run manifest; score this run again"
                )
            prediction = StudyExtraction.model_validate_json(
                (directory / "extraction.json").read_bytes()
            )
            if report.prediction_content_sha256 != _study_digest(prediction):
                raise ValueError("Score prediction digest differs from this extraction")
            if report.run_efficiency != run_accounting:
                raise ValueError(
                    "Score accounting differs from this run's bound accounting"
                )
            if report.benchmark is None or report.benchmark.paper_id != row["paper_id"]:
                raise ValueError("Score benchmark paper differs from the roster")
            if report.benchmark.source_sha256 and sorted(
                report.benchmark.source_sha256
            ) != sorted(config.get("source_sha256", [])):
                raise ValueError("Reference source hashes differ from the run sources")
            if (
                report.core_facts.scoring_status != "ready"
                or report.core_facts.macro_f1 is None
            ):
                raise ValueError(
                    "Score has unresolved alignment or no defined scientific F1"
                )
            scores[key] = report
            row.update(
                quality_available=True,
                macro_f1=report.core_facts.macro_f1,
                score_sha256=file_digest(path),
                scientific_scores=report.core_facts.model_dump(),
            )
        except (OSError, ValueError) as exc:
            row["issues"].append(str(exc))

    issues = [
        f"{row['paper_id']}/{row['repeat']}/{row['arm']}: {issue}"
        for row in rows
        if row["quality_applicable"]
        for issue in row["issues"]
    ]
    accounting_issues = [
        f"{row['paper_id']}/{row['role']}/{row['repeat']}/{row['arm']}: {issue}"
        for row in rows
        for issue in row["accounting_issues"]
    ]
    run_ids = [row["run_id"] for row in rows if row.get("run_id")]
    duplicates = {identity for identity in run_ids if run_ids.count(identity) > 1}
    if duplicates:
        issues.append(
            "A saved attempt was listed more than once (duplicate run_id); copied folders are not independent runs"
        )
        retained = set()
        for row in rows:
            if row.get("run_id") in duplicates:
                row["issues"].append(
                    "duplicate run_id: accounting cannot be attributed to distinct attempts"
                )
                row["cost_available"] = False
                row["quality_available"] = False
                if row["run_id"] in retained:
                    row.pop("accounting", None)
                retained.add(row["run_id"])
                row.pop("macro_f1", None)
                row.pop("scientific_scores", None)
    paired_rows = [row for row in rows if row["quality_applicable"]]
    papers = sorted({row["paper_id"] for row in paired_rows})
    pairs = sorted({(row["paper_id"], row["repeat"]) for row in paired_rows})
    differences = []
    for paper, repeat in pairs:
        keys = [(paper, repeat, arm) for arm in ARMS]
        if all(key in configurations for key in keys):
            control, treatment = (configurations[key] for key in keys)
            for name in ("source_sha256", "evidence_spans_sha256"):
                if (
                    name not in control
                    or name not in treatment
                    or control[name] != treatment[name]
                ):
                    issues.append(
                        f"{paper}/{repeat}: paired runs must bind the same {name}"
                    )
            left, right = _settings(control), _settings(treatment)
            changed = {
                key: {"control": left.get(key), "treatment": right.get(key)}
                for key in left.keys() | right.keys()
                if left.get(key) != right.get(key)
            }
            differences.append(dict(paper_id=paper, repeat=repeat, fields=changed))
            if factors:
                unexpected = set(changed) - set(factors)
                if unexpected:
                    issues.append(
                        f"{paper}/{repeat}: undeclared configuration differences: {', '.join(sorted(unexpected))}"
                    )
                if not set(changed) & set(factors):
                    issues.append(f"{paper}/{repeat}: declared factor did not change")
    found = list(scores.values())
    if found:
        first = found[0]
        if any(
            (score.config, score.matcher_version, score.study_schema_sha256)
            != (first.config, first.matcher_version, first.study_schema_sha256)
            for score in found
        ):
            issues.append("Scorer configuration, matcher or schema differs")
        for paper in papers:
            group = [score for key, score in scores.items() if key[0] == paper]
            if group and any(
                (score.truth_content_sha256, score.benchmark)
                != (group[0].truth_content_sha256, group[0].benchmark)
                for score in group
            ):
                issues.append(f"{paper}: references differ between arms or repetitions")
        # One score per paper checks mixed splits and source duplication without
        # treating repeat observations as independent papers.
        unique = [
            next(score for key, score in scores.items() if key[0] == paper)
            for paper in papers
            if any(key[0] == paper for key in scores)
        ]
        try:
            aggregate_evaluations(unique, bootstrap_samples=0)
        except ValueError as exc:
            issues.append(str(exc))
    quality = None
    if not issues:
        paired = []
        for paper in papers:
            deltas = [
                scores[paper, repeat, "treatment"].core_facts.macro_f1
                - scores[paper, repeat, "control"].core_facts.macro_f1
                for item, repeat in pairs
                if item == paper
            ]
            paired.append(
                dict(paper_id=paper, repeat_deltas=deltas, mean_delta=mean(deltas))
            )
        quality = dict(
            mean_paired_macro_f1_delta=mean(item["mean_delta"] for item in paired),
            paired_macro_f1_delta_interval=paired_interval(
                [item["mean_delta"] for item in paired], bootstrap_samples, seed
            ),
            papers=paired,
        )
    accounting = {}
    for arm in ARMS:
        arm_rows = [row for row in rows if row["arm"] == arm]
        complete = all(row["cost_available"] for row in arm_rows)
        known = sum(row.get("accounting", {}).get("cost_usd", 0) for row in arm_rows)
        accounting[arm] = dict(
            planned_runs=sum(row["quality_applicable"] for row in arm_rows),
            listed_attempts=len(arm_rows),
            accounting_only_runs=sum(not row["quality_applicable"] for row in arm_rows),
            accounted_runs=sum("accounting" in row for row in arm_rows),
            complete_runs=sum(
                row.get("accounting", {}).get("status") == "complete"
                for row in arm_rows
            ),
            known_cost_usd=known,
            cost_tracking_complete=complete,
            total_cost_usd=known if complete else None,
        )
    control, treatment = (accounting[arm]["total_cost_usd"] for arm in ARMS)
    saving = (
        (control - treatment) / control
        if control is not None and control > 0 and treatment is not None
        else None
    )
    return dict(
        roster_sha256=file_digest(roster),
        paper_count=len(papers),
        factors=list(factors),
        estimand="Equal-paper mean difference in populated-group macro F1; repeats averaged within paper",
        quality_status="needs_attention" if issues else "ready",
        issues=list(dict.fromkeys(issues + accounting_issues)),
        quality_issues=issues,
        accounting_issues=accounting_issues,
        quality=quality,
        accounting=accounting,
        recorded_cost_saving_fraction=saving,
        cost_scope="All unique listed paired and accounting_only attempts; unlisted work is not inferred",
        configuration_differences=differences,
        runs=rows,
    )


@click.command()
@click.argument("roster", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--output", type=click.Path(path_type=Path, dir_okay=False), required=True
)
@click.option(
    "--factor",
    "factors",
    multiple=True,
    help="Allowed differing saved setting; repeat for a combined intervention.",
)
@click.option(
    "--bootstrap-samples", type=click.IntRange(min=0), default=2000, show_default=True
)
@click.option("--seed", type=int, default=0, show_default=True)
@click.option(
    "--tables-dir",
    type=click.Path(path_type=Path, file_okay=False),
    help="New directory for plotting CSVs and analysis provenance.",
)
def main(
    roster: Path,
    output: Path,
    factors: tuple[str, ...],
    bootstrap_samples: int,
    seed: int,
    tables_dir: Path | None,
) -> None:
    """Compare saved runs listed in RUNS.CSV; make no model calls."""

    try:
        if tables_dir is not None and tables_dir.exists():
            raise ValueError(f"Table directory already exists: {tables_dir}")
        result = summarize(
            roster, factors=factors, bootstrap_samples=bootstrap_samples, seed=seed
        )
        write_json_exclusive(output, result)
        if tables_dir is not None:
            export_tables(result, tables_dir)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(output))
    if result["issues"]:
        raise click.ClickException("Comparison needs attention; diagnostics were saved")


if __name__ == "__main__":
    main()
