"""Challenge the scorer using frozen studies, not new model calls or revised truth.

Controlled copies have known identity and numerical changes. Archived review
snapshots provide real-data sensitivity checks, not independent model benchmarks.
The output deliberately excludes private review text and reviewer identities.
"""

from __future__ import annotations

import hashlib
import json
import math
import platform
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from statistics import mean
from zipfile import ZipFile

import click
from pydantic import BaseModel

from examples.scoring.equivalence_probe import probe
from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.evaluation import EvaluationConfig, evaluate_study
from perla_extract.study_extraction.evaluation_cli import (
    load_reference,
    reference_content_digest,
)
from perla_extract.study_extraction.models import ReportedValue, StudyExtraction
from perla_extract.study_extraction.reference_release import ReferenceRelease
from perla_extract.study_extraction.scoring_facts import is_plain_number

# Independent dimensional arithmetic for test transformations, not production
# chemical/unit aliases. No formulas are inferred from the particular papers.
CONVERSIONS = {
    "h": ("s", 3600, 0),
    "min": ("s", 60, 0),
    "V": ("mV", 1000, 0),
    "mA/cm2": ("A/m2", 10, 0),
    "%": ("dimensionless", 0.01, 0),
    "nm": ("m", 1e-9, 0),
    "°C": ("K", 1, 273.15),
}


def quantities(value):
    """Visit every reported quantity, including nested recipe and test conditions."""
    if isinstance(value, ReportedValue):
        yield value
    elif isinstance(value, BaseModel):
        for field in type(value).model_fields:
            yield from quantities(getattr(value, field))
    elif isinstance(value, list):
        for item in value:
            yield from quantities(item)


def convert_units(study):
    """Change representations by known factors while preserving scientific values."""
    changed = 0
    for value in quantities(study):
        if value.unit not in CONVERSIONS or not is_plain_number(value):
            continue
        unit, factor, offset = CONVERSIONS[value.unit]
        number = value.value_number * factor + offset
        if not math.isfinite(number):
            continue
        value.raw_value, value.value_number, value.unit = str(number), number, unit
        changed += 1
    return changed


def summary(score):
    """Keep counts and matching warnings; never turn an ambiguous score into a rank."""
    return {
        "macro_f1": score.core_facts.macro_f1,
        "micro_f1": score.core_facts.micro.f1,
        "status": score.core_facts.scoring_status,
        "groups": {k: v.model_dump() for k, v in score.core_facts.groups.items()},
        "issues": len(score.core_facts.issues),
    }


def controls(truth):
    """State expected behavior before evaluating controlled copies of real records."""
    converted = truth.model_copy(deep=True)
    count = convert_units(converted)
    if count:
        yield "equivalent_units", converted, count
    # Remove exactly one scalar performance value, leaving specimen and protocol
    # unchanged. Remaining scientific facts should retain their credit.
    dropped = truth.model_copy(deep=True)
    for observation in dropped.performance_observations:
        if observation.metrics:
            observation.metrics.pop(0)
            yield "drop_one_metric", dropped, 1
            break
    # Unique device+protocol tuples identify these records without their outcomes.
    keys = Counter(
        (o.device_id, o.measurement_type, o.scan_direction)
        for o in truth.performance_observations
    )
    corrupted = truth.model_copy(deep=True)
    expected_ids = []
    for observation in corrupted.performance_observations:
        key = (
            observation.device_id,
            observation.measurement_type,
            observation.scan_direction,
        )
        if observation.device_id is None or keys[key] != 1:
            continue
        changed = False
        for value in observation.metrics:
            if is_plain_number(value):
                number = value.value_number * 2 + 1
                value.raw_value, value.value_number = str(number), number
                changed = True
        if changed:
            expected_ids.append(observation.observation_id)
    if expected_ids:
        yield "wrong_outcomes_same_identity", corrupted, expected_ids


