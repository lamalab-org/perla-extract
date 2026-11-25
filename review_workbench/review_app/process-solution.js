/** The solution of a wet processing step: solvents, solutes, their ratios and concentrations.
 *
 * The schema has no solution object, so the widget writes ordinary step fields, named as
 * the reference annotations name them most often:
 *   - every component goes into `materials`, as written;
 *   - each ratio is one condition named after its components, value exactly as printed:
 *     "DMF:DMSO volume ratio" = "4:1 (v/v)",  "PbI2:FAI:MABr molar ratio" = "1.1:1:0.2";
 *   - a solute's concentration is "<solute> concentration" = "1.5" with unit "M".
 * Total volume is not recorded. Reopening a step rebuilds the widget from those names.
 */
import { reportedNumber } from "./jv-table.js";
import { SOLVENT_NAMES } from "./material-suggestions.js";

export const SOLVENT_RATIO_KINDS = ["volume", "molar"];
const RATIO_NAME = /^(.+) (volume|molar) ratio$/i;
const CONCENTRATION_NAME = /^(.+) concentration$/i;
const fold = text => String(text ?? "").normalize("NFKC").trim().toLowerCase();
const isSolvent = name => SOLVENT_NAMES.has(fold(name));

export function ratioName(names, kind) {
  return `${names.join(":")} ${kind} ratio`;
}

export const concentrationName = solute => `${solute} concentration`;

/** Components, kind and printed value of a ratio condition, or null for other conditions. */
export function parseRatio(condition) {
  const match = RATIO_NAME.exec(String(condition?.name || "").trim());
  if (!match) return null;
  const names = match[1].split(":").map(name => name.trim());
  if (names.length < 2 || names.some(name => !name)) return null;
  return { names, kind: match[2].toLowerCase(), ratio: condition.raw_value ?? "" };
}

export const emptySolution = () => ({ solvents: { names: [], kind: "volume", ratio: "" }, solutes: { rows: [], ratio: "" } });

/** The solution recorded on a step: the first ratio of only solvents, the first molar ratio
 * of solutes, and every "<material> concentration" whose material is not a solvent.
 */
export function solutionOf(step) {
  const solution = emptySolution();
  const conditions = step.conditions || [];
  let soluteNames = [];
  for (const condition of conditions) {
    const parsed = parseRatio(condition);
    if (!parsed) continue;
    if (parsed.names.every(isSolvent)) {
      if (!solution.solvents.names.length) solution.solvents = parsed;
    } else if (parsed.kind === "molar" && !soluteNames.length) {
      soluteNames = parsed.names;
      solution.solutes.ratio = parsed.ratio;
    }
  }
  const concentrations = new Map();
  for (const condition of conditions) {
    const match = CONCENTRATION_NAME.exec(String(condition.name || "").trim());
    if (!match || isSolvent(match[1]) || fold(match[1]) === "") continue;
    concentrations.set(match[1].trim(), { concentration: condition.raw_value ?? "", unit: condition.unit ?? "" });
  }
  const materials = step.materials || [];
  const ordered = [...soluteNames, ...materials.filter(name => concentrations.has(name) && !soluteNames.includes(name)),
    ...[...concentrations.keys()].filter(name => !soluteNames.includes(name) && !materials.includes(name))];
  solution.solutes.rows = ordered.map(name => ({ name, concentration: concentrations.get(name)?.concentration ?? "", unit: concentrations.get(name)?.unit ?? "M" }));
  return solution;
}

/** Condition names the widget owns on this step (kept out of "Other parameters"). */
export function solutionConditionNames(solution) {
  const names = [];
  const solutes = solution.solutes.rows.map(row => row.name).filter(Boolean);
  if (solution.solvents.names.filter(Boolean).length > 1 && solution.solvents.ratio) names.push(ratioName(solution.solvents.names.filter(Boolean), solution.solvents.kind || "volume"));
  if (solutes.length > 1 && solution.solutes.ratio) names.push(ratioName(solutes, "molar"));
  for (const row of solution.solutes.rows) if (row.name && String(row.concentration ?? "").trim()) names.push(concentrationName(row.name));
  return names;
}

/** How many numbers a printed ratio contains ("4:1 (v/v)" → 2, "9/1" → 2). */
export function ratioParts(ratio) {
  return (String(ratio).match(/\d+(?:[.,]\d+)?/g) || []).length;
}

/** Write a solution into the step, replacing what `previous` wrote. Components become
 * materials; a ratio needs two components and a printed value. An unchanged value keeps
 * its citation; a new or changed one is cited when the family is saved.
 */
export function applySolution(step, solution, previous = solutionOf(step)) {
  step.materials = step.materials || [];
  step.conditions = step.conditions || [];
  const before = new Map();
  for (const name of solutionConditionNames(previous)) {
    const index = step.conditions.findIndex(condition => condition.name === name);
    if (index >= 0) before.set(name, step.conditions.splice(index, 1)[0]);
  }
  const keep = (name, raw, unit) => {
    const old = before.get(name);
    step.conditions.push({
      name, raw_value: raw, value_number: reportedNumber(raw), unit: unit || null,
      evidence: old && old.raw_value === raw ? old.evidence : [],
    });
  };
  const solvents = solution.solvents.names.map(name => String(name).trim()).filter(Boolean);
  const solutes = solution.solutes.rows.filter(row => String(row.name).trim());
  for (const name of [...solvents, ...solutes.map(row => row.name.trim())]) {
    if (!step.materials.includes(name)) step.materials.push(name);
  }
  const solventRatio = String(solution.solvents.ratio ?? "").trim();
  if (solvents.length > 1 && solventRatio) keep(ratioName(solvents, solution.solvents.kind || "volume"), solventRatio, null);
  const soluteRatio = String(solution.solutes.ratio ?? "").trim();
  if (solutes.length > 1 && soluteRatio) keep(ratioName(solutes.map(row => row.name.trim()), "molar"), soluteRatio, null);
  for (const row of solutes) {
    const value = String(row.concentration ?? "").trim();
    if (value) keep(concentrationName(row.name.trim()), value, String(row.unit ?? "").trim());
  }
}

/** Replace one component's name in the materials list, or add/remove it. */
export function renameMaterial(step, oldName, newName) {
  step.materials = step.materials || [];
  const next = String(newName ?? "").trim();
  const index = oldName ? step.materials.indexOf(oldName) : -1;
  if (index >= 0 && next && !step.materials.includes(next)) step.materials[index] = next;
  else if (index >= 0 && next !== oldName) step.materials.splice(index, 1); // emptied, or now a duplicate
  else if (index < 0 && next && !step.materials.includes(next)) step.materials.push(next);
}
