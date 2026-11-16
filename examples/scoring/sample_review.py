"""Sample scored prediction and reference fields for independent expert review.

Sampling is without replacement within each side, scientific group and awarded/denied
credit stratum. Reviewers receive the complete scientific records and blank labels;
the scoring decisions and inclusion probabilities remain in a separate analyst key.
This prepares evidence for calibration. It does not provide expert judgments.
"""

from __future__ import annotations

import csv
import json
import random
from collections import defaultdict
from pathlib import Path

import click

from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.evaluation import EvaluationReport, _study_digest
from perla_extract.study_extraction.models import StudyExtraction
from perla_extract.study_extraction.reference_io import load_reference
from perla_extract.study_extraction.run_artifacts import (
    file_digest,
    verify_run_manifest,
)
from perla_extract.study_extraction.scoring_facts import scientific_facts

from .score_changes import decision_change, decisions

LABEL_COLUMNS = [
    "item_id",
    "group",
    "side",
    "path",
    "view",
    "same_identity",
    "equivalent_value",
    "correct_attribution",
    "credit_should_be_awarded",
    "reference_supported",
    "source_accessibility",
    "source_location",
    "reason",
]


def prepare(
    truth: Path,
    prediction: Path,
    output: Path,
    per_stratum: int,
    seed: int,
    score_path: Path | None = None,
    views: tuple[str, ...] = ("attributed",),
    baseline: Path | None = None,
) -> int:
    """Bind a saved score to its inputs, then sample both complete fact ledgers."""
    if output.exists():
        raise ValueError(f"Output already exists: {output}; choose a new directory")
    if per_stratum < 1:
        raise ValueError("per_stratum must be positive")
    if (
        not views
        or set(views) - {"attributed", "recovery"}
        or len(set(views)) != len(views)
    ):
        raise ValueError("Choose attributed, recovery, or both views once")
    expected, benchmark = load_reference(truth)
    actual = StudyExtraction.model_validate_json(
        (prediction / "extraction.json").read_bytes()
    )
    score_path = score_path or prediction / "evaluation.json"
    score = EvaluationReport.model_validate_json(score_path.read_bytes())
    manifest, digest = verify_run_manifest(prediction)
    if (
        score.truth_content_sha256 != _study_digest(expected)
        or score.prediction_content_sha256 != _study_digest(actual)
        or score.benchmark != benchmark
        or score.run_manifest_sha256 != digest
        or score.run_id != manifest.run_id
    ):
        raise ValueError(
            "Score does not match the supplied reference and sealed run; score them again"
        )
    previous = json.loads(baseline.read_text(encoding="utf-8")) if baseline else None
    if previous is not None and any(
        previous.get(key) != getattr(score, key)
        for key in ("prediction_content_sha256", "run_id", "run_manifest_sha256")
    ):
        raise ValueError("Baseline does not describe the same sealed prediction")
    strata = defaultdict(list)
    payload = score.model_dump(mode="json")
    for side, study in (("prediction", actual), ("truth", expected)):
        facts = scientific_facts(study, {}, side, score.config.operation_aliases).facts
        if {fact.path for fact in facts} != set(score.core_facts.fact_sources[side]):
            raise ValueError(
                f"Scored {side} paths differ from the current fact projection"
            )
        for view in views:
            current = decisions(payload, view, side)
            old = decisions(previous, view, side) if previous is not None else None
            for fact in facts:
                state = current[fact.path]
                change = (
                    decision_change(old.get(fact.path), state)
                    if old is not None
                    else "not_compared"
                )
                strata[(view, side, fact.group, state["credited"], change)].append(
                    fact.path
                )
    if not strata:
        raise ValueError("Neither input has scientific facts to sample")
    rng = random.Random(seed)
    selected, counts = [], []
    for (view, side, group, accepted, change), paths in sorted(strata.items()):
        count = min(per_stratum, len(paths))
        counts.append(
            {
                "side": side,
                "view": view,
                "change": change,
                "group": group,
                "scorer_awarded_credit": accepted,
                "population": len(paths),
                "sample": count,
            }
        )
        for path in rng.sample(sorted(paths), count):
            selected.append(
                {
                    "side": side,
                    "view": view,
                    "change": change,
                    "group": group,
                    "path": path,
                    "scorer_awarded_credit": accepted,
                    "inclusion_probability": count / len(paths),
                }
            )
    rng.shuffle(selected)
    for index, item in enumerate(selected, 1):
        item["item_id"] = f"item-{index:04d}"
    review = output / "review"
    review.mkdir(parents=True)
    write_json_exclusive(review / "candidate.json", actual.model_dump(mode="json"))
    write_json_exclusive(review / "reference.json", expected.model_dump(mode="json"))
    (review / "README.md").write_text(
        "# Independent scoring review\n\n"
        "For each CSV row, inspect its path in candidate.json (prediction) or "
        "reference.json (truth), the surrounding records, and the paper/SI.\n\n"
        "Attributed credit requires the right value and scientific owner/context. "
        "Recovery credit requires a compatible concept/value somewhere in the paper, "
        "even under another owner. Neither view establishes source support.\n\n"
        "Fill judgments from the source, not from a model score. Use yes, no or "
        "uncertain; record the source location and reason. Leave unreviewed items "
        "blank. The same path may occur in both views. Review source omissions "
        "separately; these rows alone cannot establish extraction recall.\n\n"
        "Lock judgments before opening the separate analyst key. It contains "
        "scorer decisions and sampling probabilities.\n",
        encoding="utf-8",
    )
    with (review / "judgments.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(
            {key: item.get(key, "") for key in LABEL_COLUMNS} for item in selected
        )
    write_json_exclusive(
        output / "analyst-key.json",
        {
            "sampling_unit": "scientific fact decision within one side and score view of this run",
            "sampling_method": "without replacement within view, side, group, credit and change stratum",
            "views": list(views),
            "baseline_sha256": file_digest(baseline) if baseline else None,
            "baseline_truth_content_sha256": previous["truth_content_sha256"]
            if previous
            else None,
            "baseline_contract": {
                key: previous[key] for key in ("matcher_version", "config")
            }
            if previous
            else None,
            "seed": seed,
            "per_stratum": per_stratum,
            "strata": counts,
            "items": selected,
            "score_sha256": file_digest(score_path),
            "run_id": manifest.run_id,
            "run_manifest_sha256": digest,
            "truth_content_sha256": score.truth_content_sha256,
            "prediction_content_sha256": score.prediction_content_sha256,
            "config": score.config.model_dump(mode="json"),
            "matcher_version": score.matcher_version,
            "fact_profile": score.core_facts.profile,
            "review_files_sha256": {
                path.name: file_digest(path) for path in sorted(review.iterdir())
            },
            "limits": "No expert labels supplied. Does not estimate extraction recall, reference completeness or corpus accuracy. Records and writing style may reveal candidate origin.",
        },
    )
    return len(selected)


