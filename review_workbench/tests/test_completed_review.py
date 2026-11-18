"""A completed expert review covers unchanged data without fictitious clicks."""

import copy
import shutil
import subprocess
from pathlib import Path

import pytest

from review_workbench.adjudication import DecisionRequest, FinalizeRequest
from review_workbench.completed_review import AdoptReviewRequest, review_candidate
from review_workbench.ground_truth_export import build_ground_truth_export
from review_workbench.study_review import (
    InventoryAuditRequest,
    MutationRequest,
    RecordDecisionRequest,
    ReviewerResetRequest,
)
from review_workbench.tests import test_adjudication as support
from review_workbench.tests.test_adjudication import PAPER, plan

queue = support.queue


def confirm(queue):
    current = queue.store.storage.load_revision("dev", PAPER)
    candidate = review_candidate(current, "expert")
    return queue.adopt_review(
        "dev",
        PAPER,
        AdoptReviewRequest(
            base_revision=current.revision,
            expert_id="expert",
            reviewed_through_revision=candidate["reviewed_through_revision"],
            completed_paper_review_confirmed=True,
            note="The coordinator confirms the expert finished reviewing the paper, including omissions.",
        ),
        "coordinator",
    )


def add_family(queue):
    record = copy.deepcopy(queue.store.load_truth("dev", PAPER)["device_families"][0])
    record["family_id"] = "family-second"
    record["label"] = "Second device family"
    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            base_revision=queue.store.revision("dev", PAPER),
            action="add",
            path="/device_families/-",
            value=record,
            evidence=record["evidence"],
        ),
        "coordinator",
    )


def finish_external_review(queue):
    queue.store.inventory_audit(
        "dev",
        PAPER,
        InventoryAuditRequest(
            base_revision=queue.store.revision("dev", PAPER),
            review_scope_sources=["main", "supplement"],
            expected_counts={},
        ),
        "expert",
    )


def freeze(queue):
    view = queue.load("dev", PAPER, "coordinator")
    return queue.finalize(
        "dev",
        PAPER,
        FinalizeRequest(
            base_revision=view["revision"],
            adopt_current_reviews=True,
            adopt_completed_review=True,
        ),
        "coordinator",
    )


@pytest.fixture(autouse=True)
def configure_trusted_expert(monkeypatch):
    monkeypatch.setenv("REVIEW_TRUSTED_REVIEWER_IDS", "expert")


def test_completed_review_covers_unchanged_records_without_individual_clicks(queue):
    add_family(queue)
    finish_external_review(queue)
    before = copy.deepcopy(queue.store.load_truth("dev", PAPER))
    assert queue.load("dev", PAPER, "coordinator")["cases"]
    view = confirm(queue)
    assert not view["cases"]
    assert view["adopted_paper_reviews"][0]["confirmed_by"] == "coordinator"
    assert view["completed_reviews"]
    assert before == queue.store.load_truth("dev", PAPER)
    freeze(queue)
    export = build_ground_truth_export(queue.store, "dev", PAPER)
    assert len(export.ground_truth.device_families) == 2
    assert (
        export.review_events[-1].details["adopted_completeness"][0]["basis"]
        == "coordinator_confirmed_external_completion"
    )
    assert not any(
        e.reviewer_id == "expert"
        and e.details.get("record_key") == "device_families:family-second"
        for e in export.review_events
    )


def test_later_addition_is_not_covered_by_external_confirmation(queue):
    add_family(queue)
    view = confirm(queue)
    assert [c["record_key"] for c in view["cases"]] == ["device_families:family-second"]
    with pytest.raises(ValueError, match="remaining"):
        freeze(queue)


@pytest.mark.parametrize("decision", ["uncertain", "needs_correction"])
def test_explicit_objection_is_not_overridden(queue, decision):
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            base_revision=2,
            collection="device_families",
            record_id="family-control",
            decision=decision,
        ),
        "other-reviewer",
    )
    assert confirm(queue)["cases"]
    with pytest.raises(ValueError, match="remaining"):
        freeze(queue)


def test_pending_proposal_is_not_silently_dismissed(queue):
    queue.import_plan("dev", PAPER, plan(queue), "coordinator")
    assert confirm(queue)["cases"][0]["id"] == "proposal:correct-label"