def validate_control(truth, name, prediction, changes, baseline):
    """Return failures against explicit expectations rather than a golden score."""
    score = evaluate_study(truth, prediction)
    failures = []
    if name == "equivalent_units":
        if score.core_facts.micro.f1 != 1 or score.core_facts.issues:
            failures.append("Equivalent units changed scientific credit or alignment")
    elif name == "drop_one_metric":
        for group, original in baseline.core_facts.groups.items():
            expected = original.truth - (1 if group == "performance" else 0)
            actual = score.core_facts.groups[group]
            if (actual.predicted, actual.matched) != (expected, expected):
                failures.append(f"Removing one metric also changed {group} credit")
    else:
        mapping = {
            m.truth_id: m.predicted_id
            for m in score.matches
            if m.kind == "performance_observations" and not m.ambiguous
        }
        if any(mapping.get(identity) != identity for identity in changes):
            failures.append(
                "Changed outcomes lost or changed known specimen/protocol identity"
            )
        changed_metrics = sum(
            a != b
            for left, right in zip(
                truth.performance_observations, prediction.performance_observations
            )
            for a, b in zip(left.metrics, right.metrics)
        )
        for group, original in baseline.core_facts.groups.items():
            expected = original.truth - (
                changed_metrics if group == "performance" else 0
            )
            if score.core_facts.groups[group].matched != expected:
                failures.append(
                    f"Outcome corruption did not produce the expected {group} credit"
                )
    return {
        "case": name,
        "changed_values_or_records": len(changes)
        if isinstance(changes, list)
        else changes,
        "passed": not failures,
        "failures": failures,
        "score": summary(score),
        "unmatched_truth_paths": score.core_facts.unmatched_truth_paths,
        "matches": [m.model_dump() for m in score.matches] if failures else [],
        "changed_performance_quantities": changed_metrics
        if name == "wrong_outcomes_same_identity"
        else None,
    }


