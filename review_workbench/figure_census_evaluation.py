"""Score figure-panel proposals against exported human annotations."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

import click

from perla_extract.study_extraction.artifacts import write_json_atomic

PanelKey = tuple[str, str, str]


def _key(paper_id: str, panel: dict[str, Any]) -> PanelKey:
    """Use only reviewer-visible identifiers when aligning proposed and gold panels."""

    return (
        paper_id,
        str(panel.get("figure_number", "")).strip().casefold(),
        str(panel.get("panel_label", "")).strip().casefold(),
    )


def _index(
    rows: list[tuple[str, dict[str, Any]]], *, name: str
) -> dict[PanelKey, dict[str, Any]]:
    """Reject duplicate panel identities because arbitrary pairing would bias scores."""

    indexed: dict[PanelKey, dict[str, Any]] = {}
    for paper_id, panel in rows:
        key = _key(paper_id, panel)
        if not key[1]:
            raise ValueError(f"{name} contains a panel without a figure number")
        if key in indexed:
            raise ValueError(f"{name} contains duplicate panel {key}")
        indexed[key] = panel
    return indexed


def proposal_rows(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Read both caption batches and per-paper visual proposal artifacts."""

    papers = payload.get("papers")
    if isinstance(papers, dict):
        return [
            (str(paper_id), panel)
            for paper_id, paper in papers.items()
            if isinstance(paper, dict)
            for panel in paper.get("panels", [])
            if isinstance(panel, dict)
        ]
    proposal = payload.get("review_proposal")
    paper_id = payload.get("paper_id")
    if isinstance(proposal, dict) and isinstance(paper_id, str):
        return [
            (paper_id, panel)
            for panel in proposal.get("panels", [])
            if isinstance(panel, dict)
        ]
    raise ValueError("proposal must contain papers or a per-paper review_proposal")


def gold_rows(path: Path, reviewer_id: str | None) -> list[tuple[str, dict[str, Any]]]:
    """Load the app's lossless figure_panels.csv export and optionally select a reviewer."""

    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    available = sorted({row.get("reviewer_id", "") for row in rows})
    if reviewer_id is None and len(available) > 1:
        raise ValueError(
            "gold CSV contains multiple reviewers; pass --reviewer-id or adjudicate first"
        )
    selected = reviewer_id or (available[0] if available else "")
    return [
        (str(row["paper_id"]), row)
        for row in rows
        if row.get("reviewer_id", "") == selected
    ]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _attribute_value(value: object) -> str:
    """Normalize JSON and CSV scalar representations without changing semantics."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip().casefold()


def evaluate_panels(
    proposed_rows: list[tuple[str, dict[str, Any]]],
    human_rows: list[tuple[str, dict[str, Any]]],
) -> dict[str, Any]:
    """Measure panel detection first, then attributes only on exactly aligned panels."""

    proposed = _index(proposed_rows, name="proposal")
    human = _index(human_rows, name="gold")
    proposed_keys, human_keys = set(proposed), set(human)
    matched = sorted(proposed_keys & human_keys)
    true_positive = len(matched)
    false_positive = len(proposed_keys - human_keys)
    false_negative = len(human_keys - proposed_keys)
    attributes = (
        "figure_class",
        "data_presentation",
        "extraction_feasibility",
        "schema_relevant",
        "x_axis_label",
        "y_axis_label",
    )
    agreements: dict[str, int] = Counter()
    class_confusion: Counter[str] = Counter()
    for key in matched:
        predicted, gold = proposed[key], human[key]
        for attribute in attributes:
            predicted_value = _attribute_value(predicted.get(attribute, ""))
            gold_value = _attribute_value(gold.get(attribute, ""))
            if predicted_value == gold_value:
                agreements[attribute] += 1
        class_confusion[
            f"{gold.get('figure_class', '')} -> {predicted.get('figure_class', '')}"
        ] += 1
    return {
        "panel_detection": {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": _ratio(true_positive, true_positive + false_positive),
            "recall": _ratio(true_positive, true_positive + false_negative),
            "f1": _ratio(
                2 * true_positive, 2 * true_positive + false_positive + false_negative
            ),
        },
        "matched_panel_attributes": {
            attribute: {
                "agreements": agreements[attribute],
                "total": len(matched),
                "accuracy": _ratio(agreements[attribute], len(matched)),
            }
            for attribute in attributes
        },
        "class_confusion": dict(sorted(class_confusion.items())),
        "false_positive_panels": [
            list(key) for key in sorted(proposed_keys - human_keys)
        ],
        "missed_panels": [list(key) for key in sorted(human_keys - proposed_keys)],
    }


@click.command()
@click.option("--proposal", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--gold-csv", type=click.Path(path_type=Path, exists=True), required=True)
@click.option("--reviewer-id")
@click.option("--output", type=click.Path(path_type=Path), required=True)
def main(proposal: Path, gold_csv: Path, reviewer_id: str | None, output: Path) -> None:
    """Evaluate a frozen proposal against one reviewer or an adjudicated CSV."""

    predicted = proposal_rows(json.loads(proposal.read_text(encoding="utf-8")))
    gold = gold_rows(gold_csv, reviewer_id)
    result = evaluate_panels(predicted, gold)
    write_json_atomic(output, result)
    click.echo(f"Wrote figure-census evaluation to {output}")


if __name__ == "__main__":
    main()
