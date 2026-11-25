/** Processing-step templates: the step category decides which methods and parameters appear.
 *
 * Pure data and functions, no DOM. Only the existing ProcessingStep shape is filled:
 * the category is never stored, it is recognized again from `operation`. Method and
 * parameter spellings follow what the scorer normalizes (`scoring_process.py`) and what
 * the reference annotations use most; annealing is always its own step whose
 * conditions are named `temperature` and `duration` (read by the NOMAD export).
 */
import { reportedNumber, valuesMissingFromQuote } from "./jv-table.js";

const parameter = (name, unit = "", placeholder = "") => ({ name, unit, placeholder });

const WET_METHODS = [
  "spin coating", "blade coating", "slot-die coating", "drop casting", "dip coating",
  "spray coating", "inkjet printing",
];
const SPIN = [parameter("spin speed", "rpm", "4000"), parameter("spin duration", "s", "30")];

/** Parameters typical of each coating method. The category adds the shared ones
 * (concentration before, substrate temperature/atmosphere after); drying and quenching
 * (antisolvent, gas knife, vacuum) have their own box, see QUENCHING_TYPES.
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
};

/** Drying and quenching during a wet deposition, after Plains' QuenchingModal (Gas,
 * Antisolvent, Vacuum) plus plain drying. They stay conditions of the deposition step; the
 * antisolvent or gas itself is a step material (convention C6), so it has no field here.
 */
