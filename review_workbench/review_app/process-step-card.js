/** The editable card of one processing step in the device-family editor.
 *
 * The card edits the step object in place and reports each change; the editor syncs its
 * JSON view and re-renders on structural changes. Template parameters are read from and
 * written to `conditions`, so the card never holds values the record does not.
 */
import {
  PROCESS_CATEGORIES, QUENCHING_TYPES, categoryById, categoryOf, findCondition, isTwoStageSpin, quenchingTypesUsed,
  materialInOperation, parametersFor, setCondition, setConditionUnit, setTwoStageSpin, usesSpinProgram,
} from "./process-templates.js";

// Drying/quenching types a reviewer opened but has not filled yet, per card; a re-render
// must not close them. Filled types are always shown because their values are in the record.
const openQuenching = new Map();

const slug = text => String(text).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** `onChange(rerender)`: rerender is true when the card's structure changed.
 * `materialList` is the id of the datalist suggesting step materials (solvents included).
 */
export function createStepCard({ element, step, idPrefix, onChange, materialList = null, layers = [], categoryHint = null }) {
  step.conditions = step.conditions || [];
  step.materials = step.materials || [];
  const category = categoryOf(step.operation) || categoryById(categoryHint);
  const changed = (rerender = false) => onChange(rerender);

  // Category and method: the category only chooses suggestions and parameters.
  const categorySelect = element("select", { attributes: { "aria-label": "Step category" } }, [
    element("option", { text: "Other", properties: { value: "", selected: !category } }),
    ...PROCESS_CATEGORIES.map(item => element("option", { text: item.label, properties: { value: item.id, selected: item.id === category?.id } })),
  ]);
  categorySelect.id = `${idPrefix}-category`;
  categorySelect.addEventListener("change", () => {
    const next = categoryById(categorySelect.value);
    step.operation = next ? next.methods[0] : "";
    changed(true);
  });
  const methodsId = `${idPrefix}-methods`;
  const methods = element("datalist", {}, (category?.methods || []).map(method => element("option", { properties: { value: method } })));
  methods.id = methodsId;
  const operation = element("input", { properties: { value: step.operation || "", placeholder: "Method, e.g. spin coating", required: true } });
  operation.id = `${idPrefix}-operation`;
  operation.setAttribute("list", methodsId);
  const warning = element("p", { className: "process-step-warning" });
  const warn = () => {
    const material = materialInOperation(step.operation, layers);
    warning.textContent = material ? `Write only the method here; "${material}" belongs in the target layer.` : "";
    warning.hidden = !material;
  };
  operation.addEventListener("input", () => { step.operation = operation.value; warn(); changed(false); });
  operation.addEventListener("change", () => {
    // Another category or method changes the parameter list.
    const next = categoryOf(step.operation);
    const nextTemplate = next ? parametersFor(next.id, isTwoStageSpin(step.conditions), step.operation) : [];
    const names = items => items.map(item => item.name).join("|");
    if (next && (next.id !== category?.id || names(nextTemplate) !== names(template))) changed(true);
  });
  warn();

  // Materials, as written in the paper; one entry per chemical.
  const materialRows = step.materials.map((material, index) => {
    const input = element("input", { properties: { value: material, placeholder: "As written, e.g. DMSO" }, attributes: { "aria-label": `Material ${index + 1}` } });
    input.id = `${idPrefix}-material-${index}`;
    if (materialList) input.setAttribute("list", materialList);
    input.addEventListener("input", () => { step.materials[index] = input.value; changed(false); });
    return element("div", { className: "process-material" }, [
      input,
      element("button", {
        text: "×", className: "danger-link", properties: { type: "button" }, attributes: { "aria-label": `Remove material ${index + 1}` },
        events: { click: () => { step.materials.splice(index, 1); changed(true); } },
      }),
    ]);
  });
  const materials = element("div", { className: "process-materials" }, [
    element("strong", { text: "Materials" }),
    element("span", { className: "muted", text: "Every chemical named for this step, exactly as written (solutes, solvents, antisolvent)." }),
    ...materialRows,
    element("button", {
      text: "+ Material", properties: { type: "button" },
      events: { click: () => { step.materials.push(""); changed(true); } },
    }),
  ]);

  // Template parameters: one value and unit per parameter; empty values are not saved.
  const twoStage = isTwoStageSpin(step.conditions);
  const template = category ? parametersFor(category.id, twoStage, step.operation) : [];
  const quenchingNames = category?.quenching ? QUENCHING_TYPES.flatMap(type => type.parameters.map(item => item.name)) : [];
  const templateKeys = new Set([...template.map(item => item.name), ...quenchingNames].map(name => name.toLowerCase()));
  const parameterRow = (item) => {
    const existing = findCondition(step, item.name);
    const id = `${idPrefix}-param-${slug(item.name)}`;
    const value = element("input", { properties: { value: existing?.raw_value ?? "", placeholder: item.placeholder || "" }, attributes: { "aria-label": item.name } });
    value.id = id;
    const unit = element("input", { className: "process-unit", properties: { value: existing ? existing.unit ?? "" : item.unit, placeholder: "unit" }, attributes: { "aria-label": `${item.name} unit (as printed)`, title: "Unit as printed in the paper" } });
    unit.id = `${idPrefix}-unit-${slug(item.name)}`;
    value.addEventListener("input", () => { setCondition(step, item.name, value.value, unit.value); changed(false); });
    unit.addEventListener("input", () => { setConditionUnit(step, item.name, unit.value); changed(false); });
    return element("label", { className: "process-parameter" }, [element("span", { text: item.name }), value, unit]);
  };
  const parameterRows = template.map(parameterRow);

  // Drying / quenching box: one section per type in use; the medium itself is a material.
  let quenchingBox = null;
  if (category?.quenching) {
    const shown = new Set([...quenchingTypesUsed(step.conditions), ...(openQuenching.get(idPrefix) || [])]);
    openQuenching.set(idPrefix, shown);
    const sections = QUENCHING_TYPES.filter(type => shown.has(type.id)).map(type => element("div", { className: "process-quenching-type" }, [
      element("div", { className: "process-quenching-heading" }, [
        element("strong", { text: type.label }),
        element("button", {
          text: "Remove", className: "danger-link", properties: { type: "button" }, attributes: { "aria-label": `Remove ${type.label}` },
          events: { click: () => {
            const names = new Set(type.parameters.map(item => item.name.toLowerCase()));
            step.conditions = step.conditions.filter(condition => !names.has(String(condition.name || "").toLowerCase()));
            shown.delete(type.id);
            changed(true);
          } },
        }),
      ]),
      ...type.parameters.map(parameterRow),
    ]));
    const adders = QUENCHING_TYPES.filter(type => !shown.has(type.id)).map(type => element("button", {
      text: `+ ${type.label}`, properties: { type: "button" },
      events: { click: () => { shown.add(type.id); changed(true); } },
    }));
    quenchingBox = element("details", { className: "process-quenching", properties: { open: shown.size > 0 } }, [
      element("summary", { text: `Drying / quenching${shown.size ? ` (${[...shown].length})` : ""}` }),
      element("span", { className: "muted", text: "Put the antisolvent or gas itself under Materials." }),
      ...sections,
      adders.length ? element("div", { className: "process-presets" }, adders) : null,
    ].filter(Boolean));
  }
  const spinToggle = category && usesSpinProgram(step.operation) ? element("button", {
    text: twoStage ? "Single spin stage" : "+ Second spin stage", properties: { type: "button" },
    events: { click: () => { setTwoStageSpin(step, !twoStage); changed(true); } },
  }) : null;

  // Everything outside the template stays visible and editable.
  const others = step.conditions.filter(condition => !templateKeys.has(String(condition.name || "").toLowerCase()));
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
      element("button", {
        text: "×", className: "danger-link", properties: { type: "button" }, attributes: { "aria-label": `Remove other parameter ${index + 1}` },
        events: { click: () => { step.conditions.splice(step.conditions.indexOf(condition), 1); changed(true); } },
      }),
    ]);
  });
  const otherSection = element("details", { className: "process-others", properties: { open: others.length > 0 } }, [
    element("summary", { text: `Other parameters (${others.length})` }),
    ...otherRows,
    element("button", {
      text: "+ Other parameter", properties: { type: "button" },
      events: { click: () => { step.conditions.push({ name: "", raw_value: "", value_number: null, unit: null, evidence: [] }); changed(true); } },
    }),
  ]);

  return element("div", { className: "process-step-card" }, [
    element("div", { className: "process-step-head" }, [
      element("label", {}, [element("span", { text: "Category" }), categorySelect]),
      element("label", {}, [element("span", { text: "Method" }), operation, methods]),
    ]),
    warning,
    materials,
    template.length ? element("div", { className: "process-parameters" }, [
      element("strong", { text: "Parameters" }),
      element("span", { className: "muted", text: "Values exactly as printed; leave unreported ones empty." }),
      ...parameterRows,
      ...(spinToggle ? [spinToggle] : []),
    ]) : null,
    quenchingBox,
    otherSection,
  ].filter(Boolean));
}
