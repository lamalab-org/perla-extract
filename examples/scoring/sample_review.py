"""Sample actual scored prediction fields for independent expert review.

Sampling is without replacement within each scientific group and awarded/denied
credit stratum. Reviewers receive the complete scientific records and blank labels;
the scoring decisions and inclusion probabilities remain in a separate analyst key.
This prepares evidence for calibration. It does not provide expert judgments.
"""

from __future__ import annotations

import csv
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

LABEL_COLUMNS = [
    "item_id",
    "group",
    "prediction_path",
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
    truth: Path, prediction: Path, output: Path, per_stratum: int, seed: int
) -> int:
    """Bind a saved score to its inputs, then sample its complete prediction ledger."""
    if output.exists():
        raise ValueError(f"Output already exists: {output}; choose a new directory")
    if per_stratum < 1:
        raise ValueError("per_stratum must be positive")
    expected, benchmark = load_reference(truth)
    actual = StudyExtraction.model_validate_json(
        (prediction / "extraction.json").read_bytes()
    )
    score_path = prediction / "evaluation.json"
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
    facts = scientific_facts(
        actual, {}, "prediction", score.config.operation_aliases
    ).facts
    credited = {match.predicted_path for match in score.core_facts.matches}
    denied = set(score.core_facts.unmatched_prediction_paths)
    if {fact.path for fact in facts} != credited | denied:
        raise ValueError(
            "Scored prediction paths differ from the current fact projection"
        )
    if not facts:
        raise ValueError(
            "No predicted scientific facts to sample; audit reference omissions separately"
        )
    strata = defaultdict(list)
    for fact in facts:
        strata[(fact.group, fact.path in credited)].append(fact.path)
    rng = random.Random(seed)
    selected, counts = [], []
    for (group, accepted), paths in sorted(strata.items()):
        count = min(per_stratum, len(paths))
        counts.append(
            {
                "group": group,
                "scorer_awarded_credit": accepted,
                "population": len(paths),
                "sample": count,
            }
        )
        for path in rng.sample(sorted(paths), count):
            selected.append(
                {
                    "group": group,
                    "prediction_path": path,
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
    with (review / "judgments.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=LABEL_COLUMNS)
        writer.writeheader()
        writer.writerows(
            {key: item.get(key, "") for key in LABEL_COLUMNS} for item in selected
        )
    write_json_exclusive(
        output / "analyst-key.json",
        {
            "sampling_unit": "predicted scientific fact within this run",
            "sampling_method": "without replacement within group and scorer-credit stratum",
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
@click.option("--per-stratum", type=click.IntRange(min=1), default=5, show_default=True)
@click.option("--seed", type=int, default=0, show_default=True)
def main(
    truth: Path, prediction: Path, output: Path, per_stratum: int, seed: int
) -> None:
    """Prepare an expert sample from one scored run; make no model calls."""
    try:
        count = prepare(truth, prediction, output, per_stratum, seed)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Prepared {count} fields in {output / 'review'}")
    click.echo("Keep analyst-key.json private until independent judgments are locked.")


if __name__ == "__main__":
    main()
