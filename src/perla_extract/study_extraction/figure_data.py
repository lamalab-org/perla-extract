"""Export verified comparison results as plotting tables without rescoring them.

Rows retain paper, arm and repetition so figures can preserve pairing. Missing
measurements stay empty; they never become zeros. Aggregate uncertainty comes from
paper resampling in comparison.py, not from treating facts or repeats as papers.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .artifacts import write_json_exclusive


def _write(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def export_tables(result: dict, directory: Path) -> None:
    """Write a fresh table directory, including failures and analysis provenance."""
    directory.mkdir(parents=True, exist_ok=False)
    runs: list[dict] = []
    groups: list[dict] = []
    subfields: list[dict] = []
    for run in result["runs"]:
        identity = {name: run[name] for name in ("paper_id", "arm", "repeat")}
        accounting = run.get("accounting", {})
        runs.append(
            {
                **identity,
                "role": run.get("role", "paired"),
                "quality_applicable": run.get("quality_applicable", True),
                "run_id": run.get("run_id"),
                "run_dir": run["run_dir"],
                "quality_available": run["quality_available"],
                "validation_status": (run.get("prediction_validation") or {}).get(
                    "status", "unavailable"
                ),
                "validation_issues": " | ".join(
                    f"{item['category']}: {item['path']}: {item['reason']}"
                    for item in (run.get("prediction_validation") or {}).get(
                        "issues", []
                    )
                ),
                "input_warnings": " | ".join(run.get("input_warnings", [])),
                "cost_available": run["cost_available"],
                "status": accounting.get("status"),
                "macro_f1": run.get("macro_f1"),
                "cost_sources": " | ".join(accounting.get("cost_sources", [])),
                "known_cost_usd": accounting.get("cost_usd"),
                "total_cost_usd": accounting.get("cost_usd")
                if run["cost_available"]
                else None,
                "token_tracking_complete": accounting.get("token_tracking_complete"),
                "known_total_tokens": accounting.get("total_tokens"),
                "prompt_tokens": accounting.get("prompt_tokens")
                if accounting.get("token_tracking_complete") is True
                else None,
                "completion_tokens": accounting.get("completion_tokens")
                if accounting.get("token_tracking_complete") is True
                else None,
                "total_tokens": accounting.get("total_tokens")
                if accounting.get("token_tracking_complete") is True
                else None,
                "provider_requests": accounting.get("provider_requests"),
                "cache_hits": accounting.get("cache_hits"),
                "elapsed_seconds": accounting.get("elapsed_seconds"),
                "issues": " | ".join(run["issues"]),
            }
        )
        score = run.get("scientific_scores")
        if not score:
            continue
        for view, scores in (
            ("strict", score),
            ("value_only", score["value_only"]),
            ("attribution", score["attribution"]),
        ):
            for category, rows in (("groups", groups), ("subfields", subfields)):
                for name, counts in scores[category].items():
                    rows.append(
                        {
                            **identity,
                            "quality_available": run["quality_available"],
                            "view": view,
                            "group" if category == "groups" else "subfield": name,
                            **{
                                key: counts[key]
                                for key in (
                                    "truth",
                                    "predicted",
                                    "matched",
                                    "precision",
                                    "recall",
                                    "f1",
                                )
                            },
                        }
                    )
    _write(directory / "runs.csv", list(runs[0]), runs)
    for filename, category, rows in (
        ("group_scores.csv", "group", groups),
        ("subfield_scores.csv", "subfield", subfields),
    ):
        _write(
            directory / filename,
            [
                "paper_id",
                "arm",
                "repeat",
                "quality_available",
                "view",
                category,
                "truth",
                "predicted",
                "matched",
                "precision",
                "recall",
                "f1",
            ],
            rows,
        )
    papers = [
        {
            "paper_id": paper["paper_id"],
            "repeat_count": len(paper["repeat_deltas"]),
            "mean_macro_f1_delta": paper["mean_delta"],
        }
        for paper in (result.get("quality") or {}).get("papers", [])
    ]
    _write(
        directory / "paired_papers.csv",
        ["paper_id", "repeat_count", "mean_macro_f1_delta"],
        papers,
    )
    write_json_exclusive(
        directory / "analysis.json",
        {key: value for key, value in result.items() if key != "runs"},
    )
