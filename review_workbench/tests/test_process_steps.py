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
        "process-solution.js",
        "material-suggestions.js",
        "jv-table.js",
        "record-editor.js",
    ):
        imports += f"import * as {module.split('.')[0].replace('-', '_')} from {json.dumps((APP / module).as_uri())};\n"
    dom = Path(__file__).with_name("dom-fixture.mjs")
    imports += (
        f"import {{editorDocument, descendants}} from {json.dumps(dom.as_uri())};\n"
    )
    imports += (
        "const T = process_templates, S = material_suggestions, P = process_solution, J = jv_table;\n"
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
    "The perovskite (PbI2:FAI 1.1:1) in DMF:DMSO (4:1, v/v) was spin-coated at 1000 rpm for 10 s and 5000 rpm for 30 s "
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
const anneal = (id, target, t, d) => step(id, target, 'annealing', 'annealing', [['annealing temperature',t,'°C'],['annealing duration',d,'min']]);
f1.processing_steps = [
  step('step-1', 'f1-sn', 'wet_deposition', 'spin coating', [['spin speed','3000','rpm'],['spin duration','30','s']], ['SnO2']),
  anneal('step-2', 'f1-sn', '150', '30'),
  step('step-3', 'f1-pvk', 'wet_deposition', 'spin coating', [['first spin speed','1000','rpm'],['first spin duration','10','s'],
    ['second spin speed','5000','rpm'],['second spin duration','30','s'],['antisolvent volume','150','µL']], ['DMF','DMSO','chlorobenzene']),
  anneal('step-4', 'f1-pvk', '100', '10'),
  step('step-5', 'f1-peai', 'wet_deposition', 'spin coating', [['concentration','2','mg mL-1'],['spin speed','5000','rpm']], ['PEAI']),
  step('step-6', 'f1-sp', 'wet_deposition', 'spin coating', [['spin speed','4000','rpm'],['spin duration','30','s']], ['Spiro-OMeTAD']),
].map((item, index) => ({...item, sequence:index + 1}));
P.applySolution(f1.processing_steps[2], {solvents:{names:['DMF','DMSO'], kind:'volume', ratio:'4:1'}, solutes:{rows:[{name:'PbI2', concentration:'', unit:'M'}, {name:'FAI', concentration:'', unit:'M'}], ratio:'1.1:1'}});
cite(f1.processing_steps[2]);
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
assert.equal(step.conditions[0].unit, 'rpm', 'r.p.m. is saved in a spelling the scorer understands');
T.setCondition(step, 'spin speed', '5000', 'rpm');
assert.deepEqual(step.conditions[0].evidence, [], 'a changed value is cited by the new quote');
T.setCondition(step, 'spin speed', '', 'rpm');
assert.deepEqual(step.conditions.map(item => item.name), ['spin duration']);
assert.equal(T.categoryOf('Spin-coating').id, 'wet_deposition');
assert.equal(T.categoryOf('annealed').id, 'annealing');
// The paper's wording is allowed; the method is recognized from it.
assert.equal(T.categoryOf('spin-coat PTAA').id, 'wet_deposition');
assert.equal(T.methodOf('spin-casting PbI2 solution and annealing'), 'spin coating');
assert.equal(T.methodOf('Successively thermally evaporate C60, BCP and Ag'), 'thermal evaporation');
assert.equal(T.methodOf('heat perovskite film'), 'annealing');
assert.equal(T.methodOf('deposited'), null);
assert.deepEqual(T.parametersFor('wet_deposition', true, 'spin coating').map(item => item.name).slice(1, 5),
  ['first spin speed', 'first spin duration', 'second spin speed', 'second spin duration']);
T.setCondition(step, 'spin speed', '4000', 'rpm');
T.setTwoStageSpin(step, true);
assert.deepEqual(step.conditions.map(item => item.name).sort(), ['first spin duration', 'first spin speed']);
assert.equal(T.isTwoStageSpin(step.conditions), true);
assert.deepEqual(T.annealingParameters(false).map(item => item.name), ['annealing temperature', 'annealing duration', 'annealing atmosphere']);
assert.deepEqual(T.annealingParameters(true).map(item => item.name).slice(0, 4),
  ['first annealing temperature', 'first annealing duration', 'second annealing temperature', 'second annealing duration']);
const staged = {conditions:[{name:'annealing temperature'}, {name:'annealing duration'}]};
T.setTwoStageAnnealing(staged, true);
assert.deepEqual(staged.conditions.map(item => item.name), ['first annealing temperature', 'first annealing duration']);
assert.equal(T.isTwoStageAnnealing(staged.conditions), true);
// Antisolvent values carry the antisolvent's name, as in most references.
const quench = {materials:['chlorobenzene'], conditions:[{name:'chlorobenzene volume'}, {name:'chlorobenzene drip time'}]};
assert.equal(T.antisolventOf(quench), 'chlorobenzene');
assert.deepEqual(T.quenchingParameters(T.QUENCHING_TYPES[0], quench).map(item => item.name).slice(0, 2), ['chlorobenzene volume', 'chlorobenzene drip time']);
T.renameAntisolvent(quench, 'toluene');
assert.deepEqual(quench.conditions.map(item => item.name), ['toluene volume', 'toluene drip time']);
assert.equal(T.antisolventOf({conditions:[{name:'antisolvent volume'}]}), 'antisolvent');
assert.equal(T.antisolventOf({conditions:[{name:'spin volume'}]}), 'antisolvent', 'only antisolvent or solvent names count');
const split = T.splitPerTarget({...step, step_id:'s1', target_layer_ids:['a','b','c']}, (() => { let n = 1; return () => `s${++n}`; })());
assert.deepEqual(split.map(item => [item.step_id, item.target_layer_ids]), [['s1',['a']],['s2',['b']],['s3',['c']]]);
// Evidence per step: a step is finished only when the server found each new value in its quote.
const own = {step_id:'s9', operation:'spin coating', evidence:[], conditions:[
  {name:'spin speed', raw_value:'3000', unit:'rpm', evidence:[]}, {name:'spin duration', raw_value:'30', unit:'s', evidence:[]},
  {name:'treatment duration', raw_value:'15', unit:'min', evidence:[{block_id:'b0', quote:'for 15 min'}]}]};
const checks = new Map();
assert.equal(T.stepStatus(own, checks).state, 'pending');
assert.ok(T.stepStatus(own, checks).blocking);
const sentence = {block_id:'b1', quote:'spin-coated at 3,000 rpm for 30 s'};
const request = T.stepCheckRequest(own, checks, sentence);
assert.deepEqual(request.values.map(item => item.evidence[0].block_id), ['b1', 'b1', 'b0', 'b1'], 'uncited values are tried with the sentence');
assert.equal(request.values.at(-1).quote_only, true);
const answer = [{ok:false, reason:'"3000" is not in the quote.', hint:'The paper prints it as "3,000"'}, {ok:true}, {ok:true}, {ok:true}];
const outcome = T.applyStepCheck(own, request, answer, checks, sentence);
assert.deepEqual(outcome.cited, ['spin duration']);
assert.deepEqual(own.conditions.map(item => item.evidence[0]?.block_id ?? null), [null, 'b1', 'b0'], 'a value not found stays uncited');
let status = T.stepStatus(own, checks);
assert.equal(status.state, 'error');
assert.match(status.problems[0].label + status.problems[0].hint, /spin speed "3000".*3,000/);
assert.equal(own.evidence[0].block_id, 'b1', 'the step is cited with its sentence');
// Correcting the value makes the old result stale; confirming again finishes the step.
own.conditions[0].raw_value = '3,000';
assert.equal(T.stepStatus(own, checks).state, 'pending');
const again = T.stepCheckRequest(own, checks, sentence);
T.applyStepCheck(own, again, again.values.map(() => ({ok:true})), checks, sentence);
assert.equal(T.stepStatus(own, checks).state, 'done');
assert.equal(T.stepStatus(own, checks, true).state, 'pending', 'an edit since confirming needs a new confirmation');
// A number the paper prints with its unit attached is saved in that spelling, keeping the number.
const glued = {step_id:'s12', operation:'spin coating', evidence:[], conditions:[{name:'concentration', raw_value:'1.45', value_number:1.45, unit:'M', evidence:[]}]};
const gluedRequest = T.stepCheckRequest(glued, checks, sentence);
const gluedOutcome = T.applyStepCheck(glued, gluedRequest, [{ok:true, raw_value:'1.45M'}, {ok:true}], checks, sentence);
assert.deepEqual([glued.conditions[0].raw_value, glued.conditions[0].value_number, glued.conditions[0].unit], ['1.45M', 1.45, 'M']);
assert.deepEqual(gluedOutcome.respelled, ['concentration "1.45M"']);
assert.equal(T.stepStatus(glued, checks).state, 'done');
// A wrong quote changes nothing.
const fresh = {step_id:'s10', operation:'annealing', evidence:[], conditions:[{name:'annealing temperature', raw_value:'100', evidence:[]}]};
const wrong = T.stepCheckRequest(fresh, checks, {block_id:'b1', quote:'typo'});
assert.ok(T.applyStepCheck(fresh, wrong, [{ok:true}, {ok:false, reason:'The quote is not in its evidence block.'}], checks, {block_id:'b1', quote:'typo'}).quoteProblem);
assert.deepEqual([fresh.evidence, fresh.conditions[0].evidence], [[], []]);
// Problems found when a record is opened are shown but do not block.
const old = {step_id:'s11', operation:'annealing', evidence:[{block_id:'b2', quote:'q'}], conditions:[{name:'annealing temperature', raw_value:'99', evidence:[{block_id:'b2', quote:'q'}]}]};
T.recordInheritedChecks([old], [{ok:false, reason:'not found'}], checks);
assert.deepEqual([T.stepStatus(old, checks).state, T.stepStatus(old, checks).blocking], ['warning', false]);
assert.equal(T.citedValuesOf([own, fresh, old]).length, 4);
// Save errors name the step and value instead of JSON indices.
const family = {processing_steps:[{operation:'cleaning'}, {operation:'spin coating', conditions:[{name:'spin speed', raw_value:'3000', evidence:[{quote:'at 3,000 rpm'}]}]}]};
assert.equal(T.readableGroundingError('problem: $.device_families[0].processing_steps[1].conditions[0]: raw_value not found in cited evidence', family),
  'problem: step 2 (spin coating): spin speed "3000" is not in its quote "at 3,000 rpm"');
assert.match(T.stepProblems({operation:' ', materials:[''], conditions:[{name:'', raw_value:'1'}]}).join(' '), /method.*no name.*empty material/);
""")


def test_numbers_and_units_are_saved_so_that_scoring_understands_them():
    run_javascript("""
assert.deepEqual(['4,000', '12,500', '1,234.5', '1,12', '0,800', '4000', '−0.8', '30 ± 2'].map(J.reportedNumber),
  [4000, 12500, 1234.5, 1.12, 0.8, 4000, -0.8, null]);
assert.deepEqual(['r.p.m.', 'RPM', 'rev/min', 'oC', 'ºC', 'Torr', 'mTorr', 'mg mL-1', 'wt%', ''].map(J.standardUnit),
  ['rpm', 'rpm', 'rpm', '°C', '°C', 'torr', 'mtorr', 'mg mL-1', 'wt%', '']);
const step = {conditions:[]};
T.setCondition(step, 'spin speed', '4,000', 'r.p.m.');
assert.deepEqual([step.conditions[0].raw_value, step.conditions[0].value_number, step.conditions[0].unit], ['4,000', 4000, 'rpm']);
""")


def test_every_default_and_standardized_unit_is_understood_by_the_scorer():
    from perla_extract.study_extraction.units import canonical_quantity

    output = run_javascript("""
const units = new Set(['M']);
const add = items => items.forEach(item => units.add(item.unit));
T.PROCESS_CATEGORIES.forEach(category => add([...category.parameters, ...(category.after || [])]));
Object.values(T.METHOD_PARAMETERS).forEach(add);
T.QUENCHING_TYPES.forEach(type => add(type.parameters));
add(T.annealingParameters(true));
J.JV_METRICS.forEach(metric => units.add(metric.unit));
['r.p.m.', 'RPM', 'rev/min', 'oC', 'ºC', 'Torr', 'mTorr'].forEach(unit => units.add(J.standardUnit(unit)));
console.log(JSON.stringify([...units].filter(Boolean)));
""")
    unknown = [
        unit for unit in json.loads(output) if canonical_quantity(1.0, unit) is None
    ]
    assert unknown == []


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
    # Named as most references and models name them; see the NOMAD note below.
    assert [c.name for c in annealing.conditions] == [
        "annealing temperature",
        "annealing duration",
    ]

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
    # Trade-off: the NOMAD projection fills temperature/duration only from conditions named
    # exactly so; annealing names stay readable in additional_parameters.
    for step in projected:
        names = [c["name"] for c in step["additional_parameters"]["conditions"]]
        assert names == ["annealing temperature", "annealing duration"]


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
assert.deepEqual(names('blade coating'), ['volume', 'coating speed', 'blade gap',
  'substrate temperature', 'atmosphere']);
assert.deepEqual(names('slot-die coating'), ['coating speed', 'flow rate', 'coating gap', 'shim thickness',
  'substrate temperature', 'atmosphere']);
assert.ok(!names('spin coating').some(name => name.startsWith('antisolvent')), 'antisolvent fields live in the quenching box');
assert.deepEqual(T.QUENCHING_TYPES.map(type => type.id), ['antisolvent', 'gas', 'vacuum', 'drying']);
assert.deepEqual(T.quenchingTypesUsed({conditions:[{name:'Gas pressure'}, {name:'spin speed'}, {name:'drying time'}]}), ['gas', 'drying']);
assert.deepEqual(T.quenchingTypesUsed({conditions:[{name:'diethyl ether volume'}]}), ['antisolvent']);
assert.deepEqual(names('Spray-coating'), ['nozzle-to-substrate distance', 'flow rate', 'carrier gas pressure',
  'number of passes', 'substrate temperature', 'atmosphere']);
assert.deepEqual(names('inkjet printing'), ['drop spacing', 'printing resolution', 'jetting voltage',
  'jetting frequency', 'nozzle temperature', 'number of printed layers', 'substrate temperature', 'atmosphere']);
assert.deepEqual(names('custom coating'), ['substrate temperature', 'atmosphere']);
assert.deepEqual(names('spin-coat HTL'), ['volume', 'spin speed', 'spin duration', 'substrate temperature', 'atmosphere']);
assert.ok(!names('blade coating').some(name => name.startsWith('spin')), 'spin parameters only for spin coating');
assert.deepEqual(names('O2 plasma treatment', 'surface_treatment'), ['plasma power', 'pressure', 'treatment duration']);
assert.deepEqual(names('UV-ozone treatment', 'surface_treatment'), ['treatment duration']);
for (const wording of ['UV-O3 treatment', 'ultraviolet-ozone (UVO) treatment', 'clean and UV-treat FTO glasses', 'substrate cleaning and UV-Ozone treatment'])
  assert.equal(T.categoryOf(wording).id, 'surface_treatment', wording);
assert.equal(T.categoryOf('treated with plasma').id, 'surface_treatment');
assert.equal(T.categoryOf('ultrasonic cleaning').id, 'substrate_preparation');
assert.equal(T.categoryOf('dipping').id, 'wet_deposition');
assert.equal(T.STEP_PRESETS.find(preset => preset.category === 'surface_treatment').operation, 'plasma treatment');
assert.ok(!T.categoryById('surface_treatment').methods.some(method => /coat|print|cast/.test(method)), 'no coating methods');
assert.equal(T.parametersFor('wet_deposition', true, 'blade coating').some(item => item.name.startsWith('first')), false);
assert.equal(T.categoryOf('slot-die coating').id, 'wet_deposition');
assert.ok(T.STEP_PRESETS.some(preset => preset.operation === 'inkjet printing'));
""")