@click.command()
@click.option("--truth", required=True, type=click.Path(path_type=Path, exists=True))
@click.option(
    "--prediction",
    required=True,
    type=click.Path(path_type=Path, exists=True, file_okay=False),
)
@click.option(
    "--output", required=True, type=click.Path(path_type=Path, file_okay=False)
)
@click.option(
    "--score",
    "score_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Fresh evaluation JSON; defaults to the run evaluation.json.",
)
@click.option("--per-stratum", type=click.IntRange(min=1), default=5, show_default=True)
@click.option("--seed", type=int, default=0, show_default=True)
@click.option(
    "--view",
    type=click.Choice(["attributed", "recovery", "both"]),
    default="both",
    show_default=True,
)
@click.option(
    "--baseline",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    help="Historical report for the same sealed prediction; stratify changed and unchanged decisions.",
)
def main(
    truth: Path,
    prediction: Path,
    output: Path,
    per_stratum: int,
    seed: int,
    score_path: Path | None,
    view: str,
    baseline: Path | None,
) -> None:
    """Prepare an expert sample from one scored run; make no model calls."""
    try:
        views = ("attributed", "recovery") if view == "both" else (view,)
        count = prepare(
            truth, prediction, output, per_stratum, seed, score_path, views, baseline
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Prepared {count} fields in {output / 'review'}")
    click.echo("Keep analyst-key.json private until independent judgments are locked.")


if __name__ == "__main__":
    main()