def test_expert_reset_withdraws_adopted_approvals_everywhere(queue):
    add_family(queue)
    finish_external_review(queue)
    view = confirm(queue)
    queue.store.reset_reviewer_state(
        "dev", PAPER, ReviewerResetRequest(base_revision=view["revision"]), "expert"
    )
    view = queue.load("dev", PAPER, "coordinator")
    assert view["cases"] and not view["adopted_paper_reviews"]
    current = queue.store.storage.load_revision("dev", PAPER)
    assert not queue.store.summary(current.ground_truth, current.events)[
        "record_decisions"
    ].get("coordinator")


def test_undo_adoption_preserves_data_and_original_reviews(queue):
    add_family(queue)
    finish_external_review(queue)
    view = confirm(queue)
    before = copy.deepcopy(queue.store.load_truth("dev", PAPER))
    undone = queue.undo(
        "dev", PAPER, view["revision"], view["last_decision"]["event_id"], "coordinator"
    )
    assert not undone["adopted_paper_reviews"] and undone["cases"]
    assert before == queue.store.load_truth("dev", PAPER)


def test_untrusted_identity_or_wrong_review_boundary_is_rejected(queue):
    current = queue.store.storage.load_revision("dev", PAPER)
    for expert, cutoff in [("unknown", 2), ("expert", 1)]:
        with pytest.raises(ValueError):
            queue.adopt_review(
                "dev",
                PAPER,
                AdoptReviewRequest(
                    base_revision=current.revision,
                    expert_id=expert,
                    reviewed_through_revision=cutoff,
                    completed_paper_review_confirmed=True,
                    note="Confirmed by coordinator.",
                ),
                "coordinator",
            )


def test_repeated_adoption_does_not_duplicate_history(queue):
    confirm(queue)
    revision = queue.store.revision("dev", PAPER)
    with pytest.raises(ValueError, match="already been adopted"):
        confirm(queue)
    assert queue.store.revision("dev", PAPER) == revision


def test_later_removal_requires_an_explicit_decision(queue):
    add_family(queue)
    finish_external_review(queue)
    record = queue.store.load_truth("dev", PAPER)["device_families"][1]
    queue.store.mutate(
        "dev",
        PAPER,
        MutationRequest(
            base_revision=queue.store.revision("dev", PAPER),
            action="remove",
            path="/device_families/1",
            evidence=record["evidence"],
            note="Duplicate not supported as a separate family.",
        ),
        "coordinator",
    )
    view = confirm(queue)
    case = view["cases"][0]
    assert case["id"].startswith("post-review-change:")
    with pytest.raises(ValueError, match="remaining"):
        freeze(queue)
    queue.decide(
        "dev",
        PAPER,
        DecisionRequest(
            base_revision=view["revision"],
            case_id=case["id"],
            action="keep",
            note="Duplicate removed after review.",
        ),
        "coordinator",
    )
    assert freeze(queue)["finalized"]


@pytest.mark.parametrize("decision", ["uncertain", "needs_correction"])
def test_objection_after_adoption_reopens_the_record(queue, decision):
    view = confirm(queue)
    queue.store.decide_record(
        "dev",
        PAPER,
        RecordDecisionRequest(
            base_revision=view["revision"],
            collection="device_families",
            record_id="family-control",
            decision=decision,
        ),
        "other-reviewer",
    )
    assert queue.load("dev", PAPER, "coordinator")["cases"]
    with pytest.raises(ValueError, match="remaining"):
        freeze(queue)


@pytest.mark.parametrize("scalar", [False, True])
def test_post_review_edits_are_not_implicitly_approved(queue, scalar):
    from review_workbench.tests.test_adjudication import trusted_correction

    trusted_correction(queue, "coordinator", scalar=scalar)
    assert confirm(queue)["cases"]


