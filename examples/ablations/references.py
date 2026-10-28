"""Inspect a frozen reference without confusing software checks with expert review."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import click

from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.evaluation import (
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_cli import _canonical_digest, _truth
from perla_extract.study_extraction.reference_release import ReferenceRelease


def inspect_reference(directory: Path):
    """Check the finalized input and expose what its manifest actually records.

    A successful self-score is a consistency control, not evidence that the source
    contains no omitted facts. Missing review-stage metadata does not prove that a
    reviewer skipped that work; retain the distinction for a provenance audit.
    """

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("artifact_format_version") != 4:
        raise ValueError(
            "Ablations require finalized format-4 references; export the reviewed revision"
        )
    truth, provenance = _truth(directory)
    if provenance is None:
        raise ValueError("A frozen reference directory is required")
    score = evaluate_study(truth, truth, benchmark=provenance)
    if score.core_facts.scoring_status != "ready" or score.core_facts.micro.f1 != 1:
        raise ValueError(
            f"Reference self-check needs attention: {provenance.paper_id}; inspect identity "
            "and scorer limitations rather than changing facts to increase a score"
        )
    review = manifest["review"]
    stages = review.get("completed_stage_names")
    if stages is None:
        stages = [
            name
            for name, people in review.get("completed_stages", {}).items()
            if people
        ]
    return score, {
        "benchmark": provenance.model_dump(mode="json"),
        "truth_content_sha256": score.truth_content_sha256,
        "manifest_sha256": _canonical_digest(manifest),
        "fact_counts": {
            name: counts.truth for name, counts in score.core_facts.groups.items()
        },
        "record_counts": {
            name: counts.truth for name, counts in score.inventory.items()
        },
        "declared_review_stages": sorted(stages),
        "completeness_stage_marker": "recorded"
        if "completeness" in stages
        else "not_recorded",
    }


def audit_release(path: Path) -> dict:
    """Load every listed paper, verify hashes, and summarize coverage without masks."""

    release = ReferenceRelease.model_validate_json(path.read_text(encoding="utf-8"))
    root = path.parent.resolve()
    checks, scores = {}, []
    for paper in release.model_dump(mode="json")["papers"]:
        directory = (root / paper["path"]).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Reference path escapes the release directory")
        score, check = inspect_reference(directory)
        if score.benchmark.paper_id != paper["paper_id"]:
            raise ValueError("Reference directory does not match its release paper ID")
        if paper.get("manifest_sha256") != check["manifest_sha256"]:
            raise ValueError(f"Reference manifest hash mismatch: {paper['paper_id']}")
        checks[paper["paper_id"]] = check
        scores.append(score)
    aggregate = aggregate_evaluations(
        scores, reference_release=release, bootstrap_samples=0
    )
    facts, coverage = Counter(), Counter()
    for check in checks.values():
        facts.update(check["fact_counts"])
        coverage.update(name for name, count in check["fact_counts"].items() if count)
    return {
        "format_version": 1,
        "release_sha256": release.digest(),
        "split": release.split,
        "paper_count": aggregate.paper_count,
        "matcher_version": aggregate.matcher_version,
        "software_checks": "passed",
        "scope": "Integrity, finalization declarations and scorer self-consistency; not independent scientific review",
        "source_documents_rechecked": False,
        "fact_counts": dict(facts),
        "papers_with_facts": dict(coverage),
        "papers_without_completeness_stage_marker": sorted(
            paper
            for paper, check in checks.items()
            if check["completeness_stage_marker"] == "not_recorded"
        ),
        "papers": checks,
    }


@click.command()
@click.option(
    "--release",
    "release_path",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    required=True,
)
@click.option(
    "--output", type=click.Path(path_type=Path, dir_okay=False), required=True
)
def main(release_path: Path, output: Path) -> None:
    """Write a non-overwriting, no-model-call audit of a published reference set."""

    try:
        if output.exists():
            raise ValueError(
                "Choose a new output path; existing audits are not overwritten"
            )
        report = audit_release(release_path)
        write_json_exclusive(output, report)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise click.ClickException(str(error)) from error
    click.echo(
        f"Checked {report['paper_count']} references; inspect coverage and review declarations in {output}"
    )


if __name__ == "__main__":
    main()
