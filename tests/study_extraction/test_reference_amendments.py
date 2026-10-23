"""Guard source-checked data changes without paper-specific extraction heuristics."""

import json
import re
from pathlib import Path

import pytest

from perla_extract.study_extraction.models import StudyExtraction
from review_workbench.adjudication import AdjudicationQueue, PlanRequest
from review_workbench.study_review import _digest

ROOT = Path(__file__).resolve().parents[2]
AMENDMENTS = ROOT / "data/study_extraction/reference_amendments/2026-10-23"
PLAN = json.loads((AMENDMENTS / "finalization-plan.json").read_text())


def amended(item):
    original = json.loads(
        (
            ROOT
            / "data/study_extraction/ground_truth/v1/dev"
            / item["paper_id"]
            / "ground_truth.json"
        ).read_text()
    )
    assert _digest(original) == item["study_sha256"]
    request = PlanRequest.model_validate(
        dict(
            base_revision=1,
            study_sha256=item["study_sha256"],
            proposals=item["proposals"],
        )
    )
    updated = AdjudicationQueue._apply(original, request.proposals[0].changes)
    return original, updated, request


@pytest.mark.parametrize("item", PLAN["papers"], ids=lambda p: p["paper_id"])
def test_amendment_is_guarded_and_preserves_existing_measurements(item):
    before, after, request = amended(item)
    StudyExtraction.model_validate(after)
    for collection, identifier in [
        ("individual_devices", "device_id"),
        ("performance_observations", "observation_id"),
    ]:
        indexed = {r[identifier]: r for r in after[collection]}
        for record in before[collection]:
            revised = indexed[record[identifier]]
            assert {k: v for k, v in record.items() if k != "evidence"} == {
                k: v for k, v in revised.items() if k != "evidence"
            }
    tampered = json.loads(json.dumps(before))
    change = request.proposals[0].changes[0]
    if change.before is not None:
        records = tampered[change.collection]
        records.remove(change.before)
        with pytest.raises(ValueError, match="changed"):
            AdjudicationQueue._apply(tampered, request.proposals[0].changes)
    else:
        tampered[change.collection].append(change.after)
        with pytest.raises(ValueError, match="changed"):
            AdjudicationQueue._apply(tampered, request.proposals[0].changes)


def test_known_missing_outcome_is_not_fabricated():
    inventory = json.loads((AMENDMENTS / "experiment-inventory.json").read_text())
    assert len(inventory) == 1
    experiment = inventory[0]["experiment"]
    assert experiment["outcomes"] == [] and experiment["duration"] is None
    assert experiment["device_id"] is None
    assert all(c["value_number"] is None for c in experiment["conditions"])


def test_public_amendment_contains_no_private_metadata():
    assert len(PLAN["papers"]) == 4
    for path in AMENDMENTS.glob("*.json"):
        assert not re.search(
            r"internal_[0-9a-f]+|/Users/|/private/|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
            path.read_text(),
        )


def test_additions_do_not_create_individual_cells_or_numeric_outcomes():
    for item in PLAN["papers"]:
        before, after, request = amended(item)
        assert len(after["individual_devices"]) == len(before["individual_devices"])
        for change in request.proposals[0].changes:
            if change.collection == "population_statistics" and change.before is None:
                assert change.after["sample_size"] is None
                for metric in change.after["metrics"]:
                    if metric["value_number"] is not None:
                        assert metric["name"] in {
                            "surface-treatment solution concentration",
                            "JV scan rate",
                            "perovskite bandgap",
                        }
