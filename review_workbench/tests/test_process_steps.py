"""Processing-step templates, copying steps between families, and material lookup.

The schema tests send the JavaScript output through the real Python model, grounding
validation, NOMAD projection and scorer, so a schema change fails here instead of the
editor drifting from the contract.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

from perla_extract.study_extraction.evaluation import evaluate_study
from perla_extract.study_extraction.models import EvidenceBlock, StudyExtraction
from perla_extract.study_extraction.nomad import to_nomad_with_report
from perla_extract.study_extraction.validation import validate_study_details

APP = Path(__file__).resolve().parents[1] / "review_app"
pytestmark = pytest.mark.usefixtures("node_runtime")


def run_javascript(script):
    imports = "import assert from 'node:assert/strict';\n"
    for module in (
        "process-templates.js",
        "material-suggestions.js",
        "record-editor.js",
    ):
        imports += f"import * as {module.split('.')[0].replace('-', '_')} from {json.dumps((APP / module).as_uri())};\n"
    dom = Path(__file__).with_name("dom-fixture.mjs")
    imports += (
        f"import {{editorDocument, descendants}} from {json.dumps(dom.as_uri())};\n"
    )
    imports += (
        "const T = process_templates, S = material_suggestions;\n"
        "const {createRecordEditor} = record_editor;\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", imports + script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


QUOTE = (
    "SnO2 was spin-coated at 3000 rpm for 30 s and annealed at 150 °C for 30 min. "
    "The perovskite in DMF:DMSO was spin-coated at 1000 rpm for 10 s and 5000 rpm for 30 s "
    "with 150 µL chlorobenzene and annealed at 100 °C for 10 min. "
    "PEAI (2 mg mL-1) was spin-coated at 5000 rpm. "
    "Spiro-OMeTAD was spin-coated at 4000 rpm for 30 s."
)

# Two families: the second has no PEAI layer, so copying skips the PEAI treatment.
FAMILY_SETUP = (
    f"const c = {{block_id:'main-1', quote:{json.dumps(QUOTE)}}};\n"
    + """
const L = (id, material, role) => ({layer_id:id, sequence:null, role, material, constituents:[],
  material_form_raw:null, material_form:'not_reported', reported_properties:[], evidence:[c]});
const family = (id, layers) => ({family_id:id, label:id, variant:null, architecture:null, polarity:'n-i-p',
  full_stack_raw:null, layers, absorbers:[{absorber_id:`${id}-abs`, layer_id:layers[3].layer_id, label:'perovskite',
  formula:null, properties:[], constituents:[], evidence:[c]}], processing_steps:[], evidence:[c]});
const f1 = family('f1', [L('f1-g','glass','substrate'), L('f1-ito','ITO','transparent_electrode'),
  L('f1-sn','SnO2','electron_transport_layer'), L('f1-pvk','perovskite','absorber'),
  L('f1-peai','PEAI','interlayer'), L('f1-sp','Spiro-OMeTAD','hole_transport_layer')]);
const f2 = family('f2', [L('f2-g','glass','substrate'), L('f2-ito','ITO','transparent_electrode'),
  L('f2-sn','SnO2','electron_transport_layer'), L('f2-pvk','perovskite','absorber'),
  L('f2-sp','Spiro-OMeTAD','hole_transport_layer')]);
