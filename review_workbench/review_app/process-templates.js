/** Processing-step templates: the method decides which parameter fields appear.
 *
 * Pure data and functions, no DOM. Only the existing ProcessingStep shape is filled:
 * the method and category are never stored. `operation` may use the paper's wording
 * ("spin-coat HTL", "spin-casting PbI2 solution and annealing"); the method is recognized
 * from it. Parameter names follow what the reference annotations use most, so that an
 * annotation and a model extraction name the same quantity alike:
 *   - concentrations per material: "<solute> concentration" (solution box);
 *   - annealing: "annealing temperature" / "annealing duration" ("first …"/"second …" for
 *     two stages), inside the deposition step or in a separate annealing step (the scorer
 *     treats them like "temperature"/"duration" under an annealing operation);
 *   - antisolvent values named by the chemical: "chlorobenzene volume", "… drip time".
 */
import { adoptPrintedSpelling, reportedNumber, standardUnit } from "./jv-table.js";
import { SOLVENT_NAMES } from "./material-suggestions.js";

const parameter = (name, unit = "", placeholder = "") => ({ name, unit, placeholder });

const WET_METHODS = [
  "spin coating", "blade coating", "slot-die coating", "drop casting", "dip coating",
  "spray coating", "inkjet printing",
];
const SPIN = [parameter("spin speed", "rpm", "4000"), parameter("spin duration", "s", "30")];
const ANNEALING = [parameter("annealing temperature", "°C", "100"), parameter("annealing duration", "min", "10")];

/** Parameters typical of each method. The category adds its shared ones (substrate
 * temperature/atmosphere); solution, annealing and drying/quenching have their own sections.
 */
export const METHOD_PARAMETERS = {
  "spin coating": [parameter("volume", "µL", "50"), ...SPIN],
  "blade coating": [
    parameter("volume", "µL", "20"), parameter("coating speed", "mm s-1", "20"), parameter("blade gap", "µm", "200"),
  ],
  "slot-die coating": [
    parameter("coating speed", "mm s-1", "10"), parameter("flow rate", "µL min-1", "50"),
    parameter("coating gap", "µm", "100"), parameter("shim thickness", "µm", "100"),
  ],
  "spray coating": [
    parameter("nozzle-to-substrate distance", "cm", "10"), parameter("flow rate", "mL min-1", "1"),
    parameter("carrier gas pressure", "psi", "20"), parameter("number of passes", "", "5"),
  ],
  "inkjet printing": [
    parameter("drop spacing", "µm", "20"), parameter("printing resolution", "dpi", "1000"),
    parameter("jetting voltage", "V", "20"), parameter("jetting frequency", "kHz", "1"),
    parameter("nozzle temperature", "°C", "40"), parameter("number of printed layers", "", "2"),
  ],
  "drop casting": [parameter("volume", "µL", "20")],
  "dip coating": [parameter("withdrawal speed", "mm min-1", "10"), parameter("dipping time", "s", "30")],
  "dipping": [parameter("dipping time", "s", "30")],
  "immersion": [parameter("immersion time", "min", "10")],
  "plasma treatment": [parameter("plasma power", "W", "100"), parameter("pressure", "mbar", "0.4")],
  "vacuum drying": [parameter("vacuum pressure", "Pa", "10")],
  "gas flow drying": [parameter("gas flow rate", "L min-1")],
};

/** Recognize the method in the paper's wording; deposition before annealing, so
 * "spin-casting PbI2 solution and annealing" is spin coating with in-step annealing.
 */