def test_completed_review_export_is_accepted_by_the_real_scorer(queue, tmp_path):
    from click.testing import CliRunner

    from perla_extract.study_extraction.evaluation_cli import main as evaluate
    from review_workbench.ground_truth_export import write_ground_truth_export

    add_family(queue)
    finish_external_review(queue)
    confirm(queue)
    freeze(queue)
    frozen = write_ground_truth_export(
        build_ground_truth_export(queue.store, "dev", PAPER), tmp_path / "reference"
    )
    result = CliRunner().invoke(
        evaluate,
        [
            "--truth",
            str(frozen),
            "--prediction",
            str(frozen / "ground_truth.json"),
            "--output",
            str(tmp_path / "score.json"),
        ],
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("already_adopted", [False, True])
def test_refresh_after_completed_review_prevents_adoption(queue, already_adopted):
    if already_adopted:
        confirm(queue)
    current = queue.store.storage.load_revision("dev", PAPER)
    replacement = copy.deepcopy(current.ground_truth)
    replacement["device_families"][0]["label"] = "New extraction label"
    queue.store.refresh_ground_truth(
        "dev",
        PAPER,
        replacement,
        base_revision=current.revision,
        reviewer_id="coordinator",
        reason="Replace extraction with a new run",
        provenance={"run": "new-run"},
    )
    with pytest.raises(ValueError, match="replaced after"):
        confirm(queue)
    assert not queue.load("dev", PAPER, "coordinator")["completed_reviews"]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")
def test_dataset_action_preserves_exceptions_and_downloads_a_report():
    source = Path(__file__).parents[1] / "review_app/adjudication.js"
    script = r"""
const assert = require('node:assert/strict');
const fs = require('node:fs');
class Element {
  constructor(tag) { this.tag=tag; this.children=[]; this.classList={toggle(){}}; }
  append(...items) { this.children.push(...items); }
  prepend(...items) { this.children.unshift(...items); }
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
  global.document = {body:new Element('body'), createElement:tag=>new Element(tag)};
  const posts=[], saved=[];
  const candidate={expert_id:'expert', expert_name:'Expert', reviewed_at:'2026-10-01', reviewed_through_revision:2};
  const views = Object.fromEntries(['ready','exception','unreviewed','failure'].map(id=>[id, {
    title:id, revision:2, record_count:1, inherited_count:1, own_approved_count:0,
    cases:id==='exception' ? [{id:'proposal:question',reason:'Unclear unit'}] : [],
    review_candidates:id==='unreviewed' ? [] : [candidate], adopted_paper_reviews:[], completed_reviews:[]
  }]));
  const ui=createFinalizationQueue({
    request:async (url, options) => {
      const [, , , , id, action]=url.split('/');
      if (id==='failure') throw new Error('Network unavailable');
      const view=views[id];
      if (options) {
        const body=JSON.parse(options.body); posts.push({id,action,body});
        assert.equal(body.base_revision,view.revision);
        view.revision++;
        if (action==='adopt-review') { view.adopted_paper_reviews=[candidate]; view.completed_reviews=[candidate]; }
        if (action==='finalize') view.finalized=true;
      }
      return structuredClone(view);
    },
    context:()=>({split:'dev', paperId:'ready', papers:Object.keys(views).map(id=>({id})), canImportPlan:true}),
    saveBlob:(blob,name)=>saved.push({blob,name}), download:async()=>{}
  });
  await ui.open();
  const action=document.body.querySelectorAll('button').find(el=>el.textContent==='Use this review across the dataset');
  await action.onclick();
  assert.equal(posts.length,0);
  document.body.querySelectorAll('input').find(el=>el.type==='checkbox').checked=true;
  document.body.querySelectorAll('select')[0].value='expert';
  await action.onclick();
  assert.deepEqual(posts.map(p=>[p.id,p.action]), [['ready','adopt-review'],['ready','finalize'],['exception','adopt-review']]);
  assert.equal(posts[0].body.completed_paper_review_confirmed,true);
  const reportButton=document.body.querySelectorAll('button').find(el=>el.textContent==='Download exception report');
  await reportButton.onclick();
  const report=JSON.parse(await saved[0].blob.text());
  assert.equal(report.papers.length,4);
  assert.equal(report.papers[0].status,'Final reference ready');
  assert.equal(report.papers[1].exceptions[0].reason,'Unclear unit');
  assert.equal(report.papers[2].status,'Not reviewed by this expert');
  assert.equal(report.papers[3].error,'Network unavailable');
})().catch(error=>{console.error(error);process.exit(1)});
"""
    subprocess.run(
        ["node", "-e", script, str(source)], check=True, capture_output=True, text=True
    )