// What the editor saves: template values, then the save citation on everything still uncited.
const cite = item => { item.evidence = [c]; item.conditions.forEach(condition => { if (!condition.evidence.length) condition.evidence = [c]; }); return item; };
const step = (id, target, category, operation, values, materials = []) => {
  const item = T.newStep(category, operation);
  for (const [name, raw, unit] of values) T.setCondition(item, name, raw, unit);
  return cite({...item, step_id:id, target_layer_ids:[target], materials});
};
const anneal = (id, target, t, d) => step(id, target, 'annealing', 'annealing', [['temperature',t,'°C'],['duration',d,'min']]);
f1.processing_steps = [
  step('step-1', 'f1-sn', 'wet_deposition', 'spin coating', [['spin speed','3000','rpm'],['spin duration','30','s']], ['SnO2']),
  anneal('step-2', 'f1-sn', '150', '30'),
  step('step-3', 'f1-pvk', 'wet_deposition', 'spin coating', [['first spin speed','1000','rpm'],['first spin duration','10','s'],
    ['second spin speed','5000','rpm'],['second spin duration','30','s'],['antisolvent volume','150','µL']], ['DMF','DMSO','chlorobenzene']),
  anneal('step-4', 'f1-pvk', '100', '10'),
  step('step-5', 'f1-peai', 'surface_treatment', 'spin coating', [['concentration','2','mg mL-1'],['spin speed','5000','rpm']], ['PEAI']),
  step('step-6', 'f1-sp', 'wet_deposition', 'spin coating', [['spin speed','4000','rpm'],['spin duration','30','s']], ['Spiro-OMeTAD']),
].map((item, index) => ({...item, sequence:index + 1}));
let next = 6;
const copied = T.copySteps(f1, f2, () => `step-${++next}`);
f2.processing_steps = copied.steps.map((item, index) => ({...item, sequence:index + 1}));
"""
)


def built_families():
    output = run_javascript(FAMILY_SETUP + "console.log(JSON.stringify([f1, f2]));")
    return json.loads(output)


def study(families):
    return StudyExtraction.model_validate(
        {
            "paper": {"title": None, "doi": None},
            "device_families": families,
            "individual_devices": [],
            "performance_observations": [],
            "population_statistics": [],
            "stability_tests": [],
            "unresolved_notes": [],
        }
    )


BLOCKS = [
    EvidenceBlock(block_id="main-1", source="main", page=1, kind="text", text=QUOTE)
]


def test_templates_write_only_reported_values_as_typed():
    run_javascript("""
const step = T.newStep('wet_deposition');
assert.equal(step.operation, 'spin coating');
T.setCondition(step, 'spin speed', ' 4000 ', 'rpm');
T.setCondition(step, 'spin duration', '30 ± 2', 's');
T.setCondition(step, 'atmosphere', '', '');
assert.deepEqual(step.conditions, [
  {name:'spin speed', raw_value:'4000', value_number:4000, unit:'rpm', evidence:[]},
  {name:'spin duration', raw_value:'30 ± 2', value_number:null, unit:'s', evidence:[]}]);
step.conditions[0].evidence = [{block_id:'b', quote:'4000'}];
T.setCondition(step, 'spin speed', '4000', 'r.p.m.');
assert.equal(step.conditions[0].evidence.length, 1, 'an unchanged value keeps its citation');
assert.equal(step.conditions[0].unit, 'r.p.m.');
T.setCondition(step, 'spin speed', '5000', 'rpm');
assert.deepEqual(step.conditions[0].evidence, [], 'a changed value is cited by the new quote');
T.setCondition(step, 'spin speed', '', 'rpm');
assert.deepEqual(step.conditions.map(item => item.name), ['spin duration']);
assert.equal(T.categoryOf('Spin-coating').id, 'wet_deposition');
assert.equal(T.categoryOf('annealed').id, 'annealing');
assert.equal(T.categoryOf('spin-coat PTAA'), null);
assert.deepEqual(T.parametersFor('wet_deposition', true, 'spin coating').map(item => item.name).slice(2, 6),
  ['first spin speed', 'first spin duration', 'second spin speed', 'second spin duration']);
T.setCondition(step, 'spin speed', '4000', 'rpm');
T.setTwoStageSpin(step, true);
assert.deepEqual(step.conditions.map(item => item.name).sort(), ['first spin duration', 'first spin speed']);
assert.equal(T.isTwoStageSpin(step.conditions), true);
const annealing = T.annealingStepFor({...step, target_layer_ids:['x']});
assert.equal(annealing.operation, 'annealing');
assert.deepEqual(annealing.target_layer_ids, ['x']);
const split = T.splitPerTarget({...step, step_id:'s1', target_layer_ids:['a','b','c']}, (() => { let n = 1; return () => `s${++n}`; })());
assert.deepEqual(split.map(item => [item.step_id, item.target_layer_ids]), [['s1',['a']],['s2',['b']],['s3',['c']]]);
assert.equal(T.materialInOperation('spin-coat PTAA', [{material:'PTAA'}]), 'PTAA');
assert.deepEqual(T.conditionsMissingFromQuote([{sequence:3, conditions:[{name:'spin speed', raw_value:'4000', evidence:[]}]}], 'at 3000 rpm'),
  ['step 3 spin speed "4000"']);