const METHOD_PATTERNS = [
  ["spin coating", /spin[\s-]*(coat|cast)|spincoat/],
  ["blade coating", /blade|doctor/],
  ["slot-die coating", /slot[\s-]*die/],
  ["spray coating", /spray/],
  ["inkjet printing", /ink[\s-]*jet/],
  ["drop casting", /drop[\s-]*cast/],
  ["dip coating", /dip[\s-]*coat/],
  ["thermal evaporation", /evapor|vacuum deposition/],
  ["sputtering", /sputter/],
  ["atomic layer deposition", /atomic layer|\bald\b/],
  ["chemical vapor deposition", /chemical vapou?r|\bcvd\b/],
  ["e-beam evaporation", /e-?beam|electron beam/],
  ["UV-ozone treatment", /uv[\s-]*(ozone|o3|treat|clean)|\buvo\b|ultraviolet[\s-]*(ozone|o3)/],
  ["plasma treatment", /plasma/],
  ["ultrasonic cleaning", /clean|sonicat|ultrason|wash|rins/],
  ["dipping", /^dip|dipped|dipping/],
  ["immersion", /immers/],
  ["gas exposure", /exposure|exposed/],
  ["solution preparation", /prepar|dissolv|stirr/],
  ["vacuum drying", /vacuum[\s-]*dr(y|ied)|\bdr(y|ied|ying)\b.*vacuum/],
  ["gas flow drying", /(n2|nitrogen|gas|air)[\s-]*(flow|stream|blow)|blow[\s-]*dr/],
  ["drying", /\bdr(y|ied|ying)\b(?![\s-]*deposit)/],
  ["annealing", /anneal|heat|sinter|bak/],
];

export function methodOf(operation) {
  const text = String(operation ?? "").normalize("NFKC").toLowerCase();
  return METHOD_PATTERNS.find(([, pattern]) => pattern.test(text))?.[0] ?? null;
}

/** Drying and quenching during a wet deposition, after Plains' QuenchingModal (Gas,
 * Antisolvent, Vacuum) plus plain drying. They stay conditions of the deposition step.
 * Antisolvent values are named by the antisolvent ("chlorobenzene volume"), or
 * "antisolvent …" when it has no name yet; its parameters are suffixes.
 */
export const QUENCHING_TYPES = [
  {
    id: "antisolvent", label: "Antisolvent", prefixed: true,
    parameters: [
      parameter("volume", "µL", "150"), parameter("drip time", "s", "10"),
      parameter("dispensing method", "", "dripping"), parameter("flow rate", "µL s-1"),
      parameter("dispensing height", "mm", "10"),
    ],
  },
  {
    id: "gas", label: "Gas quenching",
    parameters: [
      parameter("gas pressure", "psi", "20"), parameter("gas flow rate", "L min-1"),
      parameter("nozzle height", "mm", "5"), parameter("nozzle width", "mm"), parameter("nozzle form", "", "slit"),
      parameter("gas quenching start time", "s", "5"),
    ],
  },
  {
    id: "vacuum", label: "Vacuum quenching",
    parameters: [
      parameter("vacuum pressure", "Pa", "20"), parameter("evacuation time", "s", "10"),
      parameter("vacuum chamber height", "mm"), parameter("vacuum chamber base area", "cm2"),
      parameter("dead volume", "L"), parameter("pump model", "", "Edwards RV3"),
      parameter("vacuum quenching start time", "s", "5"),
    ],
  },
  {
    id: "drying", label: "Drying in this step",
    parameters: [parameter("drying temperature", "°C", "25"), parameter("drying time", "min", "10")],
  },
];

const ANTISOLVENT = QUENCHING_TYPES[0];

/** The name used for this step's antisolvent values: a recorded prefix, else "antisolvent". */
export function antisolventOf(step) {
  const materials = new Set((step.materials || []).map(name => name.trim().toLowerCase()));
  for (const condition of step.conditions || []) {
    for (const item of ANTISOLVENT.parameters) {
      const name = String(condition.name || "");
      if (!name.toLowerCase().endsWith(` ${item.name}`)) continue;
      const prefix = name.slice(0, -item.name.length - 1).trim();
      const folded = prefix.toLowerCase();
      if (folded === "antisolvent" || SOLVENT_NAMES.has(folded) || materials.has(folded)) return prefix;
    }
  }
  return "antisolvent";
}

