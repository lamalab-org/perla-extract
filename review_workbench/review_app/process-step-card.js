/** The editable card of one processing step in the device-family editor.
 *
 * The card edits the step object in place and reports each change; the editor syncs its
 * JSON view and re-renders on structural changes. Template parameters are read from and
 * written to `conditions`, so the card never holds values the record does not. The method
 * field accepts the paper's wording; the recognized method only chooses the fields.
 */
import {
  PROCESS_CATEGORIES, QUENCHING_TYPES, annealingParameters, antisolventOf, categoryById, categoryOf,
  findCondition, isTwoStageAnnealing, isTwoStageSpin, parametersFor, quenchingParameters, quenchingTypesUsed,
  renameAntisolvent, setCondition, setConditionUnit, setTwoStageAnnealing, setTwoStageSpin, usesSpinProgram,
} from "./process-templates.js";
import {
  SOLVENT_RATIO_KINDS, applySolution, ratioParts, renameMaterial, solutionConditionNames, solutionOf,
} from "./process-solution.js";

// Per card and step: a category chosen by hand, drying/quenching types opened but not filled
// yet, and the solution being entered. A re-render keeps them; opening a record resets them.
const chosenCategory = new Map();
const openQuenching = new Map();
const solutionDrafts = new Map();

export function resetStepCardState() {
  chosenCategory.clear();
  openQuenching.clear();
  solutionDrafts.clear();
}

const slug = text => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const lower = text => String(text || "").toLowerCase();

/** `onChange(rerender)`: rerender is true when the card's structure changed.
 * `materialList` is the id of the datalist suggesting step materials (solvents included).
 */
