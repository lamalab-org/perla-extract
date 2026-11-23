"""Extract, score and compare a frozen roster with one resumable command.

Planning is pure validation. Execution preflights every source, reserves spending,
then delegates to the production extractor and scorer. Resume reuses sealed work;
failed or uncertain attempts remain visible and are never silently replaced.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path

import click

from .artifacts import write_json_atomic, write_json_exclusive
from .cli import _load_env
from .comparison import arm_accounting, automatic_comparison_ready, summarize
from .comparison_study import ComparisonStudy
from .configuration import (
    implementation_sha256,
    runtime_versions,
)
from .evaluation import FACT_PROFILE_VERSION, MATCHER_VERSION
from .evaluation_cli import main as evaluate
from .experiment_plan import (
    Attempt,
    Experiment,
    prepare,
)
from .experiment_state import BudgetExceeded, CampaignSpending, campaign_lock
from .experiment_summary import write_campaign_summary, write_summary
from .extraction_prompts import prompt_sha256
from .figure_data import export_tables
from .logging import configure_logging
from .models import study_schema_sha256
from .reference_io import load_reference
from .run_artifacts import (
    COMPLETE_OUTPUT_STATUSES,
    describe_failed_run,
    file_digest,
    verify_run_manifest,
)
from .source import _parser_dependency_versions
from .workflow import run_extraction


def _identity(experiment: Experiment) -> dict:
    return {
        "inputs": {str(p): digest for p, digest in experiment.inputs.items()},
        "implementation_sha256": implementation_sha256(),
        "runtime_versions": runtime_versions(),
        "parser_dependencies": {
            name: _parser_dependency_versions(name)
            for name in {a.config.parser for a in experiment.attempts}
        },
        "schema_sha256": study_schema_sha256(),
        "prompt_sha256": prompt_sha256(),
        "matcher": MATCHER_VERSION,
        "fact_profile": FACT_PROFILE_VERSION,
        "settings": experiment.settings.model_dump(mode="json"),
        "execution_order": [a.key for a in experiment.ordered_attempts()],
        "methods": {a.arm: a.config.scientific_settings() for a in experiment.attempts},
    }


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _unchanged(experiment: Experiment, frozen: dict | None = None) -> None:
    inputs = {str(p): d for p, d in experiment.inputs.items()}
    inputs.update((frozen or {}).get("sha256", {}))
    for path, digest in inputs.items():
        if file_digest(Path(path)) != digest:
            raise ValueError(f"Input changed after the experiment was checked: {path}")
    if frozen and _identity(experiment) != frozen["plan"]:
        raise ValueError(
            "Implementation or runtime changed after the experiment was frozen"
        )


def _roster(
    path: Path, attempts: list[Attempt], *, baseline: str | None = None
) -> None:
    """Pair views reference existing attempts; they do not duplicate provider work."""
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["paper_id", "arm", "repeat", "run_dir"])
        for a in attempts:
            arm = (
                ("control" if a.arm == baseline else "treatment") if baseline else a.arm
            )
            writer.writerow(
                [
                    a.paper_id,
                    arm,
                    a.repeat,
                    str(a.config.output_dir.relative_to(path.parent))
                    if a.config.output_dir.is_relative_to(path.parent)
                    else str(a.config.output_dir),
                ]
            )


def _require_credentials(attempt: Attempt) -> None:
    for stage in attempt.config.scientific_settings()["effective_models"]:
        key = attempt.config.effective_deployment(stage).api_key_env
        if key and not os.environ.get(key):
            raise ValueError(
                f"Missing credential environment variable: {key}; set it before running (values are never recorded)"
            )


def _preflight(experiment: Experiment) -> dict:
    """Prepare all paper/arm inputs before inference and freeze the parser cache.

    Dry runs use the same production parser and request planner as paid runs.
    Shared parser settings must produce identical evidence across arms. Capacity
    and call-budget failures stop the entire roster before its first reservation.
    """
    for attempt in experiment.attempts:
        _require_credentials(attempt)
    documents = {}
    by_parser: dict[tuple[str, str], str] = {}
    for attempt in experiment.attempts:
        if attempt.repeat != 1:
            continue
        directory = (
            experiment.output
            / "preflight"
            / attempt.config.output_dir.parent.name
            / attempt.arm
        )
        config = replace(
            attempt.config, output_dir=directory, dry_run=True, langfuse=False
        )
        if directory.exists() and not (directory / "run_manifest.json").exists():
            # No provider is called during preflight; an interrupted parse is safe to repeat.
            shutil.rmtree(directory)
        if directory.exists():
            verify_run_manifest(directory)
            report = json.loads((directory / "report.json").read_text())
        else:
            report = run_extraction(config)
        if report["status"] != "dry_run" or not report["budget"]["planned_calls_fit"]:
            raise ValueError(
                f"Preflight failed for {attempt.paper_id}/{attempt.arm}: {report.get('errors', [])}; planned calls must fit max_model_calls"
            )
        digest = file_digest(directory / "document.json")
        key = (attempt.paper_id, attempt.config.parser)
        if key in by_parser and by_parser[key] != digest:
            raise ValueError(
                f"Prepared evidence differs across arms for {attempt.paper_id}"
            )
        by_parser[key] = digest
        documents[f"{attempt.paper_id}/{attempt.arm}"] = digest
    paths = [
        *(experiment.output / "cache/documents").rglob("*"),
        *(experiment.output / "preflight").rglob("*"),
    ]
    return {
        "plan": _identity(experiment),
        "document_sha256": documents,
        "sha256": {str(p): file_digest(p) for p in paths if p.is_file()},
    }


def _score(attempt: Attempt, frozen: dict) -> None:
    verify_run_manifest(attempt.config.output_dir)
    configuration = json.loads(
        (attempt.config.output_dir / "run_configuration.json").read_text()
    )
    if configuration["scientific_settings"] != attempt.config.scientific_settings():
        raise ValueError(f"Saved method differs from plan: {attempt.key}")
    report = json.loads((attempt.config.output_dir / "report.json").read_text())
    if report["status"] not in COMPLETE_OUTPUT_STATUSES:
        raise ValueError(
            describe_failed_run(report, attempt.config.output_dir / "report.json")
        )
    expected = frozen["document_sha256"][f"{attempt.paper_id}/{attempt.arm}"]
    if file_digest(attempt.config.output_dir / "document.json") != expected:
        raise ValueError(f"Run evidence differs from frozen preflight: {attempt.key}")
    score = attempt.config.output_dir / "evaluation.json"
    if not score.exists():
        evaluate.main(
            args=[
                "--truth",
                str(attempt.reference),
                "--prediction",
                str(attempt.config.output_dir),
                "--output",
                str(score),
            ],
            standalone_mode=False,
        )


def _block(result: dict, reason: str) -> None:
    result["issues"].append(reason)
    result["quality_issues"].append(reason)
    result.update(
        quality=None,
        quality_status="needs_attention",
        recorded_cost_saving_fraction=None,
    )
    result["reference_agreement"].update(
        status="blocked",
        papers=[],
        mean_paired_fact_f1_delta=None,
        paired_fact_f1_delta_interval=None,
        issues=[*result["reference_agreement"]["issues"], reason],
    )


def _paired_output(
    experiment: Experiment,
    directory: Path,
    study: ComparisonStudy,
    attempts: list[Attempt],
    stopped: str | None,
) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    study_path = directory / "study.json"
    write_json_atomic(study_path, study.model_dump(mode="json"))
    _roster(directory / "runs.csv", attempts, baseline=experiment.settings.baseline)
    result = summarize(
        directory / "runs.csv",
        study=study_path,
        bootstrap_samples=10000,
        seed=experiment.settings.order_seed,
    )
    if stopped:
        _block(result, f"Experiment stopped: {stopped}")
    write_json_atomic(directory / "comparison.json", result)
    # These are derived views. Frozen plans, run artifacts and accounting are never replaced.
    shutil.rmtree(directory / "figure-data", ignore_errors=True)
    (directory / "summary.md").unlink(missing_ok=True)
    export_tables(result, directory / "figure-data")
    write_summary(result, directory / "summary.md")
    return result


def _results(
    experiment: Experiment, spending: CampaignSpending, stopped: str | None
) -> dict:
    if experiment.legacy:
        return _paired_output(
            experiment,
            experiment.output,
            experiment.study,
            experiment.attempts,
            stopped,
        )
    comparisons = {}
    unique_runs = {}
    for arm, study in experiment.studies.items():
        groups = [("primary", study.paper_ids, experiment.settings.repeats)]
        if experiment.repeat_papers:
            groups.append(("repeatability", experiment.repeat_papers, 2))
        for phase, papers, repeats in groups:
            subset = [
                a
                for a in experiment.attempts
                if a.arm in {experiment.settings.baseline, arm}
                and a.paper_id in papers
                and a.repeat <= repeats
            ]
            paired_study = study.model_copy(
                update={"paper_ids": papers, "repeats": repeats}
            )
            name = f"{arm}-vs-{experiment.settings.baseline}/{phase}"
            result = _paired_output(
                experiment,
                experiment.output / "comparisons" / name,
                paired_study,
                subset,
                stopped,
            )
            # Full ledgers live in each paired report and once in the campaign roster.
            comparisons[name] = {
                key: value for key, value in result.items() if key != "runs"
            }
            for row in result["runs"]:
                row = row | {
                    "arm": experiment.settings.baseline
                    if row["arm"] == "control"
                    else arm
                }
                unique_runs[(row["paper_id"], row["arm"], row["repeat"])] = row
    result = {
        "comparisons": comparisons,
        "spending": spending.summary(),
        "runs": list(unique_runs.values()),
        "accounting": {
            arm: arm_accounting(
                [row for row in unique_runs.values() if row["arm"] == arm],
                sum(a.arm == arm for a in experiment.attempts),
            )
            for arm in experiment.settings.methods
        },
        "reference_agreement": {"status": "see_paired_comparisons"},
    }
    write_json_atomic(experiment.output / "comparison.json", result)
    shutil.rmtree(experiment.output / "figure-data", ignore_errors=True)
    export_tables(result, experiment.output / "figure-data")
    write_campaign_summary(
        result, experiment.output / "summary.md", experiment.settings.baseline
    )
    return result


def execute(experiment: Experiment) -> dict:
    """Reserve, extract and score each unique slot once, including after resume."""
    _unchanged(experiment)
    environment = Path(".env.local")
    _load_env(environment if environment.is_file() else None)
    output = experiment.output
    if not experiment.resume:
        output.mkdir(parents=True, exist_ok=False)
    with campaign_lock(output):
        plan = _identity(experiment)
        plan_path = output / "plan.json"
        if plan_path.exists():
            if json.loads(plan_path.read_text()) != plan:
                raise ValueError(
                    "Frozen plan differs from inputs, method, implementation or runtime; start a new experiment"
                )
        else:
            write_json_exclusive(plan_path, plan)
            (output / "experiment.toml").write_bytes(experiment.source.read_bytes())
            _roster(output / "runs.csv", experiment.attempts)
        frozen_path = output / "inputs.json"
        frozen = (
            json.loads(frozen_path.read_text())
            if frozen_path.exists()
            else _preflight(experiment)
        )
        if not frozen_path.exists():
            write_json_exclusive(frozen_path, frozen)
        _unchanged(experiment, frozen)
        spending = CampaignSpending(output, _digest(plan), experiment.settings.budget)
        errors = []
        stopped = None
        try:
            for index, attempt in enumerate(experiment.ordered_attempts(), 1):
                click.echo(
                    f"[{index}/{len(experiment.attempts)}] {attempt.paper_id} · {attempt.arm} · repeat {attempt.repeat}"
                )
                _unchanged(experiment, frozen)
                directory = attempt.config.output_dir
                if attempt.key not in spending.record.attempts:
                    _require_credentials(attempt)
                    if directory.exists():
                        raise ValueError(
                            f"Untracked run directory; refusing to dispatch: {directory}"
                        )
                    spending.reserve(attempt.key, attempt.reservation_usd)
                    try:
                        run_extraction(attempt.config)
                    except (OSError, RuntimeError, ValueError) as exc:
                        click.echo(f"Extraction failed: {exc}", err=True)
                    finally:
                        if (directory / "run_manifest.json").is_file():
                            spending.reconcile(attempt.key, directory)
                try:
                    spending.reconcile(attempt.key, directory)
                    _score(attempt, frozen)
                except (OSError, RuntimeError, ValueError, click.ClickException) as exc:
                    errors.append(
                        {
                            "run_dir": str(directory.relative_to(output)),
                            "error": str(exc),
                        }
                    )
                    click.echo(
                        f"Needs attention; retained slot and reservation: {exc}",
                        err=True,
                    )
            _unchanged(experiment, frozen)
        except BudgetExceeded as exc:
            # Missing repeatability slots must not invalidate a completed primary comparison.
            errors.append({"run_dir": "", "error": str(exc)})
        except BaseException as exc:
            stopped = str(exc) or type(exc).__name__
            errors.append({"run_dir": "", "error": stopped})
            raise
        finally:
            write_json_atomic(output / "errors.json", errors)
            write_json_atomic(output / "accounting.json", spending.summary())
            result = _results(experiment, spending, stopped)
            if errors:
                with (output / "summary.md").open("a", encoding="utf-8") as stream:
                    stream.write(
                        "\n## Execution notes\n\nSee `errors.json` for all retained failures and stops.\n\n"
                    )
                    for error in errors:
                        stream.write(f"- {error['run_dir']}: {error['error']}\n")
        return result


def _show_input_mapping(experiment: Experiment) -> None:
    click.echo("Paper/SI/reference mapping (source fingerprints verified):")
    for attempt in experiment.attempts:
        if attempt.arm != experiment.settings.baseline or attempt.repeat != 1:
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
        click.echo(
            "    Scoring: reference scope reviewed."
            if benchmark and benchmark.coverage.complete()
            else "    Scoring: diagnostic reference agreement; coverage/context review is incomplete."
        )


@click.command()
@click.argument(
    "experiment_file", type=click.Path(path_type=Path, exists=True, dir_okay=False)
)
@click.option(
    "--check",
    is_flag=True,
    help="Check all mappings/settings without parsing, writing output or calling a model.",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Continue the unchanged frozen experiment; never repeat a reserved attempt.",
)
def main(experiment_file: Path, check: bool, resume: bool) -> None:
    """Extract, score and compare the arms of EXPERIMENT_FILE."""
    configure_logging(level="INFO", json_output=False)
    try:
        experiment = prepare(experiment_file, resume=resume)
        _show_input_mapping(experiment)
        click.echo(
            f"{len(experiment.study.paper_ids)} paper(s), {len(experiment.settings.methods)} arms: {len(experiment.attempts)} runs"
        )
        for arm, study in experiment.studies.items():
            for name, contrast in sorted(study.factors.items()):
                click.echo(
                    f"  {arm}: {name}: {json.dumps(contrast.control)} → {json.dumps(contrast.treatment)}"
                )
        click.echo(f"Output: {experiment.output}")
        if check:
            click.echo(
                "Inputs and settings valid. No model calls or output files created."
            )
            return
        result = execute(experiment)
        click.echo(f"Read first: {experiment.output / 'summary.md'}")
        comparisons = result.get("comparisons", {"paired": result})
        if any(
            r["issues"] and not automatic_comparison_ready(r)
            for r in comparisons.values()
        ):
            raise ValueError(
                "Experiment needs attention; see summary.md and errors.json"
            )
    except (OSError, RuntimeError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc


if __name__ == "__main__":
    main()