def test_solution_ratios_are_saved_as_printed_and_read_back():
    run_javascript("""
const step = {materials:['chlorobenzene'], conditions:[]};
const row = (name, concentration = '') => ({name, concentration, unit:'M'});
const solution = {solvents:{names:['DMF','DMSO'], kind:'volume', ratio:'4:1 (v/v)'},
  solutes:{rows:[row('PbI2', '1.5'), row('FAI'), row('MABr')], ratio:'1.1:1:0.2'}};
P.applySolution(step, solution, P.emptySolution());
assert.deepEqual(step.materials, ['chlorobenzene','DMF','DMSO','PbI2','FAI','MABr']);
assert.deepEqual(step.conditions.map(item => [item.name, item.raw_value, item.unit]),
  [['DMF:DMSO volume ratio','4:1 (v/v)',null], ['PbI2:FAI:MABr molar ratio','1.1:1:0.2',null], ['PbI2 concentration','1.5','M']]);
const back = P.solutionOf(step);
assert.deepEqual([back.solvents.names, back.solvents.kind, back.solvents.ratio], [['DMF','DMSO'],'volume','4:1 (v/v)']);
assert.deepEqual([back.solutes.rows.map(item => [item.name, item.concentration]), back.solutes.ratio],
  [[['PbI2','1.5'],['FAI',''],['MABr','']],'1.1:1:0.2']);
// A single solute with a concentration and no ratio, e.g. "PTAA (1.5 mg/mL)".
const single = {materials:['PTAA','toluene'], conditions:[{name:'PTAA concentration', raw_value:'1.5', unit:'mg/mL'}]};
assert.deepEqual(P.solutionOf(single).solutes.rows, [{name:'PTAA', concentration:'1.5', unit:'mg/mL'}]);
// Changing components renames the ratio; an unchanged ratio keeps its citation.
step.conditions[1].evidence = [{block_id:'b', quote:'q'}];
const previous = structuredClone(back);
back.solvents.names = ['DMF','NMP'];
P.applySolution(step, back, previous);
assert.deepEqual(step.conditions.map(item => item.name), ['DMF:NMP volume ratio','PbI2:FAI:MABr molar ratio','PbI2 concentration']);
assert.equal(step.conditions[1].evidence.length, 1);
assert.equal(step.conditions[0].evidence.length, 0);
assert.equal(P.ratioParts('v / v , 9/1'), 2);
assert.equal(P.parseRatio({name:'spin speed', raw_value:'4000'}), null);
P.renameMaterial(step, 'NMP', 'GBL');
assert.ok(step.materials.includes('GBL') && !step.materials.includes('NMP'));
""")


