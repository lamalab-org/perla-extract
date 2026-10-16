"""Exercise admin adoption, guarded corrections, undo, and a scoreable final export."""

import json
import shutil
import subprocess
from pathlib import Path

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


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_finalization_ui_distinguishes_adoption_from_personal_source_review():
    source = Path(__file__).parents[1] / "review_app/adjudication.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.classList={toggle(){}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  setAttribute() {}
  showModal() {}
  querySelectorAll(selector) {
    return this.children.flatMap(child => [child, ...child.querySelectorAll('*')])
      .filter(child => selector === '*' || selector.split(',').includes(child.tag));
  }
}
(async () => {
  const code = fs.readFileSync(process.argv[1], 'utf8');
  const {createFinalizationQueue} = await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
  for (const completed of [false,true]) {
    global.document = {body:new Element('body'), createElement:tag=>new Element(tag)};
    const requests=[], downloads=[];
    const data={title:'Example', revision:2, record_count:1, inherited_count:1, own_approved_count:0, cases:[], completed_reviews:completed ? [{reviewer_id:'expert'}] : []};
    const ui=createFinalizationQueue({
      request:async (url, options) => {
        if (options) requests.push(JSON.parse(options.body));
        return {...data, finalized:!!options};
      },
      context:()=>({split:'dev', paperId:'example', papers:[{id:'example'}]}),
      download:async url=>downloads.push(url)
    });
    await ui.open();
    const buttons=document.body.querySelectorAll('button');
    const finish=buttons.find(el=>el.textContent==='Finalize & download');
    await finish.onclick(); // Must not silently approve an unchecked confirmation.
    assert.equal(requests.length,0);
    document.body.querySelectorAll('input')[0].checked=true;
    await finish.onclick();
    assert.equal(requests.length,1);
    assert.equal(requests[0].adopt_current_reviews,true);
    assert.equal(requests[0].adopt_completed_review,completed ? true : undefined);
    assert.equal(requests[0].completeness_checked,completed ? undefined : true);
    assert.equal(downloads.length,1);
  }
})().catch(error=>{ console.error(error); process.exit(1); });
"""
    subprocess.run(
        ["node", "-e", script, str(source)], check=True, capture_output=True, text=True
    )


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_diff_matches_stable_ids_and_still_shows_reordering():
    source = Path(__file__).parents[1] / "review_app/adjudication.js"
    script = f"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
(async () => {{
  const code = fs.readFileSync({json.dumps(str(source))}, 'utf8');
  const {{ differences }} = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  const a = {{checkpoint_id:'a', value:1}}, b = {{checkpoint_id:'b', value:2}}, c = {{checkpoint_id:'c', value:3}};
  const added = differences([a,c], [a,b,c]);
  assert(added.length > 0 && added.every(row => row[0].startsWith('/b/')));
  assert.deepEqual(differences([a,b], [b,a]), [['/order','a → b','b → a']]);
  assert.deepEqual(differences([a,b], [a,{{...b,value:4}}]), [['/b/value','2','4']]);
  assert(differences([{{name:'x',value:1}},{{name:'x',value:2}}], [{{name:'x',value:3}}]).some(row => row[0] === '/0/value'));
}})().catch(error => {{ console.error(error); process.exit(1); }});
"""
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


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


def test_routing_does_not_approve_unreviewed_records(queue):
    trusted_correction(queue, actor="coordinator", scalar=True)
    view = queue.load("dev", PAPER, "admin")
    assert view["case_counts"] == {
        "scientific": 0,
        "reconciliation": 0,
        "unreviewed": 1,
    }
    with pytest.raises(ValueError, match="remaining items"):
        finalize(queue)


def test_explicit_objection_stays_in_scientific_queue(queue):
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="needs_correction",
            base_revision=2,
        ),
        "another-reviewer",
    )
    view = queue.load("dev", PAPER, "admin")
    assert view["case_counts"]["scientific"] == 1
    assert view["cases"][0]["review_lane"] == "scientific"


