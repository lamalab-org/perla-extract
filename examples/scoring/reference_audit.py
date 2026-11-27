"""Inspect a frozen reference without confusing software checks with expert review."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import click

from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.evaluation import (
    EvaluationConfig,
    EvaluationReport,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.reference_io import (
    load_reference,
    reference_content_digest,
)
from perla_extract.study_extraction.reference_release import ReferenceRelease


def check_reference_self_score(score: EvaluationReport, paper_id: str) -> None:
    """Reject unexplained losses without hiding explicit quantity contradictions."""

    quantity_conflicts = {
        side: {
            path
            for issue in score.core_facts.issues
            if issue.kind == "quantity_conflict" and issue.side == side
            for path in issue.paths
        }
        for side in ("truth", "prediction")
    }
    recovery = score.core_facts.recovery
    # A contradictory reference cannot recover its own invalid numeric fields.
    # Accept only losses explicitly identified on both sides; keep their counts
    # and issues in the audit rather than masking them or changing the reference.
    explained_recovery = (
        bool(quantity_conflicts["truth"])
        and set(recovery.unmatched_truth_paths) <= quantity_conflicts["truth"]
        and set(recovery.unmatched_prediction_paths) <= quantity_conflicts["prediction"]
    )
    if (recovery.micro.f1 != 1 and not explained_recovery) or (
        score.core_facts.micro.f1 != 1 and not score.core_facts.issues
    ):
        raise ValueError(
            f"Unexplained reference self-check failure: {paper_id}; "
            "inspect the scorer before changing any reference facts"
        )


def inspect_reference(directory: Path, config: EvaluationConfig | None = None):
    """Check the finalized input and expose what its manifest actually records.

    A successful self-score is a consistency control, not evidence that the source
    contains no omitted facts. Missing review-stage metadata does not prove that a
    reviewer skipped that work; retain the distinction for a provenance audit.
    Indistinguishable identities and explicit quantity contradictions remain review
    issues, with every fact retained. Unexplained recovery losses are failures.
    """

    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("artifact_format_version") != 4:
        raise ValueError(
            "Evaluation requires finalized format-4 references; export the reviewed revision"
        )
    truth, provenance = load_reference(directory)
    if provenance is None:
        raise ValueError("A frozen reference directory is required")
    score = evaluate_study(truth, truth, benchmark=provenance, config=config)
    check_reference_self_score(score, provenance.paper_id)
    review = manifest["review"]
    stages = review["completed_stage_names"]
    return score, {
        "benchmark": provenance.model_dump(mode="json"),
        "truth_content_sha256": score.truth_content_sha256,
        "manifest_sha256": reference_content_digest(manifest),
        "self_score": {
            "status": score.core_facts.scoring_status,
            "micro": score.core_facts.micro.model_dump(),
            "recovery_micro": score.core_facts.recovery.micro.model_dump(),
            "issues": [issue.model_dump() for issue in score.core_facts.issues],
            "unmatched_truth_paths": score.core_facts.unmatched_truth_paths,
        },
        "fact_counts": {
            name: counts.truth for name, counts in score.core_facts.groups.items()
        },
        "subfield_counts": {
            name: counts.truth for name, counts in score.core_facts.subfields.items()
        },
        "reference_coverage": provenance.coverage.model_dump(mode="json"),
        "interpretation": score.interpretation,
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
        "format_version": 2,
        "release_sha256": release.digest(),
        "split": release.split,
        "paper_count": aggregate.paper_count,
        "matcher_version": aggregate.matcher_version,
        "software_checks": "passed",
        "papers_with_unreviewed_scope": aggregate.papers_with_unreviewed_scope,
        "review_worklist": {
            paper: [
                domain
                for domain, declaration in scores[i].benchmark.coverage.domains.items()
                if not declaration.complete()
            ]
            for i, paper in enumerate(checks)
        },
        "papers_needing_scoring_review": sorted(
            paper
            for paper, check in checks.items()
            if check["self_score"]["status"] == "needs_review"
        ),
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
        f"Checked {report['paper_count']} references; "
        f"references needing scientific review: {len(report['papers_needing_scoring_review'])}. "
        f"Inspect the saved issues, coverage and review declarations in {output}"
    )


if __name__ == "__main__":
    main()
