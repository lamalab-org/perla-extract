"""Keep the public expert-reviewed release intact and usable by the real scorer."""

import json
import os
import re
from pathlib import Path

import pytest

from perla_extract.study_extraction.evaluation import EvaluationConfig, evaluate_study
from perla_extract.study_extraction.evaluation_cli import _canonical_digest, _truth
from perla_extract.study_extraction.models import StudyExtraction

RELEASE = Path(__file__).resolve().parents[2] / "data/study_extraction/ground_truth/v1"
INDEX = json.loads((RELEASE / "release.json").read_text(encoding="utf-8"))
PAPERS = INDEX["papers"]


def test_release_roster_is_complete():
    """A missing paper must fail CI rather than silently shrink the benchmark."""
    expected = {paper["paper_id"] for paper in PAPERS}
    assert len(expected) == len(PAPERS) == INDEX["paper_count"] == 30
    assert {path.name for path in (RELEASE / "dev").iterdir()} == expected
    assert INDEX["evaluation_config"] == EvaluationConfig().model_dump(mode="json")


@pytest.mark.parametrize("paper", PAPERS, ids=lambda paper: paper["paper_id"])
def test_public_reference_preserves_hashes_and_excludes_private_metadata(paper):
    """Check the release's fixed inputs, not only whether its JSON parses."""
    directory = RELEASE / paper["path"]
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
@pytest.mark.parametrize("paper", PAPERS, ids=lambda paper: paper["paper_id"])
def test_reference_score_invariance_and_negative_controls(paper):
    """Perfect self-scores alone are insufficient: real mistakes must lose credit."""
    truth, provenance = _truth(RELEASE / paper["path"])
    same = evaluate_study(truth, truth, benchmark=provenance)
    assert same.matcher_version == INDEX["matcher_version"]
    assert same.core_facts.profile == INDEX["fact_profile"]
    assert same.core_facts.micro.f1 == 1 and not same.core_facts.issues
    reordered = StudyExtraction.model_validate(
        _rename_and_reorder(truth.model_dump(mode="json"))
    )
    result = evaluate_study(truth, reordered, benchmark=provenance)
    assert result.core_facts.micro.f1 == 1 and not result.core_facts.issues

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
