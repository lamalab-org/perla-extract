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
        (
            "jv-table.js",
            "createJvTable, observationsFromRows, reportedNumber, valuesMissingFromQuote",
        ),
        ("record-details.js", "renderRecordDetails, renderSubjectContext"),
        ("reference-coverage.js", "renderCoverage, readCoverage"),
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


def test_coverage_form_preserves_unknowns_and_explicit_source_review():
    run_javascript("""
const {element: makeElement} = editorDocument();
const element = (tag, options = {}, children = []) => {
  const node = makeElement(tag, options, children);
  Object.assign(node.dataset, options.dataset || {});
  return node;
};
const container = element('div');
container.querySelectorAll = () => descendants(container).filter(node => node.dataset?.coverageDomain);
let edits = 0;
renderCoverage(container, {}, element, () => edits++);
let saved = readCoverage(container);
assert.equal(Object.keys(saved.domains).length, 4);
assert.equal(saved.domains.performance.figures, 'unreviewed');
const controls = container.querySelectorAll();
const figure = controls.find(node => node.dataset.coverageDomain === 'performance' && node.dataset.coverageField === 'figures');
figure.value = 'partial';
figure.fire('input');
const evidence = controls.find(node => node.dataset.coverageDomain === 'performance' && node.dataset.coverageField === 'evidence');
evidence.value = 'Figure 7 still needs reviewed digitization.';
saved = readCoverage(container);
renderCoverage(container, saved, element, () => edits++);
assert.deepEqual(readCoverage(container), saved);
assert.equal(edits, 1);
assert.equal(saved.domains.processing.text_and_tables, 'unreviewed');
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


FAMILY_SCHEMA = """
const text = {type:'string'};
const nullableText = {anyOf:[{type:'string'},{type:'null'}]};
schema.properties.device_families = {items:{properties:{
  family_id:text, label:text, evidence:{type:'array'},
  layers:{type:'array',items:{properties:{layer_id:text,sequence:{type:'integer'},material:text,role:text,evidence:{type:'array'}}}},
  absorbers:{type:'array',items:{properties:{absorber_id:text,layer_id:nullableText,label:text,evidence:{type:'array'}}}},
  processing_steps:{type:'array',items:{properties:{step_id:text,sequence:{type:'integer'},operation:text,
    target_layer_ids:{type:'array',items:text},evidence:{type:'array'}}}},
}}};
bundle.summary.record_identifiers.device_families = 'family_id';
const family = {family_id:'control',label:'Control',evidence:[citation],
  layers:[{layer_id:'a',sequence:1,material:'SnO2',role:'electron_transport_layer',evidence:[citation]},
    {layer_id:'b',sequence:2,material:'FAPbI3',role:'absorber',evidence:[citation]}],
  absorbers:[{absorber_id:'absorber-1',layer_id:'b',label:'FAPbI3',evidence:[citation]}],
  processing_steps:[{step_id:'step-1',sequence:1,operation:'spin coating',target_layer_ids:['a','b'],evidence:[citation]}]};
bundle.ground_truth.device_families[0] = family;
const all = () => descendants(document.getElementById('structured-editor'));
const group = name => all().find(node => node.tag === 'details' && node.children[0]?.textContent?.startsWith(name));
"""


def test_editor_gives_new_records_and_nested_items_readable_unique_ids():
    run_javascript(
        EDITOR_SETUP
        + FAMILY_SCHEMA
        + """
editor.open('individual_devices');
assert.equal(editor.value().device_id,'device-1');
editor.open('device_families');
assert.equal(editor.value().family_id,'family-1');
editor.open('device_families',0);
all().find(node => node.textContent === 'Add item to absorbers').fire('click');
assert.equal(editor.value().absorbers[1].absorber_id,'absorber-2');
all().find(node => node.textContent === 'Add item to processing_steps').fire('click');
assert.equal(editor.value().processing_steps[1].step_id,'step-2');
assert.equal(family.absorbers.length,1);
"""
    )


def test_editor_links_absorbers_and_steps_to_layers_by_choice():
    run_javascript(
        EDITOR_SETUP
        + FAMILY_SCHEMA
        + """
editor.open('device_families',0);
const link = document.getElementById('record-field-absorbers-0-layer_id');
assert.equal(link.tag,'select');
assert.deepEqual(link.children.map(option => option.value),['','a','b']);
assert.match(link.children[2].textContent,/FAPbI3/);
link.value = 'a'; link.fire('input');
assert.equal(editor.value().absorbers[0].layer_id,'a');
link.value = ''; link.fire('input');
assert.equal(editor.value().absorbers[0].layer_id,null);
const boxes = all().filter(node => node.type === 'checkbox');
assert.deepEqual(boxes.map(box => [box.value, box.checked]),[['a',true],['b',true]]);
boxes[0].checked = false; boxes[0].fire('change');
assert.deepEqual(editor.value().processing_steps[0].target_layer_ids,['b']);
boxes[0].checked = true; boxes[0].fire('change');
assert.deepEqual(editor.value().processing_steps[0].target_layer_ids,['a','b']);
assert.deepEqual(JSON.parse(document.getElementById('record-json').value).processing_steps[0].target_layer_ids,['a','b']);
assert.deepEqual(family.processing_steps[0].target_layer_ids,['a','b']);
"""
    )


def test_editor_keeps_opened_sections_open_after_adding_an_item():
    run_javascript(
        EDITOR_SETUP
        + FAMILY_SCHEMA
        + """
editor.open('device_families',0);
assert.equal(group('processing_steps').open,false);
const steps = group('processing_steps'); steps.open = true; steps.fire('toggle');
all().find(node => node.textContent === 'Add item to absorbers').fire('click');
assert.equal(group('processing_steps').open,true);
assert.equal(group('absorbers').open,true);
const added = all().filter(node => node.tag === 'details' && node.className === 'editor-group')
  .find(node => node.children[0]?.textContent === 'Item 2');
assert.equal(added.open,true);
const absorbers = group('absorbers'); absorbers.open = false; absorbers.fire('toggle');
all().find(node => node.textContent === 'Add item to processing_steps').fire('click');
assert.equal(group('absorbers').open,false);
editor.open('device_families',0);
assert.equal(group('processing_steps').open,false);
"""
    )


def test_editor_opens_beside_the_paper_and_reports_unsaved_work():
    run_javascript(
        EDITOR_SETUP
        + """
editor.open('individual_devices',0);
const dialog = document.getElementById('record-dialog');
assert.equal(dialog.open,true);
assert.equal(dialog.modal,false);
assert.equal(editor.changed,false);
const label = document.getElementById('record-field-label');
label.value = 'Renamed'; label.fire('input');
assert.equal(editor.changed,true);
label.value = 'Cell'; label.fire('input');
assert.equal(editor.changed,false);
editor.setMode('json');
const raw = document.getElementById('record-json');
raw.value = '{'; raw.fire('input');
assert.equal(editor.changed,true);
editor.open('individual_devices');
assert.equal(editor.changed,false);
"""
    )


def test_jv_rows_become_observations_with_only_reported_values():
    run_javascript("""
assert.equal(reportedNumber('23.5'),23.5);
assert.equal(reportedNumber('\u22120.8'),-0.8);
assert.equal(reportedNumber('1,12'),1.12);
assert.equal(reportedNumber('20.6 \u00b1 0.3'),null);
assert.equal(reportedNumber(''),null);
const rows = [
  {measurement_type:'jv_scan',scan_direction:'reverse',values:{PCE:'20.6',Voc:'1.12',Jsc:' ',FF:''}},
  {measurement_type:'jv_scan',scan_direction:'forward',values:{}},
  {measurement_type:'stabilized_power_output',scan_direction:'not_applicable',values:{PCE:'19.8 \u00b1 0.2'}},
];
const units = {PCE:'%',Voc:'V',Jsc:'mA cm-2',FF:'%',Rs:'',Rsh:'',HI:''};
const result = observationsFromRows({rows,deviceId:'cell',units,usedIds:['observation-1']});
assert.equal(result.length,2);
assert.deepEqual(result.map(item => item.observation_id),['observation-2','observation-3']);
assert.deepEqual(result[0].metrics,[
  {name:'PCE',raw_value:'20.6',value_number:20.6,unit:'%',evidence:[]},
  {name:'Voc',raw_value:'1.12',value_number:1.12,unit:'V',evidence:[]},
]);
assert.equal(result[0].device_id,'cell');
assert.equal(result[0].scan_direction,'reverse');
assert.deepEqual(result[0].measurement_conditions,[]);
assert.equal(result[1].measurement_type,'stabilized_power_output');
assert.equal(result[1].metrics[0].value_number,null);
assert.deepEqual(valuesMissingFromQuote(result,'Reverse: 20.6 %, 1.12 V; stabilized 19.8 \u00b1 0.2 %'),[]);
assert.deepEqual(valuesMissingFromQuote(result,'PCE of 20.6%'),['row 1 Voc "1.12"','row 2 PCE "19.8 \u00b1 0.2"']);
""")


def test_jv_table_collects_rows_for_one_device():
    run_javascript("""
const {document, element} = editorDocument();
const bundle = {ground_truth:{individual_devices:[{device_id:'cell',label:'Champion'}],performance_observations:[]},
  summary:{record_identifiers:{individual_devices:'device_id',performance_observations:'observation_id'}}};
const table = createJvTable({document,element,context:() => ({bundle}),humanLabel:value => value});
table.open();
assert.equal(document.getElementById('jv-dialog').open,true);
assert.equal(document.getElementById('jv-dialog').modal,false);
assert.deepEqual(document.getElementById('jv-device').children.map(option => option.value),['','cell']);
assert.equal(table.changed,false);
const cell = (row,name,value) => { const node = document.getElementById(`jv-cell-${row}-${name}`); node.value = value; node.fire('input'); };
cell(0,'PCE','20.6'); cell(0,'Voc','1.12');
assert.equal(table.changed,true);
const presets = descendants(document.getElementById('jv-presets'));
presets.find(node => node.textContent === '+ Forward scan').fire('click');
cell(0,'PCE','20.6');
cell(1,'PCE','19.9');
let {observations,problems} = table.collect();
assert.match(problems.join(' '),/device/);
assert.match(problems.join(' '),/evidence block/);
document.getElementById('jv-device').value = 'cell';
document.getElementById('jv-citation-block').value = 'main-3';
document.getElementById('jv-citation-quote').value = 'Table 1 Champion reverse 20.6 1.12 forward 19.9';
({observations,problems} = table.collect());
assert.deepEqual(problems,[]);
assert.deepEqual(observations.map(item => [item.scan_direction,item.metrics.map(metric => metric.raw_value)]),
  [['reverse',['20.6','1.12']],['forward',['19.9']]]);
const unit = document.getElementById('jv-unit-Voc'); unit.value = 'mV'; unit.fire('input');
assert.equal(table.collect().observations[0].metrics[1].unit,'mV');
const toggles = descendants(document.getElementById('jv-columns')).filter(node => node.type === 'checkbox');
toggles.find(box => box.value === 'Rs').checked = true;
toggles.find(box => box.value === 'Rs').fire('change');
cell(0,'Rs','4.2');
assert.match(table.collect().problems.join(' '),/Rs "4.2"/);
table.removeSaved(1);
assert.deepEqual(table.collect().observations.map(item => item.scan_direction),['forward']);
""")


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


def test_nullable_context_and_empty_conditions_are_editable_without_raw_json():
    run_javascript(
        EDITOR_SETUP
        + """
initial.subject_context = null;
initial.measurement_conditions = [];
schema.$defs = {
  Quantity:{type:'object',properties:{name:{type:'string'},raw_value:{type:'string'},
    value_number:{anyOf:[{type:'number'},{type:'null'}]},evidence:{type:'array'}}},
  Subject:{type:'object',properties:{treatment_stage:{enum:['before_device_completion','not_reported']},
    conditions:{type:'array',minItems:1,maxItems:2,items:{$ref:'#/$defs/Quantity'}}}}
};
const properties = schema.properties.individual_devices.items.properties;
properties.subject_context = {default:null,anyOf:[{$ref:'#/$defs/Subject'},{type:'null'}]};
properties.measurement_conditions = {type:'array',maxItems:1,items:{$ref:'#/$defs/Quantity'}};
editor.open('individual_devices',0);
const all = () => descendants(document.getElementById('structured-editor'));
const button = text => all().find(node => node.tag === 'button' && node.textContent === text);
button('Add subject_context').fire('click');
assert.equal(editor.value().subject_context.treatment_stage,'not_reported');
assert.equal(editor.value().subject_context.conditions.length,1);
assert.deepEqual(editor.value().subject_context.conditions[0].evidence,[]);
const control = document.getElementById('record-field-subject_context-conditions-0-value_number');
assert.equal(control.type,'number');
control.value='100';control.fire('input');
assert.equal(editor.value().subject_context.conditions[0].value_number,100);
let saved;
const session = createReviewSession({mutate:async (_split,_paper,payload) => {
  saved=payload; return {...bundle,revision:6};
}});
session.select('synthetic',bundle);
await session.saveRecord({edit:editor.edit,value:editor.value(),citation});
assert.deepEqual(saved.value.subject_context.conditions[0].evidence,[citation]);
assert.deepEqual(editor.value().subject_context.conditions[0].evidence,[]);
assert.equal(button('Remove Item 1').disabled,true); // Required condition cannot disappear.
button('Clear subject_context').fire('click');
assert.equal(editor.value().subject_context,null);
button('Add item to measurement_conditions').fire('click');
assert.equal(editor.value().measurement_conditions.length,1);
assert.equal(button('Add item to measurement_conditions').disabled,true);
button('Remove Item 1').fire('click');
assert.deepEqual(editor.value().measurement_conditions,[]);
assert.deepEqual(initial.measurement_conditions,[]); // Draft never mutates saved input.
assert.equal(initial.subject_context,null);
assert.deepEqual(JSON.parse(document.getElementById('record-json').value),editor.value());
"""
    )


def test_review_details_show_context_with_its_owning_record_and_source_paths():
    run_javascript("""
const {element} = editorDocument();
const groups = [];
const ui = {element, humanLabel: value => value || 'Not reported',
  identifiers: {individual_devices:'device_id', device_families:'family_id'},
  contextField: (label,value) => element('span',{text:`${label}: ${value}`}),
  reportedValueGroup: (entry,title,values,path,empty,indexed=true) => {
    groups.push({entry,title,values,path,indexed});
    return element('section',{text:title});
  }};
const treatment = {treatment_stage:'before_device_completion',treated_material:'absorber',
  conditions:[{name:'Temperature',raw_value:'100 C'}]};
const light = {name:'Irradiance',raw_value:'100 mW/cm2'};
const device = {kind:'individual_devices',index:2,item:{subject_context:treatment}};
const nodes = renderRecordDetails(device,ui);
assert.ok(nodes.flatMap(descendants).some(node => node.textContent === 'Treatment stage: before_device_completion'));
assert.equal(groups[0].entry,device);
assert.deepEqual(groups[0].path,['subject_context','conditions']);
assert.deepEqual(groups[0].values,treatment.conditions);
groups.length=0;
const observation = {kind:'performance_observations',index:0,item:{measurement_conditions:[light],metrics:[]}};
renderRecordDetails(observation,ui);
assert.deepEqual(groups[0].path,['measurement_conditions']);
assert.deepEqual(groups[0].values,[light]);
assert.equal(groups[0].entry,observation);
groups.length=0;
const population = {kind:'population_statistics',index:1,item:{subjects:[treatment],error_bar_type:'standard_error',
  measurement_type:'jv_scan',scan_direction:'reverse',measurement_conditions:[light],metrics:[]}};
const text = renderRecordDetails(population,ui).flatMap(descendants).map(node => node.textContent);
assert.ok(text.includes('Error bars: standard_error'));
assert.ok(text.includes('Scan direction: reverse'));
assert.ok(text.includes('Measurement type: jv_scan'));
assert.deepEqual(groups[0].path,['subjects',0,'conditions']);
assert.equal(groups[0].entry,population);
assert.deepEqual(groups[1].path,['measurement_conditions']);
groups.length=0;
const time = {name:'Time',raw_value:'100 h'};
renderRecordDetails({kind:'stability_tests',index:0,item:{conditions:[light],
  checkpoints:[{checkpoint_id:'point',time,conditions:[],outcomes:[]}]}},ui);
assert.deepEqual(groups.map(group => group.path),[
  ['conditions'],['checkpoints',0,'time'],['checkpoints',0,'conditions'],['checkpoints',0,'outcomes']]);
assert.equal(groups[1].indexed,false); // The singleton time has no array index in JSON.
assert.deepEqual(device.item.subject_context,treatment); // Rendering does not mutate a reference.
""")
