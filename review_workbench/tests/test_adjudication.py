"""Exercise admin adoption, guarded corrections, undo, and a scoreable final export."""

import pytest
from pydantic import ValidationError

from review_workbench.adjudication import (
    AdjudicationQueue,
    ConfirmRecordsRequest,
    DecisionRequest,
    FinalizeRequest,
    PlanRequest,
)
from review_workbench.ground_truth_export import build_ground_truth_export
from review_workbench.review_storage import StaleRevisionError
from review_workbench.study_review import (
    RecordDecisionRequest,
    StudyReviewStore,
    _digest,
)
from review_workbench.tests.test_ground_truth_export import _study_with_evidence

PAPER = "10.0000--example"


@pytest.fixture
def queue(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path)
    store.import_seed(
        "dev",
        PAPER,
        _study_with_evidence(empty_study, "champion device"),
        document=document_payload,
        manifest={},
        reviewer_id="expert",
    )
    store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="verified",
            base_revision=1,
        ),
        "expert",
    )
    return AdjudicationQueue(store)


def finalize(queue):
    return queue.finalize(
        "dev",
        PAPER,
        FinalizeRequest(
            base_revision=queue.load("dev", PAPER, "admin")["revision"],
            adopt_current_reviews=True,
            completeness_checked=True,
        ),
        "admin",
    )


def plan(queue):
    current = queue.store.storage.load_revision("dev", PAPER)
    before = current.ground_truth["device_families"][0]
    after = {**before, "label": "Champion control"}
    return PlanRequest(
        base_revision=current.revision,
        study_sha256=_digest(current.ground_truth),
        proposals=[
            {
                "id": "correct-label",
                "title": "Correct the label",
                "reason": "The reviewer distinguishes this control.",
                "changes": [
                    {
                        "collection": "device_families",
                        "record_id": "family-control",
                        "before": before,
                        "after": after,
                    }
                ],
                "evidence": before["evidence"],
            }
        ],
    )


def decide(queue, action="accept"):
    view = queue.load("dev", PAPER, "admin")
    return queue.decide(
        "dev",
        PAPER,
        DecisionRequest(
            base_revision=view["revision"],
            case_id=view["cases"][0]["id"],
            action=action,
            note="Checked the source.",
        ),
        "admin",
    )


def test_adopt_unchanged_expert_review_and_export(queue):
    view = queue.load("dev", PAPER, "admin")
    assert view["cases"] == []
    assert view["inherited_count"] == 1
    assert finalize(queue)["finalized"]
    export = build_ground_truth_export(queue.store, "dev", PAPER)
    assert export.manifest.artifact_format_version == 4
    assert export.manifest.review.adjudicators == ["admin"]
    assert export.review_events[-1].details["adopted_reviews"] == {
        "device_families:family-control": {"expert": "verified"}
    }
    assert export.manifest.review.uncertain_record_keys == []


def test_accept_is_atomic_approved_undoable_and_retains_original(queue):
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    view = decide(queue)
    assert view["cases"] == []
    assert view["own_approved_count"] == 1
    assert (
        queue.store.load_truth("dev", PAPER)["device_families"][0]["label"]
        == "Champion control"
    )
    assert (
        queue.store.storage.load_source("dev", PAPER).seed_extraction[
            "device_families"
        ][0]["label"]
        == "Control"
    )
    event_id = view["last_decision"]["event_id"]
    restored = queue.undo("dev", PAPER, view["revision"], event_id, "admin")
    assert len(restored["cases"]) == 1
    assert (
        queue.store.load_truth("dev", PAPER)["device_families"][0]["label"] == "Control"
    )
    assert any(e["event_id"] == event_id for e in queue.store.events("dev", PAPER))


def test_keep_current_records_is_audited_and_does_not_apply_proposal(queue):
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    view = decide(queue, "keep")
    assert view["cases"] == []
    assert (
        queue.store.load_truth("dev", PAPER)["device_families"][0]["label"] == "Control"
    )
    finalize(queue)
    assert build_ground_truth_export(queue.store, "dev", PAPER)


def test_conflicting_decision_requires_explicit_admin_resolution(queue):
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="uncertain",
            base_revision=2,
        ),
        "second-expert",
    )
    assert queue.load("dev", PAPER, "admin")["inherited_count"] == 0
    with pytest.raises(ValueError, match="remaining"):
        finalize(queue)
    decide(queue, "keep")
    assert finalize(queue)["finalized"]


def test_missing_approval_cannot_be_inherited(queue):
    current = queue.store.storage.load_revision("dev", PAPER)
    # An actual content change makes the prior expert decision stale.
    from review_workbench.study_review import MutationRequest

    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            action="replace",
            path="/device_families/0/label",
            value="New label",
            evidence=[{"block_id": "main_p1_text_1", "quote": "champion device"}],
            base_revision=current.revision,
        ),
        "expert",
    )
    view = queue.load("dev", PAPER, "admin")
    assert view["inherited_count"] == 0
    assert len(view["cases"]) == 1
    with pytest.raises(ValueError, match="remaining"):
        finalize(queue)