def test_drying_is_also_a_step_of_its_own():
    run_javascript(
        EDITOR
        + """
assert.equal(T.methodOf('dried at 70 °C for 10 min'), 'drying');
assert.equal(T.methodOf('dried in a vacuum chamber'), 'vacuum drying');
assert.equal(T.methodOf('blow-dried with nitrogen'), 'gas flow drying');
assert.equal(T.methodOf('spin-coated and dried'), 'spin coating', 'drying during a deposition stays with it');
assert.equal(T.methodOf('dry deposition'), null);
const names = operation => T.parametersFor('drying', false, operation).map(item => item.name);
assert.deepEqual(names('drying'), ['drying temperature', 'drying time', 'atmosphere']);
assert.deepEqual(names('vacuum drying'), ['vacuum pressure', 'drying temperature', 'drying time', 'atmosphere']);
editor.open('device_families', 0);
click('+ Drying');
const step = editor.value().processing_steps[1];
assert.equal(step.operation, 'drying');
assert.equal(document.getElementById('record-step-1-category').value, 'drying');
field('record-step-1-param-drying-temperature', '70');
assert.deepEqual(editor.value().processing_steps[1].conditions, [{name:'drying temperature', raw_value:'70', value_number:70, unit:'°C', evidence:[]}]);
assert.ok(!all().some(node => node.id === 'record-step-1-param-annealing-temperature'), 'no annealing section in a drying step');
assert.equal(all().filter(node => node.className === 'process-quenching').length, 1, 'only the deposition step has the quenching box');
"""
    )


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
  linkedRecordSummary:() => '', reviewReferencedRecord:() => {}, focusCitation:() => {},
  checkGrounding: async values => { checked.push(values); return values.map(groundingAnswer); }});
