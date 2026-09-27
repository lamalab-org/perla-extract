"""Compare planned arms without dropping failures or treating repeats as papers.

This reads existing score reports; it never calls a model or changes a prediction.
Missing scores remain visible and prevent a whole-roster quality or cost claim.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean

import click

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.evaluation import (
    EvaluationReport,
    aggregate_evaluations,
)

ARMS = ("control", "treatment")


def summarize(plan_path: Path) -> dict:
    """Retain the full roster and require compatible references before pairing.

    Repeated runs estimate variability on the same papers. Average their paired
    differences within each paper before averaging across papers. Cost totals are
    withheld if any job lacks complete accounting, rather than treating it as free.
    """

    plan_bytes = plan_path.read_bytes()
    plan = json.loads(plan_bytes)
    jobs = plan["jobs"]
    keys = [(j["paper_id"], j["repeat"], j["arm"]) for j in jobs]
    papers = sorted({key[0] for key in keys})
    repeats = plan["repeats"]
    if type(repeats) is not int or repeats < 1:
        raise ValueError("Plan repeats must be a positive integer")
    expected = {
        (paper, repeat, arm)
        for paper in papers
        for repeat in range(1, repeats + 1)
        for arm in ARMS
    }
    if not jobs or len(set(keys)) != len(keys) or set(keys) != expected:
        raise ValueError("Plan must contain both arms for every paper and repeat")

    reports = {}
    rows = []
    paths = set()
    for key, job in zip(keys, jobs):
        command = job["score"]
        path = Path(command[command.index("--output") + 1])
        if not path.is_absolute():
            raise ValueError("Use absolute score paths from the prepared plan")
        if path in paths:
            raise ValueError("Each planned job needs a distinct score report")
        paths.add(path)
        row = dict(zip(("paper_id", "repeat", "arm"), key))
        row.update(report=str(path), issues=[])
        try:
            content = path.read_bytes()
            report = EvaluationReport.model_validate_json(content)
            row["report_sha256"] = hashlib.sha256(content).hexdigest()
            reports[key] = report
            row["macro_f1"] = report.core_facts.macro_f1
            row["run"] = (
                report.run_efficiency.model_dump() if report.run_efficiency else None
            )
            row["prediction_validation"] = (
                report.prediction_validation.model_dump()
                if report.prediction_validation
                else None
            )
            if report.benchmark is None or report.benchmark.paper_id != key[0]:
                row["issues"].append("Missing or mismatched benchmark paper identity")
            if report.core_facts.scoring_status != "ready":
                row["issues"].append("Resolve scoring alignment issues")
            if report.core_facts.macro_f1 is None:
                row["issues"].append("No defined core-fact F1")
        except (OSError, ValueError) as error:
            row["issues"].append(f"Cannot read score: {error}")
        rows.append(row)

    issues = [
        f"{r['paper_id']}/{r['repeat']}/{r['arm']}: {s}"
        for r in rows
        for s in r["issues"]
    ]
    # Check the same reference across *all* repetitions, not only within each pair.
    for paper in papers:
        found = [report for key, report in reports.items() if key[0] == paper]
        if found and any(
            report.truth_content_sha256 != found[0].truth_content_sha256
            or report.benchmark != found[0].benchmark
            for report in found[1:]
        ):
            issues.append(f"{paper}: references differ between arms or repetitions")
    found = list(reports.values())
    if found and any(
        (r.config, r.matcher_version, r.study_schema_sha256)
        != (found[0].config, found[0].matcher_version, found[0].study_schema_sha256)
        for r in found[1:]
    ):
        issues.append("Scorer configuration, matcher, or schema differs")

    quality = None
    if not issues:
        by_repeat = []
        try:
            for repeat in range(1, repeats + 1):
                aggregates = {
                    arm: aggregate_evaluations(
                        [reports[paper, repeat, arm] for paper in papers],
                        bootstrap_samples=0,
                    )
                    for arm in ARMS
                }
                by_repeat.append(
                    {
                        "repeat": repeat,
                        "groups": {
                            arm: {
                                group: counts.model_dump()
                                for group, counts in aggregate.core_fact_groups_micro.items()
                            }
                            for arm, aggregate in aggregates.items()
                        },
                    }
                )
            paired = [
                {
                    "paper_id": paper,
                    "repeat_deltas": [
                        reports[paper, repeat, "treatment"].core_facts.macro_f1
                        - reports[paper, repeat, "control"].core_facts.macro_f1
                        for repeat in range(1, repeats + 1)
                    ],
                }
                for paper in papers
            ]
            for pair in paired:
                pair["mean_delta"] = mean(pair["repeat_deltas"])
            quality = {
                "mean_paired_macro_f1_delta": mean(
                    pair["mean_delta"] for pair in paired
                ),
                "papers": paired,
                "by_repeat": by_repeat,
            }
        except ValueError as error:
            issues.append(str(error))

    accounting = {}
    for arm in ARMS:
        runs = [
            r.run_efficiency
            for key, r in reports.items()
            if key[2] == arm and r.run_efficiency is not None
        ]
        complete = len(runs) == len(papers) * repeats and all(
            r.cost_tracking_complete is True for r in runs
        )
        observed_cost = sum(r.cost_usd for r in runs)
        accounting[arm] = {
            "planned_runs": len(papers) * repeats,
            "scored_runs": sum(key[2] == arm for key in reports),
            "accounted_runs": len(runs),
            "noncomplete_runs": sum(r.status != "complete" for r in runs),
            "cache_hits": sum(r.cache_hits for r in runs),
            "recorded_scored_cost_usd": observed_cost,
            "cost_tracking_complete": complete,
            "total_cost_usd": observed_cost if complete else None,
        }
    control = accounting["control"]["total_cost_usd"]
    treatment = accounting["treatment"]["total_cost_usd"]
    saving = (
        (control - treatment) / control
        if control is not None and control > 0 and treatment is not None
        else None
    )
    return {
        "format_version": 1,
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "study": plan["study"],
        "revision": plan["revision"],
        "paper_count": len(papers),
        "repeats": repeats,
        "quality_status": "needs_attention" if issues else "ready",
        "issues": issues,
        "quality": quality,
        "accounting": accounting,
        "recorded_cost_saving_fraction": saving,
        "runs": rows,
    }


@click.command()
@click.argument("plan", type=click.Path(path_type=Path, exists=True, dir_okay=False))
@click.option(
    "--output", type=click.Path(path_type=Path, dir_okay=False), required=True
)
def main(plan: Path, output: Path) -> None:
    """Summarize a prepared study after scoring; no model calls or automatic promotion."""

    try:
        if output.exists():
            raise ValueError(
                "Choose a new output path; existing artifacts are not overwritten"
            )
        result = summarize(plan)
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        raise click.ClickException(str(error)) from error
    write_json_atomic(output, result)
    click.echo(
        f"Wrote {output}: {result['quality_status']}; {result['paper_count']} papers"
    )
    if result["issues"]:
        raise click.ClickException(
            "Comparison incomplete; inspect issues in the saved report"
        )


if __name__ == "__main__":
    main()
