const $ = (id) => document.getElementById(id);
const COLLECTIONS = {
  device_families: "Device families",
  individual_devices: "Individual devices",
  performance_observations: "Performance observations",
  population_statistics: "Population statistics",
  stability_tests: "Stability tests",
  identity_links: "Cross-window identity links",
};
const QUALITY_GATES = [
  "Main-paper device variants checked",
  "Supporting-information device variants checked",
  "Champion and representative devices kept distinct",
  "Individual observations and population statistics kept distinct",
  "Scan directions and stabilized measurements kept distinct",
  "Composition, layer stack, and processing details checked",
  "Stability specimens, conditions, and checkpoints checked",
  "Every cross-window identity link explicitly justified",
  "Every accepted reported value has source evidence",
  "Remaining uncertainty recorded without inventing a value",
];
const state = { split: "calibration", papers: [], paperId: null, bundle: null, user: null, page: 1, pageCount: 1, source: "main", tab: "inventory", edit: null };

async function request(url, options = {}) {
  const token = localStorage.getItem("review-token");
  const headers = { ...(options.headers || {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...options, headers });
  const payload = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function element(tag, options = {}, children = []) {
  const result = document.createElement(tag);
  if (options.className) result.className = options.className;
  if (options.text != null) result.textContent = String(options.text);
  Object.assign(result, options.properties || {});
  Object.assign(result.dataset, options.dataset || {});
  for (const [name, value] of Object.entries(options.attributes || {})) result.setAttribute(name, value);
  for (const [name, listener] of Object.entries(options.events || {})) result.addEventListener(name, listener);
  result.append(...children.filter((child) => child != null));
  return result;
}

function pointerPart(value) { return String(value).replaceAll("~", "~0").replaceAll("/", "~1"); }
function entityId(kind, item, index) {
  const identifier = state.bundle.summary.record_identifiers[kind];
  return item[identifier] || `${kind} ${index + 1}`;
}
function recordKey(kind, item, index) { return `${kind}:${entityId(kind, item, index)}`; }
function recordDecision(kind, item, index) {
  return state.bundle.summary.record_decisions?.[state.user.id]?.[recordKey(kind, item, index)] || "";
}
function entityTitle(kind, item, index) { return item.label || item.specimen_label || entityId(kind, item, index); }
function metrics(item) { return (item.metrics || item.conditions || []).map((value) => `${value.name}: ${value.raw_value}`).slice(0, 5).join(" · "); }
function entityDetail(kind, item) {
  if (kind === "device_families") return item.full_stack_raw || (item.layers || []).map((layer) => layer.material).join(" / ") || item.variant;
  if (kind === "individual_devices") return `${item.champion_status === "yes" ? "Champion · " : ""}${item.variant || "variant not reported"}`;
  if (kind === "identity_links") return `${item.entity_kind}: ${(item.candidate_ids || []).join(" = ")}`;
  return metrics(item) || item.statistic_type || item.measurement_type || item.link_status || "";
}

async function loadSession() {
  const payload = await request("/api/session");
  state.user = payload.user;
  $("reviewer").textContent = payload.user.name;
}

async function loadPapers() {
  const payload = await request(`/api/papers?split=${encodeURIComponent(state.split)}`);
  state.papers = payload.papers;
  renderPapers();
}

function renderPapers() {
  const query = $("paper-filter").value.toLowerCase();
  const papers = state.papers.filter((paper) => paper.id.toLowerCase().includes(query));
  $("paper-count").textContent = papers.length;
  const cards = papers.map((paper) => {
    const completed = Object.keys(paper.completed_stages || {});
    return element("button", {
      className: `paper-card ${paper.id === state.paperId ? "selected" : ""}`,
      events: { click: () => selectPaper(paper.id) },
    }, [
      element("strong", { text: paper.id }),
      element("span", { text: `${paper.individual_devices} devices · ${paper.performance_observations} observations` }),
      element("span", { text: `${completed.length}/4 review stages · revision ${paper.revision}` }),
    ]);
  });
  $("paper-list").replaceChildren(...(cards.length ? cards : [element("p", { className: "muted", text: "No imported papers in this split." })]));
}

async function selectPaper(paperId) {
  state.paperId = paperId;
  state.bundle = await request(`/api/paper/${state.split}/${encodeURIComponent(paperId)}`);
  state.source = state.bundle.sources.includes("main") ? "main" : state.bundle.sources[0];
  state.page = 1;
  $("empty-state").hidden = true;
  $("workspace").hidden = false;
  renderPapers();
  renderStudy();
  await renderPdf();
}

function hasAudit() { return Boolean(state.bundle?.summary.inventory_audits?.[state.user.id]); }

function renderStudy() {
  const truth = state.bundle.ground_truth;
  const paper = state.papers.find((candidate) => candidate.id === state.paperId);
  if (paper) Object.assign(paper, state.bundle.summary, { revision: state.bundle.revision });
  renderPapers();
  $("paper-split").textContent = state.split;
  $("paper-title").textContent = truth.paper.title || state.paperId;
  $("paper-doi").textContent = truth.paper.doi || "DOI not reported";
  $("revision").textContent = `Revision ${state.bundle.revision}`;
  $("pdf-source").replaceChildren(...state.bundle.sources.map((source) => element("option", {
    text: source === "main" ? "Main paper" : "Supplement",
    properties: { value: source, selected: source === state.source },
  })));
  $("blind-audit").hidden = hasAudit();
  $("inventory-revealed").hidden = !hasAudit();
  renderInventoryForm();
  if (hasAudit()) {
    renderInventoryComparison();
    renderRecordGroups("inventory-lists", true);
    renderRecordGroups("record-groups", false);
  } else {
    $("record-groups").replaceChildren(element("p", { className: "callout", text: "Submit the blind device census before reviewing model candidates." }));
  }
  renderStageControls();
  renderQualityGates();
  renderHistory();
}

function renderInventoryForm() {
  const inputs = Object.entries(COLLECTIONS)
    .filter(([key]) => key !== "identity_links")
    .map(([key, label]) => element("label", {}, [
      label,
      element("input", { properties: { type: "number", min: "0", value: "0" }, dataset: { count: key } }),
    ]));
  $("inventory-counts").replaceChildren(...inputs);
  $("searched-supplement").disabled = !state.bundle.sources.includes("supplement");
}

function renderInventoryComparison() {
  const audit = state.bundle.summary.inventory_audits[state.user.id];
  const rows = Object.entries(COLLECTIONS).filter(([key]) => key !== "identity_links").map(([key, label]) => {
    const expected = audit.expected_counts[key] ?? 0;
    const extracted = state.bundle.summary[key] ?? 0;
    const difference = expected === extracted ? "match" : `${extracted - expected > 0 ? "+" : ""}${extracted - expected}`;
    return element("tr", {}, [
      element("td", { text: label }),
      element("td", { text: expected }),
      element("td", { text: extracted }),
      element("td", { className: expected === extracted ? "match" : "difference", text: difference }),
    ]);
  });
  const header = element("tr", {}, ["Record type", "Your census", "Current truth", "Difference"].map((label) => element("th", { text: label })));
  const comparison = [
    element("h3", { text: "Census versus candidates" }),
    element("table", {}, [element("thead", {}, [header]), element("tbody", {}, rows)]),
  ];
  if (audit.missing_or_ambiguous) comparison.push(element("p", { className: "callout", text: audit.missing_or_ambiguous }));
  $("inventory-comparison").replaceChildren(...comparison);
}

function recordCard(kind, item, index, compact) {
  const decision = recordDecision(kind, item, index);
  const summary = element("div", {}, [
    element("span", { className: "eyebrow", text: entityId(kind, item, index) }),
    element("h4", { text: entityTitle(kind, item, index) }),
    element("p", { text: entityDetail(kind, item) }),
  ]);
  if (compact) return element("article", { className: `record-card ${decision}` }, [summary]);
  const decisionSelect = element("select", {
    properties: { value: decision },
    attributes: { "aria-label": `Review decision for ${entityTitle(kind, item, index)}` },
  }, [
    element("option", { text: "Not reviewed", properties: { value: "" } }),
    element("option", { text: "Verified", properties: { value: "verified" } }),
    element("option", { text: "Uncertain", properties: { value: "uncertain" } }),
    element("option", { text: "Needs correction", properties: { value: "needs_correction" } }),
  ]);
  decisionSelect.value = decision;
  decisionSelect.addEventListener("change", () => decideRecord(decisionSelect, kind, index));
  const actions = element("div", { className: "record-actions" }, [
    decisionSelect,
    element("button", { text: "Duplicate", events: { click: () => openRecord(kind, null, item) } }),
    element("button", { text: "Review", events: { click: () => openRecord(kind, index) } }),
  ]);
  return element("article", { className: `record-card ${decision}` }, [summary, actions]);
}

function renderRecordGroups(target, compact) {
  const truth = state.bundle.ground_truth;
  const groups = Object.entries(COLLECTIONS).map(([kind, label]) => {
    const records = truth[kind].map((item, index) => recordCard(kind, item, index, compact));
    return element("section", { className: "record-group" }, [
      element("div", { className: "group-heading" }, [element("h3", { text: label }), element("span", { text: truth[kind].length })]),
      ...(records.length ? records : [element("p", { className: "muted", text: "No records" })]),
    ]);
  });
  $(target).replaceChildren(...groups);
}

function renderQualityGates() {
  const completed = state.bundle.summary.completed_stages.completeness || [];
  const complete = completed.includes(state.user.id);
  $("quality-gates").replaceChildren(...QUALITY_GATES.map((label, index) => element("label", {}, [
    element("input", { properties: { type: "checkbox", checked: complete, disabled: complete }, dataset: { gate: String(index) } }),
    label,
  ])));
}

function renderStageControls() {
  const stages = state.bundle.summary.completed_stages;
  const mine = (stage) => (stages[stage] || []).includes(state.user.id);
  const decisions = state.bundle.summary.record_decisions?.[state.user.id] || {};
  const reviewed = Object.values(decisions).filter((decision) => decision === "verified" || decision === "uncertain").length;
  const remaining = state.bundle.summary.record_count - reviewed;
  const labels = { inventory: "Mark inventory reviewed", fields: "Mark all record fields reviewed", completeness: "Complete paper review", adjudication: "Complete adjudication" };
  document.querySelectorAll(".complete-stage").forEach((button) => {
    const prerequisites = { inventory: hasAudit(), fields: mine("inventory") && remaining === 0, completeness: mine("fields"), adjudication: mine("completeness") };
    button.disabled = mine(button.dataset.stage) || !prerequisites[button.dataset.stage];
    button.textContent = mine(button.dataset.stage) ? `${button.dataset.stage} completed` : labels[button.dataset.stage];
    if (button.dataset.stage === "fields" && remaining > 0 && !mine("fields")) button.textContent = `Review ${remaining} remaining record${remaining === 1 ? "" : "s"}`;
  });
  $("complete-adjudication").hidden = state.user.role !== "admin";
  const finalEvent = state.bundle.events.at(-1);
  const canExport = state.user.role === "admin" && finalEvent?.kind === "stage_complete" && finalEvent?.details?.stage === "adjudication";
  $("download-truth").hidden = state.user.role !== "admin";
  $("download-truth").disabled = !canExport;
  $("add-record").disabled = !hasAudit();
  $("new-record-kind").disabled = !hasAudit();
}

function renderHistory() {
  const events = [...state.bundle.events].reverse().map((event) => {
    const subject = event.path || event.details?.record_key || event.details?.stage || event.note || "";
    const decision = event.details?.decision ? ` · ${event.details.decision.replaceAll("_", " ")}` : "";
    return element("article", { className: "event" }, [
      element("div", {}, [
        element("strong", { text: `r${event.revision} · ${event.kind.replaceAll("_", " ")}` }),
        element("span", { text: `${event.reviewer_id} · ${new Date(event.timestamp).toLocaleString()}` }),
      ]),
      element("p", { text: subject + decision }),
    ]);
  });
  $("event-history").replaceChildren(...events);
}

async function renderPdf() {
  if (!state.paperId || !state.source) return;
  const query = `source=${state.source}&page=${state.page}&scale=1.5`;
  $("pdf-page").src = `/api/pdf-page/${encodeURIComponent(state.paperId)}?${query}&t=${Date.now()}`;
  const text = await request(`/api/pdf-text/${encodeURIComponent(state.paperId)}?source=${state.source}&page=${state.page}`);
  state.pageCount = text.page_count;
  $("page-number").value = state.page;
  $("page-number").max = state.pageCount;
  $("page-count").textContent = `/ ${state.pageCount}`;
  $("pdf-text").textContent = text.text;
}

async function submitAudit() {
  const searched = [$("searched-main").checked && "main", $("searched-supplement").checked && "supplement"].filter(Boolean);
  const counts = Object.fromEntries([...document.querySelectorAll("[data-count]")].map((input) => [input.dataset.count, Number(input.value)]));
  try {
    state.bundle = await request(`/api/inventory-audits/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ base_revision: state.bundle.revision, searched_sources: searched, expected_counts: counts, missing_or_ambiguous: $("inventory-notes").value }) });
    renderStudy();
    setStatus("Inventory census recorded. Model candidates are now visible.");
  } catch (error) { setStatus(error.message, true); }
}

function openRecord(kind, index = null, template = null) {
  if (!hasAudit()) return setStatus("Complete the blind inventory audit first.", true);
  const item = template || (index == null ? {} : state.bundle.ground_truth[kind][index]);
  state.edit = { kind, index };
  $("record-kind").textContent = COLLECTIONS[kind];
  $("record-title").textContent = index == null ? (template ? "Duplicate record" : "Add record") : entityTitle(kind, item, index);
  $("record-json").value = JSON.stringify(item, null, 2);
  $("citation-block").value = "";
  $("citation-quote").value = "";
  $("mutation-note").value = "";
  $("remove-record").hidden = index == null;
  $("evidence-results").replaceChildren();
  $("dialog-status").textContent = "";
  $("record-dialog").showModal();
}

async function decideRecord(select, kind, index) {
  if (!select.value) return;
  const item = state.bundle.ground_truth[kind][index];
  select.disabled = true;
  try {
    state.bundle = await request(`/api/record-decisions/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ collection: kind, record_id: entityId(kind, item, index), decision: select.value, base_revision: state.bundle.revision, note: "" }) });
    renderStudy();
    setStatus(`Marked ${entityTitle(kind, item, index)} as ${select.value.replaceAll("_", " ")}.`);
  } catch (error) {
    select.disabled = false;
    setStatus(error.message, true);
  }
}