// The grounding check is the server's; here every value is found unless a test says otherwise.
const checked = [];
let groundingAnswer = () => ({ok:true, reason:'', hint:''});
const all = () => descendants(document.getElementById('structured-editor'));
const field = (id, value) => { const node = document.getElementById(id); node.value = value; node.fire('input'); return node; };
const click = label => all().find(node => node.textContent === label).fire('click');
const settle = () => new Promise(resolve => setTimeout(resolve, 0));
const status = () => document.getElementById('dialog-status').textContent;
const badges = () => all().filter(node => node.className === 'process-step-status').map(node => node.dataset.state);
/** Select a sentence and confirm step `index` with it. */
const confirmStep = async (index, quote = 'q') => {
  document.getElementById('citation-block').value = 'main-1';
  document.getElementById('citation-quote').value = quote;
  all().filter(node => node.textContent === 'Use selected evidence & save step')[index].fire('click');
  await settle();
};
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
assert.equal(editor.value().processing_steps[0].conditions[0].unit, 'rpm');
assert.equal(JSON.parse(document.getElementById('record-json').value).processing_steps[0].conditions[0].raw_value, '4000');
assert.equal(family.processing_steps[0].conditions.length, 0, 'the saved record is unchanged until saved');
// Materials keep the paper's wording; the suggestion only adds a hint.
click('+ Material');
field('record-step-0-material-0', 'dmso');
assert.deepEqual(editor.value().processing_steps[0].materials, ['dmso']);
assert.equal(document.getElementById('record-step-0-material-0').attributes.list, 'process-material-suggestions');
assert.equal(all().filter(node => node.attributes?.list === 'layer-material-suggestions').length, 2, 'stack materials use the layer list');
// The paper's wording keeps the spin-coating fields.
field('record-step-0-operation', 'spin-coat SnO2').fire('change');
assert.ok(all().some(node => node.id === 'record-step-0-param-spin-speed'));
assert.equal(editor.value().processing_steps[0].operation, 'spin-coat SnO2');
// Annealing inside the step, named as in most papers.
field('record-step-0-param-annealing-temperature', '100');
assert.deepEqual(editor.value().processing_steps[0].conditions[1], {name:'annealing temperature', raw_value:'100', value_number:100, unit:'°C', evidence:[]});
click('+ Second annealing stage');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['spin speed', 'first annealing temperature']);
click('Single annealing stage');
// One step per target layer.
click('Split per target layer');
assert.deepEqual(editor.value().processing_steps.map(item => [item.step_id, item.sequence, item.target_layer_ids]),
  [['step-1',1,['a']],['step-2',2,['b']]]);