def test_reconciliation_proposal_still_blocks_finalization(queue):
    request = plan(queue)
    request.proposals[0].review_lane = "reconciliation"
    queue.import_plan("dev", PAPER, request, "admin")
    assert queue.load("dev", PAPER, "admin")["case_counts"] == {
        "scientific": 0,
        "reconciliation": 1,
        "unreviewed": 0,
    }
    with pytest.raises(ValueError, match="remaining items"):
        finalize(queue)


@pytest.mark.parametrize(
    "note, lane", [("", "reconciliation"), ("An electrode is missing.", "scientific")]
)
def test_count_difference_alone_is_not_a_scientific_objection(queue, note, lane):
    from review_workbench.study_review import InventoryAuditRequest

    queue.store.inventory_audit(
        "dev",
        PAPER,
        InventoryAuditRequest(
            base_revision=2,
            review_scope_sources=["main", "supplement"],
            expected_counts={"device_families": 2},
            missing_or_ambiguous=note,
        ),
        "expert",
    )
    view = queue.load("dev", PAPER, "admin")
    assert view["cases"][0]["review_lane"] == lane
    with pytest.raises(ValueError, match="remaining items"):
        finalize(queue)


def test_workbook_routing_never_hides_a_later_record_objection(queue):
    request = workbook_plan(queue)
    request.proposals[0].review_lane = "reconciliation"
    queue.import_plan("dev", PAPER, request, "admin")
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="needs_correction",
            base_revision=3,
        ),
        "expert",
    )
    view = queue.load("dev", PAPER, "admin")
    record = next(c for c in view["cases"] if c.get("record_key"))
    assert record["workbook_feedback"]
    assert record["review_lane"] == "scientific"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_scientific_view_does_not_offer_finalization_for_hidden_work():
    source = Path(__file__).parents[1] / "review_app/adjudication.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.classList={toggle(){}}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children=items; }
  setAttribute() {}
  showModal() {}
  querySelectorAll(selector) {
    return this.children.flatMap(child => [child, ...child.querySelectorAll('*')])
      .filter(child => selector === '*' || selector.split(',').includes(child.tag));
  }
}
(async () => {
  const code=fs.readFileSync(process.argv[1],'utf8');
  const {casesInLane,createFinalizationQueue}=await import('data:text/javascript;base64,'+Buffer.from(code).toString('base64'));
  const cases=[{id:'record:x',title:'Unreviewed',record_key:'x',record:{label:'Unreviewed'},review_lane:'unreviewed',changes:[],reason:'Awaiting review'}];
  assert.deepEqual(casesInLane(cases,'scientific'),[]);
  assert.equal(casesInLane([{id:'legacy'}],'scientific').length,1);
  global.document={body:new Element('body'),createElement:tag=>new Element(tag)};
  let writes=0;
  const ui=createFinalizationQueue({request:async (url,options)=>{if(options) writes++;return {title:'Example',revision:2,record_count:1,inherited_count:0,own_approved_count:0,cases};},context:()=>({split:'dev',paperId:'example',papers:[{id:'example'}]})});
  await ui.open();
  let buttons=document.body.querySelectorAll('button');
  assert(!buttons.some(b=>b.textContent==='Finalize & download'));
  await buttons.find(b=>b.textContent==='Show remaining work').onclick();
  buttons=document.body.querySelectorAll('button');
  assert(buttons.some(b=>b.textContent==='Approve this record'));
  assert(!buttons.some(b=>b.textContent==='Finalize & download'));
  assert.equal(writes,0);
})().catch(e=>{console.error(e);process.exit(1)});
"""
    subprocess.run(
        ["node", "-e", script, str(source)], check=True, capture_output=True, text=True
    )


def trusted_correction(queue, actor="expert", *, scalar=False):
    from review_workbench.study_review import MutationRequest

    before = queue.store.load_truth("dev", PAPER)["device_families"][0]
    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            base_revision=queue.store.revision("dev", PAPER),
            action="replace",
            path="/device_families/0/label" if scalar else "/device_families/0",
            value="Corrected control"
            if scalar
            else {**before, "label": "Corrected control"},
            evidence=before["evidence"],
            note="Corrected against the paper.",
        ),
        actor,
    )


def completed_expert_review(queue, sources=None):
    from review_workbench.study_review import InventoryAuditRequest, StageRequest

    queue.store.inventory_audit(
        "dev",
        PAPER,
        InventoryAuditRequest(
            base_revision=queue.store.revision("dev", PAPER),
            review_scope_sources=sources or ["main", "supplement"],
            expected_counts={},
        ),
        "expert",
    )
    for stage in ("inventory", "fields", "completeness"):
        queue.store.complete_stage(
            "dev",
            PAPER,
            StageRequest(stage=stage, base_revision=queue.store.revision("dev", PAPER)),
            "expert",
        )


def test_trusted_correction_is_adopted_and_exportable(queue, monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    trusted_correction(queue)
    view = queue.load("dev", PAPER, "admin")
    assert not view["cases"]
    assert (
        view["adopted_corrections"]["device_families:family-control"]["expert"][
            "revision"
        ]
        == 3
    )
    finalize(queue)
    # Once frozen, the export doesn't depend on a deployment's trust setting.
    monkeypatch.delenv("REVIEW_TRUSTED_REVIEWER_IDS")
    exported = build_ground_truth_export(queue.store, "dev", PAPER)
    assert exported.ground_truth.device_families[0].label == "Corrected control"
    assert exported.review_events[-1].details["adopted_corrections"]


@pytest.mark.parametrize("actor,scalar", [("other", False), ("expert", True)])
def test_trust_does_not_spread_to_other_people_or_unreviewed_record_fields(
    queue, monkeypatch, actor, scalar
):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    trusted_correction(queue, actor, scalar=scalar)
    assert queue.load("dev", PAPER, "admin")["cases"]


def test_trusted_correction_does_not_override_conflict_or_reset(queue, monkeypatch):
    from review_workbench.study_review import ReviewerResetRequest

    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    trusted_correction(queue)
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="uncertain",
            base_revision=3,
        ),
        "other",
    )
    assert queue.load("dev", PAPER, "admin")["cases"]
    queue.store.reset_reviewer_state(
        "dev", PAPER, ReviewerResetRequest(base_revision=4), "expert"
    )
    assert not queue.load("dev", PAPER, "admin")["adopted_corrections"]


def test_changed_trusted_correction_reopens(queue, monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    trusted_correction(queue)
    from review_workbench.study_review import MutationRequest

    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            base_revision=3,
            action="replace",
            path="/device_families/0/label",
            value="New unreviewed label",
            evidence=queue.store.load_truth("dev", PAPER)["device_families"][0][
                "evidence"
            ],
        ),
        "other",
    )
    assert queue.load("dev", PAPER, "admin")["cases"]


def test_already_applied_approved_proposal_needs_no_second_decision(queue, monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    request = plan(queue)
    request.proposals[0].changes[0].after["label"] = "Corrected control"
    queue.import_plan("dev", PAPER, request, "admin")
    trusted_correction(queue)
    view = queue.load("dev", PAPER, "admin")
    assert not view["cases"]
    assert view["adopted_proposals"][0]["case_id"] == "proposal:correct-label"
    finalize(queue)
    monkeypatch.delenv("REVIEW_TRUSTED_REVIEWER_IDS")
    assert build_ground_truth_export(queue.store, "dev", PAPER)


def test_adopt_completed_expert_review_without_claiming_a_second_source_check(
    queue, monkeypatch
):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    completed_expert_review(queue)
    view = queue.load("dev", PAPER, "admin")
    assert view["completed_reviews"][0]["review_scope_sources"] == [
        "main",
        "supplement",
    ]
    queue.finalize(
        "dev",
        PAPER,
        FinalizeRequest(
            base_revision=view["revision"],
            adopt_current_reviews=True,
            adopt_completed_review=True,
        ),
        "admin",
    )
    event = build_ground_truth_export(queue.store, "dev", PAPER).review_events[-1]
    assert not event.details["completeness_checked"]
    assert event.details["adopted_completeness"][0]["reviewer_id"] == "expert"


@pytest.mark.parametrize(
    "trust,sources", [("other", ["main", "supplement"]), ("expert", ["main"])]
)
def test_completeness_requires_trusted_identity_and_all_available_sources(
    queue, monkeypatch, trust, sources
):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", trust)
    completed_expert_review(queue, sources)
    view = queue.load("dev", PAPER, "admin")
    assert not view["completed_reviews"]
    with pytest.raises(ValueError, match="No completed expert review"):
        queue.finalize(
            "dev",
            PAPER,
            FinalizeRequest(
                base_revision=view["revision"],
                adopt_current_reviews=True,
                adopt_completed_review=True,
            ),
            "admin",
        )


def test_completeness_does_not_survive_scientific_edit(queue, monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    completed_expert_review(queue)
    trusted_correction(queue)
    assert not queue.load("dev", PAPER, "admin")["completed_reviews"]


def test_old_completion_adopted_only_if_nothing_scientific_changed(queue, monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    completed_expert_review(queue)
    current = queue.store.storage.load_revision("dev", PAPER)
    # Emulate the historical event format without modifying saved history.
    old = current.model_copy(deep=True)
    old.events[-1]["details"].pop("reviewed_snapshot")
    view = queue.load("dev", PAPER, "admin", current=old)
    assert (
        view["completed_reviews"][0]["binding_method"] == "unchanged_since_completion"
    )
    trusted_correction(queue)
    changed = queue.store.storage.load_revision("dev", PAPER).model_copy(deep=True)
    changed.events[-2]["details"].pop("reviewed_snapshot")
    assert not queue.load("dev", PAPER, "admin", current=changed)["completed_reviews"]


@pytest.mark.parametrize(
    "comment,mode,expected",
    [
        (False, "validated_current_workbook", True),
        (True, "validated_current_workbook", False),
        (False, "comments_only_from_older_workbook", False),
    ],
)
def test_workbook_trust_requires_exact_replacement_without_open_comments(
    queue, comment, mode, expected
):
    from review_workbench.adjudication import _records, _trusted_corrections

    current = queue.store.storage.load_revision("dev", PAPER)
    record = current.ground_truth["device_families"][0]
    key = "device_families:family-control"
    event = {
        "kind": "spreadsheet_review",
        "reviewer_id": "expert",
        "event_id": "workbook",
        "revision": 3,
        "details": {
            "workbook_import_mode": mode,
            "record_replacements": [{"collection": "device_families", "after": record}],
            "decisions": [{"record_key": key, "decision": "needs_correction"}],
            "cell_comments": [
                {
                    "record_collection": "device_families",
                    "record_id": "family-control",
                    "text": "Still unsure",
                }
            ]
            if comment
            else [],
        },
    }
    assert (
        bool(_trusted_corrections([event], _records(current.ground_truth), {"expert"}))
        == expected
    )
    later = {
        "kind": "record_decision",
        "reviewer_id": "expert",
        "event_id": "new-question",
        "revision": 4,
        "details": {"record_key": key, "decision": "needs_correction"},
    }
    assert not _trusted_corrections(
        [event, later], _records(current.ground_truth), {"expert"}
    )


def test_undo_withdraws_trusted_correction(queue, monkeypatch):
    from review_workbench.study_review import UndoMutationRequest

    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    trusted_correction(queue)
    current = queue.store.storage.load_revision("dev", PAPER)
    queue.store.undo_mutation(
        "dev",
        PAPER,
        UndoMutationRequest(
            base_revision=current.revision, event_id=current.events[-1]["event_id"]
        ),
        "expert",
    )
    assert not queue.load("dev", PAPER, "admin")["adopted_corrections"]


def test_trusted_completion_does_not_dismiss_comment_only_proposals(queue, monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")
    completed_expert_review(queue)
    queue.import_plan("dev", PAPER, workbook_plan(queue), "admin")
    assert queue.load("dev", PAPER, "admin")["cases"]
    with pytest.raises(ValueError, match="remaining items"):
        queue.finalize(
            "dev",
            PAPER,
            FinalizeRequest(
                base_revision=queue.store.revision("dev", PAPER),
                adopt_current_reviews=True,
                adopt_completed_review=True,
            ),
            "admin",
        )


def workbook_plan(queue):
    request = plan(queue)
    payload = request.model_dump()
    payload["proposals"][0].update(
        changes=[],
        feedback=[
            {
                "id": "original-B2",
                "workbook_sha256": "a" * 64,
                "filename": "review.xlsx",
                "sheet": "Record review",
                "cell": "B2",
                "text": "Check the electrode.",
                "old_record_key": "device_families:old-id",
                "review_outcome": "Correct fields",
                "reviewed_fields": {
                    "/label": {"extracted": "Old", "reviewed": "Control"}
                },
                "current_record_keys": ["device_families:family-control"],
            }
        ],
    )
    payload["feedback_counts"] = {"a" * 64: 1}
    return PlanRequest.model_validate(payload)


def test_workbook_comments_remain_pending_despite_current_record_approval(queue):
    request = workbook_plan(queue)
    view = queue.import_plan("dev", PAPER, request, "admin")
    assert view["inherited_count"] == 1
    assert len(view["cases"]) == 1
    assert len(view["workbook_feedback"]) == 1
    assert (
        view["workbook_current_records"]["device_families:family-control"]["label"]
        == "Control"
    )
    with pytest.raises(ValueError, match="remaining items"):
        finalize(queue)
    view = decide(queue, "keep")
    assert view["cases"] == []
    # Resolving a comment is not approval of an entire linked record.
    assert view["own_approved_count"] == 0
    assert view["last_decision"]["details"]["decisions"] == []
    assert len(view["workbook_feedback"]) == 1
    finalize(queue)
    export = build_ground_truth_export(queue.store, "dev", PAPER)
    event = next(e for e in export.review_events if e.kind == "adjudication_plan")
    assert (
        event.details["proposals"][0]["feedback"][0]["text"] == "Check the electrode."
    )


def test_resolved_comment_survives_unrelated_addition_but_not_subject_edit(queue):
    queue.import_plan("dev", PAPER, workbook_plan(queue), "admin")
    view = decide(queue, "keep")
    assert view["last_decision"]["details"]["decisions"] == []
    # A new family does not change the electrode in the already checked family.
    request = plan(queue)
    proposal = request.proposals[0]
    proposal.id = "add-other-family"
    proposal.changes[0].record_id = "family-other"
    proposal.changes[0].before = None
    proposal.changes[0].after["family_id"] = "family-other"
    queue.import_plan("dev", PAPER, request, "admin")
    view = decide(queue)
    assert not view["cases"]
    trusted_correction(queue, actor="coordinator", scalar=True)
    assert any(
        c["id"] == "proposal:correct-label"
        for c in queue.load("dev", PAPER, "admin")["cases"]
    )


def test_unmapped_workbook_comment_still_binds_whole_paper(queue):
    request = workbook_plan(queue)
    request.proposals[0].feedback[0].current_record_keys = []
    queue.import_plan("dev", PAPER, request, "admin")
    view = decide(queue, "keep")
    assert view["last_decision"]["details"]["case"]["binding_keys"] == []
    trusted_correction(queue, actor="coordinator", scalar=True)
    assert any(
        c["id"] == "proposal:correct-label"
        for c in queue.load("dev", PAPER, "admin")["cases"]
    )


@pytest.mark.parametrize(
    "failure", ["missing-count", "duplicate", "unknown-record", "duplicate-link"]
)
def test_incomplete_or_ambiguous_workbook_accounting_is_rejected(queue, failure):
    payload = workbook_plan(queue).model_dump()
    entries = payload["proposals"][0]["feedback"]
    if failure == "missing-count":
        payload["feedback_counts"] = {}
    elif failure == "duplicate":
        entries.append(entries[0])
        payload["feedback_counts"] = {"a" * 64: 2}
    elif failure == "unknown-record":
        entries[0]["current_record_keys"] = ["device_families:missing"]
    else:
        entries[0]["current_record_keys"] *= 2
    with pytest.raises(ValueError):
        queue.import_plan("dev", PAPER, PlanRequest.model_validate(payload), "admin")
    assert queue.load("dev", PAPER, "admin")["revision"] == 2


def test_replacing_correspondence_requires_supersession_and_preserves_history(queue):
    request = workbook_plan(queue)
    queue.import_plan("dev", PAPER, request, "admin")
    payload = request.model_dump()
    payload["base_revision"] = 3
    payload["proposals"][0]["id"] = "replacement"
    with pytest.raises(ValueError, match="Supersede"):
        queue.import_plan("dev", PAPER, PlanRequest.model_validate(payload), "admin")
    payload["supersedes"] = [request.proposals[0].id]
    view = queue.import_plan("dev", PAPER, PlanRequest.model_validate(payload), "admin")
    assert len(view["workbook_feedback"]) == 1
    assert view["workbook_feedback"][0]["case_id"] == "proposal:replacement"
    assert (
        len(
            [
                e
                for e in queue.store.events("dev", PAPER)
                if e["kind"] == "adjudication_plan"
            ]
        )
        == 2
    )


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


def test_refined_plan_retires_checklist_without_erasing_or_approving(queue):
    first = plan(queue)
    queue.import_plan("dev", PAPER, first, "admin")
    replacement = plan(queue)
    replacement.proposals[0].id = "source-backed-label"
    replacement.supersedes = [first.proposals[0].id]
    result = queue.import_plan("dev", PAPER, replacement, "admin")
    assert [case["id"] for case in result["cases"]] == ["proposal:source-backed-label"]
    assert result["own_approved_count"] == 0
    assert (
        queue.store.load_truth("dev", PAPER)["device_families"][0]["label"] == "Control"
    )
    events = queue.store.events("dev", PAPER)
    assert len([e for e in events if e["kind"] == "adjudication_plan"]) == 2
    with pytest.raises(ValueError, match="remaining"):
        finalize(queue)
    decided = decide(queue)
    assert decided["cases"] == []


@pytest.mark.parametrize(
    "supersedes", [["not-saved"], ["correct-label", "correct-label"]]
)
def test_supersession_requires_known_distinct_proposals(queue, supersedes):
    request = plan(queue)
    request.supersedes = supersedes
    with pytest.raises(ValueError, match="distinct saved"):
        queue.import_plan("dev", PAPER, request, "admin")
    assert queue.load("dev", PAPER, "admin")["revision"] == 2


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


def test_later_objection_reopens_shared_approval_for_every_finalizer(queue):
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    result = decide(queue)
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            collection="device_families",
            record_id="family-control",
            decision="uncertain",
            base_revision=result["revision"],
        ),
        "second-expert",
    )
    for actor in ("admin", "collaborator"):
        view = queue.load("dev", PAPER, actor)
        assert any(
            c.get("record_key") == "device_families:family-control"
            for c in view["cases"]
        )
        assert view["own_approved_count"] == 0
    with pytest.raises(ValueError, match="remaining"):
        finalize(queue)
    decide(queue, "keep")
    assert not queue.load("dev", PAPER, "collaborator")["cases"]


def test_reset_withdraws_shared_finalization_but_preserves_correction(queue):
    from review_workbench.study_review import ReviewerResetRequest

    queue.import_plan("dev", PAPER, plan(queue), "admin")
    result = decide(queue)
    queue.store.reset_reviewer_state(
        "dev", PAPER, ReviewerResetRequest(base_revision=result["revision"]), "admin"
    )
    assert (
        queue.store.load_truth("dev", PAPER)["device_families"][0]["label"]
        == "Champion control"
    )
    for actor in ("admin", "collaborator"):
        view = queue.load("dev", PAPER, actor)
        assert view["cases"]
        assert view["last_decision"] is None


def test_legacy_completion_cannot_bypass_pending_proposals(queue):
    from review_workbench.study_review import InventoryAuditRequest, StageRequest

    queue.store.inventory_audit(
        "dev",
        PAPER,
        InventoryAuditRequest(
            base_revision=2, searched_sources=["main"], expected_counts={}
        ),
        "expert",
    )
    for stage in ("inventory", "fields", "completeness"):
        queue.store.complete_stage(
            "dev",
            PAPER,
            StageRequest(stage=stage, base_revision=queue.store.revision("dev", PAPER)),
            "expert",
        )
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    with pytest.raises(ValueError, match="pending finalization"):
        queue.store.complete_stage(
            "dev",
            PAPER,
            StageRequest(
                stage="adjudication", base_revision=queue.store.revision("dev", PAPER)
            ),
            "expert",
        )


def test_export_rejects_historical_completion_with_pending_proposals(queue):
    queue.import_plan("dev", PAPER, plan(queue), "admin")
    current = queue.store.storage.load_revision("dev", PAPER)
    # Reproduce a snapshot produced by the old completion route, not a new approval.
    queue._commit(
        "dev", PAPER, current, "expert", "stage_complete", {"stage": "adjudication"}
    )
    with pytest.raises(ValueError, match="pending finalization"):
        build_ground_truth_export(queue.store, "dev", PAPER)


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
        ("POST", "/adopt-review"),
        ("POST", "/undo"),
        ("POST", "/finalize"),
    ],
)
def test_http_queue_denies_reviewers_by_default(queue, method, action, monkeypatch):
    monkeypatch.delenv("REVIEW_ALLOW_REVIEWER_FINALIZATION", raising=False)
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


def test_finalizers_share_resolutions_and_keep_undo_ownership(queue):
    queue.import_plan("dev", PAPER, workbook_plan(queue), "admin")
    result = decide(queue, "keep")
    other = queue.load("dev", PAPER, "collaborator")
    assert other["cases"] == []
    assert other["last_decision"] is None
    event = result["last_decision"]
    with pytest.raises(ValueError, match="your saved"):
        queue.undo("dev", PAPER, result["revision"], event["event_id"], "collaborator")
    queue.undo("dev", PAPER, result["revision"], event["event_id"], "admin")
    assert queue.load("dev", PAPER, "collaborator")["cases"]


def test_shared_adjudication_resolves_conflict_and_allows_another_finalizer(queue):
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
    decide(queue, "keep")
    view = queue.load("dev", PAPER, "collaborator")
    assert view["cases"] == []
    assert view["inherited"] == {
        "device_families:family-control": {"admin": "verified"}
    }
    result = queue.finalize(
        "dev",
        PAPER,
        FinalizeRequest(
            base_revision=view["revision"],
            adopt_current_reviews=True,
            completeness_checked=True,
        ),
        "collaborator",
    )
    assert result["finalized"]
    assert build_ground_truth_export(
        queue.store, "dev", PAPER
    ).manifest.review.adjudicators == ["collaborator"]


@pytest.mark.parametrize(
    "path,method,status",
    [
        (f"/api/adjudication/dev/{PAPER}", "GET", 200),
        (f"/api/adjudication/dev/{PAPER}/finalize", "POST", 201),
        (f"/api/adjudication/dev/{PAPER}/plan", "POST", 403),
        (f"/api/adjudication/dev/{PAPER}/adopt-review", "POST", 403),
        ("/api/users", "POST", 403),
        ("/api/session", "GET", 200),
    ],
)
def test_reviewer_finalization_permission_is_narrow(
    queue, monkeypatch, path, method, status
):
    from types import SimpleNamespace

    from review_workbench.server import make_handler

    monkeypatch.setenv("REVIEW_ALLOW_REVIEWER_FINALIZATION", "true")
    handler = object.__new__(
        make_handler(
            SimpleNamespace(store=queue.store, users=lambda: []), authenticator=object()
        )
    )
    handler.path = path
    handler._review_user = {"id": "collaborator", "role": "reviewer"}
    handler.read_json = lambda: {
        "base_revision": 2,
        "adopt_current_reviews": True,
        "completeness_checked": True,
    }
    responses = []
    handler.send_json = lambda payload, status=200, **kwargs: responses.append(
        (status, payload)
    )
    getattr(handler, f"do_{method}")()
    assert int(responses[0][0]) == status
    if path == "/api/session":
        assert responses[0][1]["user"]["can_finalize"] is True
        assert responses[0][1]["user"]["role"] == "reviewer"


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
    assert len(queue.load("dev", PAPER, "collaborator")["cases"]) == 2


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
