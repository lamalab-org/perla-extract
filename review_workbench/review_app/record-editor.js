import { relationshipChoices } from "./review-state.js";

/** One record draft shared by the field controls and the raw JSON editing mode.
 * Invalid JSON never replaces the last valid draft; callers must surface the error.
 */
export function createDraftState(initial) {
  let value = structuredClone(initial);
  return {
    get value() { return value; },
    snapshot: () => structuredClone(value),
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

  function newRecordDraft(kind) {
    return draftFromSchema(context().studySchema.properties[kind].items);
  }

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
    if (!dialog.open) dialog.showModal();
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
    return element("details", { className: "stack-layer-advanced" }, [
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
    if (relationship) {
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
    const inputId = `record-field-${path.map((part) => encodeURIComponent(String(part)).replaceAll("-", "%2D")).join("-")}`;
    input.id = inputId;
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

  function technicalFields(entries, path) {
    if (!entries.length) return null;
    return element("details", { className: "editor-technical" }, [
      element("summary", { text: "Technical details" }),
      element("p", { text: "Record links and identifiers. Most corrections do not need these." }),
      element("div", { className: "editor-fields" }, entries.map(([key, value]) => structuredLeaf(key, value, [...path, key]))),
    ]);
  }

  function moveOrderedItem(path, index, targetIndex) {
    if (!draft.reorder(path, index, targetIndex)) return;
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

  function isOrderedArray(value) {
    return value.length > 1 && value.every((item) => item && typeof item === "object" && Object.hasOwn(item, "sequence"));
  }

  function structuredNode(label, value, path, open = false) {
    if (value == null || typeof value !== "object") return structuredLeaf(label, value, path);
    const array = Array.isArray(value);
    const entries = array
      ? value.map((item, index) => [item?.label || item?.name || item?.material || item?.operation || `Item ${index + 1}`, item, index])
      : Object.entries(value).filter(([key]) => key !== "evidence").map(([key, item]) => [key, item, key]);
    const technical = array ? [] : entries.filter(([key]) => isTechnicalRecordField(key));
    const primary = array ? entries : entries.filter(([key]) => !isTechnicalRecordField(key));
    const ordered = array && isOrderedArray(value);
    const children = primary.map(([childLabel, item, key]) => ordered
      ? orderedArrayItem(childLabel, item, key, path, value.length)
      : structuredNode(childLabel, item, [...path, key]));
    const technicalSection = technicalFields(technical, path);
    if (technicalSection) children.push(technicalSection);
    const count = array ? ` (${value.length})` : "";
    return element("details", { className: "editor-group", properties: { open } }, [
      element("summary", { text: `${humanLabel(label)}${count}` }),
      element("div", { className: "editor-fields" }, children.length ? children : [element("p", { className: "muted", text: "No editable fields" })]),
    ]);
  }

  function renderStructuredEditor() {
    const entries = Object.entries(edit.value).filter(([key]) => key !== "evidence");
    if (edit.kind !== "device_families") {
      $("structured-editor").replaceChildren(...entries.map(([key, value]) => structuredNode(key, value, [key], true)));
      return;
    }
    const simple = entries
      .filter(([key, value]) => !["full_stack_raw", "layers"].includes(key) && !isTechnicalRecordField(key) && (value == null || typeof value !== "object"))
      .map(([key, value]) => structuredLeaf(key, value, [key]));
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
    setSource(source) { edit.source = structuredClone(source); },
    value() {
      // Programmatic textarea changes and invalid raw input must be checked on save.
      if (!$("json-editor-panel").hidden) draft.replaceJson($("record-json").value);
      return draft.snapshot();
    },
  };
}