assert.match(T.stepProblems({operation:' ', materials:[''], conditions:[{name:'', raw_value:'1'}]}).join(' '), /method.*no name.*empty material/);
""")


def test_editor_output_passes_the_schema_grounding_and_nomad_export():
    families = built_families()
    extraction = study(families)
    validation = validate_study_details(extraction, BLOCKS)
    assert [issue.as_dict() for issue in validation.issues] == []
    annealing = next(
        step
        for step in extraction.device_families[0].processing_steps
        if step.operation == "annealing"
    )
    assert [c.name for c in annealing.conditions] == ["temperature", "duration"]

    export = to_nomad_with_report(extraction, evidence_blocks=BLOCKS).model_dump(
        mode="json"
    )
    projected = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("method") == "annealing":
                projected.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(export)
    assert projected, "annealing steps reach the NOMAD export"
    assert {(step["temperature"], step["duration"]) for step in projected} == {
        (150.0, 1800.0),
        (100.0, 600.0),
    }


def test_a_value_outside_its_quote_is_rejected_by_grounding():
    families = built_families()
    families[0]["processing_steps"][0]["conditions"][0]["raw_value"] = "9999"
    issues = validate_study_details(study(families), BLOCKS).issues
    assert any(
        "raw_value not found in cited evidence" in issue.reason for issue in issues
    )


def test_conventions_score_fully_and_free_text_operations_do_not():
    truth = study(built_families())
    prediction = truth.model_copy(deep=True)
    for family in prediction.device_families:
        for index, step in enumerate(family.processing_steps, start=1):
            step.step_id = (
                f"{family.family_id}-proc-{index:03d}"  # a model's own identifiers
            )
    assert evaluate_study(truth, prediction).core_facts.groups["processing"].f1 == 1

    free_text = prediction.model_copy(deep=True)
    free_text.device_families[0].processing_steps[0].operation = "spin-coat SnO2"
    score = evaluate_study(truth, free_text).core_facts.groups["processing"].f1
    assert score < 1


def test_coating_methods_have_their_own_parameters():
    run_javascript("""
const names = (operation, category = 'wet_deposition') => T.parametersFor(category, false, operation).map(item => item.name);
assert.deepEqual(names('blade coating'), ['concentration', 'volume', 'coating speed', 'blade gap',
  'substrate temperature', 'atmosphere']);
assert.deepEqual(names('slot-die coating'), ['concentration', 'coating speed', 'flow rate', 'coating gap', 'shim thickness',
  'substrate temperature', 'atmosphere']);
assert.ok(!names('spin coating').some(name => name.startsWith('antisolvent')), 'antisolvent fields live in the quenching box');
assert.deepEqual(T.QUENCHING_TYPES.map(type => type.id), ['antisolvent', 'gas', 'vacuum', 'drying']);
assert.deepEqual(T.quenchingTypesUsed([{name:'Gas pressure'}, {name:'spin speed'}, {name:'drying time'}]), ['gas', 'drying']);
assert.deepEqual(names('Spray-coating'), ['concentration', 'nozzle-to-substrate distance', 'flow rate', 'carrier gas pressure',
  'number of passes', 'substrate temperature', 'atmosphere']);
assert.deepEqual(names('inkjet printing'), ['concentration', 'drop spacing', 'printing resolution', 'jetting voltage',
  'jetting frequency', 'nozzle temperature', 'number of printed layers', 'substrate temperature', 'atmosphere']);
assert.deepEqual(names('custom coating'), ['concentration', 'substrate temperature', 'atmosphere']);
assert.ok(!names('blade coating').some(name => name.startsWith('spin')), 'spin parameters only for spin coating');
assert.deepEqual(names('blade coating', 'surface_treatment').slice(-3), ['temperature', 'duration', 'atmosphere']);
assert.equal(T.parametersFor('wet_deposition', true, 'blade coating').some(item => item.name.startsWith('first')), false);
assert.equal(T.categoryOf('slot-die coating').id, 'wet_deposition');
assert.ok(T.STEP_PRESETS.some(preset => preset.operation === 'inkjet printing'));
""")


def test_copied_steps_follow_the_matching_layers_and_skip_missing_ones():
    run_javascript(
        FAMILY_SETUP
        + """
assert.deepEqual(copied.skipped, ['spin coating (PEAI)']);
assert.deepEqual(f2.processing_steps.map(item => [item.step_id, item.sequence, item.target_layer_ids]), [
  ['step-7',1,['f2-sn']], ['step-8',2,['f2-sn']], ['step-9',3,['f2-pvk']], ['step-10',4,['f2-pvk']], ['step-11',5,['f2-sp']]]);
