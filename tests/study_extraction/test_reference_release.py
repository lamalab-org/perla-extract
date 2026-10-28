"""Keep the public expert-reviewed release intact and usable by the real scorer."""

import json
import os
import re
from pathlib import Path

import pytest

from perla_extract.study_extraction.evaluation import (
    EvaluationConfig,
    aggregate_evaluations,
    evaluate_study,
)
from perla_extract.study_extraction.evaluation_cli import _canonical_digest, _truth
from perla_extract.study_extraction.models import StudyExtraction
from perla_extract.study_extraction.reference_release import ReferenceRelease

ROOT = Path(__file__).resolve().parents[2] / "data/study_extraction/ground_truth"
RELEASES = [
    (path.parent, json.loads(path.read_text(encoding="utf-8")))
    for path in sorted(ROOT.glob("v*/release.json"))
]
PAPERS = [
    pytest.param(release, index, paper, id=f"{release.name}/{paper['paper_id']}")
    for release, index in RELEASES
    for paper in index["papers"]
]


@pytest.mark.parametrize(
    "release,index",
    RELEASES,
    ids=lambda item: item.name if isinstance(item, Path) else item["release"],
)
def test_release_roster_is_complete(release, index):
    """A missing paper must fail CI rather than silently shrink the benchmark."""
    expected = {paper["paper_id"] for paper in index["papers"]}
    assert len(expected) == len(index["papers"]) == index["paper_count"] == 30
    assert {path.name for path in (release / "dev").iterdir()} == expected
    assert index["evaluation_config"] == EvaluationConfig().model_dump(mode="json")


@pytest.mark.parametrize("release,index,paper", PAPERS)
def test_public_reference_preserves_hashes_and_excludes_private_metadata(
    release, index, paper
):
    """Check the release's fixed inputs, not only whether its JSON parses."""
    directory = release / paper["path"]
    assert set(path.name for path in directory.iterdir()) == {
        "ground_truth.json",
        "manifest.json",
    }
    manifest_text = (directory / "manifest.json").read_text(encoding="utf-8")
    truth_text = (directory / "ground_truth.json").read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    assert _canonical_digest(manifest) == paper["manifest_sha256"]
    assert _canonical_digest(json.loads(truth_text)) == paper["ground_truth_sha256"]
    assert manifest["revision"] == paper["revision"]
    assert manifest["review"]["uncertain_record_keys"] == []
    assert "adjudication" in manifest["review"]["completed_stage_names"]
    assert set(manifest["source_manifest"]) == {
        "source_sha256",
        "private_source_manifest_sha256",
    }
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", item)
        for item in manifest["source_manifest"]["source_sha256"]
    )
    assert manifest["source_manifest"]["source_sha256"]
    assert not re.search(
        r"internal_[0-9a-f]+|/Users/|/home/|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
        manifest_text + truth_text,
    )
    _, provenance = _truth(directory)
    assert provenance is not None
    assert provenance.ground_truth_sha256 == paper["ground_truth_sha256"]


def _rename_and_reorder(value, field=""):
    """Change serialization choices without changing any scientific statement."""
    if isinstance(value, dict):
        return {key: _rename_and_reorder(item, key) for key, item in value.items()}
    if isinstance(value, list):
        return [_rename_and_reorder(item, field) for item in reversed(value)]
    identifiers = {
        "family_id",
        "device_id",
        "observation_id",
        "population_id",
        "test_id",
        "layer_id",
        "absorber_id",
        "step_id",
        "checkpoint_id",
        "target_layer_ids",
    }
    if isinstance(value, str) and field in identifiers:
        return "test-renamed-" + value
    return value


