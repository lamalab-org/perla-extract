import { relationshipChoices } from "./review-state.js";
import { createStepCard, resetStepCardState } from "./process-step-card.js";
import { STEP_PRESETS, copySteps, splitPerTarget } from "./process-templates.js";
import { SUGGESTION_LISTS } from "./material-suggestions.js";

/** One record draft shared by the field controls and the raw JSON editing mode.
 * Invalid JSON never replaces the last valid draft; callers must surface the error.
 */
export function createDraftState(initial) {
  let value = structuredClone(initial);
  return {
    get value() { return value; },
    snapshot: () => structuredClone(value),
    replace(path, next) {
      const parent = path.slice(0, -1).reduce((node, key) => node[key], value);
      parent[path.at(-1)] = structuredClone(next);
    },
    replaceJson(text) {
      const parsed = JSON.parse(text);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("A record must be a JSON object.");
      value = parsed;
    },
    update(path, raw, original, schema = {}) {
      const choices = schema.anyOf || schema.oneOf || [schema];
      const type = choices.find(choice => choice.type && choice.type !== "null")?.type;
      const nullable = original === null || choices.some(choice => choice.type === "null");
      let next = raw;
      if (raw === "" && nullable) next = null;
      else if (type === "number" || type === "integer" || typeof original === "number") next = raw === "" ? null : Number(raw);
      else if (type === "boolean" || typeof original === "boolean") next = raw === "true";
      const parent = path.slice(0, -1).reduce((node, key) => node[key], value);
      parent[path.at(-1)] = next;
    },
    reorder(path, index, target) {
      const items = path.reduce((node, key) => node?.[key], value);
      if (!Array.isArray(items) || index < 0 || index >= items.length || target < 0 || target >= items.length || index === target) return false;
      const [item] = items.splice(index, 1);
      items.splice(target, 0, item);
      items.forEach((entry, position) => {
        if (entry && typeof entry === "object" && Object.hasOwn(entry, "sequence")) entry.sequence = position + 1;
      });
      return true;
    },
  };
}

/** Own the record dialog and draft, with explicit navigation and source callbacks.
 * context supplies read-only current schema/truth; saving belongs to review-session.
 */
