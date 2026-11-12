"""Behavioral contracts for imported UI modules, without source slicing."""

import json
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "review_app"
pytestmark = pytest.mark.usefixtures("node_runtime")


def run_javascript(script):
    imports = "import assert from 'node:assert/strict';\n"
    for module, names in (
        ("api-client.js", "createApiClient, ReviewApiError"),
        ("review-session.js", "createReviewSession"),
        ("record-editor.js", "createRecordEditor, createDraftState"),
    ):
        imports += f"import {{ {names} }} from {json.dumps((APP / module).as_uri())};\n"
    dom = Path(__file__).with_name("dom-fixture.mjs")
    imports += (
        f"import {{editorDocument, descendants}} from {json.dumps(dom.as_uri())};\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", imports + script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_api_preserves_auth_json_conflicts_and_retry_boundaries():
    run_javascript("""
const calls = [];
let response = new Response(JSON.stringify({revision:2}), {headers:{'Content-Type':'application/json'}});
const api = createApiClient({getToken:async () => 'test-token', fetch:async (url, options) => {
  calls.push({url, options}); return response.clone();
}, wait:async () => {}});
assert.equal((await api.mutate('dev','paper/one',{base_revision:1})).revision,2);
assert.equal(calls[0].url,'/api/mutations/dev/paper%2Fone');
assert.equal(calls[0].options.headers.get('Authorization'),'Bearer test-token');
assert.equal(calls[0].options.headers.get('Content-Type'),'application/json');
assert.deepEqual(JSON.parse(calls[0].options.body),{base_revision:1});
response = new Response(JSON.stringify({code:'review_revision_conflict',error:'Reload'}),
  {status:409,headers:{'Content-Type':'application/json'}});
await assert.rejects(api.requestWithRetry('/conflict'), error =>
  error instanceof ReviewApiError && error.status === 409 && error.code === 'review_revision_conflict');
assert.equal(calls.length,2); // A revision conflict is never retried.
response = new Response('temporary',{status:503});
await assert.rejects(api.requestWithRetry('/retry'),error => error.status === 503);
assert.equal(calls.length,4);
const aborting = createApiClient({fetch:async () => {throw new DOMException('Cancelled','AbortError');},wait:async () => assert.fail('retried abort')});
await assert.rejects(aborting.requestWithRetry('/abort'),error => error.name === 'AbortError');
response = new Response('downloaded');
assert.equal(await (await api.download('/file')).text(),'downloaded');
""")


def test_session_saves_owner_correction_with_original_revision_and_retains_failed_draft():
    run_javascript("""
const calls = [];
let reject = false;
const api = {mutate:async (split,paper,payload) => {
  calls.push({split,paper,payload});
  if (reject) throw new ReviewApiError('Reload',{code:'review_revision_conflict',status:409});
  return {revision:payload.base_revision+1,ground_truth:{individual_devices:[payload.value]}};
}};
const session = createReviewSession(api);
assert.equal(session.split,'dev');
session.select('paper',{revision:3});
const original = {key:'device_families:old',kind:'device_families'};
session.beginPending('reclassify',original);
const draft = createDraftState({device_id:'cell',family_id:'old'});
draft.update(['family_id'],'new','old');
const edit = {kind:'individual_devices',index:0,baseRevision:3};
await session.saveRecord({edit,value:draft.snapshot(),citation:{block_id:'main-1',quote:'source'}});
assert.equal(calls[0].payload.path,'/individual_devices/0');
assert.equal(session.bundle.ground_truth.individual_devices[0].family_id,'new');
assert.equal(session.pendingAction.key,original.key);
assert.equal(session.resolvePending([original],() => []).ready,true);
session.cancelPending();
assert.equal(session.bundle.revision,4); // Cancel does not undo the saved link correction.
reject = true;
await assert.rejects(session.saveRecord({edit,value:draft.snapshot(),citation:{}}),error => error.status === 409);
assert.equal(calls.at(-1).payload.base_revision,3); // Opening revision, not current session revision.
assert.equal(session.bundle.revision,4);
assert.equal(draft.value.family_id,'new');
session.reset('test');
assert.equal(session.bundle,null);
assert.equal(session.paperId,null);
assert.equal(session.split,'test');
""")


EDITOR_SETUP = """
const {document, element} = editorDocument();
const citation = {block_id:'main-1',quote:'The cell belongs to Alternative.'};
const initial = {device_id:'cell',family_id:'control',label:'Cell',count:null,evidence:[citation]};
const bundle = {revision:5,ground_truth:{
  device_families:[{family_id:'control',label:'Control'},{family_id:'alternative',label:'Alternative'}],
  individual_devices:[initial]},summary:{record_identifiers:{device_families:'family_id',individual_devices:'device_id'}}};
const schema = {properties:{individual_devices:{items:{properties:{
  device_id:{type:'string'}, family_id:{anyOf:[{type:'string'},{type:'null'}]},
  count:{anyOf:[{type:'integer'},{type:'null'}]},label:{type:'string'},evidence:{type:'array'},
}}}}};
const editor = createRecordEditor({document,element,context:() => ({bundle,studySchema:schema}),
  collections:{individual_devices:'Individual devices'},humanLabel:value => value,
  singularCollection:() => 'Individual device',recordKey:() => 'individual_devices:cell',
  recordReferences:() => [],entityTitle:(_kind,item) => item.label,linkedRecordSummary:() => '',
  reviewReferencedRecord:() => {},focusCitation:() => {}});
"""


def test_editor_owner_selection_and_json_mode_share_one_draft():
    run_javascript(
        EDITOR_SETUP
        + """
editor.open('individual_devices',0);
assert.equal(document.getElementById('record-dialog').open,true);
assert.equal(document.getElementById('json-editor-panel').hidden,true);
const owner = document.getElementById('record-field-family_id');
assert.equal(owner.tag,'select');
assert.deepEqual(owner.children.map(option => option.value),['','control','alternative']);
owner.value = 'alternative'; owner.fire('input');
assert.equal(editor.value().family_id,'alternative');
assert.equal(JSON.parse(document.getElementById('record-json').value).family_id,'alternative');
assert.equal(initial.family_id,'control');
const count = document.getElementById('record-field-count');
assert.equal(count.type,'number');
count.value = '12'; count.fire('input');
assert.equal(editor.value().count,12);
editor.setMode('json');
const raw = document.getElementById('record-json');
raw.value = JSON.stringify({...editor.value(),label:'Edited in JSON'}); raw.fire('input');
assert.equal(editor.edit.value.label,'Edited in JSON');
assert.equal(editor.setMode('fields'),true);
assert.equal(document.getElementById('record-field-label').value,'Edited in JSON');
assert.equal(editor.edit.baseRevision,5);
editor.setMode('json'); raw.value = '{'; raw.fire('input');
assert.equal(editor.setMode('fields'),false);
assert.throws(() => editor.value());
assert.equal(editor.edit.value.label,'Edited in JSON');
raw.value = '[]'; assert.throws(() => editor.value(),/JSON object/);
"""
    )


def test_editor_new_and_copied_records_use_schema_and_distinct_identity():
    run_javascript(
        EDITOR_SETUP
        + """
editor.open('individual_devices');
assert.equal(editor.edit.index,null);
assert.equal(editor.value().family_id,null);
assert.equal(editor.value().count,null);
assert.deepEqual(editor.value().evidence,[]);
editor.open('individual_devices',null,initial,'copy');
assert.equal(editor.value().device_id,'cell-review-copy');
assert.equal(initial.device_id,'cell');
assert.equal(editor.value().family_id,'control');
"""
    )


def test_draft_reordering_preserves_content_and_renumbers_sequences():
    run_javascript("""
const original = {steps:[{sequence:7,operation:'A',conditions:[{value:10}]},
  {sequence:2,operation:'B',conditions:[{value:20}]},{sequence:99,operation:'C',conditions:[{value:30}]}]};
const draft = createDraftState(original);
assert.equal(draft.reorder(['steps'],0,2),true);
assert.deepEqual(draft.value.steps.map(step => step.operation),['B','C','A']);
assert.deepEqual(draft.value.steps.map(step => step.sequence),[1,2,3]);
assert.equal(draft.value.steps[2].conditions[0].value,10);
assert.equal(draft.reorder(['steps'],9,0),false);
assert.equal(draft.reorder(['steps'],0,-1),false);
assert.deepEqual(original.steps.map(step => step.sequence),[7,2,99]);
const snapshot = draft.snapshot(); snapshot.steps[0].operation = 'changed';
assert.equal(draft.value.steps[0].operation,'B');
assert.throws(() => draft.replaceJson('null'),/JSON object/);
""")


def test_editor_render_failure_keeps_an_open_json_correction_dialog():
    setup = EDITOR_SETUP.replace(
        "document,element,context:",
        """document,element:(...args) => {
  assert.equal(document.getElementById('record-dialog').open,true);
  if (args[1]?.className === 'editor-field') throw new Error('broken field');
  return element(...args);
},context:""",
    )
    run_javascript(
        setup
        + """
editor.open('individual_devices',0);
assert.equal(document.getElementById('json-editor-panel').hidden,false);
assert.match(document.getElementById('dialog-status').textContent,/broken field/);
const raw = document.getElementById('record-json');
raw.value = JSON.stringify({...initial,family_id:'alternative'}); raw.fire('input');
assert.equal(editor.value().family_id,'alternative');
"""
    )


def test_stack_controls_preserve_layer_content_and_detach_removed_links():
    run_javascript(
        EDITOR_SETUP
        + """
const family = {family_id:'control',label:'Control',full_stack_raw:'A/B',
  layers:[{layer_id:'a',sequence:1,material:'A',role:'not_reported',constituents:[],reported_properties:[],evidence:[citation]},
    {layer_id:'b',sequence:2,material:'B',role:'not_reported',constituents:[],reported_properties:[],evidence:[citation]}],
  absorbers:[{absorber_id:'absorber',layer_id:'a',evidence:[citation]}],
  processing_steps:[{sequence:1,operation:'coat',target_layer_ids:['a','b'],evidence:[citation]}],evidence:[citation]};
bundle.ground_truth.device_families[0] = family;
editor.open('device_families',0);
const all = () => descendants(document.getElementById('structured-editor'));
assert.ok(all().some(node => node.textContent === 'Technical details'));
assert.ok(all().some(node => node.textContent === 'Materials and fabrication'));
const down = all().find(node => node.attributes?.['aria-label'] === 'Move layer down');
down.fire('click');
assert.deepEqual(editor.value().layers.map(layer => layer.material),['B','A']);
assert.deepEqual(editor.value().layers.map(layer => layer.sequence),[1,2]);
const remove = all().find(node => node.attributes?.['aria-label'] === 'Remove layer 2');
remove.fire('click');
assert.equal(editor.value().absorbers[0].layer_id,null);
assert.deepEqual(editor.value().processing_steps[0].target_layer_ids,['b']);
assert.deepEqual(editor.value().layers[0].evidence,[citation]);
assert.equal(family.layers.length,2);
assert.equal(family.absorbers[0].layer_id,'a');
all().find(node => node.textContent === '+ Add layer').fire('click');
assert.equal(editor.value().layers.length,2);
assert.equal(new Set(editor.value().layers.map(layer => layer.layer_id)).size,2);
"""
    )


def test_ordered_controls_update_the_same_draft_and_json_view():
    run_javascript(
        EDITOR_SETUP
        + """
initial.steps = [{sequence:7,operation:'first'},{sequence:9,operation:'second'}];
editor.open('individual_devices',0);
const all = () => descendants(document.getElementById('structured-editor'));
all().find(node => node.textContent === '↓ Later').fire('click');
assert.deepEqual(editor.value().steps.map(step => step.operation),['second','first']);
assert.deepEqual(editor.value().steps.map(step => step.sequence),[1,2]);
assert.deepEqual(JSON.parse(document.getElementById('record-json').value).steps,editor.value().steps);
assert.deepEqual(initial.steps.map(step => step.sequence),[7,9]);
"""
    )


def test_session_reclassification_and_removal_complete_pending_actions():
    run_javascript("""
const calls = [];
const summary = {record_identifiers:{individual_devices:'device_id'}};
const api = {reclassify:async (split,paper,payload) => {
  calls.push(payload); return {revision:2,summary};
}, mutate:async (split,paper,payload) => {calls.push(payload); return {revision:3,summary};}};
const session = createReviewSession(api);
session.select('paper',{revision:1,summary});
session.beginPending('reclassify',{kind:'device_families',key:'device_families:old'});
const citation = {block_id:'source',quote:'Exact source'};
const value = {device_id:'new',evidence:[],details:{evidence:[]}};
await session.saveRecord({edit:{kind:'individual_devices',index:null,intent:'reclassify',baseRevision:1,
  source:{kind:'device_families',recordId:'old'}},value,citation,note:'Correct type'});
assert.equal(calls[0].source_record_id,'old');
assert.equal(calls[0].target_collection,'individual_devices');
assert.deepEqual(calls[0].value.details.evidence,[citation]);
assert.deepEqual(value.evidence,[]); // Evidence preparation cannot mutate the editor draft.
assert.equal(session.pendingAction,null);
session.beginPending('remove',{kind:'individual_devices',key:'individual_devices:new'});
await session.removeRecord({kind:'individual_devices',index:0,value,baseRevision:2},'Unsupported');
assert.equal(calls[1].action,'remove');
assert.equal(calls[1].path,'/individual_devices/0');
assert.equal(calls[1].base_revision,2);
assert.equal(session.pendingAction,null);
assert.equal(session.bundle.revision,3);
""")


def test_slow_record_save_cannot_replace_a_different_selected_paper():
    run_javascript("""
let finish;
const session = createReviewSession({mutate:() => new Promise(resolve => {finish = resolve;})});
session.select('first',{revision:1});
const saving = session.saveRecord({edit:{kind:'individual_devices',index:0,baseRevision:1},
  value:{device_id:'cell'},citation:{block_id:'source',quote:'Source'}});
session.select('second',{revision:8});
finish({revision:2});
await saving;
assert.equal(session.paperId,'second');
assert.equal(session.bundle.revision,8);
""")