assert.deepEqual(editor.value().processing_steps[1].conditions[0].raw_value, '4000');
assert.ok(!all().some(node => node.textContent === '+ Annealing after this step'));
// A new step waits until the edited steps are confirmed with their evidence.
assert.deepEqual(badges(), ['pending', 'pending']);
click('+ Annealing');
assert.equal(editor.value().processing_steps.length, 2);
assert.match(status(), /Finish this step first\. Step 1 \(spin-coat SnO2\): spin speed "4000" has no evidence yet.*Use selected evidence & save step/);
await confirmStep(0, 'spin-coated at 4000 rpm');
assert.deepEqual(editor.value().processing_steps[0].conditions[0].evidence, [{block_id:'main-1', quote:'spin-coated at 4000 rpm'}]);
assert.equal(checked.at(-1).at(-1).quote_only, true);
assert.match(status(), /Step 1 \(spin-coat SnO2\) saved with "spin-coated at 4000 rpm": cited spin speed, annealing temperature/);
await confirmStep(1);
assert.deepEqual(badges(), ['done', 'done']);
assert.deepEqual(editor.unfinishedSteps(), []);
click('+ Annealing');
assert.equal(editor.value().processing_steps[2].operation, 'annealing');
field('record-step-2-param-annealing-temperature', '100');
assert.deepEqual(editor.value().processing_steps[2].conditions[0], {name:'annealing temperature', raw_value:'100', value_number:100, unit:'°C', evidence:[]});
// A value not found stays uncited, and the step says which value and why.
groundingAnswer = value => value.raw_value === '100'
  ? {ok:false, reason:'"100" is not in the quote.', hint:'Numbers in this quote: 10.'} : {ok:true, reason:'', hint:''};