/** Full condition names of a drying/quenching type on this step. */
export function quenchingParameters(type, step) {
  if (!type.prefixed) return type.parameters;
  const prefix = antisolventOf(step);
  return type.parameters.map(item => ({ ...item, name: `${prefix} ${item.name}` }));
}

/** The drying/quenching types that already have a value on the step. */
export function quenchingTypesUsed(step) {
  const names = new Set((step.conditions || []).map(condition => key(condition.name)));
  return QUENCHING_TYPES.filter(type => quenchingParameters(type, step).some(item => names.has(key(item.name)))).map(type => type.id);
}

/** Give the antisolvent values a new prefix (its name as written). */
export function renameAntisolvent(step, name) {
  const before = antisolventOf(step);
  const after = String(name ?? "").trim() || "antisolvent";
  for (const condition of step.conditions || []) {
    for (const item of ANTISOLVENT.parameters) {
      if (key(condition.name) === key(`${before} ${item.name}`)) condition.name = `${after} ${item.name}`;
    }
  }
}

/** Annealing inside a deposition step; two stages use "first"/"second" names. */
export function annealingParameters(twoStage = false) {
  const items = [...ANNEALING];
  const staged = twoStage ? ["first", "second"].flatMap(stage => ANNEALING.map(item => ({ ...item, name: `${stage} ${item.name}` }))) : items;
  return [...staged, parameter("annealing atmosphere", "", "N2")];
}

export const isTwoStageAnnealing = (conditions = []) => conditions.some(condition => /^(first|second) annealing /i.test(condition.name || ""));

/** `role`: "forms" creates a layer, "treats" acts on an existing one, "prepares" makes its solution. */
export const PROCESS_CATEGORIES = [
  {
    id: "solution_preparation", label: "Solution preparation", role: "prepares", solution: true,
    methods: ["solution preparation"],
    parameters: [
      parameter("stirring temperature", "°C", "60"), parameter("stirring duration", "h", "2"),
      parameter("filter pore size", "µm", "0.22"),
    ],
  },
  {
    id: "wet_deposition", label: "Wet deposition", role: "forms", methods: [...WET_METHODS, "dipping", "immersion"],
    quenching: true, solution: true, annealing: true,
    parameters: [],
    after: [parameter("substrate temperature", "°C"), parameter("atmosphere", "", "N2 glovebox")],
  },
  {
    id: "dry_deposition", label: "Dry deposition", role: "forms",
    methods: ["thermal evaporation", "sputtering", "atomic layer deposition", "chemical vapor deposition", "e-beam evaporation"],
    parameters: [
      parameter("deposition rate", "Å s-1", "0.5"), parameter("pressure", "Pa"), parameter("thickness", "nm", "100"),
      parameter("substrate temperature", "°C"), parameter("atmosphere"),
    ],
  },
  {
    // Plasma, UV-ozone or gas treatment of a surface; the plasma gas goes into materials.
    // A layer applied from solution to modify a surface (e.g. PEAI) is a wet deposition
    // whose target layer says what it modifies.
    id: "surface_treatment", label: "Surface treatment (plasma, UV-ozone)", role: "treats",
    methods: ["plasma treatment", "UV-ozone treatment", "gas exposure"],
    parameters: [],
    after: [parameter("treatment duration", "min", "15")],
  },
  {
    id: "substrate_preparation", label: "Substrate cleaning", role: "treats",
    methods: ["ultrasonic cleaning", "rinsing"],
    parameters: [parameter("duration", "min", "15")],
  },
  {
    // Drying as a step of its own, e.g. "dried at 70 °C for 10 min" or "dried in vacuum".
    // Drying during a wet deposition stays in that step's Drying / quenching box.
    id: "drying", label: "Drying", role: "treats",
    methods: ["drying", "vacuum drying", "gas flow drying"],
    parameters: [],
    after: [parameter("drying temperature", "°C", "70"), parameter("drying time", "min", "10"), parameter("atmosphere", "", "N2")],
  },
  {
    id: "annealing", label: "Annealing", role: "treats", methods: ["annealing"],
    parameters: [
      parameter("annealing temperature", "°C", "100"), parameter("annealing duration", "min", "10"),
      parameter("annealing atmosphere", "", "air"),
    ],
  },
];

