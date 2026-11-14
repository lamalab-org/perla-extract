"""Explain an experiment's verified comparison without computing another score."""

from __future__ import annotations

import json
from pathlib import Path

from .artifacts import write_bytes_exclusive


def write_summary(result: dict, path: Path) -> None:
    """Write the first file a researcher should read after an experiment finishes.

    Quality and accounting have separate availability checks. A failed comparison
    must not acquire an effect estimate here, and a missing cost must not become
    zero. All numerical results come directly from the saved comparison.
    """
    study = result["study"]
    lines = [
        "# Experiment result",
        "",
        f"Paper count: {len(study['paper_ids'])}. Repeats per arm: {study['repeats']}. "
        f"Planned runs: {len(study['paper_ids']) * 2 * study['repeats']}.",
        "",
        "## Compared settings",
        "",
    ]
    for name, values in sorted(study["factors"].items()):
        control, treatment = (
            json.dumps(values[arm], ensure_ascii=False)
            for arm in ("control", "treatment")
        )
        lines.append(f"- `{name}`: control `{control}` → treatment `{treatment}`.")
    lines.extend(["", "## Quality", ""])
    quality = result["quality"]
    if quality is None:
        lines.extend(
            [
                "**Comparison unavailable.** Missing, failed or inconsistent runs prevent a valid paired result.",
                "Inspect the issues below and retain failed attempts when accounting for resources.",
            ]
        )
    else:
        delta = quality["mean_paired_macro_f1_delta"]
        lines.extend(
            [
                f"**Treatment − control: {delta:+.4f} strict macro F1 ({delta * 100:+.2f} percentage points).**",
                "Positive favors treatment; negative favors control; zero means equal mean scores.",
            ]
        )
        interval = quality["paired_macro_f1_delta_interval"]
        if interval["status"] == "available":
            lines.append(
                f"95% paper-bootstrap interval: {interval['ci95_lower']:+.4f} to {interval['ci95_upper']:+.4f}."
            )
        else:
            lines.append(
                "95% paper-bootstrap interval: unavailable ("
                + (
                    "only one paper"
                    if interval["status"] == "insufficient_papers"
                    else "bootstrap disabled"
                )
                + ")."
            )
        lines.extend(
            [
                "",
                "Strict scoring requires the value and its scientific context to match. "
                "F1 is averaged over populated fact groups within each paper. "
                "Repeat differences are averaged within a paper, then papers receive equal weight.",
                "The interval resamples papers, conditional on the observed repeats. "
                "An interval spanning zero does not establish a direction; a positive mean alone is not evidence of a reliable improvement.",
                "Passing consistency checks does not certify reference accuracy or performance on unseen papers.",
            ]
        )
    lines.extend(
        [
            "",
            "## Coverage and recorded spend",
            "",
            "| Arm | Scored / planned runs | Recorded attempts | Total USD | Known USD subtotal |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for arm in ("control", "treatment"):
        counts = result["accounting"][arm]
        total = counts["total_cost_usd"]
        cost = "unavailable" if total is None else f"{total:.6f}"
        lines.append(
            f"| {arm} | {counts['paired_scored_runs']} / {counts['planned_runs']} "
            f"| {counts['listed_attempts']} | {cost} | {counts['known_cost_usd']:.6f} |"
        )
    validation = result["validation_coverage"]
    lines.extend(
        [
            "",
            f"Evidence/link validation: {validation['checked_runs']} checked runs, "
            f"{validation['unavailable_runs']} unavailable, {validation['issues']} issue(s). "
            "Inspect validation details in figure-data/runs.csv; citation checks do not establish scientific support.",
        ]
    )
    lines.extend(
        [
            "",
            "The known subtotal includes only recorded amounts; zero does not mean a free run when the total is unavailable. "
            "Spend includes failed attempts. Recorded USD comes from provider adapters, not audited bills. "
            "For open models, also inspect tokens, requests and elapsed time; GPU cost must be measured separately.",
            "",
            "## Files to inspect",
            "",
            "| Question | File |",
            "| --- | --- |",
            "| Which papers improved or worsened? | [figure-data/paired_papers.csv](figure-data/paired_papers.csv): treatment minus control per paper |",
            "| Which fact groups changed? | [figure-data/group_scores.csv](figure-data/group_scores.csv): use `strict` for primary quality; `value_only` and `attribution` diagnose errors |",
            "| Which specific fields changed? | [figure-data/subfield_scores.csv](figure-data/subfield_scores.csv): material identity, outcomes, protocol and processing breakdowns; filter `quality_available` for verified runs |",
            "| What did each attempt consume? | [figure-data/runs.csv](figure-data/runs.csv): scores, tokens, requests, time, cost and failures; empty cells mean unavailable |",
            "| Which exact values were wrong? | `runs/paper-NNN/ARM-REPEAT/evaluation.json`: matched and unmatched field paths; `extraction.json` contains the prediction |",
            "| Why did a run fail? | [errors.json](errors.json) and that run's `report.json` / `events.jsonl` |",
            "| What was planned and scored? | [experiment.toml](experiment.toml), [runs.csv](runs.csv) (the planned roster), [inputs.json](inputs.json) (hashes), [comparison.json](comparison.json) (full result) |",
        ]
    )
    if result["issues"]:
        issues = result["issues"]
        lines.extend(["", "## Needs attention", ""])
        lines.extend(f"- {issue}" for issue in issues[:10])
        if len(issues) > 10:
            lines.append(f"- {len(issues) - 10} further issues are in comparison.json.")
    write_bytes_exclusive(path, ("\n".join(lines) + "\n").encode("utf-8"))