await confirmStep(2, 'annealed for 10 min');
assert.deepEqual(editor.value().processing_steps[2].conditions[0].evidence, []);
assert.equal(badges()[2], 'error');
assert.match(status(), /Not found in "annealed for 10 min": annealing temperature "100": Numbers in this quote: 10\./);
assert.ok(all().some(node => node.textContent === 'annealing temperature "100": '), 'the problem is listed in the step');
assert.match(editor.unfinishedSteps()[0], /Step 3 \(annealing\): annealing temperature "100": "100" is not in the quote\. Numbers in this quote: 10\./);
click('+ Blade coating');
assert.equal(editor.value().processing_steps.length, 3, 'blocked while a value is not in its quote');
groundingAnswer = () => ({ok:true, reason:'', hint:''});
await confirmStep(2, 'annealed at 100 °C');
assert.equal(badges()[2], 'done');
click('+ Blade coating');
assert.equal(editor.value().processing_steps[3].operation, 'blade coating');
assert.ok(document.getElementById('record-step-3-param-blade-gap'));
assert.ok(!all().some(node => node.id === 'record-step-3-param-spin-speed'), 'no spin parameters for blade coating');
field('record-step-3-param-blade-gap', '150');
assert.equal(badges()[3], 'pending', 'a typed value changes the state without a re-render');
// Switching the method changes the parameter list; values already entered stay in the record.
field('record-step-3-operation', 'slot-die coating').fire('change');
assert.ok(all().some(node => node.id === 'record-step-3-param-shim-thickness'));
assert.deepEqual(editor.value().processing_steps[3].conditions.map(item => item.name), ['blade gap']);
await confirmStep(3);
click('+ Evaporation');

assert.equal(editor.value().processing_steps[4].operation, 'thermal evaporation');
assert.equal(editor.value().processing_steps[4].sequence, 5);
// A two-stage spin program renames the stage parameters.
click('+ Second spin stage');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['first spin speed', 'annealing temperature']);
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
family.processing_steps[0].materials = ['chlorobenzene'];
family.processing_steps[0].conditions = [{name:'chlorobenzene volume', raw_value:'150', value_number:150, unit:'µL', evidence:[c]}];
editor.open('device_families', 0);
const box = () => all().find(node => node.className === 'process-quenching');
assert.equal(box().open, true, 'a step with quenching values shows the box open');
assert.ok(all().some(node => node.id === 'record-step-0-param-chlorobenzene-volume'));
assert.equal(document.getElementById('record-step-0-antisolvent').value, 'chlorobenzene');
assert.ok(!all().some(node => node.textContent === 'Other parameters (1)'), 'not duplicated under other parameters');
click('+ Gas quenching');
assert.ok(all().some(node => node.id === 'record-step-0-param-gas-pressure'), 'an added type stays open before it has values');
field('record-step-0-param-gas-pressure', '20');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => [item.name, item.raw_value, item.unit]),
  [['chlorobenzene volume','150','µL'], ['gas pressure','20','psi']]);
// Renaming the antisolvent renames its values and the material.
field('record-step-0-antisolvent', 'toluene').fire('change');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['toluene volume', 'gas pressure']);
assert.deepEqual(editor.value().processing_steps[0].materials, ['toluene']);
all().find(node => node.attributes?.['aria-label'] === 'Remove Antisolvent').fire('click');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['gas pressure']);
click('+ Annealing');
assert.ok(!all().some(node => node.id === 'record-step-1-param-gas-pressure'));
assert.equal(all().filter(node => node.className === 'process-quenching').length, 1, 'no box for annealing');
"""
    )


def test_solution_box_writes_materials_and_ratios_of_the_step():
    run_javascript(
        EDITOR
        + """
editor.open('device_families', 0);
const add = label => all().find(node => node.textContent === label).fire('click');
add('+ Solvent');
field('record-step-0-solvents-0', 'DMF').fire('change');
add('+ Solvent');
field('record-step-0-solvents-1', 'DMSO').fire('change');
assert.equal(document.getElementById('record-step-0-solvents-0').attributes.list, 'solvent-suggestions');
field('record-step-0-solvents-ratio', '4:1');
add('+ Solute');
field('record-step-0-solutes-0', 'PbI2').fire('change');
assert.equal(document.getElementById('record-step-0-solutes-0').attributes.list, 'solute-suggestions');
field('record-step-0-solutes-0-concentration', '1.5');
let step = editor.value().processing_steps[0];
assert.deepEqual(step.materials, ['DMF','DMSO','PbI2']);
assert.deepEqual(step.conditions.map(item => [item.name, item.raw_value, item.unit]),
  [['DMF:DMSO volume ratio','4:1',null], ['PbI2 concentration','1.5','M']]);
