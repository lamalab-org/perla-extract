from __future__ import annotations

import copy
import json

import pytest

from review_workbench.study_review import (
    InventoryAuditRequest,
    MutationRequest,
    RecordDecisionRequest,
    StageRequest,
    StudyReviewStore,
)


def study_with_family(empty_study):
    study = copy.deepcopy(empty_study)
    study["device_families"] = [{
        "family_id": "family-control",
        "label": "Control",
        "variant": None,
        "architecture": None,
        "polarity": "not_reported",
        "full_stack_raw": None,
        "layers": [],
        "absorber_formula": None,
        "absorber_properties": [],
        "absorber_constituents": [],
        "processing_steps": [],
        "evidence": [{
            "block_id": "main_p1_text_1",
            "quote": "champion device",
        }],
    }]
    return study


def seed(store, empty_study, document_payload):
    return store.import_seed(
        "calibration", "10.0000--example", empty_study,
        document=document_payload, manifest={"model": "frontier"}, reviewer_id="ada",
    )


def test_seed_is_immutable_and_truth_is_versioned(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    bundle = seed(store, empty_study, document_payload)
    assert bundle["revision"] == 1

    request = MutationRequest.model_validate({
        "action": "replace", "path": "/unresolved_notes/0",
        "value": "Checked against the paper",
        "evidence": [{"block_id": "main_p1_text_1", "quote": "champion device"}],
        "note": "Resolve the model note", "base_revision": 1,
    })
    updated = store.mutate("calibration", "10.0000--example", request, "ada")

    assert updated["ground_truth"]["unresolved_notes"] == ["Checked against the paper"]
    assert json.loads(store.seed_path("calibration", "10.0000--example").read_text())["unresolved_notes"] == ["Initial model note"]
    assert updated["events"][-1]["before"] == "Initial model note"
    assert updated["revision"] == 2


def test_corrections_require_supplied_exact_evidence(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    request = MutationRequest.model_validate({
        "action": "replace", "path": "/unresolved_notes/0", "value": "corrected",
        "evidence": [{"block_id": "main_p1_text_1", "quote": "not in the block"}],
        "base_revision": 1,
    })
    with pytest.raises(ValueError, match="quote is not present"):
        store.mutate("calibration", "10.0000--example", request, "ada")


def test_no_op_replacements_are_rejected(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    request = MutationRequest.model_validate({
        "action": "replace", "path": "/unresolved_notes/0",
        "value": "Initial model note",
        "evidence": [{"block_id": "main_p1_text_1", "quote": "champion device"}],
        "base_revision": 1,
    })
    with pytest.raises(ValueError, match="does not change"):
        store.mutate("calibration", "10.0000--example", request, "ada")


def test_blind_inventory_precedes_inventory_completion(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    with pytest.raises(ValueError, match="blind inventory"):
        store.complete_stage(
            "calibration", "10.0000--example",
            StageRequest(stage="inventory", base_revision=1, note=""), "ada",
        )

    audited = store.inventory_audit(
        "calibration", "10.0000--example",
        InventoryAuditRequest(
            base_revision=1, searched_sources=["main", "supplement"],
            expected_counts={"individual_devices": 2},
            missing_or_ambiguous="One stabilized specimen needs linkage.",
        ), "ada",
    )
    completed = store.complete_stage(
        "calibration", "10.0000--example",
        StageRequest(stage="inventory", base_revision=audited["revision"], note=""), "ada",
    )
    assert completed["summary"]["completed_stages"]["inventory"] == ["ada"]


def test_review_stages_cannot_be_skipped(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    with pytest.raises(ValueError, match="inventory stage"):
        store.complete_stage(
            "calibration", "10.0000--example",
            StageRequest(stage="fields", base_revision=1, note=""), "ada",
        )


def test_field_completion_requires_current_record_decisions(
    tmp_path, empty_study, document_payload
):
    store = StudyReviewStore(tmp_path)
    store.import_seed(
        "calibration", "10.0000--example", study_with_family(empty_study),
        document=document_payload, manifest={}, reviewer_id="ada",
    )
    audited = store.inventory_audit(
        "calibration", "10.0000--example",
        InventoryAuditRequest(
            base_revision=1, searched_sources=["main"],
            expected_counts={"device_families": 1},
        ), "ada",
    )
    inventory = store.complete_stage(
        "calibration", "10.0000--example",
        StageRequest(stage="inventory", base_revision=audited["revision"]), "ada",
    )
    with pytest.raises(ValueError, match="1 remaining"):
        store.complete_stage(
            "calibration", "10.0000--example",
            StageRequest(stage="fields", base_revision=inventory["revision"]), "ada",
        )

    decided = store.decide_record(
        "calibration", "10.0000--example",
        RecordDecisionRequest(
            collection="device_families", record_id="family-control",
            decision="verified", base_revision=inventory["revision"],
        ), "ada",
    )
    completed = store.complete_stage(
        "calibration", "10.0000--example",
        StageRequest(stage="fields", base_revision=decided["revision"]), "ada",
    )
    assert completed["summary"]["completed_stages"]["fields"] == ["ada"]


def test_edit_invalidates_record_decision(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    store.import_seed(
        "calibration", "10.0000--example", study_with_family(empty_study),
        document=document_payload, manifest={}, reviewer_id="ada",
    )
    decided = store.decide_record(
        "calibration", "10.0000--example",
        RecordDecisionRequest(
            collection="device_families", record_id="family-control",
            decision="verified", base_revision=1,
        ), "ada",
    )
    changed = copy.deepcopy(decided["ground_truth"]["device_families"][0])
    changed["label"] = "Corrected control"
    corrected = store.mutate(
        "calibration", "10.0000--example",
        MutationRequest(
            action="replace", path="/device_families/0", value=changed,
            evidence=[{"block_id": "main_p1_text_1", "quote": "champion device"}],
            base_revision=decided["revision"],
        ), "ada",
    )
    assert corrected["summary"]["record_decisions"].get("ada", {}) == {}


def test_stage_cannot_be_completed_twice(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    audited = store.inventory_audit(
        "calibration", "10.0000--example",
        InventoryAuditRequest(base_revision=1, searched_sources=["main"], expected_counts={}),
        "ada",
    )
    completed = store.complete_stage(
        "calibration", "10.0000--example",
        StageRequest(stage="inventory", base_revision=audited["revision"]), "ada",
    )
    with pytest.raises(ValueError, match="already complete"):
        store.complete_stage(
            "calibration", "10.0000--example",
            StageRequest(stage="inventory", base_revision=completed["revision"]), "ada",
        )


def test_stale_revisions_cannot_overwrite_other_reviewers(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    store.inventory_audit(
        "calibration", "10.0000--example",
        InventoryAuditRequest(base_revision=1, searched_sources=["main"], expected_counts={}),
        "ada",
    )
    request = MutationRequest.model_validate({
        "action": "replace", "path": "/unresolved_notes/0", "value": "old write",
        "evidence": [{"block_id": "main_p1_text_1", "quote": "champion device"}],
        "base_revision": 1,
    })
    with pytest.raises(ValueError, match="stale revision"):
        store.mutate("calibration", "10.0000--example", request, "grace")


def test_lists_rich_record_counts(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    seed(store, empty_study, document_payload)
    paper = store.list_papers("calibration")[0]
    assert paper["id"] == "10.0000--example"
    assert paper["device_families"] == 0
    assert paper["revision"] == 1