/** Buttons that add a typed step. */
export const STEP_PRESETS = [
  { label: "+ Spin coating", category: "wet_deposition", operation: "spin coating" },
  { label: "+ Blade coating", category: "wet_deposition", operation: "blade coating" },
  { label: "+ Slot-die coating", category: "wet_deposition", operation: "slot-die coating" },
  { label: "+ Spray coating", category: "wet_deposition", operation: "spray coating" },
  { label: "+ Inkjet printing", category: "wet_deposition", operation: "inkjet printing" },
  { label: "+ Evaporation", category: "dry_deposition", operation: "thermal evaporation" },
  { label: "+ Surface treatment", category: "surface_treatment", operation: "plasma treatment" },
  { label: "+ Substrate cleaning", category: "substrate_preparation", operation: "ultrasonic cleaning" },
  { label: "+ Solution preparation", category: "solution_preparation", operation: "solution preparation" },
  { label: "+ Drying", category: "drying", operation: "drying" },
  { label: "+ Annealing", category: "annealing", operation: "annealing" },
];

const key = text => String(text ?? "").normalize("NFKC").toLowerCase().replace(/[\s_-]+/g, "");

export const categoryById = id => PROCESS_CATEGORIES.find(category => category.id === id) || null;

/** The category of a step, recognized from its operation (paper wording allowed); null when no
 * method is recognized. A method in several categories (spin coating) resolves to the first.
 */
export function categoryOf(operation) {
  const method = methodOf(operation);
  if (!method) return null;
  return PROCESS_CATEGORIES.find(category => category.methods.includes(method)) || null;
}

/** Whether the conditions describe a two-stage spin program (first/second spin …). */
export function isTwoStageSpin(conditions = []) {
  return conditions.some(condition => /^(first|second) spin /i.test(condition.name || ""));
}

/** Whether the method is spin coating, which may run as a two-stage spin program. */
export const usesSpinProgram = operation => methodOf(operation) === "spin coating";

/** The template parameters of a step: its method's parameters, then the category's shared ones.
 * A two-stage spin program replaces spin speed/duration by first/second stage parameters.
 */
export function parametersFor(categoryId, twoStage = false, operation = "") {
  const category = categoryById(categoryId);
  if (!category) return [];
  const method = METHOD_PARAMETERS[methodOf(operation)] || [];
  const all = [...category.parameters, ...(category.after ? method : []), ...(category.after || [])];
  if (!twoStage || !usesSpinProgram(operation)) return all;
  return all.flatMap((item) => {
    if (item.name === "spin speed") return ["first", "second"].flatMap(stage => SPIN.map(each => ({ ...each, name: `${stage} ${each.name}` })));
    return item.name.startsWith("spin ") ? [] : [item];
  });
}

export const findCondition = (step, name) => (step.conditions || []).find(condition => key(condition.name) === key(name));

/** Set one template parameter on the step; an empty value removes it, so nothing unreported is saved.
 * The value is kept exactly as typed; value_number only for a plain number. A new condition has
 * empty evidence, filled with the citation chosen when the family is saved.
 */
export function setCondition(step, name, raw, unit) {
  step.conditions = step.conditions || [];
  const text = String(raw ?? "").trim();
  const index = step.conditions.findIndex(condition => key(condition.name) === key(name));
  if (!text) {
    if (index >= 0) step.conditions.splice(index, 1);
    return;
  }
  const value = { raw_value: text, value_number: reportedNumber(text), unit: standardUnit(unit) || null };
  if (index < 0) return void step.conditions.push({ name, ...value, evidence: [] });
  const condition = step.conditions[index];
  // A changed value needs a quote that contains it: the save citation replaces the old one.
  if (condition.raw_value !== text) condition.evidence = [];
  Object.assign(condition, value);
}