@pytest.mark.skipif(
    os.environ.get("PERLA_RUN_REFERENCE_RELEASE_TESTS") != "1",
    reason="Full release controls run once in the dedicated CI job.",
)
@pytest.mark.parametrize("release,index,paper", PAPERS)
def test_reference_score_invariance_and_negative_controls(release, index, paper):
    """Perfect self-scores alone are insufficient: real mistakes must lose credit."""
    truth, provenance = _truth(release / paper["path"])
    same = evaluate_study(truth, truth, benchmark=provenance)
    # Release metadata records the original scorer. New scoring rules must not
    # rewrite a frozen reference just to change that historical metadata.
    assert index["matcher_version"] == "rich-study-hungarian-v6"
    assert index["fact_profile"] == "core-scientific-facts-v4"
    assert same.matcher_version == "rich-study-hungarian-v7"
    assert same.core_facts.profile == "core-scientific-facts-v5"
    assert same.core_facts.micro.f1 == 1 and not same.core_facts.issues
    reordered = StudyExtraction.model_validate(
        _rename_and_reorder(truth.model_dump(mode="json"))
    )
    result = evaluate_study(truth, reordered, benchmark=provenance)
    assert result.core_facts.micro.f1 == 1 and not result.core_facts.issues

    # Recipe recall is not device identity. This control failed on 12/30 v2 papers
    # in the audit despite unchanged structure, composition, devices and outcomes.
    no_recipes = truth.model_copy(deep=True)
    for family in no_recipes.device_families:
        family.processing_steps = []
    partial = evaluate_study(truth, no_recipes)
    assert (
        partial.core_facts.groups["performance"]
        == same.core_facts.groups["performance"]
    )
    assert (
        partial.core_facts.groups["processing"].truth
        == same.core_facts.groups["processing"].truth
    )
    if any(family.processing_steps for family in truth.device_families):
        assert (
            partial.core_facts.groups["processing"].matched
            < same.core_facts.groups["processing"].matched
        )

    collection, id_field = (
        ("performance_observations", "observation_id")
        if truth.performance_observations
        else ("population_statistics", "population_id")
    )
    changed = truth.model_copy(deep=True)
    records = getattr(changed, collection)
    metric = next(
        (
            metric
            for record in records
            for metric in record.metrics
            if metric.value_number is not None
        ),
        None,
    )
    if metric is not None:
        metric.value_number = metric.value_number * 2 + 1
        metric.raw_value = str(metric.value_number)
    else:
        population = next(
            record for record in records if record.sample_size is not None
        )
        population.sample_size = population.sample_size * 2 + 1
    assert evaluate_study(truth, changed).core_facts.micro.f1 < 1

    missing = truth.model_copy(deep=True)
    getattr(missing, collection).pop()
    assert evaluate_study(truth, missing).core_facts.micro.recall < 1
    extra = truth.model_copy(deep=True)
    records = getattr(extra, collection)
    duplicate = records[0].model_copy(deep=True)
    setattr(duplicate, id_field, "test-extra-" + getattr(duplicate, id_field))
    records.append(duplicate)
    assert evaluate_study(truth, extra).core_facts.micro.precision < 1


@pytest.mark.parametrize("release,index", RELEASES)
def test_release_roster_can_be_enforced_on_real_reference_reports(release, index):
    """Load real manifests and aggregate a complete set, including zero-score runs."""
    reports = []
    for paper in index["papers"]:
        truth, provenance = _truth(release / paper["path"])
        empty = StudyExtraction(
            paper=truth.paper,
            device_families=[],
            individual_devices=[],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[],
        )
        reports.append(evaluate_study(truth, empty, benchmark=provenance))
    frozen = ReferenceRelease.model_validate(index)
    result = aggregate_evaluations(
        reports, reference_release=frozen, bootstrap_samples=0
    )
    assert result.paper_count == 30
    assert result.core_facts_micro.f1 == 0
    assert result.reference_release_sha256 == frozen.digest()
    assert len(result.input_reports) == 30
    with pytest.raises(ValueError, match="missing"):
        aggregate_evaluations(
            reports[:-1], reference_release=frozen, bootstrap_samples=0
        )