export function createStepCard({ element, step, idPrefix, onChange, materialList = null, categoryHint = null }) {
  step.conditions = step.conditions || [];
  step.materials = step.materials || [];
  const stateKey = `${idPrefix}|${step.step_id || ""}`;
  const category = categoryById(chosenCategory.get(stateKey)) || categoryOf(step.operation) || categoryById(categoryHint);
  const changed = (rerender = false) => onChange(rerender);
  const button = (text, click, extra = {}) => element("button", { text, properties: { type: "button" }, events: { click }, ...extra });

  // Category and method. The category only chooses suggestions and fields; the method may
  // be written as in the paper ("spin-coat HTL") and is recognized from that text.
  const categorySelect = element("select", { attributes: { "aria-label": "Step category" } }, [
    element("option", { text: "Other", properties: { value: "", selected: !category } }),
    ...PROCESS_CATEGORIES.map(item => element("option", { text: item.label, properties: { value: item.id, selected: item.id === category?.id } })),
  ]);
  categorySelect.id = `${idPrefix}-category`;
  categorySelect.addEventListener("change", () => {
    const next = categoryById(categorySelect.value);
    chosenCategory.set(stateKey, next?.id ?? "");
    // Only an empty method is filled in; the paper's wording is never replaced.
    if (next && !String(step.operation || "").trim()) step.operation = next.methods[0];
    changed(true);
  });
  const methodsId = `${idPrefix}-methods`;
  const methods = element("datalist", {}, (category?.methods || []).map(method => element("option", { properties: { value: method } })));
  methods.id = methodsId;
  const operation = element("input", { properties: { value: step.operation || "", placeholder: "Method, or the paper's wording", required: true } });
  operation.id = `${idPrefix}-operation`;
  operation.setAttribute("list", methodsId);
  operation.addEventListener("input", () => { step.operation = operation.value; changed(false); });

  // Template parameters: one value and unit per parameter; empty values are not saved.
  const parameterRow = (item) => {
    const existing = findCondition(step, item.name);
    const value = element("input", { properties: { value: existing?.raw_value ?? "", placeholder: item.placeholder || "" }, attributes: { "aria-label": item.name } });
    value.id = `${idPrefix}-param-${slug(item.name)}`;
    const unit = element("input", { className: "process-unit", properties: { value: existing ? existing.unit ?? "" : item.unit, placeholder: "unit" }, attributes: { "aria-label": `${item.name} unit (as printed)`, title: "Unit as printed in the paper" } });
    unit.id = `${idPrefix}-unit-${slug(item.name)}`;
    value.addEventListener("input", () => { setCondition(step, item.name, value.value, unit.value); changed(false); });
    unit.addEventListener("input", () => { setConditionUnit(step, item.name, unit.value); changed(false); });
    return element("label", { className: "process-parameter" }, [element("span", { text: item.name }), value, unit]);
  };
  const twoStageSpin = isTwoStageSpin(step.conditions);
  const template = category ? parametersFor(category.id, twoStageSpin, step.operation) : [];
  operation.addEventListener("change", () => {
    if (chosenCategory.get(stateKey)) return changed(true);
    const next = categoryOf(step.operation);
    const nextTemplate = next ? parametersFor(next.id, twoStageSpin, step.operation) : [];
    const names = items => items.map(item => item.name).join("|");
    if (next && (next.id !== category?.id || names(nextTemplate) !== names(template))) changed(true);
  });
  const spinToggle = category && usesSpinProgram(step.operation)
    ? button(twoStageSpin ? "Single spin stage" : "+ Second spin stage", () => { setTwoStageSpin(step, !twoStageSpin); changed(true); })
    : null;

  // Annealing inside this step, as most papers and references write it.
  let annealing = null;
  let annealingNames = [];
  if (category?.annealing) {
    const twoStage = isTwoStageAnnealing(step.conditions);
    const items = annealingParameters(twoStage);
    annealingNames = items.map(item => item.name);
    annealing = element("div", { className: "process-parameters" }, [
      element("strong", { text: "Annealing in this step" }),
      element("span", { className: "muted", text: "Use a separate + Annealing step only if the paper describes annealing as its own step." }),
      ...items.map(parameterRow),
      button(twoStage ? "Single annealing stage" : "+ Second annealing stage", () => { setTwoStageAnnealing(step, !twoStage); changed(true); }),
    ]);
  }

  // Solution: components (as written, in the paper's order), ratios as printed, concentrations.
  let solution = null;
  if (category?.solution) {
    solution = solutionDrafts.get(stateKey);
    if (!solution) {
      solution = solutionOf(step);
      solution.applied = structuredClone({ solvents: solution.solvents, solutes: solution.solutes });
      solutionDrafts.set(stateKey, solution);
    }
  }
  let solutionBox = null;
  if (solution) {
    const write = () => {
      applySolution(step, solution, solution.applied);
      solution.applied = structuredClone({ solvents: solution.solvents, solutes: solution.solutes });
    };
    const nameInput = (value, id, label, list, onName) => {
      const input = element("input", { properties: { value, placeholder: `${label} as written` }, attributes: { "aria-label": label } });
      input.id = id;
      input.setAttribute("list", list);
      input.addEventListener("input", () => { onName(input.value); write(); changed(false); });
      input.addEventListener("change", () => changed(true));
      return input;
    };
    const ratioField = (part, key, count, label) => {
      const ratio = element("input", { properties: { value: part.ratio || "", placeholder: key === "solvents" ? "as printed, e.g. 4:1" : "as printed, e.g. 1:1:0.2" }, attributes: { "aria-label": `${label} as printed` } });
      ratio.id = `${idPrefix}-${key}-ratio`;
      const check = element("span", { className: "process-step-warning" });
      const verify = () => {
        const parts = ratioParts(part.ratio || "");
        check.textContent = count() > 1 && part.ratio && parts !== count() ? `The ratio has ${parts} numbers for ${count()} components.` : "";
      };
      ratio.addEventListener("input", () => { part.ratio = ratio.value; write(); verify(); changed(false); });
      verify();
      return [ratio, check];
    };
    const solvents = solution.solvents;
    const solventRows = solvents.names.map((name, index) => element("div", { className: "process-material" }, [
      nameInput(name, `${idPrefix}-solvents-${index}`, `Solvent ${index + 1}`, "solvent-suggestions", (value) => {
        renameMaterial(step, solvents.names[index], value);
        solvents.names[index] = value.trim();
      }),
      button("×", () => { renameMaterial(step, solvents.names[index], ""); solvents.names.splice(index, 1); write(); changed(true); },
        { className: "danger-link", attributes: { "aria-label": `Remove solvent ${index + 1}` } }),
    ]));
    const [solventRatio, solventCheck] = ratioField(solvents, "solvents", () => solvents.names.filter(Boolean).length, "Solvent ratio");
    const kind = element("select", { attributes: { "aria-label": "Solvent ratio type" } }, SOLVENT_RATIO_KINDS.map(item => element("option", {
      text: `${item} ratio`, properties: { value: item, selected: (solvents.kind || "volume") === item },
    })));
    kind.id = `${idPrefix}-solvents-kind`;
    kind.addEventListener("change", () => { solvents.kind = kind.value; write(); changed(true); });

    const solutes = solution.solutes;
    const soluteRows = solutes.rows.map((row, index) => {
      const concentration = element("input", { properties: { value: row.concentration || "", placeholder: "concentration" }, attributes: { "aria-label": `Solute ${index + 1} concentration as printed` } });
      concentration.id = `${idPrefix}-solutes-${index}-concentration`;
      concentration.addEventListener("input", () => { row.concentration = concentration.value; write(); changed(false); });
      const unit = element("input", { className: "process-unit", properties: { value: row.unit ?? "M", placeholder: "unit" }, attributes: { "aria-label": `Solute ${index + 1} concentration unit` } });
      unit.id = `${idPrefix}-solutes-${index}-unit`;
      unit.addEventListener("input", () => { row.unit = unit.value; write(); changed(false); });
      return element("div", { className: "process-material process-solute" }, [
        nameInput(row.name, `${idPrefix}-solutes-${index}`, `Solute ${index + 1}`, "solute-suggestions", (value) => {
          renameMaterial(step, row.name, value);
          row.name = value.trim();
        }),
        concentration, unit,
        button("×", () => { renameMaterial(step, row.name, ""); solutes.rows.splice(index, 1); write(); changed(true); },
          { className: "danger-link", attributes: { "aria-label": `Remove solute ${index + 1}` } }),
      ]);
    });
    const [soluteRatio, soluteCheck] = ratioField(solutes, "solutes", () => solutes.rows.filter(row => row.name).length, "Solute molar ratio");

    const saved = solutionConditionNames(solution.applied).map((name) => {
      const condition = step.conditions.find(item => item.name === name);
      return condition ? `${name} = ${condition.raw_value}${condition.unit ? ` ${condition.unit}` : ""}` : null;
    }).filter(Boolean);
    const count = solvents.names.length + solutes.rows.length;
    solutionBox = element("details", { className: "process-solution", properties: { open: count > 0 } }, [
      element("summary", { text: `Solution${count ? ` (${count} component${count === 1 ? "" : "s"})` : ""}` }),
      element("span", { className: "muted", text: "Components in the paper's order, added to Materials. Ratios and concentrations exactly as printed; total volume is not recorded." }),
      element("div", { className: "process-solution-group" }, [
        element("strong", { text: "Solvents" }),
        ...solventRows,
        button("+ Solvent", () => { solvents.names.push(""); changed(true); }),
        solvents.names.length > 1 ? element("label", { className: "process-parameter" }, [element("span", { text: "Ratio" }), solventRatio, kind]) : null,
        solventCheck,
      ].filter(Boolean)),
      element("div", { className: "process-solution-group" }, [
        element("strong", { text: "Solutes" }),
        ...soluteRows,
        button("+ Solute", () => { solutes.rows.push({ name: "", concentration: "", unit: "M" }); changed(true); }),
        solutes.rows.length > 1 ? element("label", { className: "process-parameter" }, [element("span", { text: "Molar ratio" }), soluteRatio, element("span", { text: "molar" })]) : null,
        soluteCheck,
      ].filter(Boolean)),
      element("span", { className: "muted", text: saved.length ? `Saved as: ${saved.join("; ")}` : "Ratios need two components and a printed value; concentrations are saved as \"<solute> concentration\"." }),
    ]);
  }

  // Drying / quenching: one section per type in use; antisolvent values carry its name.
  let quenchingBox = null;
  let quenchingNames = [];
  if (category?.quenching) {
    quenchingNames = QUENCHING_TYPES.flatMap(type => quenchingParameters(type, step).map(item => item.name));
    const shown = new Set([...quenchingTypesUsed(step), ...(openQuenching.get(stateKey) || [])]);
    openQuenching.set(stateKey, shown);
    const sections = QUENCHING_TYPES.filter(type => shown.has(type.id)).map((type) => {
      const items = quenchingParameters(type, step);
      let antisolvent = null;
      if (type.prefixed) {
        const current = antisolventOf(step);
        antisolvent = element("input", { properties: { value: current === "antisolvent" ? "" : current, placeholder: "antisolvent as written, e.g. chlorobenzene" }, attributes: { "aria-label": "Antisolvent" } });
        antisolvent.id = `${idPrefix}-antisolvent`;
        antisolvent.setAttribute("list", "solvent-suggestions");
        antisolvent.addEventListener("change", () => {
          const before = antisolventOf(step);
          renameAntisolvent(step, antisolvent.value);
          renameMaterial(step, before === "antisolvent" ? "" : before, antisolvent.value);
          changed(true);
        });
      }
      return element("div", { className: "process-quenching-type" }, [
        element("div", { className: "process-quenching-heading" }, [
          element("strong", { text: type.label }),
          button("Remove", () => {
            const names = new Set(items.map(item => lower(item.name)));
            step.conditions = step.conditions.filter(condition => !names.has(lower(condition.name)));
            shown.delete(type.id);
            changed(true);
          }, { className: "danger-link", attributes: { "aria-label": `Remove ${type.label}` } }),
        ]),
        antisolvent ? element("label", { className: "process-parameter" }, [element("span", { text: "antisolvent" }), antisolvent]) : null,
        ...items.map(parameterRow),
      ].filter(Boolean));
    });
    const adders = QUENCHING_TYPES.filter(type => !shown.has(type.id)).map(type => button(`+ ${type.label}`, () => { shown.add(type.id); changed(true); }));
    quenchingBox = element("details", { className: "process-quenching", properties: { open: shown.size > 0 } }, [
      element("summary", { text: `Drying / quenching${shown.size ? ` (${shown.size})` : ""}` }),
      element("span", { className: "muted", text: "Antisolvent values are named by the antisolvent (e.g. chlorobenzene volume); it is also added to Materials." }),
      ...sections,
      adders.length ? element("div", { className: "process-presets" }, adders) : null,
    ].filter(Boolean));
  }

  // Materials, as written in the paper; one entry per chemical.
  const materialRows = step.materials.map((material, index) => {
    const input = element("input", { properties: { value: material, placeholder: "As written, e.g. DMSO" }, attributes: { "aria-label": `Material ${index + 1}` } });
    input.id = `${idPrefix}-material-${index}`;
    if (materialList) input.setAttribute("list", materialList);
    input.addEventListener("input", () => { step.materials[index] = input.value; changed(false); });
    return element("div", { className: "process-material" }, [
      input,
      button("×", () => { step.materials.splice(index, 1); changed(true); }, { className: "danger-link", attributes: { "aria-label": `Remove material ${index + 1}` } }),
    ]);
  });
  const materials = element("div", { className: "process-materials" }, [
    element("strong", { text: "Materials" }),
    element("span", { className: "muted", text: "Every chemical named for this step, as written." }),
    ...materialRows,
    button("+ Material", () => { step.materials.push(""); changed(true); }),
  ]);

  // Everything outside the sections stays visible and editable.
  const owned = new Set([...template.map(item => item.name), ...annealingNames, ...quenchingNames,
    ...(solution ? solutionConditionNames(solution.applied) : [])].map(lower));
  const others = step.conditions.filter(condition => !owned.has(lower(condition.name)));
  const otherRows = others.map((condition, index) => {
    const field = (key, placeholder) => {
      const input = element("input", { properties: { value: condition[key] ?? "", placeholder }, attributes: { "aria-label": `Other parameter ${index + 1} ${placeholder}` } });
      input.id = `${idPrefix}-other-${index}-${key}`;
      input.addEventListener("input", () => {
        if (key === "raw_value") {
          condition.raw_value = input.value;
          condition.evidence = []; // the new value is cited by the save quote
          const number = input.value.trim().replace("−", "-");
          condition.value_number = /^[+-]?\d+(?:[.,]\d+)?$/.test(number) ? Number(number.replace(",", ".")) : null;
        } else condition[key] = key === "unit" ? (input.value.trim() || null) : input.value;
        changed(false);
      });
      return input;
    };
    return element("div", { className: "process-parameter process-other" }, [
      field("name", "name"), field("raw_value", "value as printed"), field("unit", "unit"),
      button("×", () => { step.conditions.splice(step.conditions.indexOf(condition), 1); changed(true); },
        { className: "danger-link", attributes: { "aria-label": `Remove other parameter ${index + 1}` } }),
    ]);
  });
  const otherSection = element("details", { className: "process-others", properties: { open: others.length > 0 } }, [
    element("summary", { text: `Other parameters (${others.length})` }),
    ...otherRows,
    button("+ Other parameter", () => { step.conditions.push({ name: "", raw_value: "", value_number: null, unit: null, evidence: [] }); changed(true); }),
  ]);

  return element("div", { className: "process-step-card" }, [
    element("div", { className: "process-step-head" }, [
      element("label", {}, [element("span", { text: "Category" }), categorySelect]),
      element("label", {}, [element("span", { text: "Method" }), operation, methods]),
    ]),
    solutionBox,
    materials,
    template.length || spinToggle ? element("div", { className: "process-parameters" }, [
      element("strong", { text: "Parameters" }),
      element("span", { className: "muted", text: "Values exactly as printed; leave unreported ones empty." }),
      ...template.map(parameterRow),
      ...(spinToggle ? [spinToggle] : []),
    ]) : null,
    quenchingBox,
    annealing,
    otherSection,
  ].filter(Boolean));
}