export function setConditionUnit(step, name, unit) {
  const condition = findCondition(step, name);
  if (condition) condition.unit = standardUnit(unit) || null;
}

/** Switch spin speed/duration between one stage and first/second stage names. */
export function setTwoStageSpin(step, twoStage) {
  for (const condition of step.conditions || []) {
    if (twoStage && /^spin (speed|duration)$/i.test(condition.name)) condition.name = `first ${condition.name}`;
    if (!twoStage && /^first spin (speed|duration)$/i.test(condition.name)) condition.name = condition.name.replace(/^first /i, "");
  }
}

/** Switch in-step annealing between one stage and first/second stage names. */
export function setTwoStageAnnealing(step, twoStage) {
  for (const condition of step.conditions || []) {
    if (twoStage && /^annealing (temperature|duration)$/i.test(condition.name)) condition.name = `first ${condition.name}`;
    if (!twoStage && /^first annealing (temperature|duration)$/i.test(condition.name)) condition.name = condition.name.replace(/^first /i, "");
  }
}

/** A new step of a category; identifiers, sequence and targets belong to the caller. */
export function newStep(category, operation = categoryById(category)?.methods[0] || "") {
  return { step_id: "", sequence: null, operation, target_layer_ids: [], materials: [], conditions: [], evidence: [] };
}

/** One step per target layer; the first copy keeps the original identifier. */
export function splitPerTarget(step, freshId) {
  const targets = step.target_layer_ids || [];
  if (targets.length < 2) return [step];
  return targets.map((target, index) => ({
    ...structuredClone(step),
    step_id: index === 0 ? step.step_id : freshId(),
    target_layer_ids: [target],
  }));
}

/** The target family's layer for a source layer: same material, else same function at the same rank. */
export function matchingLayer(sourceLayers, layerId, targetLayers) {
  const source = sourceLayers.find(layer => layer.layer_id === layerId);
  if (!source) return null;
  const fold = text => String(text ?? "").normalize("NFKC").trim().toLowerCase();
  const sameMaterial = targetLayers.filter(layer => fold(layer.material) && fold(layer.material) === fold(source.material));
  if (sameMaterial.length === 1) return sameMaterial[0];
  if (sameMaterial.length > 1) {
    // Several layers of one material (e.g. two PEAI layers): keep their order.
    const rank = sourceLayers.filter(layer => fold(layer.material) === fold(source.material)).indexOf(source);
    return sameMaterial[rank] ?? null;
  }
  if (!source.role || source.role === "not_reported" || source.role === "other") return null;
  const rank = sourceLayers.filter(layer => layer.role === source.role).indexOf(source);
  const sameRole = targetLayers.filter(layer => layer.role === source.role);
  return sameRole.length === sourceLayers.filter(layer => layer.role === source.role).length ? sameRole[rank] ?? null : null;
}

const bySequence = items => [...items]
  .map((item, index) => [item, index])
  .sort(([a, i], [b, j]) => (a.sequence ?? Infinity) - (b.sequence ?? Infinity) || i - j)
  .map(([item]) => item);

/** Add to `targetFamily` the layers of `sourceFamily` that it has no counterpart for (see
 * matchingLayer), each placed after the counterpart of the source layer before it, with a
 * new identifier from `freshId(prefix)`. Absorbers on a copied layer are copied with it.
 * Returns new `layers` and `absorbers` lists (renumbered) and the materials added.
 */