def test_stale_plan_wrong_before_and_repeated_import_are_rejected(queue):
    request = plan(queue)
    with pytest.raises(ValueError, match="different records"):
        queue.import_plan(
            "dev", PAPER, request.model_copy(update={"study_sha256": "a" * 64}), "admin"
        )
    wrong = request.model_copy(deep=True)
    wrong.proposals[0].changes[0].before["label"] = "Wrong snapshot"
    with pytest.raises(ValueError, match="changed"):
        queue.import_plan("dev", PAPER, wrong, "admin")
    queue.import_plan("dev", PAPER, request, "admin")
    with pytest.raises(ValueError, match="already saved"):
        queue.import_plan(
            "dev", PAPER, request.model_copy(update={"base_revision": 3}), "admin"
        )


def test_stale_click_and_unresolved_proposal_cannot_finalize(queue):
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    with pytest.raises(ValueError, match="remaining"):
        finalize(queue)
    view = queue.load("dev", PAPER, "admin")
    request = DecisionRequest(
        base_revision=view["revision"],
        case_id=view["cases"][0]["id"],
        action="accept",
        note="Approved",
    )
    queue.decide("dev", PAPER, request, "admin")
    with pytest.raises(StaleRevisionError):
        queue.decide("dev", PAPER, request, "admin")


def test_finalization_requires_explicit_attestations():
    with pytest.raises(ValidationError):
        FinalizeRequest(
            base_revision=1, adopt_current_reviews=True, completeness_checked=False
        )


def test_stale_suggestion_shows_actual_current_record_and_cannot_apply(queue):
    from review_workbench.study_review import MutationRequest

    queue.import_plan("dev", PAPER, plan(queue), "admin")
    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            action="replace",
            path="/device_families/0/label",
            value="Later correction",
            base_revision=3,
            evidence=[{"block_id": "main_p1_text_1", "quote": "champion device"}],
        ),
        "expert",
    )
    case = queue.load("dev", PAPER, "admin")["cases"][0]
    assert case["stale"]
    assert case["current_changes"][0]["current"]["label"] == "Later correction"
    with pytest.raises(ValueError, match="changed after"):
        decide(queue)
    decide(queue, "keep")
    assert (
        queue.store.load_truth("dev", PAPER)["device_families"][0]["label"]
        == "Later correction"
    )


def test_finalization_queue_export_is_accepted_by_scorer(queue, tmp_path):
    import json

    from click.testing import CliRunner

    from perla_extract.study_extraction.evaluation_cli import main as evaluate
    from review_workbench.ground_truth_export import write_ground_truth_export

    finalize(queue)
    frozen = write_ground_truth_export(
        build_ground_truth_export(queue.store, "dev", PAPER), tmp_path / "frozen"
    )
    output = tmp_path / "score.json"
    result = CliRunner().invoke(
        evaluate,
        [
            "--truth",
            str(frozen),
            "--prediction",
            str(frozen / "ground_truth.json"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text(encoding="utf-8"))
    assert (
        report["benchmark"]["evidence_document_sha256"]
        == build_ground_truth_export(
            queue.store, "dev", PAPER
        ).manifest.evidence_document_sha256
    )


@pytest.mark.parametrize(
    "method,action",
    [
        ("GET", ""),
        ("POST", "/plan"),
        ("POST", "/decide"),
        ("POST", "/confirm-records"),
        ("POST", "/undo"),
        ("POST", "/finalize"),
    ],
)
def test_http_queue_is_admin_only(queue, method, action):
    from types import SimpleNamespace

    from review_workbench.server import make_handler

    handler_class = make_handler(
        SimpleNamespace(store=queue.store), authenticator=object()
    )
    handler = object.__new__(handler_class)
    handler.path = f"/api/adjudication/dev/{PAPER}{action}"
    handler._review_user = {"id": "expert", "role": "reviewer"}
    responses = []
    handler.send_json = lambda payload, status=200, **kwargs: responses.append(
        (status, payload)
    )
    getattr(handler, f"do_{method}")()
    assert int(responses[0][0]) == 403
    assert queue.store.revision("dev", PAPER) == 2


def test_completeness_resolution_reopens_after_a_scientific_change(queue):
    request = plan(queue)
    request.proposals[0].changes = []
    queue.import_plan("dev", PAPER, request, "admin")
    decide(queue, "keep")
    assert not queue.load("dev", PAPER, "admin")["cases"]
    from review_workbench.study_review import MutationRequest

    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            action="replace",
            path="/device_families/0/label",
            value="Another label",
            evidence=[{"block_id": "main_p1_text_1", "quote": "champion device"}],
            base_revision=queue.store.revision("dev", PAPER),
        ),
        "expert",
    )
    assert len(queue.load("dev", PAPER, "admin")["cases"]) == 2


def test_undo_cannot_change_another_admins_decision(queue):
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    view = decide(queue)
    with pytest.raises(ValueError, match="your saved"):
        queue.undo(
            "dev",
            PAPER,
            view["revision"],
            view["last_decision"]["event_id"],
            "someone-else",
        )


def test_bulk_confirmation_is_explicit_and_undoable(queue):
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="uncertain",
            base_revision=2,
        ),
        "expert",
    )
    view = queue.confirm_records(
        "dev",
        PAPER,
        ConfirmRecordsRequest(
            base_revision=3,
            record_keys=["device_families:family-control"],
            current_records_checked=True,
            note="Checked the current record against the paper and workbook.",
        ),
        "admin",
    )
    assert view["cases"] == []
    view = queue.undo(
        "dev", PAPER, view["revision"], view["last_decision"]["event_id"], "admin"
    )
    assert len(view["cases"]) == 1
    assert view["last_decision"] is None