assert.deepEqual(f2.processing_steps[2].conditions, f1.processing_steps[2].conditions, 'values and citations are kept');
f2.processing_steps[0].conditions[0].raw_value = 'changed';
assert.equal(f1.processing_steps[0].conditions[0].raw_value, '3000', 'copies are independent');
// Without the same material, the same function at the same rank is used; ambiguity is not guessed.
const source = [{layer_id:'h1', material:'PTAA', role:'hole_transport_layer'}, {layer_id:'h2', material:'MoO3', role:'hole_transport_layer'}];
const target = [{layer_id:'x1', material:'P3HT', role:'hole_transport_layer'}, {layer_id:'x2', material:'V2O5', role:'hole_transport_layer'}];
assert.equal(T.matchingLayer(source, 'h2', target).layer_id, 'x2');
assert.equal(T.matchingLayer(source, 'h2', target.slice(0, 1)), null);
const twice = [{layer_id:'p1', material:'PEAI', role:'interlayer'}, {layer_id:'p2', material:'PEAI', role:'interlayer'}];
assert.equal(T.matchingLayer(twice, 'p2', [{layer_id:'q1', material:'peai'}, {layer_id:'q2', material:'PEAI'}]).layer_id, 'q2');
"""
    )


def test_material_suggestions_are_separate_per_field_and_keep_spellings():
    run_javascript("""
const values = groups => S.suggestionOptions(groups).map(option => option.value);
const layer = values(S.LAYER_MATERIALS), constituent = values(S.CONSTITUENTS), process = values(S.PROCESS_MATERIALS);
for (const list of [layer, constituent, process]) assert.equal(new Set(list).size, list.length, 'each spelling once');
// Solvents, antisolvents and cleaning agents are only suggested for processing steps.
for (const name of ['DMF', 'DMSO', 'chlorobenzene', 'CB', 'IPA', 'acetone', 'diethyl ether', 'deionized water']) {
  assert.ok(process.includes(name), name);
  assert.ok(!layer.includes(name) && !constituent.includes(name), name);
}
// Constituents carry ions, precursors and dopants; layers carry whole materials.
for (const name of ['FA', 'Pb', 'I', 'PbI2', 'CH3NH3I', 'Li-TFSI', '4-tert-butylpyridine']) assert.ok(constituent.includes(name), name);
for (const name of ['FA', 'Pb', 'Li-TFSI']) assert.ok(!layer.includes(name), name);
for (const name of ['ITO', 'spiro-OMeTAD', 'Spiro-MeOTAD', 'c-TiO2', 'bathocuproine', 'MAPbI3']) assert.ok(layer.includes(name), name);
// Only exact spellings are completed: no labels or alias hints.
const options = S.suggestionOptions(S.PROCESS_MATERIALS);
assert.ok(options.every(option => Object.keys(option).join() === 'value'));
for (const name of ['CB', 'chlorobenzene', 'MAI', 'CH3NH3I', 'methylammonium iodide']) assert.ok(process.includes(name), name);
// Roles and amount names follow what the scorer and NOMAD export read.
assert.deepEqual(values(S.CONSTITUENT_ROLES).slice(0, 2), ['precursor', 'precursor additive']);
assert.ok(values(S.AMOUNT_NAMES).includes('stoichiometric coefficient'));
assert.ok(!values(S.AMOUNT_NAMES).some(name => /^(FA|Cs|I|Br) /.test(name)), 'no element prefix');
const {document, element} = editorDocument();
S.fillMaterialDatalists(document, element);
assert.deepEqual(document.getElementById('process-material-suggestions').children.map(option => option.value), process);
assert.deepEqual(document.getElementById('constituent-suggestions').children.map(option => option.value), constituent);
assert.deepEqual(document.getElementById('constituent-role-suggestions').children.map(option => option.value), values(S.CONSTITUENT_ROLES));
""")


EDITOR = """
const {document, element} = editorDocument();
const c = {block_id:'main-1', quote:'q'};
const text = {type:'string'};
const schema = {properties:{device_families:{items:{properties:{
  family_id:text, label:text, evidence:{type:'array'},
  layers:{type:'array',items:{properties:{layer_id:text,sequence:{type:'integer'},material:text,role:text,evidence:{type:'array'}}}},
  processing_steps:{type:'array',items:{properties:{step_id:text,sequence:{type:'integer'},operation:text,
    target_layer_ids:{type:'array',items:text},materials:{type:'array',items:text},conditions:{type:'array'},evidence:{type:'array'}}}},
}}}}};
const family = {family_id:'control', label:'Control', evidence:[c],
  layers:[{layer_id:'a',sequence:1,material:'SnO2',role:'electron_transport_layer',evidence:[c]},
    {layer_id:'b',sequence:2,material:'FAPbI3',role:'absorber',evidence:[c]}],
  processing_steps:[{step_id:'step-1',sequence:1,operation:'spin coating',target_layer_ids:['a','b'],materials:[],conditions:[],evidence:[c]}]};