export function copyMissingLayers(sourceFamily, targetFamily, freshId) {
  const sourceLayers = bySequence(sourceFamily.layers || []);
  const layers = bySequence(targetFamily.layers || []).map(layer => structuredClone(layer));
  const absorbers = (targetFamily.absorbers || []).map(absorber => structuredClone(absorber));
  const original = [...layers];
  const counterpart = new Map();
  for (const layer of sourceLayers) {
    const match = matchingLayer(sourceLayers, layer.layer_id, original);
    if (match) counterpart.set(layer.layer_id, match);
  }
  const added = [];
  sourceLayers.forEach((layer, index) => {
    if (counterpart.has(layer.layer_id)) return;
    const copy = { ...structuredClone(layer), layer_id: freshId("layer") };
    const before = sourceLayers.slice(0, index).reverse().find(item => counterpart.has(item.layer_id));
    layers.splice(before ? layers.indexOf(counterpart.get(before.layer_id)) + 1 : 0, 0, copy);
    counterpart.set(layer.layer_id, copy);
    added.push(layer.material || layer.role || copy.layer_id);
    for (const absorber of sourceFamily.absorbers || []) {
      if (absorber.layer_id === layer.layer_id) {
        absorbers.push({ ...structuredClone(absorber), absorber_id: freshId("absorber"), layer_id: copy.layer_id });
      }
    }
  });
  layers.forEach((layer, index) => { layer.sequence = index + 1; });
  return { layers, absorbers, added };
}

/** A family copied as a new record keeps its layers (identifiers are per family) but needs
 * new step and absorber identifiers, which must be unique across all families.
 */
export function renewFamilyIdentifiers(family, freshId) {
  for (const step of family.processing_steps || []) step.step_id = freshId("step");
  for (const absorber of family.absorbers || []) absorber.absorber_id = freshId("absorber");
  return family;
}

/** Copies of another family's steps, retargeted to this family's layers (see matchingLayer).
 * A step whose target layer has no counterpart is not copied; its label is returned in `skipped`.
 * Copies keep their values and citations (same paper) and get new identifiers from `freshId`.
 */
export function copySteps(sourceFamily, targetFamily, freshId) {
  const sourceLayers = sourceFamily.layers || [];
  const targetLayers = targetFamily.layers || [];
  const ordered = [...(sourceFamily.processing_steps || [])]
    .map((step, index) => [step, index])
    .sort(([a, i], [b, j]) => (a.sequence ?? Infinity) - (b.sequence ?? Infinity) || i - j)
    .map(([step]) => step);
  const steps = [];
  const skipped = [];
  const present = [];
  // A step the family already has (same method, materials, values, evidence and layers) is not
  // added twice. Counted, so a step the source repeats is copied as often as it is missing.
  const content = item => JSON.stringify([item.operation, item.materials, item.conditions, item.evidence, item.target_layer_ids]);
  const existing = new Map();
  for (const item of targetFamily.processing_steps || []) existing.set(content(item), (existing.get(content(item)) || 0) + 1);
  for (const step of ordered) {
    const targets = (step.target_layer_ids || []).map(id => matchingLayer(sourceLayers, id, targetLayers));
    if (targets.some(layer => !layer)) {
      const missing = (step.target_layer_ids || [])
        .filter((_id, index) => !targets[index])
        .map(id => sourceLayers.find(layer => layer.layer_id === id)?.material || id);
      skipped.push(`${step.operation || "step"} (${missing.join(", ")})`);
      continue;
    }
    const copy = { ...structuredClone(step), sequence: null, target_layer_ids: targets.map(layer => layer.layer_id) };
    const remaining = existing.get(content(copy)) || 0;
    if (remaining) {
      existing.set(content(copy), remaining - 1);
      present.push(step.operation || "step");
      continue;
    }
    steps.push({ ...copy, step_id: freshId() });
  }
  return { steps, skipped, present };
}

/* Evidence per step. A step is finished when the step and each of its values are cited
 * and the server found every new value in its quote (the same check as on save).
 * Check results are kept per value with a signature of what was checked, so that any
 * later edit of the value or its evidence makes the result stale.
 */