assert.ok(!all().some(node => node.textContent === 'Other parameters (1)'), 'the ratio is not repeated under other parameters');
field('record-step-0-solvents-ratio', '4:1:2');
assert.match(all().find(node => node.textContent?.startsWith('The ratio has')).textContent, /3 numbers for 2 components/);
document.getElementById('record-step-0-solvents-kind').value = 'molar';
document.getElementById('record-step-0-solvents-kind').fire('change');
assert.deepEqual(editor.value().processing_steps[0].conditions.map(item => item.name), ['DMF:DMSO molar ratio', 'PbI2 concentration']);
all().find(node => node.attributes?.['aria-label'] === 'Remove solvent 2').fire('click');
step = editor.value().processing_steps[0];
assert.deepEqual(step.materials, ['DMF','PbI2']);
assert.deepEqual(step.conditions.map(item => item.name), ['PbI2 concentration'], 'one solvent has no ratio');
// Reopening rebuilds the box from the saved fields.
bundle.ground_truth.device_families[0] = {...family, processing_steps:[{...family.processing_steps[0], materials:['GBL','DMSO'],
  conditions:[{name:'GBL:DMSO volume ratio', raw_value:'7 : 3 v/v', value_number:null, unit:null, evidence:[c]}]}]};
editor.open('device_families', 0);
assert.equal(document.getElementById('record-step-0-solvents-ratio').value, '7 : 3 v/v');
assert.equal(document.getElementById('record-step-0-solvents-1').value, 'DMSO');
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
assert.equal(document.getElementById('record-copy-steps-layers').checked, true, 'missing layers are added by default');
document.getElementById('record-copy-steps-layers').checked = false;
click('Copy steps');
assert.deepEqual(editor.value().processing_steps, []);
assert.match(document.getElementById('dialog-status').textContent, /Copied 0 steps.*FAPbI3/);
bundle.ground_truth.device_families[0].processing_steps[0].target_layer_ids = ['a'];
editor.open('device_families', 1);
document.getElementById('record-copy-steps-layers').checked = false;
click('Copy steps');
assert.deepEqual(editor.value().processing_steps.map(item => [item.step_id, item.sequence, item.target_layer_ids, item.operation]),
  [['step-2', 1, ['v1'], 'spin coating']]);
assert.equal(bundle.ground_truth.device_families[1].processing_steps.length, 0, 'saved only on save');
"""
    )


def test_copying_steps_can_add_the_layers_and_absorber_a_family_is_missing():
    output = run_javascript(
        FAMILY_SETUP
        + """
let n = 0;
const freshId = prefix => `${prefix}-new-${++n}`;
// f2 has no PEAI layer: it is added between the perovskite and Spiro-OMeTAD, with its citation.
const withPeai = T.copyMissingLayers(f1, f2, freshId);
assert.deepEqual(withPeai.added, ['PEAI']);
assert.deepEqual(withPeai.layers.map(layer => [layer.layer_id, layer.sequence, layer.material]), [
  ['f2-g',1,'glass'], ['f2-ito',2,'ITO'], ['f2-sn',3,'SnO2'], ['f2-pvk',4,'perovskite'], ['layer-new-1',5,'PEAI'], ['f2-sp',6,'Spiro-OMeTAD']]);
assert.deepEqual(withPeai.layers[4].evidence, [c]);
assert.equal(withPeai.absorbers.length, 1, 'the perovskite layer already exists');
assert.equal(f2.layers.length, 5, 'the family itself is not changed');
// A family with only the bottom layers gets the rest, and the absorber of the copied perovskite layer.
const bare = {...structuredClone(f2), family_id:'f3', layers:structuredClone(f2.layers.slice(0, 3)), absorbers:[], processing_steps:[]};
const filled = T.copyMissingLayers(f1, bare, freshId);
assert.deepEqual(filled.added, ['perovskite', 'PEAI', 'Spiro-OMeTAD']);
assert.deepEqual(filled.layers.map(layer => layer.material), ['glass', 'ITO', 'SnO2', 'perovskite', 'PEAI', 'Spiro-OMeTAD']);
assert.deepEqual(filled.absorbers.map(item => [item.absorber_id, item.layer_id]), [['absorber-new-3', filled.layers[3].layer_id]]);
const f3 = {...bare, layers:filled.layers, absorbers:filled.absorbers};
const all = T.copySteps(f1, f3, () => `step-${++next}`);
assert.deepEqual(all.skipped, [], 'every step has its layer now');
f3.processing_steps = all.steps.map((item, index) => ({...item, sequence:index + 1}));
// Copying again adds nothing: every step is already there. A repeated source step counts twice.
const again = T.copySteps(f1, f3, () => `step-${++next}`);
assert.deepEqual([again.steps.length, again.present.length], [0, 6]);
const repeated = {...structuredClone(f1), processing_steps:[f1.processing_steps[1], f1.processing_steps[1]]};
const partly = {...structuredClone(f2), processing_steps:[structuredClone(f1.processing_steps[1])]};
partly.processing_steps[0].target_layer_ids = ['f2-sn'];
const counted = T.copySteps(repeated, partly, () => `step-${++next}`);
assert.deepEqual([counted.steps.length, counted.present], [1, ['annealing']]);
// Nothing to add when every layer has a counterpart.
assert.deepEqual(T.copyMissingLayers(f1, f3, freshId).added, []);
// A duplicated family gets new step and absorber identifiers, which are unique across families.
const copy = T.renewFamilyIdentifiers({...structuredClone(f1), family_id:'f4'}, freshId);
assert.ok(copy.processing_steps.every(item => item.step_id.startsWith('step-new-')));
assert.ok(copy.absorbers.every(item => item.absorber_id.startsWith('absorber-new-')));
assert.deepEqual(copy.layers.map(layer => layer.layer_id), f1.layers.map(layer => layer.layer_id), 'layer ids are per family');
console.log(JSON.stringify([f1, f2, f3, copy]));
"""
    )
    families = json.loads(output)
    issues = [
        issue.as_dict()
        for issue in validate_study_details(study(families), BLOCKS).issues
    ]
    assert issues == [], "copies validate, with no duplicate identifiers"


def test_values_without_a_current_check_are_found_and_recorded():
    run_javascript("""