def run(
    release_path: Path,
    feedback_zip: Path | None,
    include_controls: bool = True,
    descriptor_equivalences: Path | None = None,
):
    """Run controls and a prespecified sensitivity grid, preserving every result."""
    release = json.loads(release_path.read_text(encoding="utf-8"))
    ReferenceRelease.model_validate(release)
    snapshots = {}
    if feedback_zip:
        with ZipFile(feedback_zip) as archive:
            feedback = json.loads(archive.read("feedback.json"))
        for item in feedback["ground_truth_reviews"]:
            if item["split"] != release["split"]:
                continue
            key = item["paper_id"]
            if key in snapshots:
                raise ValueError(f"Ambiguous archived snapshots for {key}")
            snapshot = item.get("review_snapshot")
            if snapshot:
                # Feedback ZIPs hash indented JSON; reference exports use compact
                # JSON. Preserve the producer's encoding rather than comparing
                # hashes from two different serialization contracts.
                encoded = (
                    json.dumps(
                        snapshot["study"], ensure_ascii=False, indent=2, sort_keys=True
                    )
                    + "\n"
                ).encode()
                if hashlib.sha256(encoded).hexdigest() != snapshot["study_sha256"]:
                    raise ValueError(f"Archived snapshot hash mismatch: {key}")
                snapshots[key] = StudyExtraction.model_validate(snapshot["study"])
    results, sensitivity = [], []
    configurations = {
        "default": EvaluationConfig(),
        "matching_0.20": EvaluationConfig(minimum_record_similarity=0.20),
        "matching_0.50": EvaluationConfig(minimum_record_similarity=0.50),
        "numeric_exact": EvaluationConfig(numeric_relative_tolerance=0),
        "numeric_1e-4": EvaluationConfig(numeric_relative_tolerance=1e-4),
    }
    if descriptor_equivalences is not None:
        configurations["reviewed_descriptors"] = EvaluationConfig(
            descriptor_equivalences=json.loads(
                descriptor_equivalences.read_text(encoding="utf-8")
            )
        )
    missing = []
    for paper in release["papers"]:
        identity = paper["paper_id"]
        directory = (release_path.parent / paper["path"]).resolve()
        if not directory.is_relative_to(release_path.parent.resolve()):
            raise ValueError("Reference path escapes the release directory")
        truth, provenance = load_reference(directory)
        if (
            provenance is None
            or provenance.paper_id != identity
            or provenance.split != release["split"]
            or provenance.ground_truth_sha256 != paper["ground_truth_sha256"]
        ):
            raise ValueError(f"Reference differs from release: {identity}")
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if reference_content_digest(manifest) != paper["manifest_sha256"]:
            raise ValueError(f"Reference manifest differs from release: {identity}")
        baseline = evaluate_study(truth, truth)
        if baseline.core_facts.micro.f1 != 1 or baseline.core_facts.issues:
            raise ValueError(f"Baseline self-check failed: {identity}")
        for name, prediction, changes in controls(truth) if include_controls else []:
            result = validate_control(truth, name, prediction, changes, baseline)
            results.append({"paper_id": identity, **result})
        if identity in snapshots:
            scores = {
                name: summary(evaluate_study(truth, snapshots[identity], config=config))
                for name, config in configurations.items()
            }
            sensitivity.append(
                {
                    "paper_id": identity,
                    "snapshot_sha256": reference_content_digest(
                        snapshots[identity].model_dump(mode="json")
                    ),
                    "identical_study": snapshots[identity] == truth,
                    "scores": scores,
                }
            )
        else:
            missing.append(identity)
        click.echo(f"Checked {identity}", err=True)
    counts = {}
    for name in {r["case"] for r in results}:
        rows = [r for r in results if r["case"] == name]
        counts[name] = {"tested": len(rows), "passed": sum(r["passed"] for r in rows)}
    return {
        "format_version": 1,
        "scope": "Controlled perturbations and archived reviewed-snapshot sensitivity; not expert calibration or a model ranking",
        "independent_expert_calibration": False,
        "environment": {
            "python": platform.python_version(),
            "pydantic": version("pydantic"),
            "pint": version("pint"),
        },
        "code_sha256": {
            str(path.relative_to(Path(__file__).resolve().parents[2])): hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
            for path in [
                Path(__file__).resolve(),
                Path(__file__).with_name("equivalence_probe.py"),
                *[
                    Path(__file__).resolve().parents[2]
                    / "src/perla_extract/study_extraction"
                    / name
                    for name in (
                        "evaluation.py",
                        "scoring_facts.py",
                        "units.py",
                        "models.py",
                    )
                ],
            ]
        },
        "equivalence_probe": probe(),
        "release_file_sha256": hashlib.sha256(release_path.read_bytes()).hexdigest(),
        "feedback_zip_sha256": hashlib.sha256(feedback_zip.read_bytes()).hexdigest()
        if feedback_zip
        else None,
        "matcher_version": baseline.matcher_version,
        "configurations": {
            k: v.model_dump(mode="json") for k, v in configurations.items()
        },
        "control_summary": counts,
        "controls": results,
        "snapshot_sensitivity": sensitivity,
        "missing_snapshots": missing,
        "snapshot_macro_f1_means": {
            name: mean(r["scores"][name]["macro_f1"] for r in sensitivity)
            for name in configurations
        }
        if sensitivity
        else {},
    }


@click.command()
@click.option(
    "--release",
    "release_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
)
@click.option("--feedback-zip", type=click.Path(exists=True, path_type=Path))
@click.option("--output", required=True, type=click.Path(path_type=Path))
@click.option("--controls/--no-controls", "include_controls", default=True)
@click.option(
    "--descriptor-equivalences",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def main(release_path, feedback_zip, output, include_controls, descriptor_equivalences):
    """Write an audit without modifying references, predictions or existing reports."""
    if output.exists():
        raise click.ClickException("Choose a new audit output path")
    report = run(release_path, feedback_zip, include_controls, descriptor_equivalences)
    write_json_exclusive(output, report)
    click.echo(json.dumps(report["control_summary"], indent=2))
    click.echo(
        f"Semantic-equivalence discrepancies: {sum(not item['agrees'] for item in report['equivalence_probe'])}; inspect {output}"
    )
    if any(not item["passed"] for item in report["controls"]):
        raise click.ClickException(
            "Controlled regression failures; results were preserved"
        )


if __name__ == "__main__":
    main()