// Per value position, not name alone: two values of one name (a typo, or two volumes) must
// not overwrite each other's result. A moved value is simply checked again.
export const checkKey = (step, condition) => `${step.step_id || ""}|${(step.conditions || []).indexOf(condition)}|${condition.name || ""}`;
export const conditionSignature = condition => `${condition.raw_value ?? ""}\u0000${condition.unit ?? ""}\u0000${JSON.stringify(condition.evidence || [])}`;

/** The evidence state of a step:
 *   done      every value and the step are cited, new values were found in their quotes;
 *   pending   something has no evidence yet, or the step was edited since it was confirmed;
 *   error     a value was not found in the quote chosen for it;
 *   warning   a value already failed the check when the record was opened (not blocking);
 *   checking  cited values whose check has not returned yet.
 * `blocking` steps must be confirmed before another step is added or the family is saved.
 */
export function stepStatus(step, checks = new Map(), edited = false) {
  const problems = [];
  const warnings = [];
  const pending = [];
  let unchecked = 0;
  for (const condition of step.conditions || []) {
    const label = `${condition.name || "unnamed parameter"} "${condition.raw_value ?? ""}"`;
    const result = checks.get(checkKey(step, condition));
    const current = result && result.signature === conditionSignature(condition);
    if (current && !result.ok) (result.inherited ? warnings : problems).push({ label, reason: result.reason, hint: result.hint, quote: result.quote });
    else if (!condition.evidence?.length) pending.push(`${label} has no evidence yet`);
    else if (!current) unchecked += 1;
  }
  if (!step.evidence?.length) pending.push("the step itself has no evidence yet");
  else if (edited && !pending.length) pending.push("edited since it was last confirmed");
  const state = problems.length ? "error" : pending.length ? "pending" : warnings.length ? "warning" : unchecked ? "checking" : "done";
  return { state, blocking: state === "error" || state === "pending", problems, warnings, pending };
}

/** What to send to the grounding check when a step is confirmed with `citation`: every value
 * (uncited and failed ones tried with the citation, cited ones as they are) and, last, the
 * citation's quote on its own.
 */
export function stepCheckRequest(step, checks, citation) {
  const entries = (step.conditions || []).map((condition) => {
    const result = checks.get(checkKey(step, condition));
    const failed = result && !result.ok && result.signature === conditionSignature(condition);
    const candidate = !condition.evidence?.length || Boolean(failed);
    return { condition, candidate, evidence: candidate ? [{ ...citation }] : condition.evidence };
  });
  const values = entries.map(({ condition, evidence }) => ({ raw_value: condition.raw_value, unit: condition.unit, evidence }));
  values.push({ quote_only: true, evidence: [{ ...citation }] });
  return { entries, values };
}

/** Apply the check results: found values get the citation, others keep no evidence and
 * record why. The step is cited too when it has no evidence. Returns what happened.
 */
export function applyStepCheck(step, request, results, checks, citation) {
  const quote = results[request.entries.length] || { ok: false, reason: "No answer from the check.", hint: "" };
  const cited = [];
  const failed = [];
  const respelled = [];
  if (!quote.ok) return { cited, failed, respelled, quoteProblem: quote };
  request.entries.forEach(({ condition, candidate }, index) => {
    const result = results[index] || { ok: false, reason: "No answer from the check.", hint: "" };
    if (result.ok && adoptPrintedSpelling(condition, result.raw_value)) respelled.push(`${condition.name} "${condition.raw_value}"`);
    if (candidate) condition.evidence = result.ok ? [{ ...citation }] : [];
    if (candidate && result.ok) cited.push(condition.name);
    if (!result.ok) failed.push({ label: `${condition.name} "${condition.raw_value ?? ""}"`, reason: result.reason, hint: result.hint });
    checks.set(checkKey(step, condition), {
      signature: conditionSignature(condition), ok: result.ok, reason: result.reason, hint: result.hint,
      quote: candidate ? citation.quote : condition.evidence?.[0]?.quote, inherited: false,
    });
  });
  if (!step.evidence?.length) step.evidence = [{ ...citation }];
  return { cited, failed, respelled, quoteProblem: null };
}