const c = {block_id:'b', quote:'spun at 3000 rpm'};
const step = {step_id:'s1', evidence:[c], conditions:[
  {name:'spin speed', raw_value:'3000', unit:'rpm', evidence:[c]},
  {name:'spin duration', raw_value:'30', unit:'s', evidence:[]}]};
const checks = new Map();
assert.equal(T.stepStatus(step, checks).state, 'pending');
step.conditions[1].evidence = [c];
let entries = T.uncheckedConditions([step], checks);
assert.deepEqual(entries.map(entry => entry.condition.name), ['spin speed', 'spin duration']);
assert.equal(T.stepStatus(step, checks).state, 'checking');
// A value changed while the check ran gets no result and is checked again.
step.conditions[1].unit = 'min';
T.recordConditionChecks(entries, [{ok:true}, {ok:true}], checks);
assert.deepEqual(T.uncheckedConditions([step], checks).map(entry => entry.condition.name), ['spin duration']);
entries = T.uncheckedConditions([step], checks);
T.recordConditionChecks(entries, [{ok:false, reason:'"30" is not in the quote.', hint:''}], checks);
assert.equal(T.stepStatus(step, checks).state, 'error', 'a failure blocks like one found on confirming');
// Two values of one name keep their own results (they used to overwrite each other).
const twice = {step_id:'s2', evidence:[c], conditions:[
  {name:'volume', raw_value:'350', unit:'µL', evidence:[c]}, {name:'volume', raw_value:'28.5', unit:'µL', evidence:[c]}]};
const both = T.uncheckedConditions([twice], checks);
T.recordConditionChecks(both, [{ok:true}, {ok:true}], checks);
assert.deepEqual(T.uncheckedConditions([twice], checks), []);
assert.equal(T.stepStatus(twice, checks).state, 'done');
""")


def test_family_editor_adds_missing_layers_checks_copies_and_defaults_step_evidence():
    run_javascript(
        EDITOR
        + """
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const sentence = {block_id:'main-2', quote:'The perovskite was spin-coated.'};
bundle.ground_truth.device_families[0].processing_steps[0].evidence = [sentence];
bundle.ground_truth.device_families[0].processing_steps[0].conditions = [{name:'spin speed', raw_value:'3000', value_number:3000, unit:'rpm', evidence:[sentence]}];
bundle.ground_truth.device_families.push({family_id:'variant', label:'Variant', evidence:[c],
  layers:[{layer_id:'v1',sequence:1,material:'SnO2',role:'electron_transport_layer',evidence:[c]}],
  absorbers:[], processing_steps:[]});
editor.open('device_families', 1);
await wait(10);
click('Copy steps');
const value = editor.value();
assert.deepEqual(value.layers.map(layer => [layer.material, layer.sequence]), [['SnO2', 1], ['FAPbI3', 2]]);
assert.deepEqual(value.processing_steps[0].target_layer_ids, ['v1', value.layers[1].layer_id]);
assert.match(status(), /Added 1 layer this family was missing.*FAPbI3/);
// Copied steps are checked on their own; "Checking evidence" ends.
assert.deepEqual(badges(), ['checking']);
await wait(400);
assert.deepEqual(badges(), ['done']);
assert.deepEqual(checked.at(-1).map(item => item.raw_value), ['3000'], 'the copied value was checked');
// A new step offers the evidence of the step before it...
assert.equal(document.getElementById('citation-block').value, 'main-1', 'the family citation');
click('+ Spin coating');
assert.deepEqual([document.getElementById('citation-block').value, document.getElementById('citation-quote').value], ['main-2', sentence.quote]);
// ...unless new evidence was already selected in the paper.
await confirmStep(1, sentence.quote);
document.getElementById('citation-block').value = 'main-9';
document.getElementById('citation-quote').value = 'A sentence selected just now.';
click('+ Spin coating');
assert.equal(document.getElementById('citation-block').value, 'main-9');
// A duplicated family gets new step identifiers, so that it can be saved.
editor.open('device_families', null, bundle.ground_truth.device_families[0], 'copy');
assert.notEqual(editor.value().processing_steps[0].step_id, 'step-1');
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