const bundle = {revision:1, ground_truth:{device_families:[family]}, summary:{record_identifiers:{device_families:'family_id'}}};
const editor = createRecordEditor({document, element, context:() => ({bundle, studySchema:schema}),
  collections:{device_families:'Device families'}, humanLabel:value => value, singularCollection:() => 'Device family',
  recordKey:() => 'device_families:control', recordReferences:() => [], entityTitle:(_kind, item) => item.label,
  linkedRecordSummary:() => '', reviewReferencedRecord:() => {}, focusCitation:() => {}});
const all = () => descendants(document.getElementById('structured-editor'));
const field = (id, value) => { const node = document.getElementById(id); node.value = value; node.fire('input'); return node; };
const click = label => all().find(node => node.textContent === label).fire('click');
"""


def test_family_editor_shows_steps_as_typed_cards():
    run_javascript(
        EDITOR
        + """
editor.open('device_families', 0);
assert.equal(document.getElementById('record-step-0-category').value, 'wet_deposition');
field('record-step-0-param-spin-speed', '4000');
assert.deepEqual(editor.value().processing_steps[0].conditions,
  [{name:'spin speed', raw_value:'4000', value_number:4000, unit:'rpm', evidence:[]}]);
field('record-step-0-unit-spin-speed', 'r.p.m.');
assert.equal(editor.value().processing_steps[0].conditions[0].unit, 'r.p.m.');
assert.equal(JSON.parse(document.getElementById('record-json').value).processing_steps[0].conditions[0].raw_value, '4000');
assert.equal(family.processing_steps[0].conditions.length, 0, 'the saved record is unchanged until saved');
// Materials keep the paper's wording; the suggestion only adds a hint.
click('+ Material');
field('record-step-0-material-0', 'dmso');
assert.deepEqual(editor.value().processing_steps[0].materials, ['dmso']);
assert.equal(document.getElementById('record-step-0-material-0').attributes.list, 'process-material-suggestions');
assert.equal(all().filter(node => node.attributes?.list === 'layer-material-suggestions').length, 2, 'stack materials use the layer list');
// A layer in the operation is flagged.
field('record-step-0-operation', 'spin-coat SnO2');
assert.match(all().find(node => node.className === 'process-step-warning').textContent, /SnO2/);
field('record-step-0-operation', 'spin coating');
// One step per target layer, then annealing as its own step on the same layer.
click('Split per target layer');
assert.deepEqual(editor.value().processing_steps.map(item => [item.step_id, item.sequence, item.target_layer_ids]),
  [['step-1',1,['a']],['step-2',2,['b']]]);
assert.deepEqual(editor.value().processing_steps[1].conditions[0].raw_value, '4000');
all().filter(node => node.textContent === '+ Annealing after this step')[0].fire('click');
assert.deepEqual(editor.value().processing_steps.map(item => [item.step_id, item.operation, item.target_layer_ids]),
  [['step-1','spin coating',['a']],['step-3','annealing',['a']],['step-2','spin coating',['b']]]);
field('record-step-1-param-temperature', '100');
assert.deepEqual(editor.value().processing_steps[1].conditions[0], {name:'temperature', raw_value:'100', value_number:100, unit:'°C', evidence:[]});
click('+ Blade coating');
assert.equal(editor.value().processing_steps[3].operation, 'blade coating');
assert.ok(document.getElementById('record-step-3-param-blade-gap'));
assert.ok(!all().some(node => node.id === 'record-step-3-param-spin-speed'), 'no spin parameters for blade coating');
field('record-step-3-param-blade-gap', '150');
// Switching the method changes the parameter list; values already entered stay in the record.
field('record-step-3-operation', 'slot-die coating').fire('change');
assert.ok(all().some(node => node.id === 'record-step-3-param-shim-thickness'));
assert.deepEqual(editor.value().processing_steps[3].conditions.map(item => item.name), ['blade gap']);
click('+ Evaporation');