/** Record the check of a just-opened record's cited values; failures there are inherited. */
export function recordInheritedChecks(steps, results, checks) {
  let index = 0;
  for (const step of steps) for (const condition of step.conditions || []) {
    if (!condition.evidence?.length) continue;
    const result = results[index++];
    if (result) checks.set(checkKey(step, condition), {
      signature: conditionSignature(condition), ok: result.ok, reason: result.reason, hint: result.hint,
      quote: condition.evidence[0]?.quote, inherited: true,
    });
  }
}

/** Cited values whose current form has no check result: copied steps, and values whose unit
 * or evidence changed while the citation stayed. Each entry keeps the signature it is checked with.
 */
export function uncheckedConditions(steps, checks) {
  const entries = [];
  for (const step of steps) for (const condition of step.conditions || []) {
    if (!condition.evidence?.length) continue;
    const result = checks.get(checkKey(step, condition));
    if (!result || result.signature !== conditionSignature(condition)) {
      entries.push({ step, condition, signature: conditionSignature(condition) });
    }
  }
  return entries;
}

/** Record the results for `uncheckedConditions` entries. A value changed while the check ran
 * keeps no result, so it is checked again. Failures block like those of a confirmed step.
 */
export function recordConditionChecks(entries, results, checks) {
  entries.forEach(({ step, condition, signature }, index) => {
    const result = results[index];
    if (!result || conditionSignature(condition) !== signature) return;
    if (result.ok) adoptPrintedSpelling(condition, result.raw_value);
    checks.set(checkKey(step, condition), {
      signature: conditionSignature(condition), ok: result.ok, reason: result.reason, hint: result.hint,
      quote: condition.evidence[0]?.quote, inherited: false,
    });
  });
}

export const citedValuesOf = steps => steps.flatMap(step => (step.conditions || [])
  .filter(condition => condition.evidence?.length)
  .map(condition => ({ raw_value: condition.raw_value, unit: condition.unit, evidence: condition.evidence })));

/** Replace JSON paths in a save error by the step and value they point to. */
export function readableGroundingError(message, value) {
  return String(message).replace(/\$\.device_families\[\d+\]((?:\.[a-z_]+\[\d+\])+)(?::\s*raw_value not found in cited evidence)?/g, (match, rest) => {
    const parts = [...rest.matchAll(/\.([a-z_]+)\[(\d+)\]/g)].map(([, key, index]) => [key, Number(index)]);
    if (parts[0]?.[0] !== "processing_steps" || !value) return match;
    const step = value.processing_steps?.[parts[0][1]];
    if (!step) return match;
    const condition = parts[1]?.[0] === "conditions" ? step.conditions?.[parts[1][1]] : null;
    const where = `step ${parts[0][1] + 1} (${step.operation || "no method"})`;
    return condition
      ? `${where}: ${condition.name} "${condition.raw_value}" is not in its quote "${condition.evidence?.[0]?.quote ?? "(none)"}"`
      : where;
  });
}

/** Problems that the schema would reject: an empty operation, unnamed parameters, empty materials. */
export function stepProblems(step, label = "Step") {
  const problems = [];
  if (!String(step.operation || "").trim()) problems.push(`${label}: choose a method.`);
  for (const condition of step.conditions || []) {
    if (!String(condition.name || "").trim()) problems.push(`${label}: a parameter has no name.`);
    if (!String(condition.raw_value || "").trim()) problems.push(`${label}: parameter "${condition.name}" has no value.`);
  }
  if ((step.materials || []).some(material => !String(material || "").trim())) problems.push(`${label}: remove the empty material.`);
  return problems;
}
