"""Exercise imported browser helpers; labels and source-code layout are not contracts."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "review_app/review-state.js"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is required")


def run_javascript(script):
    source = (
        "import assert from 'node:assert/strict';\n"
        f"import * as review from {json.dumps(MODULE.as_uri())};\n" + script
    )
    subprocess.run(
        ["node", "--input-type=module", "-e", source],
        check=True,
        capture_output=True,
        text=True,
    )


def test_change_type_intent_survives_link_correction_and_cancel():
    run_javascript("""
const original = {key:'device_families:f', kind:'device_families', index:0};
const dependent = {key:'individual_devices:d', kind:'individual_devices'};
const before = structuredClone([original, dependent]);
let pending = review.pendingRecordAction('reclassify', original);
let resolved = review.resolvePendingAction(pending, [original, dependent], () => [dependent]);
assert.equal(resolved.operation, 'reclassify');
assert.equal(resolved.ready, false);
// Saving a linked record can change indices. Resume by stable identity.
resolved = review.resolvePendingAction(pending, [{...original,index:3}, dependent], () => []);
assert.equal(resolved.operation, 'reclassify');
assert.equal(resolved.entry.index, 3);
assert.equal(resolved.ready, true);
assert.equal(review.resolvePendingAction(pending, [], () => []).ready, false);
pending = null;
assert.equal(review.resolvePendingAction(pending, before, () => []), null);
assert.deepEqual([original, dependent], before); // Navigation never mutates truth.
assert.equal(review.pendingRecordAction('remove', original).operation, 'remove');
""")


def test_every_record_citation_selects_its_own_source():
    run_javascript("""
const citations = [{block_id:'main-1',quote:'main evidence'}, {block_id:'si-2',quote:'SI evidence'}];
const blocks = {'main-1':{source:'main',page:1}, 'si-2':{source:'supplement',page:7}};
const element = (tag, options={}, children=[]) => ({tag, ...options, children});
const selected = [];
const section = review.recordEvidenceSection(citations, element,
    citation => selected.push(blocks[citation.block_id]), id => blocks[id]);
const nodes = node => [node, ...node.children.flatMap(nodes)];
const buttons = nodes(section).filter(node => node.tag === 'button');
assert.equal(buttons.length, 2);
buttons[0].events.click(); buttons[1].events.click();
assert.deepEqual(selected, [{source:'main',page:1},{source:'supplement',page:7}]);
assert.equal(nodes(section).filter(node => node.tag === 'blockquote').length, 2);
assert.equal(nodes(review.recordEvidenceSection([], element, () => {})).filter(node => node.tag === 'button').length, 0);
""")


def test_review_completion_does_not_finalize_uncertainty():
    run_javascript("""
const summary = {record_count:1, record_decisions:{reviewer:{record:'uncertain'}},
    completed_stages:{inventory:['reviewer'],fields:['reviewer'],completeness:['reviewer']}};
const events = [{kind:'stage_complete',reviewer_id:'reviewer',details:{stage:'adjudication'}}];
const user = {id:'reviewer',role:'reviewer',can_finalize:true};
let result = review.reviewProgress(summary, events, user);
assert.equal(result.reviewed, 1);
assert.equal(result.reviewCompleted, true);
assert.equal(result.uncertain, 1);
assert.equal(result.unresolved, 1);
assert.equal(result.canFinalize, false);
assert.equal(result.canExport, false);
summary.record_decisions.reviewer.record = 'verified';
result = review.reviewProgress(summary, events, user);
assert.equal(result.canFinalize, true);
assert.equal(result.canExport, true); // Capability, not an administrator role.
assert.equal(review.reviewProgress(summary, events, {...user,can_finalize:false}).canExport, false);
assert.equal(review.reviewProgress(summary, [...events,{kind:'mutation'}], user).canExport, false);
""")


def test_schema_notice_reports_only_known_compatibility():
    run_javascript("""
assert.equal(review.schemaCompatibilityMessage({exact_match:true}), '');
const readable = review.schemaCompatibilityMessage({exact_match:false,readable_by_current_schema:true});
assert.ok(!readable.includes('fields added'));
const compatible = review.schemaCompatibilityMessage({exact_match:false,readable_by_current_schema:true,structurally_compatible:true});
assert.ok(compatible.includes('compatible'));
assert.ok(review.schemaCompatibilityMessage({exact_match:false,readable_by_current_schema:false}).includes('cannot be read'));
""")


def test_relationship_choices_keep_ids_and_never_reclassify_identity_fields():
    run_javascript("""
const truth = {device_families:[{family_id:'first',label:'Control'},{family_id:'second',label:'Alternative'}]};
const ids = {device_families:'family_id',individual_devices:'device_id'};
const result = review.relationshipChoices('family_id','individual_devices',truth,ids,'first');
assert.deepEqual(result.choices, [{value:'first',label:'Control'},{value:'second',label:'Alternative'}]);
assert.equal(review.relationshipChoices('family_id','device_families',truth,ids,'first'), null);
assert.equal(review.relationshipChoices('label','individual_devices',truth,ids,'Control'), null);
const missing = review.relationshipChoices('family_id','individual_devices',truth,ids,'archived');
assert.equal(missing.choices.at(-1).value, 'archived'); // Never silently clears an invalid link.
assert.equal(truth.device_families.length, 2);
""")