export function createRecordEditor({
  document, element, context, collections: COLLECTIONS, humanLabel, singularCollection,
  recordKey, recordReferences, entityTitle, linkedRecordSummary, reviewReferencedRecord, focusCitation,
}) {
  const $ = id => document.getElementById(id);
  const pointerPart = value => String(value).replaceAll("~", "~0").replaceAll("/", "~1");
  let draft = null;
  let edit = null;
  // Which collapsible sections the reviewer opened or closed, keyed by field path.
  // Re-rendering after an added item must not collapse work in progress.
  let groups = new Map();
  let focusPath = null;
  let initialJson = null;
  const RECORD_ID_PREFIXES = {
    device_families: "family", individual_devices: "device",
    performance_observations: "observation", population_statistics: "population",
    stability_tests: "stability",
  };
  // Nested lists whose items carry their own identifier.
  const ITEM_IDENTIFIERS = {
    layers: ["layer_id", "layer"], absorbers: ["absorber_id", "absorber"],
    processing_steps: ["step_id", "step"], checkpoints: ["checkpoint_id", "checkpoint"],
  };
  const MATERIAL_FORMS = [
    "self_assembled_monolayer", "monolayer", "compact_layer",
    "mesoporous_layer", "nanostructured_layer", "bulk_heterojunction", "other",
    "not_reported",
  ];
  function resolvedSchema(node, schema = context().studySchema) {
    let result = node || {};
    const visited = new Set();
    while (result.$ref) {
      if (!schema?.$defs || visited.has(result.$ref)) return {};
      visited.add(result.$ref);
      result = schema.$defs[result.$ref.split("/").at(-1)] || {};
    }
    return result;
  }

  function draftFromSchema(node, fieldName = "") {
    const schema = resolvedSchema(node);
    if (Object.hasOwn(schema, "default")) return structuredClone(schema.default);
    if (fieldName === "evidence") return [];
    const choices = schema.anyOf || schema.oneOf;
    if (choices) {
      if (choices.some((choice) => resolvedSchema(choice).type === "null")) return null;
      return draftFromSchema(choices[0], fieldName);
    }
    if (schema.enum) return schema.enum.includes("not_reported") ? "not_reported" : schema.enum[0];
    if (schema.type === "object" || schema.properties) {
      return Object.fromEntries(Object.entries(schema.properties || {}).map(([name, child]) => [name, draftFromSchema(child, name)]));
    }
    if (schema.type === "array") {
      return Array.from({ length: schema.minItems || 0 }, () => draftFromSchema(schema.items));
    }
    if (schema.type === "integer" || schema.type === "number") return schema.minimum ?? 0;
    if (schema.type === "boolean") return false;
    return "";
  }

  // Nullable referenced objects still have editable fields. Unwrap only a single
  // non-null choice; an ambiguous union must not be guessed into another type.
  function concreteSchema(node) {
    const schema = resolvedSchema(node);
    const choices = (schema.anyOf || schema.oneOf || []).map(choice => resolvedSchema(choice));
    const values = choices.filter(choice => choice.type !== "null");
    if (values.length !== 1) return schema;
    return { ...schema, ...values[0], anyOf: undefined, oneOf: undefined };
  }

  function newNestedValue(node, fieldName) {
    const schema = { ...concreteSchema(node) };
    if (schema.default == null) delete schema.default;
    // The review session attaches the citation chosen at save time to empty
    // evidence arrays, without replacing existing nested citations.
    return draftFromSchema(schema, fieldName);
  }

  function replaceNestedValue(path, value) {
    draft.replace(path, value);
    $("record-json").value = JSON.stringify(edit.value, null, 2);
    renderStructuredEditor();
  }

  function replaceArrayItems(path, items) {
    const next = structuredClone(items);
    if (next.every(item => item && typeof item === "object" && Object.hasOwn(item, "sequence"))) {
      next.forEach((item, index) => { item.sequence = index + 1; });
    }
    replaceNestedValue(path, next);
  }

  function usedIdentifiers(...values) {
    const used = new Set();
    const visit = (node) => {
      if (Array.isArray(node)) node.forEach(visit);
      else if (node && typeof node === "object") {
        for (const [key, item] of Object.entries(node)) {
          if (key.endsWith("_id") && typeof item === "string" && item) used.add(item);
          else visit(item);
        }
      }
    };
    values.forEach(visit);
    return used;
  }

  function freshIdentifier(prefix, used) {
    let suffix = 1;
    while (used.has(`${prefix}-${suffix}`)) suffix += 1;
    return `${prefix}-${suffix}`;
  }

  function newRecordDraft(kind) {
    const value = draftFromSchema(context().studySchema.properties[kind].items);
    const identifier = context().bundle?.summary?.record_identifiers?.[kind];
    if (identifier && value && typeof value === "object" && Object.hasOwn(value, identifier) && !value[identifier]) {
      value[identifier] = freshIdentifier(RECORD_ID_PREFIXES[kind] || kind, usedIdentifiers(context().bundle.ground_truth));
    }
    return value;
  }

  /** A new list item; items that carry their own ID get a readable unique one. */
  function newArrayItem(path, itemSchema, label) {
    const item = newNestedValue(itemSchema, label);
    const own = ITEM_IDENTIFIERS[path.at(-1)];
    if (own && item && typeof item === "object" && Object.hasOwn(item, own[0]) && !item[own[0]]) {
      item[own[0]] = freshIdentifier(own[1], usedIdentifiers(context().bundle?.ground_truth, edit.value));
    }
    return item;
  }

  function expand(...paths) {
    for (const path of paths) groups.set(JSON.stringify(path), true);
  }

  /** A collapsible section that keeps its open/closed state across re-renders. */
  function rememberedDetails(path, fallback, className, children) {
    const key = JSON.stringify(path);
    const node = element("details", { className, properties: { open: groups.has(key) ? groups.get(key) : fallback } }, children);
    node.addEventListener("toggle", (event) => { groups.set(key, Boolean(event.currentTarget.open)); });
    return node;
  }

  const fieldIdPart = part => encodeURIComponent(String(part)).replaceAll("-", "%2D");

  function copiedRecordDraft(kind, item) {
    const draft = structuredClone(item);
    const identifier = context().bundle.summary.record_identifiers[kind];
    const used = new Set(context().bundle.ground_truth[kind].map((record) => String(record[identifier])));
    const stem = `${draft[identifier] || kind}-review-copy`;
    let candidate = stem;
    let suffix = 2;
    while (used.has(candidate)) candidate = `${stem}-${suffix++}`;
    draft[identifier] = candidate;
    return draft;
  }

  function openRecord(kind, index = null, template = null, intent = index == null ? "add" : "edit") {
    const source = template || (index == null ? newRecordDraft(kind) : context().bundle.ground_truth[kind][index]);
    const item = index == null && template ? copiedRecordDraft(kind, source) : structuredClone(source);
    const entry = index == null ? null : { kind, index, item, key: recordKey(kind, item, index) };
    const references = entry ? recordReferences(entry) : [];
    const resolving = intent === "resolve-links";
    draft = createDraftState(item);
    edit = { kind, index, get value() { return draft.value; }, intent, references, baseRevision: context().bundle.revision };
    groups = new Map();
    focusPath = null;
    resetStepCardState();
    $("record-kind").textContent = COLLECTIONS[kind];
    $("record-title").textContent = index == null
      ? `Add missing ${singularCollection(kind).toLowerCase()}`
      : entityTitle(kind, item, index);
    $("record-dialog-help").textContent = resolving
      ? "Before changing this record type, correct the records that link to it. The original record remains unchanged until you return and save the new type."
      : intent === "copy"
      ? "This is a new record copied from the current one as a structurally valid starting point. The original stays unchanged. Check every copied value and its evidence before saving."
      : intent === "remove"
        ? "Remove this record only if it should not exist in the ground truth. Linked records are never deleted automatically."
        : index == null
          ? "Fill the missing record from the paper. The draft shape comes from the current schema; blank required fields must be completed before it can be saved."
          : "Change only the fields that are wrong. The existing source evidence stays attached unless you replace it below.";
    $("record-json").value = JSON.stringify(item, null, 2);
    const adding = index == null;
    $("evidence-search-label").textContent = adding ? "Choose source evidence for this new record" : "Source evidence for this correction";
    $("mutation-note-label").textContent = intent === "remove" ? "Reason for removal" : adding ? "Review note" : "Reason for correction";
    $("mutation-note-help").textContent = intent === "remove" ? "Required: explain why this record is not supported by the paper." : adding ? "Optional: explain why this record was missing." : "Explain what the extraction got wrong.";
    $("mutation-note").placeholder = intent === "remove" ? "Why should this record not exist?" : adding ? "Why was this record added?" : "What did the extraction get wrong?";
    $("save-record").textContent = adding ? "Add missing record" : "Save my correction";
    $("save-record").hidden = intent === "remove" || resolving;
    $("mutation-note").closest("label").hidden = resolving;
    const dialog = $("record-dialog");
    // Non-modal: the editor covers the review column while the paper stays usable.
    if (!dialog.open) dialog.show();
    initialJson = JSON.stringify(item);
    let editorError = null;
    try {
      renderStructuredEditor();
      setRecordEditorMode("fields", false);
    } catch (error) {
      editorError = error;
      setRecordEditorMode("json", false);
    }
    const citation = item.evidence?.[0];
    $("citation-block").value = citation?.block_id || "";
    $("citation-quote").value = citation?.quote || "";
    $("mutation-note").value = "";
    document.querySelector(".evidence-search").open = adding || !citation;
    $("record-save-help").replaceChildren(
      document.createTextNode("Your change is saved with your name. Administrators can retrieve it. If no later edit changes the same record, you can undo it from "),
      element("strong", { text: "My work & undo" }),
      document.createTextNode("."),
    );
    $("remove-record").hidden = intent !== "remove";
    $("remove-record").disabled = references.length > 0;
    $("remove-record").textContent = references.length ? "Resolve linked records first" : "Remove extra record";
    const dependency = $("record-dependencies");
    dependency.hidden = (!resolving && intent !== "remove") || references.length === 0;
    dependency.replaceChildren();
    if ((resolving || intent === "remove") && references.length) {
      const type = singularCollection(kind).toLowerCase();
      dependency.append(
        element("strong", { text: `${references.length} linked record${references.length === 1 ? " must" : "s must"} be handled first` }),
        element("p", { text: `This ${type} is used by ${linkedRecordSummary(references)}. ${resolving ? "Changing its type" : "Removing it"} now would leave ${references.length === 1 ? "that record" : "those records"} pointing to something that no longer exists.` }),
        element("p", { text: "Open each linked record below and change its family or device link. If the linked record is also unsupported by the paper, remove it instead." }),
        element("ul", {}, references.map((reference) => element("li", {}, [element("button", {
          text: `Open ${singularCollection(reference.kind).toLowerCase()} · ${entityTitle(reference.kind, reference.item, reference.index)}`,
          properties: { type: "button" },
          events: { click: () => reviewReferencedRecord(reference) },
        })]))),
      );
    }
    $("evidence-results").replaceChildren();
    $("dialog-status").textContent = editorError
      ? `The field layout could not be displayed (${editorError.message}). The complete record is open as JSON so your correction is not blocked.`
      : "";
    dialog.querySelector(".dialog-card")?.scrollTo({ top: 0 });
    if (intent === "remove") $("mutation-note").focus();
    if (citation) focusCitation(citation, false, false);
  }

  function recordFieldPath(path) {
    const recordIndex = edit.index == null ? "-" : edit.index;
    return `/${[edit.kind, recordIndex, ...path].map(pointerPart).join("/")}`;
  }

  function setRecordEditorMode(mode, syncFromJson = true) {
    if (mode === "fields" && syncFromJson && !$("json-editor-panel").hidden) {
      try {
        draft.replaceJson($("record-json").value);
        renderStructuredEditor();
        $("dialog-status").textContent = "";
      } catch (error) {
        $("dialog-status").textContent = `Raw JSON is not valid: ${error.message}`;
        return false;
      }
    }
    const fields = mode === "fields";
    $("fields-editor-panel").hidden = !fields;
    $("json-editor-panel").hidden = fields;
    $("show-fields-editor").classList.toggle("active", fields);
    $("show-fields-editor").setAttribute("aria-pressed", String(fields));
    $("show-json-editor").classList.toggle("active", !fields);
    $("show-json-editor").setAttribute("aria-pressed", String(!fields));
    return true;
  }

  function updateEditValue(path, rawValue, original) {
    draft.update(path, rawValue, original, recordSchemaNode(edit.kind, path));
    $("record-json").value = JSON.stringify(edit.value, null, 2);
  }

  function recordSchemaNode(kind, path = []) {
    let node = resolvedSchema(context().studySchema?.properties?.[kind]?.items);
    for (const part of path) {
      node = concreteSchema(node);
      node = resolvedSchema((part === "items" || typeof part === "number") ? node?.items : node?.properties?.[part]);
    }
    return node;
  }

  function layerRoleOptions() {
    const choices = recordSchemaNode("device_families", ["layers", "items", "role"]).enum;
    if (Array.isArray(choices) && choices.length) return choices;
    const rolesInStudy = context().bundle?.ground_truth?.device_families
      ?.flatMap((family) => family.layers || [])
      .map((layer) => layer.role)
      .filter(Boolean) || [];
    return [...new Set([...rolesInStudy, "not_reported"])];
  }

  function syncLayerOrder() {
    edit.value.layers.forEach((layer, index) => { layer.sequence = index + 1; });
    $("record-json").value = JSON.stringify(edit.value, null, 2);
  }

  function moveLayer(index, offset) {
    const target = index + offset;
    const layers = edit.value.layers;
    if (target < 0 || target >= layers.length) return;
    [layers[index], layers[target]] = [layers[target], layers[index]];
    syncLayerOrder();
    renderStructuredEditor();
  }

  function removeLayer(index) {
    const [removed] = edit.value.layers.splice(index, 1);
    let detachedAbsorbers = 0;
    let detachedSteps = 0;
    for (const absorber of edit.value.absorbers || []) {
      if (absorber.layer_id === removed.layer_id) {
        absorber.layer_id = null;
        detachedAbsorbers += 1;
      }
    }
    for (const step of edit.value.processing_steps || []) {
      const targets = step.target_layer_ids || [];
      const remaining = targets.filter((layerId) => layerId !== removed.layer_id);
      if (remaining.length !== targets.length) {
        step.target_layer_ids = remaining;
        detachedSteps += 1;
      }
    }
    syncLayerOrder();
    renderStructuredEditor();
    if (detachedAbsorbers || detachedSteps) {
      $("dialog-status").textContent = `Removed the layer and cleared its link from ${detachedAbsorbers} absorber${detachedAbsorbers === 1 ? "" : "s"} and ${detachedSteps} processing step${detachedSteps === 1 ? "" : "s"}. Their scientific content is preserved.`;
    }
  }

  function addLayer() {
    const layers = edit.value.layers;
    const used = new Set(layers.map((layer) => layer.layer_id));
    let suffix = layers.length + 1;
    while (used.has(`review-layer-${suffix}`)) suffix += 1;
    layers.push({
      layer_id: `review-layer-${suffix}`,
      sequence: layers.length + 1,
      role: "not_reported",
      material: "",
      constituents: [],
      material_form_raw: null,
      material_form: "not_reported",
      reported_properties: [],
      evidence: [],
    });
    syncLayerOrder();
    renderStructuredEditor();
    document.querySelector(".stack-layer:last-of-type input")?.focus();
  }

  function renderLayerAdvanced(layer, index) {
    const fields = ["constituents", "material_form_raw", "material_form", "reported_properties"]
      .map((key) => structuredNode(key, layer[key], ["layers", index, key]));
    return rememberedDetails(["#layer", layer.layer_id], false, "stack-layer-advanced", [
      element("summary", { text: "Composition, form, and properties" }),
      element("div", { className: "editor-fields" }, fields),
    ]);
  }

  function renderDeviceStackEditor() {
    const layers = edit.value.layers || [];
    const stackRaw = element("input", {
      properties: {
        value: edit.value.full_stack_raw || "",
        placeholder: "For example: glass / ITO / ETL / absorber / HTL / metal",
      },
    });
    stackRaw.addEventListener("input", () => updateEditValue(["full_stack_raw"], stackRaw.value, edit.value.full_stack_raw));
    const cards = layers.map((layer, index) => {
      const material = element("input", { properties: { value: layer.material || "", placeholder: "Layer material", required: true } });
      material.setAttribute("list", SUGGESTION_LISTS.layer.id);
      material.addEventListener("input", () => {
        layer.material = material.value;
        $("record-json").value = JSON.stringify(edit.value, null, 2);
      });
      const role = element("select", {}, layerRoleOptions().map((choice) => element("option", {
        text: humanLabel(choice),
        properties: { value: choice, selected: layer.role === choice },
      })));
      role.addEventListener("change", () => {
        layer.role = role.value;
        $("record-json").value = JSON.stringify(edit.value, null, 2);
      });
      return element("article", { className: "stack-layer" }, [
        element("div", { className: "stack-layer-main" }, [
          element("strong", { className: "stack-position", text: String(index + 1), attributes: { "aria-label": `Layer ${index + 1}` } }),
          element("label", {}, [element("span", { text: "Material" }), material]),
          element("label", {}, [element("span", { text: "Function" }), role]),
          element("div", { className: "stack-layer-actions" }, [
            element("button", { text: "↑", properties: { type: "button", disabled: index === 0 }, attributes: { "aria-label": "Move layer up" }, events: { click: () => moveLayer(index, -1) } }),
            element("button", { text: "↓", properties: { type: "button", disabled: index === layers.length - 1 }, attributes: { "aria-label": "Move layer down" }, events: { click: () => moveLayer(index, 1) } }),
            element("button", { className: "danger-link", text: "Remove", properties: { type: "button" }, attributes: { "aria-label": `Remove layer ${index + 1}` }, events: { click: () => removeLayer(index) } }),
          ]),
        ]),
        renderLayerAdvanced(layer, index),
      ]);
    });
    return element("section", { className: "device-stack-editor" }, [
      element("div", { className: "device-stack-heading" }, [
        element("div", {}, [
          element("h3", { text: "Device stack" }),
          element("p", { text: "Correct the material, function, and order directly. Layer IDs and evidence stay attached automatically." }),
        ]),
        element("button", { text: "+ Add layer", properties: { type: "button" }, events: { click: addLayer } }),
      ]),
      element("label", { className: "stack-raw" }, [
        element("span", { text: "Stack exactly as written in the paper (optional)" }),
        stackRaw,
      ]),
      element("div", { className: "stack-layers" }, cards.length ? cards : [element("p", { className: "muted", text: "No layers extracted. Add the first layer above." })]),
    ]);
  }

  function structuredLeaf(label, value, path) {
    let input;
    let editOriginal = value;
    let displayLabel = humanLabel(label);
    const fieldSchema = recordSchemaNode(edit.kind, path);
    const fieldType = (fieldSchema.anyOf || fieldSchema.oneOf || [fieldSchema])
      .find(choice => choice.type && choice.type !== "null")?.type;
    const relationship = path.length === 1
      ? relationshipChoices(path[0], edit.kind, context().bundle.ground_truth, context().bundle.summary.record_identifiers, value)
      : null;
    const layerLink = isLayerLink(path.slice(0, -1), path.at(-1)) && path.at(-1) === "layer_id";
    if (layerLink) {
      displayLabel = "Absorber layer";
      editOriginal = null;
      const layers = edit.value.layers || [];
      const known = layers.some(layer => layer.layer_id === value);
      input = element("select", {}, [
        element("option", { text: "Not linked", properties: { value: "", selected: value == null || value === "" } }),
        ...layers.map((layer, layerIndex) => element("option", {
          text: `${layerIndex + 1} · ${layer.material || "(no material yet)"} · ${humanLabel(layer.role || "not_reported")}`,
          properties: { value: layer.layer_id, selected: layer.layer_id === value },
        })),
        ...(value && !known ? [element("option", { text: `Unknown layer (${value})`, properties: { value, selected: true } })] : []),
      ]);
    } else if (relationship) {
      const nullable = value === null || fieldSchema.anyOf?.some(choice => choice.type === "null");
      displayLabel = relationship.target === "device_families" ? "Linked device family" : "Linked individual device";
      editOriginal = nullable ? null : value;
      input = element("select", { properties: { required: !nullable } }, [
        element("option", { text: nullable ? "Not linked" : "Choose a record", properties: { value: "", selected: value == null || value === "", disabled: !nullable } }),
        ...relationship.choices.map(choice => element("option", {
          text: `${choice.label} · ${choice.value}`,
          properties: { value: choice.value, selected: choice.value === value },
        })),
      ]);
    } else if (path.at(-1) === "material_form") {
      input = element("select", {}, MATERIAL_FORMS.map((form) => element("option", {
        text: humanLabel(form), properties: { value: form, selected: value === form },
      })));
    } else if (fieldSchema.enum) {
      input = element("select", {}, fieldSchema.enum.map(choice => element("option", {
        text: humanLabel(choice), properties: { value: choice, selected: value === choice },
      })));
    } else if (typeof value === "boolean") {
      input = element("select", {}, [element("option", { text: "true", properties: { value: "true" } }), element("option", { text: "false", properties: { value: "false" } })]);
      input.value = String(value);
    } else if (typeof value === "string" && value.length > 140) {
      input = element("textarea", { text: value, properties: { rows: 3 } });
    } else {
      const numeric = typeof value === "number" || fieldType === "number" || fieldType === "integer";
      input = element("input", { properties: { value: value ?? "", type: numeric ? "number" : "text", placeholder: value === null ? "Not reported" : "" } });
    }
    const inputId = `record-field-${path.map(fieldIdPart).join("-")}`;
    input.id = inputId;
    // Constituent name, role and amount name get suggestions; the typed text is saved as is.
    const textInput = !layerLink && !relationship && !fieldSchema.enum && typeof value !== "boolean";
    const constituentField = path.at(-3) === "constituents" ? path.at(-1)
      : path.at(-4) === "constituents" && path.at(-2) === "amount" ? `amount.${path.at(-1)}` : null;
    const list = { name: "constituent", role: "role", "amount.name": "amount" }[constituentField];
    if (textInput && list) input.setAttribute("list", SUGGESTION_LISTS[list].id);
    input.addEventListener("input", () => updateEditValue(path, input.value, editOriginal));
    return element("div", { className: "editor-field" }, [
      element("div", { className: "editor-field-heading" }, [
        element("label", { text: displayLabel, attributes: { for: inputId } }),
        element("details", { className: "field-schema-path" }, [
          element("summary", { text: "Schema field" }),
          element("code", { className: "json-path", text: recordFieldPath(path) }),
        ]),
      ]),
      input,
    ]);
  }

  function isTechnicalRecordField(key) {
    return key === "sequence" || key.endsWith("_id") || key.endsWith("_ids");
  }

  /** Links from an absorber or processing step to the family's layers are shown as choices. */
  function isLayerLink(parentPath, key) {
    if (edit.kind !== "device_families" || parentPath.length !== 2) return false;
    return (parentPath[0] === "absorbers" && key === "layer_id")
      || (parentPath[0] === "processing_steps" && key === "target_layer_ids");
  }

  function layerTargets(path, selected) {
    const layers = edit.value.layers || [];
    const stale = (selected || []).filter(id => !layers.some(layer => layer.layer_id === id));
    const options = [
      ...layers.map((layer, index) => [layer.layer_id, `${index + 1} · ${layer.material || "(no material yet)"} · ${humanLabel(layer.role || "not_reported")}`]),
      ...stale.map(id => [id, `Unknown layer (${id})`]),
    ];
    const boxes = options.map(([id, text]) => {
      const box = element("input", { properties: { type: "checkbox", checked: (selected || []).includes(id), value: id } });
      box.addEventListener("change", () => {
        const current = new Set(path.reduce((node, key) => node[key], edit.value) || []);
        if (box.checked) current.add(id); else current.delete(id);
        draft.replace(path, options.map(([optionId]) => optionId).filter(optionId => current.has(optionId)));
        $("record-json").value = JSON.stringify(edit.value, null, 2);
      });
      return element("label", { className: "layer-target" }, [box, element("span", { text })]);
    });
    return element("section", { className: "editor-field layer-targets" }, [
      element("strong", { text: "Target layers" }),
      ...(boxes.length ? boxes : [element("p", { className: "muted", text: "Add layers to the device stack first." })]),
    ]);
  }

  function technicalFields(entries, path) {
    if (!entries.length) return null;
    return rememberedDetails([...path, "#technical"], false, "editor-technical", [
      element("summary", { text: "Technical details" }),
      element("p", { text: "Record links and identifiers. Most corrections do not need these." }),
      element("div", { className: "editor-fields" }, entries.map(([key, value]) => structuredNode(key, value, [...path, key]))),
    ]);
  }

  function moveOrderedItem(path, index, targetIndex) {
    if (!draft.reorder(path, index, targetIndex)) return;
    expand([...path, targetIndex]);
    $("record-json").value = JSON.stringify(edit.value, null, 2);
    renderStructuredEditor();
    const pathKey = JSON.stringify(path);
    const moved = [...document.querySelectorAll(".ordered-array-item")]
      .find((node) => node.dataset.arrayPath === pathKey && Number(node.dataset.arrayIndex) === targetIndex);
    const details = moved?.querySelector(":scope > details");
    if (details) details.open = true;
    moved?.querySelector("select")?.focus();
  }

  function orderedArrayItem(label, item, index, path, itemCount) {
    const position = element("select", {
      attributes: { "aria-label": `Position of ${humanLabel(label)}` },
    }, Array.from({ length: itemCount }, (_, optionIndex) => element("option", {
      text: String(optionIndex + 1),
      properties: { value: String(optionIndex), selected: optionIndex === index },
    })));
    position.addEventListener("change", () => moveOrderedItem(path, index, Number(position.value)));
    return element("div", {
      className: "ordered-array-item",
      attributes: { "data-array-path": JSON.stringify(path), "data-array-index": String(index) },
    }, [
      element("div", { className: "ordered-array-controls" }, [
        element("span", { className: "ordered-array-position", text: `Step ${index + 1}` }),
        element("label", { className: "ordered-array-jump" }, [element("span", { text: "Position" }), position]),
        element("button", {
          text: "↑ Earlier",
          properties: { type: "button", disabled: index === 0 },
          events: { click: () => moveOrderedItem(path, index, index - 1) },
        }),
        element("button", {
          text: "↓ Later",
          properties: { type: "button", disabled: index === itemCount - 1 },
          events: { click: () => moveOrderedItem(path, index, index + 1) },
        }),
      ]),
      structuredNode(label, item, [...path, index]),
    ]);
  }

  function isProcessingStep(path) {
    return edit.kind === "device_families" && path.length === 2 && path[0] === "processing_steps" && typeof path[1] === "number";
  }

  /** Insert a step at a position; the new step gets the next readable identifier and stays open. */
  function insertStep(position, step) {
    const steps = [...(edit.value.processing_steps || [])];
    if (!step.step_id) step.step_id = freshIdentifier("step", usedIdentifiers(context().bundle?.ground_truth, edit.value));
    steps.splice(position, 0, step);
    const added = ["processing_steps", position];
    expand(["processing_steps"], added);
    replaceArrayItems(["processing_steps"], steps);
  }

  /** Append another family's steps, retargeted to this family's layers by material, then function. */
  function copyStepsControl() {
    const identifier = context().bundle?.summary?.record_identifiers?.device_families || "family_id";
    const others = (context().bundle?.ground_truth?.device_families || [])
      .filter(family => family[identifier] !== edit.value[identifier] && (family.processing_steps || []).length);
    if (!others.length) return null;
    const source = element("select", { attributes: { "aria-label": "Family to copy processing steps from" } }, others.map(family => element("option", {
      text: `${family.label || family[identifier]} · ${family.processing_steps.length} step${family.processing_steps.length === 1 ? "" : "s"}`,
      properties: { value: family[identifier] },
    })));
    source.id = "record-copy-steps-source";
    const copy = () => {
      const family = others.find(item => item[identifier] === source.value) || others[0];
      const used = usedIdentifiers(context().bundle?.ground_truth, edit.value);
      const { steps, skipped } = copySteps(family, edit.value, () => {
        const id = freshIdentifier("step", used);
        used.add(id);
        return id;
      });
      const current = edit.value.processing_steps || [];
      steps.forEach((_step, offset) => expand(["processing_steps", current.length + offset]));
      expand(["processing_steps"]);
      replaceArrayItems(["processing_steps"], [...current, ...steps]);
      $("dialog-status").textContent = `Copied ${steps.length} step${steps.length === 1 ? "" : "s"} from ${family.label || family[identifier]}. `
        + (skipped.length ? `Not copied, because this family has no matching layer: ${skipped.join("; ")}. ` : "")
        + "Change what differs, remove what this family did not get, and check the target layers.";
    };
    return element("div", { className: "process-copy" }, [
      element("label", {}, [element("span", { text: "Copy steps from" }), source]),
      element("button", { text: "Copy steps", properties: { type: "button" }, events: { click: copy } }),
    ]);
  }

  function splitStep(index) {
    const steps = [...edit.value.processing_steps];
    const used = usedIdentifiers(context().bundle?.ground_truth, edit.value);
    const parts = splitPerTarget(steps[index], () => {
      const id = freshIdentifier("step", used);
      used.add(id);
      return id;
    });
    steps.splice(index, 1, ...parts);
    parts.forEach((_part, offset) => expand(["processing_steps", index + offset]));
    replaceArrayItems(["processing_steps"], steps);
    $("dialog-status").textContent = `Split into ${parts.length} steps, one per target layer. Check each copy's values against the paper.`;
  }

  /** One processing step as a typed card: category decides methods and parameters. */
  function renderProcessStep(step, index) {
    const path = ["processing_steps", index];
    const sync = (rerender) => {
      $("record-json").value = JSON.stringify(edit.value, null, 2);
      if (rerender) { expand(path); renderStructuredEditor(); }
    };
    const layers = edit.value.layers || [];
    const targets = (step.target_layer_ids || [])
      .map(id => layers.find(layer => layer.layer_id === id)?.material || id).join(", ");
    const action = (text, click) => element("button", { text, properties: { type: "button" }, events: { click } });
    const actions = [
      (step.target_layer_ids || []).length > 1 ? action("Split per target layer", () => splitStep(index)) : null,
    ].filter(Boolean);
    return rememberedDetails(path, false, "editor-group process-step", [
      element("summary", { text: `${step.operation || "New step"}${targets ? ` → ${targets}` : ""}` }),
      element("div", { className: "editor-fields" }, [
        createStepCard({ element, step, idPrefix: `record-step-${index}`, onChange: sync, materialList: SUGGESTION_LISTS.process.id }),
        layerTargets([...path, "target_layer_ids"], step.target_layer_ids || []),
        actions.length ? element("div", { className: "process-step-actions" }, actions) : null,
        technicalFields([["step_id", step.step_id], ["sequence", step.sequence]], path),
      ].filter(Boolean)),
    ]);
  }

  function isOrderedArray(value) {
    return value.length > 1 && value.every((item) => item && typeof item === "object" && Object.hasOwn(item, "sequence"));
  }

  function structuredNode(label, value, path, open = false) {
    const node = recordSchemaNode(edit.kind, path);
    const schema = concreteSchema(node);
    const nullableObject = (node.anyOf || node.oneOf || []).some(choice => resolvedSchema(choice).type === "null")
      && (schema.type === "object" || schema.properties);
    if (value == null && nullableObject) {
      return element("section", { className: "editor-group" }, [
        element("strong", { text: humanLabel(label) }),
        element("p", { className: "muted", text: "Not reported. Add only when supported by the source; new values use the selected citation." }),
        element("button", {
          text: `Add ${humanLabel(label)}`, properties: { type: "button" },
          events: { click: () => {
            expand(path);
            focusPath = path;
            replaceNestedValue(path, newNestedValue(node, label));
          } },
        }),
      ]);
    }
    if (isLayerLink(path.slice(0, -1), path.at(-1)) && Array.isArray(value)) return layerTargets(path, value);
    if (isProcessingStep(path) && value && typeof value === "object") return renderProcessStep(value, path[1]);
    if (value == null || typeof value !== "object") return structuredLeaf(label, value, path);
    const array = Array.isArray(value);
    const entries = array
      ? value.map((item, index) => [item?.label || item?.name || item?.material || item?.operation || `Item ${index + 1}`, item, index])
      : Object.entries(value).filter(([key]) => key !== "evidence").map(([key, item]) => [key, item, key]);
    const technicalKey = key => isTechnicalRecordField(key) && !isLayerLink(path, key);
    const technical = array ? [] : entries.filter(([key]) => technicalKey(key));
    const primary = array ? entries : entries.filter(([key]) => !technicalKey(key));
    const ordered = array && isOrderedArray(value);
    const children = primary.map(([childLabel, item, key]) => {
      const child = ordered
        ? orderedArrayItem(childLabel, item, key, path, value.length)
        : structuredNode(childLabel, item, [...path, key]);
      if (!array) return child;
      return element("div", {}, [child, element("button", {
        text: `Remove ${humanLabel(childLabel)}`,
        properties: { type: "button", disabled: value.length <= (schema.minItems || 0) },
        events: { click: () => replaceArrayItems(path, value.filter((_item, index) => index !== key)) },
      })]);
    });
    const technicalSection = technicalFields(technical, path);
    if (technicalSection) children.push(technicalSection);
    if (array && schema.items && edit.kind === "device_families" && path.length === 1 && path[0] === "processing_steps") {
      children.push(element("div", { className: "process-presets" }, STEP_PRESETS.map(preset => element("button", {
        text: preset.label, properties: { type: "button", disabled: value.length >= (schema.maxItems ?? Infinity) },
        events: { click: () => {
          const step = newArrayItem(path, schema.items, label);
          step.operation = preset.operation;
          insertStep(value.length, step);
        } },
      }))));
      const copy = copyStepsControl();
      if (copy) children.push(copy);
    }
    if (array && schema.items) {
      children.push(element("button", {
        text: `Add item to ${humanLabel(label)}`,
        properties: { type: "button", disabled: value.length >= (schema.maxItems ?? Infinity) },
        events: { click: () => {
          const added = [...path, value.length];
          expand(path, added);
          focusPath = added;
          replaceArrayItems(path, [...value, newArrayItem(path, schema.items, label)]);
        } },
      }));
      children.push(element("p", { className: "muted", text: "New values use the selected source citation on save. Check that it supports each added value." }));
    }
    if (nullableObject) children.push(element("button", {
      text: `Clear ${humanLabel(label)}`, properties: { type: "button" },
      events: { click: () => replaceNestedValue(path, null) },
    }));
    const count = array ? ` (${value.length})` : "";
    return rememberedDetails(path, open, "editor-group", [
      element("summary", { text: `${humanLabel(label)}${count}` }),
      element("div", { className: "editor-fields" }, children.length ? children : [element("p", { className: "muted", text: "No editable fields" })]),
    ]);
  }

  /** Redraw the form without losing the reviewer's place; focus a just-added item. */
  function renderStructuredEditor() {
    const card = $("record-dialog").querySelector?.(".dialog-card");
    const scroll = card?.scrollTop;
    renderEditorContent();
    if (card && scroll != null) card.scrollTop = scroll;
    if (!focusPath) return;
    const prefix = `record-field-${focusPath.map(fieldIdPart).join("-")}-`;
    focusPath = null;
    const fields = document.querySelectorAll("#structured-editor input, #structured-editor select, #structured-editor textarea");
    [...fields].find(node => node.id?.startsWith(prefix))?.focus();
  }

  function renderEditorContent() {
    const entries = Object.entries(edit.value).filter(([key]) => key !== "evidence");
    if (edit.kind !== "device_families") {
      $("structured-editor").replaceChildren(...entries.map(([key, value]) => structuredNode(key, value, [key], true)));
      return;
    }
    const simple = entries
      .filter(([key, value]) => !["full_stack_raw", "layers"].includes(key) && !isTechnicalRecordField(key) && (value == null || typeof value !== "object"))
      .map(([key, value]) => structuredNode(key, value, [key]));
    const technical = entries.filter(([key, value]) => !["full_stack_raw", "layers"].includes(key) && isTechnicalRecordField(key) && (value == null || typeof value !== "object"));
    const related = entries
      .filter(([key, value]) => !["full_stack_raw", "layers"].includes(key) && value != null && typeof value === "object")
      .map(([key, value]) => structuredNode(key, value, [key], false));
    $("structured-editor").replaceChildren(
      element("section", { className: "device-identity-editor" }, [
        element("h3", { text: "Device family" }),
        element("div", { className: "editor-fields" }, simple),
        technicalFields(technical, []),
      ]),
      renderDeviceStackEditor(),
      element("section", { className: "device-related-editor" }, [
        element("h3", { text: "Materials and fabrication" }),
        ...related,
      ]),
    );
  }


  $("record-json").addEventListener("input", () => {
    if (!draft) return;
    try {
      draft.replaceJson($("record-json").value);
      $("dialog-status").textContent = "";
    } catch (error) {
      $("dialog-status").textContent = `Raw JSON is not valid: ${error.message}`;
    }
  });
  return {
    open: openRecord,
    setMode: setRecordEditorMode,
    newDraft: newRecordDraft,
    get edit() { return edit ? structuredClone(edit) : null; },
    /** Whether the open draft differs from what was loaded (unsaved work). */
    get changed() {
      if (!draft) return false;
      if (!$("json-editor-panel").hidden && $("record-json").value !== JSON.stringify(draft.value, null, 2)) return true;
      return JSON.stringify(draft.value) !== initialJson;
    },
    setSource(source) { edit.source = structuredClone(source); },
    value() {
      // Programmatic textarea changes and invalid raw input must be checked on save.
      if (!$("json-editor-panel").hidden) draft.replaceJson($("record-json").value);
      return draft.snapshot();
    },
  };
}
