"""Explain an experiment's verified comparison without computing another score."""

from __future__ import annotations

import json
from pathlib import Path

from .artifacts import write_bytes_exclusive
from .run_artifacts import COMPLETE_OUTPUT_STATUSES
from .scoring_fields import IMPORTANT_FIELDS


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
    scored_pairs = set.intersection(
        *(
            {
                (r["paper_id"], r["repeat"])
                for r in result["runs"]
                if r["arm"] == arm
                and r.get("scientific_scores")
                and r.get("accounting", {}).get("status") in COMPLETE_OUTPUT_STATUSES
            }
            for arm in ("control", "treatment")
        )
    )
    lines.extend(
        [
            "",
            "## Important scientific fields",
            "",
            f"Counts pooled over the same {len(scored_pairs)} paper/repetition pairs with completed, scored outputs in both arms. Failed or unscored runs remain in the completion table; these conditional scores do not measure their missing values. Interpret these as reference agreement unless the annotation scope is complete.",
            "",
            "| Arm | Field | TP | FP | FN | Precision | Recall | F1 |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm in ("control", "treatment"):
        scores = [
            r["scientific_scores"]
            for r in result["runs"]
            if r["arm"] == arm
            and (r["paper_id"], r["repeat"]) in scored_pairs
            and r.get("scientific_scores")
        ]
        for key, label in IMPORTANT_FIELDS.items():
            # Reuse existing strata: an aggregate must not enter an individual
            # measurement's headline denominator merely because both report PCE.
            levels = (
                ("individual", "population")
                if key
                in {
                    "measurement:pce",
                    "measurement:ff",
                    "measurement:voc",
                    "measurement:jsc",
                }
                else (None,)
            )
            for level in levels:
                counts = (
                    [
                        count
                        for score in scores
                        for name, count in score["strata"].items()
                        if name.startswith(f"{key}|{level}|")
                    ]
                    if level
                    else [
                        score["fields"][key]
                        for score in scores
                        if key in score["fields"]
                    ]
                )
                truth, predicted, matched = (
                    sum(c[k] for c in counts) for k in ("truth", "predicted", "matched")
                )
                if not (truth or predicted):
                    continue
                rates = [
                    matched / predicted if predicted else None,
                    matched / truth if truth else None,
                    2 * matched / (truth + predicted) if truth + predicted else None,
                ]
                formatted = " | ".join(
                    f"{v:.1%}" if v is not None else "—" for v in rates
                )
                display = f"{label} ({level})" if level else label
                lines.append(
                    f"| {arm} | {display} | {matched} | {predicted - matched} | {truth - matched} | {formatted} |"
                )
    lines.extend(
        [
            "",
            "[field_scores.csv](figure-data/field_scores.csv) retains every scored output, including those outside this shared cohort. [field_strata.csv](figure-data/field_strata.csv) separates measurement types and population statistics. This table is conditional on shared scored outputs, not a full-roster model ranking.",
        ]
    )
    lines.extend(
        [
            "",
            "## Attribution context",
            "",
            "[scoring_diagnostics.csv](figure-data/scoring_diagnostics.csv) records the rule, parsing status and overlapping candidate blockers for every fact. Context availability is independent of value agreement; it is not a probability of correctness.",
            "",
            "| Arm | Predicted facts | With compatible attribution context | Literal quantity claims |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for arm in ("control", "treatment"):
        coverage = [
            r["scientific_scores"]["diagnostics"]["coverage"]["prediction"]
            for r in result["runs"]
            if r["arm"] == arm and r.get("scientific_scores")
        ]
        lines.append(
            f"| {arm} | {sum(c['total'] for c in coverage)} | {sum(c['context_available'] for c in coverage)} | {sum(c['literal_values'] for c in coverage)} |"
        )
    lines.extend(
        [
            "",
            "These counts describe all scored outputs, including partial runs; they are not the paired complete-run contrast below.",
        ]
    )
    lines.extend(["", "## Reference agreement", ""])
    quality = result["quality"]
    if quality is None:
        diagnostic = result.get("reference_agreement") or {}
        if diagnostic.get("mean_paired_fact_f1_delta") is not None:
            delta = diagnostic["mean_paired_fact_f1_delta"]
            lines.extend(
                [
                    "",
                    f"**Automatic reference-agreement difference: {delta:+.4f} attributed fact micro F1 (treatment minus control).**",
                    f"Scope: `{diagnostic['status']}`; {diagnostic['complete_pair_count']} / {diagnostic['planned_pair_count']} complete paired repetitions across {len(diagnostic['papers'])} papers.",
                    "This is an automatic comparison against the fixed references, not validated extraction accuracy. Unresolved facts retain zero credit; failures and partial runs do not enter this contrast.",
                    "See [paired_reference_agreement.csv](figure-data/paired_reference_agreement.csv) for the contributing papers and repetitions; omitted pairs are listed in comparison.json.",
                ]
            )
            interval = diagnostic["paired_fact_f1_delta_interval"]
            if interval["status"] == "available":
                lines.append(
                    f"95% paper-bootstrap interval, conditional on complete pairs: {interval['ci95_lower']:+.4f} to {interval['ci95_upper']:+.4f}."
                )
        else:
            lines.append(
                "**Comparison unavailable.** No valid paired effect estimate is available. "
                "Per-run scores remain in the evaluation files and plotting tables."
            )
        lines.extend(
            [
                "",
                "**Reviewed-scope conclusion unavailable.** Inspect the issues below for "
                "incomplete reference review, ambiguous scoring, or missing, failed or inconsistent runs. "
                "An available automatic reference-agreement result does not resolve these limitations.",
            ]
        )
    else:
        delta = quality["mean_paired_fact_f1_delta"]
        lines.extend(
            [
                f"**Treatment − control: {delta:+.4f} attributed fact micro F1 ({delta * 100:+.2f} percentage points).**",
                "Positive favors treatment; negative favors control; zero means equal mean scores.",
            ]
        )
        interval = quality["paired_fact_f1_delta_interval"]
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
                "Attributed scoring requires the value and its scientific context to match. "
                "Each paper's F1 uses matched, predicted and reference fact counts pooled across groups. "
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
    lines.extend(
        [
            "",
            "## Cost at corpus scale",
            "",
            "All listed spend, including failures, divided by completed paired extractions. Completion does not establish scientific correctness. Projections assume the same paper mix, provider rates and failure rate; they exclude parsing infrastructure, storage and unreported GPU charges. Unknown cost or no completed outputs leaves the rate unavailable.",
            "",
            "| Arm | Completed extractions | USD / completed extraction | Projected USD / 10,000 | Projected USD / 50,000 |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm in ("control", "treatment"):
        counts = result["accounting"][arm]
        values = [
            counts["cost_per_completed_extraction_usd"],
            *(counts["projected_cost_usd"][str(size)] for size in (10_000, 50_000)),
        ]
        formatted = " | ".join(
            "unavailable" if value is None else f"{value:,.4f}" for value in values
        )
        lines.append(f"| {arm} | {counts['paired_complete_runs']} | {formatted} |")
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
            "| Which papers improved or worsened? | [figure-data/paired_reference_agreement.csv](figure-data/paired_reference_agreement.csv): automatic treatment minus control; [paired_papers.csv](figure-data/paired_papers.csv) applies the additional review checks |",
            "| Which fact groups changed? | [figure-data/group_scores.csv](figure-data/group_scores.csv): use `attributed` for primary quality and `recovery` for information found; inspect explanations for context errors |",
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