async function saveRecord() {
  try {
    const value = JSON.parse($("record-json").value);
    const { kind, index } = state.edit;
    const blockId = $("citation-block").value.trim();
    const quote = $("citation-quote").value.trim();
    if (!blockId || !quote) throw new Error("Choose an evidence block and provide an exact quote.");
    const payload = { action: index == null ? "add" : "replace", path: `/${pointerPart(kind)}/${index == null ? "-" : index}`, value, evidence: [{ block_id: blockId, quote }], note: $("mutation-note").value, base_revision: state.bundle.revision };
    state.bundle = await request(`/api/mutations/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify(payload) });
    $("record-dialog").close();
    renderStudy();
    setStatus("Correction saved and the complete study schema revalidated.");
  } catch (error) { $("dialog-status").textContent = error.message; }
}

async function removeRecord() {
  if (!window.confirm("Remove this record from ground truth?")) return;
  try {
    const { kind, index } = state.edit;
    state.bundle = await request(`/api/mutations/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ action: "remove", path: `/${pointerPart(kind)}/${index}`, note: $("mutation-note").value, evidence: [], base_revision: state.bundle.revision }) });
    $("record-dialog").close();
    renderStudy();
  } catch (error) { $("dialog-status").textContent = error.message; }
}

async function searchEvidence() {
  try {
    const payload = await request(`/api/evidence/${state.split}/${encodeURIComponent(state.paperId)}?q=${encodeURIComponent($("evidence-query").value)}`);
    const results = payload.blocks.map((block) => element("button", {
      className: "evidence-result",
      properties: { type: "button" },
      events: { click: () => chooseEvidence(block) },
    }, [element("span", { text: `${block.source} · p.${block.page}` }), block.text]));
    $("evidence-results").replaceChildren(...(results.length ? results : [element("p", { className: "muted", text: "No evidence blocks found." })]));
  } catch (error) { $("dialog-status").textContent = error.message; }
}