assert.equal(editor.value().processing_steps[4].operation, 'thermal evaporation');
assert.equal(editor.value().processing_steps[4].sequence, 5);
// A two-stage spin program renames the stage parameters.
click('+ Second spin stage');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['first spin speed']);
assert.ok(document.getElementById('record-step-0-param-second-spin-speed'));
"""
    )


def test_constituent_fields_get_their_own_suggestions():
    run_javascript(
        EDITOR
        + """
const amount = {anyOf:[{type:'object', properties:{name:text, raw_value:text, unit:{anyOf:[text,{type:'null'}]}}}, {type:'null'}]};
schema.properties.device_families.items.properties.layers.items.properties.constituents = {type:'array', items:{properties:{
  name:text, role:{anyOf:[text,{type:'null'}]}, amount, evidence:{type:'array'}}}};
family.layers[1].constituents = [{name:'MAI', role:null, amount:{name:'stoichiometric coefficient', raw_value:'0.5', unit:null}, evidence:[c]}];
editor.open('device_families', 0);
const list = id => document.getElementById(id).attributes.list;
assert.equal(list('record-field-layers-1-constituents-0-name'), 'constituent-suggestions');
assert.equal(list('record-field-layers-1-constituents-0-role'), 'constituent-role-suggestions');
assert.equal(list('record-field-layers-1-constituents-0-amount-name'), 'amount-name-suggestions');
assert.equal(list('record-field-layers-1-constituents-0-amount-raw_value'), undefined);
"""
    )


def test_drying_and_quenching_box_writes_conditions_of_the_deposition_step():
    run_javascript(
        EDITOR
        + """
family.processing_steps[0].conditions = [{name:'antisolvent volume', raw_value:'150', value_number:150, unit:'µL', evidence:[c]}];
editor.open('device_families', 0);
const box = () => all().find(node => node.className === 'process-quenching');
assert.equal(box().open, true, 'a step with quenching values shows the box open');
assert.ok(document.getElementById('record-step-0-param-antisolvent-volume'));
assert.ok(!all().some(node => node.textContent === 'Other parameters (1)'), 'not duplicated under other parameters');
click('+ Gas quenching');
assert.ok(all().some(node => node.id === 'record-step-0-param-gas-pressure'), 'an added type stays open before it has values');
field('record-step-0-param-gas-pressure', '20');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => [item.name, item.raw_value, item.unit]),
  [['antisolvent volume','150','µL'], ['gas pressure','20','psi']]);
all().find(node => node.attributes?.['aria-label'] === 'Remove Antisolvent').fire('click');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['gas pressure']);
click('+ Annealing after this step');
assert.ok(!all().some(node => node.id === 'record-step-1-param-gas-pressure'));
assert.equal(all().filter(node => node.className === 'process-quenching').length, 1, 'no box for annealing');
"""
    )


def test_family_editor_copies_steps_from_another_family():
    run_javascript(
        EDITOR
        + """
bundle.ground_truth.device_families.push({family_id:'variant', label:'Variant', evidence:[c],
  layers:[{layer_id:'v1',sequence:1,material:'SnO2',role:'electron_transport_layer',evidence:[c]}],
  processing_steps:[]});
editor.open('device_families', 1);
const source = document.getElementById('record-copy-steps-source');
assert.deepEqual(source.children.map(option => option.value), ['control']);
click('Copy steps');
assert.deepEqual(editor.value().processing_steps, []);
assert.match(document.getElementById('dialog-status').textContent, /Copied 0 steps.*FAPbI3/);
bundle.ground_truth.device_families[0].processing_steps[0].target_layer_ids = ['a'];
editor.open('device_families', 1);
click('Copy steps');
assert.deepEqual(editor.value().processing_steps.map(item => [item.step_id, item.sequence, item.target_layer_ids, item.operation]),
  [['step-2', 1, ['v1'], 'spin coating']]);
assert.equal(bundle.ground_truth.device_families[1].processing_steps.length, 0, 'saved only on save');
"""
    )


def test_every_imported_workbench_module_is_served():
    allowlist = (APP.parent / "server.py").read_text(encoding="utf-8")
    pending, seen = ["app.js"], set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        source = (APP / module).read_text(encoding="utf-8")
        pending += re.findall(r'from "\./([\w-]+\.js)"', source)
    for module in seen:
        assert f'"{module}"' in allowlist, module