export const QUENCHING_TYPES = [
  {
    id: "antisolvent", label: "Antisolvent",
    parameters: [
      parameter("antisolvent volume", "µL", "150"), parameter("antisolvent drip time", "s", "10"),
      parameter("antisolvent dispensing method", "", "dripping"), parameter("antisolvent flow rate", "µL s-1"),
      parameter("antisolvent dispensing height", "mm", "10"),
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
    id: "drying", label: "Drying",
    parameters: [parameter("drying temperature", "°C", "25"), parameter("drying time", "min", "10")],
  },
];

/** The drying/quenching types that already have a value on the step. */
export function quenchingTypesUsed(conditions = []) {
  const names = new Set(conditions.map(condition => key(condition.name)));
  return QUENCHING_TYPES.filter(type => type.parameters.some(item => names.has(key(item.name)))).map(type => type.id);
}

/** `role`: "forms" creates a layer, "treats" acts on an existing one, "prepares" makes its solution. */
export const PROCESS_CATEGORIES = [
  {
    id: "solution_preparation", label: "Solution preparation", role: "prepares",
    methods: ["solution preparation"],
    parameters: [
      parameter("concentration", "mg mL-1", "1.2 M or 10 mg mL-1"), parameter("volume ratio", "", "4:1"),
      parameter("molar ratio", "", "1:1"), parameter("stirring temperature", "°C", "60"),
      parameter("stirring duration", "h", "2"), parameter("filter pore size", "µm", "0.22"),
    ],
  },
  {
    id: "wet_deposition", label: "Wet deposition", role: "forms", methods: WET_METHODS, quenching: true,
    parameters: [parameter("concentration", "mg mL-1")],
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
    id: "surface_treatment", label: "Surface treatment / doping", role: "treats", quenching: true,
    methods: [...WET_METHODS, "dipping", "immersion", "gas exposure", "doping/aging"],
    parameters: [parameter("concentration", "mg mL-1")],
    after: [parameter("temperature", "°C"), parameter("duration", "min"), parameter("atmosphere")],
  },
  {
    id: "substrate_preparation", label: "Substrate preparation", role: "treats",
    methods: ["ultrasonic cleaning", "UV-ozone treatment", "plasma treatment", "rinsing", "blow drying"],
    parameters: [parameter("duration", "min", "15"), parameter("power", "W")],
  },
  {
    id: "annealing", label: "Annealing", role: "treats", methods: ["annealing"],
    parameters: [parameter("temperature", "°C", "100"), parameter("duration", "min", "10"), parameter("atmosphere", "", "air")],
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
  { label: "+ Surface treatment", category: "surface_treatment", operation: "spin coating" },
  { label: "+ Substrate cleaning", category: "substrate_preparation", operation: "ultrasonic cleaning" },
  { label: "+ Solution preparation", category: "solution_preparation", operation: "solution preparation" },
  { label: "+ Annealing", category: "annealing", operation: "annealing" },
];

const key = text => String(text ?? "").normalize("NFKC").toLowerCase().replace(/[\s_-]+/g, "");

export const categoryById = id => PROCESS_CATEGORIES.find(category => category.id === id) || null;

/** The category of a saved step, from its operation; null for operations outside the templates.
 * A method shared by several categories (spin coating) resolves to the first, wet deposition.
 */
export function categoryOf(operation) {
  const wanted = key(operation);
  if (!wanted) return null;
  if (wanted === "annealed" || wanted === "thermalannealing") return categoryById("annealing");
  return PROCESS_CATEGORIES.find(category => category.methods.some(method => key(method) === wanted)) || null;
}

/** Whether the conditions describe a two-stage spin program (first/second spin …). */
export function isTwoStageSpin(conditions = []) {
  return conditions.some(condition => /^(first|second) spin /i.test(condition.name || ""));
}

/** Whether the method is spin coating, which may run as a two-stage spin program. */
export const usesSpinProgram = operation => key(operation) === key("spin coating");

/** The template parameters of a step: the category's shared ones around those of its method.
 * A two-stage spin program replaces spin speed/duration by first/second stage parameters.
 */
export function parametersFor(categoryId, twoStage = false, operation = "") {
  const category = categoryById(categoryId);
  if (!category) return [];
  const method = Object.entries(METHOD_PARAMETERS).find(([name]) => key(name) === key(operation))?.[1] || [];
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
  const value = { raw_value: text, value_number: reportedNumber(text), unit: String(unit ?? "").trim() || null };
  if (index < 0) return void step.conditions.push({ name, ...value, evidence: [] });
  const condition = step.conditions[index];
  // A changed value needs a quote that contains it: the save citation replaces the old one.
  if (condition.raw_value !== text) condition.evidence = [];
  Object.assign(condition, value);
}

export function setConditionUnit(step, name, unit) {
  const condition = findCondition(step, name);
  if (condition) condition.unit = String(unit ?? "").trim() || null;
}

/** Switch spin speed/duration between one stage and first/second stage names. */
export function setTwoStageSpin(step, twoStage) {
  for (const condition of step.conditions || []) {
    if (twoStage && /^spin (speed|duration)$/i.test(condition.name)) condition.name = `first ${condition.name}`;
    if (!twoStage && /^first spin (speed|duration)$/i.test(condition.name)) condition.name = condition.name.replace(/^first /i, "");
  }
}

/** A new step of a category; identifiers, sequence and targets belong to the caller. */
export function newStep(category, operation = categoryById(category)?.methods[0] || "") {
  return { step_id: "", sequence: null, operation, target_layer_ids: [], materials: [], conditions: [], evidence: [] };
}

/** The separate annealing step that follows a step on the same target layers. */
export function annealingStepFor(step) {
  return { ...newStep("annealing"), target_layer_ids: [...(step.target_layer_ids || [])] };
}

/** One step per target layer (convention C2); the first copy keeps the original identifier. */
export function splitPerTarget(step, freshId) {
  const targets = step.target_layer_ids || [];
  if (targets.length < 2) return [step];
  return targets.map((target, index) => ({
    ...structuredClone(step),
    step_id: index === 0 ? step.step_id : freshId(),
    target_layer_ids: [target],
  }));
}

/** A layer material written into the operation (e.g. "spin-coat PTAA"), which belongs in the targets. */
export function materialInOperation(operation, layers = []) {
  const text = String(operation || "").toLowerCase();
  return layers.map(layer => layer.material).find(material => material && material.length > 1 && text.includes(material.toLowerCase())) || null;
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
  for (const step of ordered) {
    const targets = (step.target_layer_ids || []).map(id => matchingLayer(sourceLayers, id, targetLayers));
    if (targets.some(layer => !layer)) {
      const missing = (step.target_layer_ids || [])
        .filter((_id, index) => !targets[index])
        .map(id => sourceLayers.find(layer => layer.layer_id === id)?.material || id);
      skipped.push(`${step.operation || "step"} (${missing.join(", ")})`);
      continue;
    }
    steps.push({ ...structuredClone(step), step_id: freshId(), sequence: null, target_layer_ids: targets.map(layer => layer.layer_id) });
  }
  return { steps, skipped };
}

/** New condition values that the save quote does not contain; the server would reject them. */
export function conditionsMissingFromQuote(steps, quote) {
  return steps.flatMap((step, index) => valuesMissingFromQuote(
    [{ metrics: (step.conditions || []).filter(condition => !condition.evidence?.length) }], quote,
  ).map(problem => problem.replace(/^row 1/, `step ${step.sequence ?? index + 1}`)));
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