function chooseEvidence(block) {
  $("citation-block").value = block.block_id;
  $("citation-quote").value = block.text.slice(0, 1600);
  if (state.bundle.sources.includes(block.source)) {
    state.source = block.source;
    state.page = block.page;
    $("pdf-source").value = state.source;
    renderPdf();
  }
}

async function completeStage(stage) {
  if (stage === "completeness" && [...document.querySelectorAll("[data-gate]")].some((input) => !input.checked)) return setStatus("Complete every quality gate first.", true);
  try {
    state.bundle = await request(`/api/stages/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ stage, base_revision: state.bundle.revision, note: $("stage-note").value }) });
    renderStudy();
    setStatus(`${stage} stage completed.`);
  } catch (error) { setStatus(error.message, true); }
}

function setTab(tab) {
  state.tab = tab;
  document.querySelectorAll("[data-tab]").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  for (const name of ["inventory", "records", "completeness", "history"]) $(`${name}-tab`).hidden = name !== tab;
}

function setStatus(message, error = false) { $("status").textContent = message; $("status").className = error ? "error" : "success"; }

async function downloadGroundTruth() {
  const token = localStorage.getItem("review-token");
  const response = await fetch(`/api/ground-truth-export/${state.split}/${encodeURIComponent(state.paperId)}`, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const payload = await response.json();
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  const link = document.createElement("a");
  link.href = URL.createObjectURL(await response.blob());
  link.download = `${state.paperId}.ground-truth.zip`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function importPaper(event) {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    $("import-status").textContent = "Importing and validating…";
    const bundle = await request("/api/papers/import", { method: "POST", body: form });
    state.split = form.get("split");
    $("split").value = state.split;
    $("import-dialog").close();
    await loadPapers();
    await selectPaper(bundle.paper_id);
  } catch (error) { $("import-status").textContent = error.message; }
}

$("split").addEventListener("change", async (event) => { state.split = event.target.value; state.paperId = null; state.bundle = null; $("workspace").hidden = true; $("empty-state").hidden = false; await loadPapers(); });
$("paper-filter").addEventListener("input", renderPapers);
$("submit-audit").addEventListener("click", submitAudit);
$("pdf-source").addEventListener("change", (event) => { state.source = event.target.value; state.page = 1; renderPdf(); });
$("previous-page").addEventListener("click", () => { state.page = Math.max(1, state.page - 1); renderPdf(); });
$("next-page").addEventListener("click", () => { state.page = Math.min(state.pageCount, state.page + 1); renderPdf(); });
$("page-number").addEventListener("change", (event) => { state.page = Math.max(1, Math.min(state.pageCount, Number(event.target.value))); renderPdf(); });
document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
document.querySelectorAll(".complete-stage").forEach((button) => button.addEventListener("click", () => completeStage(button.dataset.stage)));
$("add-record").addEventListener("click", () => openRecord($("new-record-kind").value));
$("save-record").addEventListener("click", saveRecord);
$("remove-record").addEventListener("click", removeRecord);
$("search-evidence").addEventListener("click", searchEvidence);
$("evidence-query").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); searchEvidence(); } });
$("download-truth").addEventListener("click", async () => {
  try {
    await downloadGroundTruth();
    setStatus("Downloaded the adjudicated PR bundle.");
  } catch (error) { setStatus(error.message, true); }
});
$("open-import").addEventListener("click", () => $("import-dialog").showModal());
$("close-import").addEventListener("click", () => $("import-dialog").close());
$("cancel-import").addEventListener("click", () => $("import-dialog").close());
$("import-form").addEventListener("submit", importPaper);

await loadSession();
await loadPapers();
