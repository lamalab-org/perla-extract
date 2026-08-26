const $ = (id) => document.getElementById(id);
const REVIEW_TOKEN_KEY = "review-token";
const COLLECTIONS = {
  device_families: "Device families",
  individual_devices: "Individual devices",
  performance_observations: "Performance observations",
  population_statistics: "Population statistics",
  stability_tests: "Stability tests",
  identity_links: "Cross-window identity links",
};
const RECORD_GUIDANCE = {
  device_families: {
    census: "One shared recipe or architecture variant, such as a control or treatment group. Do not count a champion, scan direction, or mean as another family.",
    review: "Check the shared stack, composition, architecture, and fabrication recipe. Performance numbers belong in linked observations or population statistics.",
  },
  individual_devices: {
    census: "One particular measured specimen that the paper distinguishes, such as a champion, representative, or certified cell. Multiple measurements of the same cell are not additional devices.",
    review: "Check that this is a distinct specimen, that its family link and champion or selection status are supported, and that its specimen-specific properties are correct.",
  },
  performance_observations: {
    census: "One measurement protocol on one device. Forward and reverse scans, stabilized output, and certification are separate observations—not separate devices.",
    review: "Check the linked device, measurement type, scan direction, and every atomic metric shown below.",
  },
  population_statistics: {
    census: "One reported aggregate over multiple devices, such as a mean, median, range, or distribution. Do not count it as an individual device.",
    review: "Check the family, statistic type, sample size, and every aggregate metric. Do not accept a champion value as a population statistic.",
  },
  stability_tests: {
    census: "One aging experiment on a stated specimen or device group. Its checkpoints and multiple reported outcomes remain inside that test.",
    review: "Check what specimen was aged, how confidently it links to a device or family, all test-wide conditions, and every checkpoint's time, local conditions, and outcomes.",
  },
  identity_links: {
    census: "One explicit claim that records extracted from different text regions refer to the same real-world entity.",
    review: "Check that the cited evidence supports identity; similarity alone is not enough.",
  },
};
const DECISION_GUIDANCE = {
  verified: "All fields match the source",
  uncertain: "Cannot establish from the source",
  needs_correction: "One or more fields need correction",
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
  "Every retained reported value has source evidence",
  "Remaining uncertainty recorded without inventing a value",
];
const MATERIAL_FORMS = [
  "self_assembled_monolayer", "monolayer", "compact_layer",
  "mesoporous_layer", "nanostructured_layer", "bulk_heterojunction", "other",
  "not_reported",
];
const COMPOSITION_STATUS_TEXT = {
  accepted: {
    label: "Passed automated checks",
    explanation: "The proposed A/B/X assignment is internally consistent with the extracted formula. It still requires human comparison with the source.",
  },
  needs_review: {
    label: "Manual check needed",
    explanation: "Automated checks found an incomplete or ambiguous A/B/X assignment. Compare it with the source or leave the interpretation unresolved.",
  },
  rejected: {
    label: "Not usable downstream",
    explanation: "The proposed A/B/X assignment failed automated checks and will not be used for export.",
  },
};
const REPAIR_STATUS_TEXT = {
  accepted: "The targeted text reread proposed changes that passed automated validation and are included in this draft; they are not yet human-verified.",
  rejected: "The targeted text reread proposed changes, but they failed automated validation and were not included.",
  no_change: "The targeted text reread completed without changing the draft.",
  not_needed: "The automated audit found no targeted text reread to perform.",
  failed: "The targeted text reread failed; no repair result was applied.",
};
const state = {
  split: "calibration", papers: [], paperId: null, bundle: null, user: null,
  page: 1, pageCount: 1, source: "main", tab: "inventory", edit: null,
  queueIndex: 0, queueKey: null, evidenceCache: new Map(), annotations: null,
  studySchema: null, activeCitation: null, pdfRequest: 0, pdfAbortController: null,
  pdfObjectUrl: null, pdfDisplayed: null, censusDraft: null, editingCensus: false, loadingPaperId: null,
  annotationView: "current", authMode: "local", clerk: null,
};

const LAPTOP_LAYOUT = "(max-width: 1400px)";
const PDF_VIEW_VERSION = 2;

function setPaperListOpen(open, remember = true) {
  document.querySelector("main").classList.toggle("paper-list-hidden", !open);
  const button = $("toggle-paper-list");
  button.setAttribute("aria-expanded", String(open));
  button.textContent = open ? "Hide papers" : "Show papers";
  if (remember) sessionStorage.setItem("perla-paper-list-open", String(open));
}

function setWorkspaceView(view, remember = true) {
  if (!["split", "paper", "review"].includes(view)) return;
  document.querySelector(".workspace-grid").dataset.view = view;
  document.querySelectorAll("[data-workspace-view]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.workspaceView === view));
  });
  if (remember) sessionStorage.setItem("perla-workspace-view", view);
}

function initializeWorkspaceLayout() {
  const savedPaperList = sessionStorage.getItem("perla-paper-list-open");
  const paperListOpen = savedPaperList == null
    ? !window.matchMedia(LAPTOP_LAYOUT).matches
    : savedPaperList === "true";
  setPaperListOpen(paperListOpen, false);
  setWorkspaceView(sessionStorage.getItem("perla-workspace-view") || "split", false);
}

async function authorizationHeaders(headers = {}) {
  const token = localStorage.getItem(REVIEW_TOKEN_KEY);
  if (token) return { ...headers, Authorization: `Bearer ${token}` };
  if (!state.clerk?.session) return headers;
  const clerkToken = await state.clerk.session.getToken();
  return clerkToken ? { ...headers, Authorization: `Bearer ${clerkToken}` } : headers;
}

async function request(url, options = {}) {
  const { responseType, ...fetchOptions } = options;
  const headers = await authorizationHeaders(options.headers || {});
  if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const response = await fetch(url, { ...fetchOptions, headers });
  if (!response.ok) {
    const payload = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.code = payload.code || "request_failed";
    error.status = response.status;
    if (error.code === "review_revision_conflict") showRevisionConflictActions();
    throw error;
  }
  if (responseType === "blob") return response.blob();
  if (responseType === "pdfPage") return {
    blob: await response.blob(),
    pageCount: Number(response.headers.get("X-PDF-Pages")) || null,
  };
  return response.headers.get("content-type")?.includes("json") ? response.json() : response.text();
}

function transientRequest(error) {
  return error.name !== "AbortError" && (!error.status || error.status === 408 || error.status === 429 || error.status >= 500);
}

async function requestWithRetry(url, options = {}, attempts = 2) {
  let lastError;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try { return await request(url, options); }
    catch (error) {
      lastError = error;
      if (attempt === attempts || !transientRequest(error)) throw error;
      console.warn(`Retrying ${url} after a temporary failure (${attempt}/${attempts})`, error);
      await new Promise((resolve) => setTimeout(resolve, 300 * attempt));
    }
  }
  throw lastError;
}

async function loadPdfPage(url, signal) {
  const { blob, pageCount } = await requestWithRetry(url, { responseType: "pdfPage", signal });
  const objectUrl = URL.createObjectURL(blob);
  const preview = new Image();
  try {
    preview.src = objectUrl;
    await preview.decode();
    return { objectUrl, pageCount };
  } catch (error) {
    URL.revokeObjectURL(objectUrl);
    throw error;
  }
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
function metrics(item) { return (item.metrics || item.reported_properties || item.conditions || []).map((value) => `${value.name}: ${value.raw_value}`).slice(0, 5).join(" · "); }
function layerLabel(layer) {
  const form = layer.material_form && layer.material_form !== "not_reported"
    ? humanLabel(layer.material_form)
    : null;
  const constituents = (layer.constituents || []).map((item) => item.name).join(" + ");
  return [layer.material, form, constituents && constituents !== layer.material ? constituents : null]
    .filter(Boolean).join(" · ");
}
function entityDetail(kind, item) {
  if (kind === "device_families") return (item.layers || []).map(layerLabel).join(" / ") || item.full_stack_raw || item.variant;
  if (kind === "individual_devices") return metrics(item) || `${item.champion_status === "yes" ? "Champion · " : ""}${item.variant || "variant not reported"}`;
  if (kind === "stability_tests") {
    const conditionCount = (item.conditions || []).length;
    const checkpoints = item.checkpoints || [];
    const outcomeCount = checkpoints.reduce((count, checkpoint) => count + (checkpoint.outcomes || []).length, 0);
    return `${conditionCount} test-wide condition${conditionCount === 1 ? "" : "s"} · ${checkpoints.length} checkpoint${checkpoints.length === 1 ? "" : "s"} · ${outcomeCount} outcome${outcomeCount === 1 ? "" : "s"}`;
  }
  if (kind === "identity_links") return `${item.entity_kind}: ${(item.candidate_ids || []).join(" = ")}`;
  return metrics(item) || item.statistic_type || item.measurement_type || item.link_status || "";
}

function compositionProposal(family, absorber) {
  const familyIdentifier = state.bundle.summary.record_identifiers.device_families;
  const results = state.bundle.manifest.quality_artifacts?.enrichment?.composition_results || [];
  return results.find((result) => result.proposal?.absorber_id === absorber.absorber_id)
    || ((family.absorbers || []).length === 1
      ? results.find((result) => !result.proposal?.absorber_id && result.proposal?.[familyIdentifier] === family[familyIdentifier])
      : null);
}

function sourceComposition(absorber) {
  const formula = absorber.formula?.raw_value || "Formula not reported";
  const constituents = (absorber.constituents || []).map((constituent) => {
    const amount = constituent.amount?.raw_value;
    return amount ? `${constituent.name} (${amount})` : constituent.name;
  });
  return [element("strong", { text: formula }), ...(constituents.length ? [element("p", { text: `Reported constituents: ${constituents.join(" · ")}` })] : [])];
}

function interpretedComposition(result) {
  if (!result) return [element("p", { className: "muted", text: "No A/B/X-site proposal was supplied." })];
  const status = COMPOSITION_STATUS_TEXT[result.status] || {
    label: humanLabel(result.status),
    explanation: "This automated interpretation has an unknown status and requires manual review.",
  };
  const bySite = (result.proposal.ions || []).reduce((groups, ion) => {
    (groups[ion.site] ||= []).push(ion);
    return groups;
  }, {});
  const sites = ["A", "B", "X"].map((site) => {
    const ions = (bySite[site] || []).map((ion) => `${ion.abbreviation}${ion.coefficient === "1" ? "" : ion.coefficient}`);
    return `${site}: ${ions.length ? ions.join(" + ") : "—"}`;
  });
  return [
    element("span", { className: `proposal-status ${result.status}`, text: status.label, attributes: { title: status.explanation } }),
    element("p", { className: "proposal-explanation", text: status.explanation }),
    element("p", { text: sites.join(" · ") }),
    ...((result.issues || []).length ? [element("p", { className: "proposal-issues", text: result.issues.join(" · ") })] : []),
  ];
}

function compositionComparison(family) {
  const absorbers = family.absorbers || [];
  if (!absorbers.length) return element("p", { className: "muted", text: "No absorber composition was extracted." });
  return element("div", {}, absorbers.map((absorber) => {
    const result = compositionProposal(family, absorber);
    const scope = [absorber.label, absorber.layer_id].filter(Boolean).join(" · ");
    return element("section", { className: "composition-comparison" }, [
      element("div", {}, [element("span", { className: "eyebrow", text: `Source-reported composition${scope ? ` · ${scope}` : ""}` }), ...sourceComposition(absorber)]),
      element("div", {}, [element("span", { className: "eyebrow", text: "Proposed site interpretation" }), ...interpretedComposition(result)]),
    ]);
  }));
}

async function loadSession() {
  const payload = await request("/api/session");
  state.user = payload.user;
  $("reviewer").textContent = payload.user.name;
}

function loadScript(src, attributes = {}) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    Object.entries(attributes).forEach(([name, value]) => script.setAttribute(name, value));
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error("The sign-in service could not be loaded.")), { once: true });
    document.head.append(script);
  });
}

function internalSignInEnabled() {
  return state.authMode === "internal" || state.authMode === "internal_or_clerk";
}

function clerkSignInEnabled() {
  return state.authMode === "clerk" || state.authMode === "internal_or_clerk";
}

function showInternalSignIn() {
  $("internal-sign-in").hidden = !internalSignInEnabled();
  $("clerk-sign-in").hidden = true;
  $("use-email-sign-in").hidden = !clerkSignInEnabled();
  $("use-project-password").hidden = true;
  $("auth-help").textContent = "Use the review password provided by the project team.";
}

function showClerkSignIn() {
  $("internal-sign-in").hidden = true;
  $("clerk-sign-in").hidden = false;
  $("use-email-sign-in").hidden = true;
  $("use-project-password").hidden = !internalSignInEnabled();
  $("auth-help").textContent = "Sign in with your email account. Choose Forgot password in the form to receive a recovery code.";
}

function showSignIn(message = "") {
  $("workbench").hidden = true;
  $("auth-gate").hidden = false;
  $("login-status").textContent = message;
  if (internalSignInEnabled()) showInternalSignIn();
  else if (clerkSignInEnabled()) showClerkSignIn();
}

function showWorkbench() {
  $("auth-gate").hidden = true;
  $("workbench").hidden = false;
  $("sign-out").hidden = !localStorage.getItem(REVIEW_TOKEN_KEY);
}

async function initializeAuthentication() {
  const response = await fetch("/api/auth/config");
  if (!response.ok) throw new Error("The sign-in service is temporarily unavailable.");
  const config = await response.json();
  state.authMode = config.enabled ? config.mode : "local";
  if (state.authMode === "internal") {
    if (!localStorage.getItem(REVIEW_TOKEN_KEY)) {
      showSignIn();
      return false;
    }
    return true;
  }
  if (state.authMode === "internal_or_clerk" && localStorage.getItem(REVIEW_TOKEN_KEY)) return true;
  if (state.authMode === "internal_or_clerk") showSignIn();
  if (clerkSignInEnabled()) {
    await loadScript(`${config.frontend_api}/npm/@clerk/ui@1/dist/ui.browser.js`, { crossorigin: "anonymous" });
    await loadScript(`${config.frontend_api}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`, {
      crossorigin: "anonymous", "data-clerk-publishable-key": config.publishable_key,
    });
    await window.Clerk.load({
      ui: { ClerkUI: window.__internal_ClerkUICtor },
      appearance: {
        options: { elevation: "flush", socialButtonsPlacement: "bottom" },
        variables: {
          colorPrimary: "#176b52",
          colorForeground: "#17201d",
          colorMutedForeground: "#66716d",
          colorBackground: "#ffffff",
          borderRadius: "0.5rem",
          fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
        },
      },
    });
    state.clerk = window.Clerk;
    if (!state.clerk.isSignedIn) {
      if (state.authMode === "clerk") showSignIn();
      state.clerk.mountSignIn($("clerk-sign-in"));
      return false;
    }
    state.clerk.mountUserButton($("clerk-user-button"));
  }
  return true;
}

async function loadStudySchema() {
  state.studySchema = await request("/api/study-schema");
}

function paperCacheKey() { return `perla-paper-list:${state.split}`; }

function savePaperCache() {
  try { localStorage.setItem(paperCacheKey(), JSON.stringify(state.papers)); }
  catch (error) { console.warn("Could not cache the paper list", error); }
}

async function loadPapers() {
  const status = $("paper-load-status");
  status.hidden = false;
  status.textContent = "Loading papers…";
  if (!state.papers.length) {
    try {
      state.papers = JSON.parse(localStorage.getItem(paperCacheKey()) || "[]");
      if (state.papers.length) renderPapers();
    } catch { localStorage.removeItem(paperCacheKey()); }
  }
  try {
    const payload = await request(`/api/papers?split=${encodeURIComponent(state.split)}`);
    const known = new Map(state.papers.map((paper) => [paper.id, paper]));
    state.papers = payload.papers.map((paper) => {
      const cached = known.get(paper.id);
      return cached?.revision === paper.revision ? { ...cached, ...paper } : paper;
    });
    savePaperCache();
    renderPapers();
    status.hidden = true;
  } catch (error) {
    if (!state.papers.length) throw error;
    status.textContent = "Showing the saved paper list while the latest list reconnects.";
  }
}

function renderPapers() {
  const query = $("paper-filter").value.toLowerCase();
  const papers = state.papers.filter((paper) => paper.id.toLowerCase().includes(query));
  $("paper-count").textContent = papers.length;
  const cards = papers.map((paper) => {
    const completed = paper.completed_stages ? Object.keys(paper.completed_stages) : [];
    const hasCounts = Number.isInteger(paper.device_families)
      && Number.isInteger(paper.individual_devices)
      && Number.isInteger(paper.performance_observations);
    const loading = paper.id === state.loadingPaperId;
    return element("button", {
      className: `paper-card ${paper.id === state.paperId ? "selected" : ""} ${loading ? "loading" : ""}`,
      properties: { disabled: loading },
      attributes: loading ? { "aria-busy": "true" } : {},
      events: { click: () => selectPaper(paper.id) },
    }, [
      element("strong", { text: paper.id }),
      element("span", { text: hasCounts
        ? `${paper.device_families} families · ${paper.individual_devices} individual devices · ${paper.performance_observations} observations`
        : "Open to inspect extracted records" }),
      element("span", { text: loading
        ? "Loading review…"
        : `${paper.completed_stages ? `${completed.length}/4 review stages · ` : ""}revision ${paper.revision}` }),
    ]);
  });
  $("paper-list").replaceChildren(...(cards.length ? cards : [element("p", { className: "muted", text: "No imported papers in this split." })]));
}

async function selectPaper(paperId) {
  if (state.loadingPaperId) return;
  state.loadingPaperId = paperId;
  renderPapers();
  if (state.bundle) setStatus(`Loading ${paperId}…`);
  else {
    $("empty-title").textContent = "Loading paper…";
    $("empty-message").textContent = "Fetching the review records. The paper will appear as soon as they are ready.";
  }
  let bundle;
  try {
    bundle = await request(`/api/paper/${state.split}/${encodeURIComponent(paperId)}`);
  } catch (error) {
    if (state.bundle) setStatus(`Could not open ${paperId}: ${error.message}`, true);
    else {
      $("empty-title").textContent = "Could not open this paper";
      $("empty-message").textContent = error.message;
      $("empty-state").hidden = false;
    }
    return;
  } finally {
    state.loadingPaperId = null;
    renderPapers();
  }
  state.paperId = paperId;
  state.bundle = bundle;
  state.queueIndex = 0;
  state.queueKey = null;
  state.evidenceCache.clear();
  state.activeCitation = null;
  state.censusDraft = null;
  state.editingCensus = false;
  if (state.pdfObjectUrl) URL.revokeObjectURL(state.pdfObjectUrl);
  state.pdfObjectUrl = null;
  state.pdfDisplayed = null;
  $("pdf-page").removeAttribute("src");
  state.source = state.bundle.sources.includes("main") ? "main" : state.bundle.sources[0];
  state.page = 1;
  $("empty-state").hidden = true;
  $("workspace").hidden = false;
  renderStudy();
  if (window.matchMedia(LAPTOP_LAYOUT).matches) setPaperListOpen(false, false);
  savePaperCache();
  try { await renderPdf(); }
  catch (error) { setStatus(`The records are ready, but the PDF page did not load: ${error.message}`, true); }
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
  const compatibility = state.bundle.schema_compatibility;
  const compatibilityNotice = $("schema-compatibility");
  compatibilityNotice.hidden = compatibility?.exact_match !== false;
  if (compatibility?.exact_match === false) {
    compatibilityNotice.textContent = compatibility.readable_by_current_schema
      ? `This draft uses study schema v${compatibility.seed_schema_version ?? "unknown"}; the current extractor uses v${compatibility.current_schema_version}. It remains readable, but fields added since import still require review or regeneration.`
      : `This draft is not readable by current study schema v${compatibility.current_schema_version}. Re-import a current extraction before review.`;
  }
  $("pdf-source").replaceChildren(...state.bundle.sources.map((source) => element("option", {
    text: source === "main" ? "Main paper" : "Supporting information (SI)",
    properties: { value: source, selected: source === state.source },
  })));
  $("download-main-pdf").hidden = !state.bundle.sources.includes("main");
  $("download-supplement-pdf").hidden = !state.bundle.sources.includes("supplement");
  $("census-form").hidden = hasAudit() && !state.editingCensus;
  $("inventory-revealed").hidden = !hasAudit();
  renderInventoryForm();
  renderReviewQueue();
  if (hasAudit()) {
    renderInventoryComparison();
    renderQualityArtifacts();
  }
  renderStageControls();
  renderQualityGates();
  renderHistory();
}

function renderQualityArtifacts() {
  const artifacts = state.bundle.manifest.quality_artifacts || {};
  const coverage = artifacts.coverage_audit;
  const refinement = artifacts.refinement_audit;
  const repair = artifacts.targeted_repair;
  const sections = [];
  if (coverage?.counts) {
    sections.push(element("p", {
      className: "callout",
      text: `Extractor recall audit: ${coverage.counts.covered || 0} covered · ${coverage.counts.possible_match || 0} possible · ${coverage.counts.unmatched || 0} unmatched inventory candidates.`,
    }));
  }
  const changes = refinement?.collections || {};
  const changed = Object.entries(changes).flatMap(([kind, value]) => {
    const count = (value.added_ids || []).length + (value.removed_ids || []).length + (value.changed_ids || []).length;
    return count ? [`${COLLECTIONS[kind] || kind}: ${count}`] : [];
  });
  if (changed.length) {
    sections.push(element("p", {
      className: "callout",
      text: `Second extraction read changed the current draft (${changed.join(" · ")}). Compare these records with their evidence; a change is a review priority, not a correctness claim.`,
    }));
  }
  if (repair?.status) {
    const itemCount = repair.worklist?.items?.length || 0;
    sections.push(element("p", {
      className: "callout",
      text: `${REPAIR_STATUS_TEXT[repair.status] || `Targeted text reread status: ${humanLabel(repair.status)}.`} ${itemCount} audit item${itemCount === 1 ? "" : "s"} prompted this step.`,
    }));
  }
  $("quality-artifacts").replaceChildren(...sections);
}

function savedCensusDraft() {
  const audit = state.bundle?.summary.inventory_audits?.[state.user.id];
  return audit ? {
    expected_counts: structuredClone(audit.expected_counts || {}),
    main_text_figure_census: structuredClone(audit.main_text_figure_census || {}),
    missing_or_ambiguous: audit.missing_or_ambiguous || "",
  } : {
    expected_counts: {},
    main_text_figure_census: {},
    missing_or_ambiguous: "",
  };
}

function updateCensusDraft() {
  if (!document.querySelector("[data-count]")) return;
  state.censusDraft = {
    expected_counts: Object.fromEntries([...document.querySelectorAll("[data-count]")].map((input) => [input.dataset.count, Number(input.value)])),
    main_text_figure_census: {
      figures_reviewed: Number($("figures-reviewed").value),
      schema_relevant_figures: Number($("schema-relevant-figures").value),
      figure_only_records: Number($("figure-only-records").value),
      figure_only_atomic_values: Number($("figure-only-values").value),
      notes: $("figure-census-notes").value,
    },
    missing_or_ambiguous: $("inventory-notes").value,
  };
}

function renderInventoryForm() {
  state.censusDraft ||= savedCensusDraft();
  const draft = state.censusDraft;
  const definitions = Object.entries(COLLECTIONS)
    .filter(([key]) => key !== "identity_links")
    .map(([key, label]) => element("article", {}, [
      element("strong", { text: label }),
      element("p", { text: RECORD_GUIDANCE[key].census }),
    ]));
  $("inventory-definitions").replaceChildren(...definitions);
  const inputs = Object.entries(COLLECTIONS)
    .filter(([key]) => key !== "identity_links")
    .map(([key, label]) => element("label", {}, [
      element("strong", { text: label }),
      element("span", { text: RECORD_GUIDANCE[key].census }),
      element("input", { properties: { type: "number", min: "0", value: String(draft.expected_counts[key] ?? 0) }, dataset: { count: key } }),
    ]));
  $("inventory-counts").replaceChildren(...inputs);
  const figures = draft.main_text_figure_census;
  $("figures-reviewed").value = figures.figures_reviewed ?? 0;
  $("schema-relevant-figures").value = figures.schema_relevant_figures ?? 0;
  $("figure-only-records").value = figures.figure_only_records ?? 0;
  $("figure-only-values").value = figures.figure_only_atomic_values ?? 0;
  $("figure-census-notes").value = figures.notes || "";
  $("inventory-notes").value = draft.missing_or_ambiguous;
  for (const input of document.querySelectorAll("#census-form input, #census-form textarea")) input.addEventListener("input", updateCensusDraft);
  $("submit-audit").textContent = hasAudit() ? "Update census" : "Save census";
}

function editSavedCensus() {
  state.censusDraft = savedCensusDraft();
  state.editingCensus = true;
  renderStudy();
  $("census-form").scrollIntoView({ behavior: "smooth", block: "start" });
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
  const header = element("tr", {}, ["Record type", "Your census", "Current records", "Difference"].map((label) => element("th", { text: label })));
  const comparison = [
    element("h3", { text: "Saved census versus current records" }),
    element("table", {}, [element("thead", {}, [header]), element("tbody", {}, rows)]),
  ];
  const figures = audit.main_text_figure_census;
  comparison.push(figures
    ? element("section", { className: "figure-census-result" }, [
      element("h4", { text: "Main-text figure gap" }),
      element("p", { text: `${figures.schema_relevant_figures} of ${figures.figures_reviewed} reviewed figures contained schema-relevant information. A text-only extraction would miss ${figures.figure_only_records} record${figures.figure_only_records === 1 ? "" : "s"} and ${figures.figure_only_atomic_values} atomic value${figures.figure_only_atomic_values === 1 ? "" : "s"} reported only in those figures.` }),
      ...(figures.notes ? [element("p", { className: "callout", text: figures.notes })] : []),
    ])
    : element("p", { className: "callout", text: "This legacy inventory did not record a main-text figure census." }));
  if (audit.missing_or_ambiguous) comparison.push(element("p", { className: "callout", text: audit.missing_or_ambiguous }));
  $("inventory-comparison").replaceChildren(...comparison);
}

function recordEntries() {
  const truth = state.bundle.ground_truth;
  const entries = Object.fromEntries(Object.keys(COLLECTIONS).map((kind) => [kind, truth[kind].map((item, index) => ({ kind, item, index }))]));
  const familyField = state.bundle.summary.record_identifiers.device_families;
  const deviceField = state.bundle.summary.record_identifiers.individual_devices;
  const ordered = [];
  const added = new Set();
  const push = (entry) => {
    const key = recordKey(entry.kind, entry.item, entry.index);
    if (!added.has(key)) { ordered.push({ ...entry, key }); added.add(key); }
  };
  for (const family of entries.device_families) {
    push(family);
    entries.population_statistics.filter((entry) => entry.item[familyField] === family.item[familyField]).forEach(push);
    for (const device of entries.individual_devices.filter((entry) => entry.item[familyField] === family.item[familyField])) {
      push(device);
      entries.performance_observations.filter((entry) => entry.item[deviceField] === device.item[deviceField]).forEach(push);
      entries.stability_tests.filter((entry) => entry.item[deviceField] === device.item[deviceField]).forEach(push);
    }
    entries.stability_tests.filter((entry) => !entry.item[deviceField] && entry.item[familyField] === family.item[familyField]).forEach(push);
  }
  for (const device of entries.individual_devices) {
    push(device);
    entries.performance_observations.filter((entry) => entry.item[deviceField] === device.item[deviceField]).forEach(push);
    entries.stability_tests.filter((entry) => entry.item[deviceField] === device.item[deviceField]).forEach(push);
  }
  Object.values(entries).flat().forEach(push);
  return ordered;
}

function attentionReasons(entry) {
  const reasons = [];
  const decision = recordDecision(entry.kind, entry.item, entry.index);
  if (decision === "needs_correction") reasons.push({
    label: "You marked this for correction",
    explanation: "You selected Correct fields for this record. Save the correction, remove the unsupported record, or choose another review decision.",
  });
  const changes = state.bundle.manifest.quality_artifacts?.refinement_audit?.collections?.[entry.kind];
  const identifier = entityId(entry.kind, entry.item, entry.index);
  if (changes?.added_ids?.includes(identifier)) reasons.push({
    label: "Added during the second extraction read",
    explanation: "This record was absent from the first model draft and added when the model reread the evidence. Compare every field with the cited source.",
  });
  if (changes?.changed_ids?.includes(identifier)) reasons.push({
    label: "Revised during the second extraction read",
    explanation: "The record existed in the first model draft, but one or more fields changed when the model reread the evidence. The current values still need human verification.",
  });
  if (entry.kind === "device_families") {
    const results = (entry.item.absorbers || []).map((absorber) => compositionProposal(entry.item, absorber)).filter(Boolean);
    if (results.some((result) => result.status !== "accepted")) reasons.push({
      label: "A/B/X assignment needs checking",
      explanation: "At least one automated perovskite-site interpretation is incomplete, ambiguous, or rejected. Review the source formula and the proposed assignment below.",
    });
  }
  return reasons;
}

function filteredEntries() {
  const kind = $("record-kind-filter").value;
  const status = $("record-status-filter").value;
  return recordEntries().filter((entry) => {
    const decision = recordDecision(entry.kind, entry.item, entry.index);
    if (kind !== "all" && entry.kind !== kind) return false;
    if (status === "remaining") return decision !== "verified" && decision !== "uncertain";
    if (status === "attention") return attentionReasons(entry).length > 0;
    if (status === "all") return true;
    return decision === status;
  });
}

function relatedContext(entry) {
  const truth = state.bundle.ground_truth;
  const familyField = state.bundle.summary.record_identifiers.device_families;
  const deviceField = state.bundle.summary.record_identifiers.individual_devices;
  const find = (kind, value) => value ? truth[kind].find((item) => item[state.bundle.summary.record_identifiers[kind]] === value) : null;
  let device = entry.kind === "individual_devices" ? entry.item : null;
  if (!device && (entry.kind === "performance_observations" || entry.kind === "stability_tests")) device = find("individual_devices", entry.item[deviceField]);
  let family = entry.kind === "device_families" ? entry.item : find("device_families", entry.item[familyField]);
  if (!family && device) family = find("device_families", device[familyField]);
  return { family, device };
}

function recordJsonPath(entry, ...parts) {
  return `/${[entry.kind, entry.index, ...parts].map(pointerPart).join("/")}`;
}

function reportedValueItem(entry, value, path) {
  const normalized = value.value_number == null
    ? "No unambiguous numeric normalization"
    : `Parsed as ${value.value_number}${value.unit ? ` ${value.unit}` : ""}`;
  const citations = value.evidence || [];
  return element("li", { className: "reported-value" }, [
    element("div", {}, [
      element("strong", { text: value.name }),
      element("span", { className: "reported-raw", text: value.raw_value }),
      element("span", { className: "muted", text: normalized }),
      element("code", { className: "json-path", text: recordJsonPath(entry, ...path) }),
    ]),
    citations.length ? element("div", { className: "value-evidence-actions" }, citations.map((citation, index) => element("button", {
      text: citations.length === 1 ? "Show value in paper" : `Show source ${index + 1}`,
      properties: { type: "button" },
      events: { click: () => focusCitation(citation) },
    }))) : null,
  ]);
}

function reportedValueGroup(entry, title, values, path, emptyText = "None reported", indexed = true) {
  return element("section", { className: "reported-value-group" }, [
    element("h5", { text: `${title} (${values.length})` }),
    ...(values.length
      ? [element("ul", {}, values.map((value, index) => reportedValueItem(entry, value, indexed ? [...path, index] : path)))]
      : [element("p", { className: "muted", text: emptyText })]),
  ]);
}

function renderStabilityRecord(entry) {
  const test = entry.item;
  const checkpoints = test.checkpoints || [];
  const familyField = state.bundle.summary.record_identifiers.device_families;
  const deviceField = state.bundle.summary.record_identifiers.individual_devices;
  return [
    element("div", { className: "record-facts" }, [
      contextField("Aged specimen", test.specimen_label),
      contextField("Identity link", humanLabel(test.link_status)),
      contextField("Linked device", test[deviceField] || "No device link"),
      contextField("Linked family", test[familyField] || "No family link"),
    ]),
    reportedValueGroup(entry, "Test-wide conditions", test.conditions || [], ["conditions"]),
    element("section", { className: "checkpoint-list" }, [
      element("h4", { text: `Checkpoints (${checkpoints.length})` }),
      ...checkpoints.map((checkpoint, checkpointIndex) => element("details", {
        className: "stability-checkpoint",
        properties: { open: checkpointIndex === 0 },
      }, [
        element("summary", { text: `${checkpoint.checkpoint_id}${checkpoint.time ? ` · ${checkpoint.time.raw_value}` : " · time not reported"}` }),
        element("div", { className: "checkpoint-content" }, [
          reportedValueGroup(entry, "Time", checkpoint.time ? [checkpoint.time] : [], ["checkpoints", checkpointIndex, "time"], "Time not reported", false),
          reportedValueGroup(entry, "Checkpoint-specific conditions", checkpoint.conditions || [], ["checkpoints", checkpointIndex, "conditions"]),
          reportedValueGroup(entry, "Outcomes", checkpoint.outcomes || [], ["checkpoints", checkpointIndex, "outcomes"], "No outcome extracted"),
        ]),
      ])),
    ]),
  ];
}

function renderReviewTarget(entry) {
  const content = [];
  if (entry.kind === "stability_tests") content.push(...renderStabilityRecord(entry));
  else if (entry.kind === "performance_observations") {
    const deviceField = state.bundle.summary.record_identifiers.individual_devices;
    content.push(element("div", { className: "record-facts" }, [
      contextField("Measurement type", humanLabel(entry.item.measurement_type)),
      contextField("Scan direction", humanLabel(entry.item.scan_direction)),
      contextField("Linked device", entry.item[deviceField]),
    ]));
    content.push(reportedValueGroup(entry, "Performance metrics", entry.item.metrics || [], ["metrics"]));
  } else if (entry.kind === "population_statistics") {
    const familyField = state.bundle.summary.record_identifiers.device_families;
    content.push(element("div", { className: "record-facts" }, [
      contextField("Statistic", humanLabel(entry.item.statistic_type)),
      contextField("Sample size", entry.item.sample_size == null ? "Not reported" : String(entry.item.sample_size)),
      contextField("Linked family", entry.item[familyField] || "No family link"),
    ]));
    content.push(reportedValueGroup(entry, "Aggregate metrics", entry.item.metrics || [], ["metrics"]));
  } else if (entry.kind === "individual_devices") {
    content.push(reportedValueGroup(entry, "Specimen-specific properties", entry.item.reported_properties || [], ["reported_properties"]));
  }
  return element("section", { className: "review-target" }, [
    element("p", { className: "review-instruction", text: RECORD_GUIDANCE[entry.kind].review }),
    ...content,
  ]);
}

function contextField(label, value) {
  return value ? element("div", { className: "context-field" }, [element("span", { text: label }), element("strong", { text: value })]) : null;
}

function renderDeviceContext(entry) {
  const { family, device } = relatedContext(entry);
  if (!family && !device) return element("p", { className: "muted", text: "This record is not linked to a specific device or family." });
  const fields = [
    contextField("Device family", family?.label),
    contextField("Individual device", device?.label),
    contextField("Device status", device ? `${device.champion_status === "yes" ? "Champion" : "Not marked champion"} · ${device.selection_basis.replaceAll("_", " ")}` : null),
    contextField("Device-specific values", (device?.reported_properties || []).map((value) => `${value.name}: ${value.raw_value}`).join(" · ")),
    contextField("Architecture", family?.architecture || family?.polarity),
    contextField("Layer stack", (family?.layers || []).map(layerLabel).join(" / ") || family?.full_stack_raw),
    contextField("Absorbers", (family?.absorbers || []).map((absorber) => absorber.formula?.raw_value || absorber.label).join(" · ")),
  ];
  const primaryDeviceRecord = ["device_families", "individual_devices"].includes(entry.kind);
  return element("details", { className: "device-context", properties: { open: primaryDeviceRecord } }, [
    element("summary", { text: primaryDeviceRecord ? "Device structure and composition" : "Related device context (expand if needed)" }),
    element("div", { className: "context-grid" }, fields),
    family ? compositionComparison(family) : null,
  ]);
}

function resolvedSchema(node) {
  let result = node || {};
  while (result.$ref) result = state.studySchema.$defs[result.$ref.split("/").at(-1)];
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
  return draftFromSchema(state.studySchema.properties[kind].items);
}

function copiedRecordDraft(kind, item) {
  const draft = structuredClone(item);
  const identifier = state.bundle.summary.record_identifiers[kind];
  const used = new Set(state.bundle.ground_truth[kind].map((record) => String(record[identifier])));
  const stem = `${draft[identifier] || kind}-review-copy`;
  let candidate = stem;
  let suffix = 2;
  while (used.has(candidate)) candidate = `${stem}-${suffix++}`;
  draft[identifier] = candidate;
  return draft;
}

function recordReferences(entry) {
  const keys = new Set(state.bundle.summary.record_references?.[entry.key] || []);
  return recordEntries().filter((candidate) => keys.has(candidate.key));
}

function linkedRecordSummary(references) {
  const counts = references.reduce((result, reference) => {
    result[reference.kind] = (result[reference.kind] || 0) + 1;
    return result;
  }, {});
  return Object.entries(counts).map(([kind, count]) => {
    const label = count === 1 ? singularCollection(kind) : COLLECTIONS[kind];
    return `${count} ${label.toLowerCase()}`;
  }).join(" and ");
}

function normalizedCitation(value) {
  let result = "";
  for (const character of String(value || "").normalize("NFKC").replaceAll("−", "-").replaceAll("–", "-").replaceAll("—", "-")) {
    for (const folded of character.toLocaleLowerCase()) {
      if (/[\p{L}\p{N}_.%<>~=+\/-]/u.test(folded)) result += folded;
    }
  }
  return result;
}

function citationLineIndexes(lines, quote) {
  const needle = normalizedCitation(quote);
  if (!needle) return new Set();
  let haystack = "";
  const owners = [];
  lines.forEach((line, index) => {
    const normalized = normalizedCitation(line.text);
    haystack += normalized;
    owners.push(...Array(normalized.length).fill(index));
  });
  const start = haystack.indexOf(needle);
  return start < 0 ? new Set() : new Set(owners.slice(start, start + needle.length));
}

function renderCitationLocation(lines = []) {
  const citation = state.activeCitation;
  const visible = citation && citation.source === state.source && citation.page === state.page;
  $("citation-location").hidden = !visible;
  document.querySelector(".pdf-panel").classList.toggle("citation-active", Boolean(visible));
  if (!visible) return $("pdf-highlights").replaceChildren();
  $("citation-page-label").textContent = `${citation.source === "main" ? "Main paper" : "Supplement"} · page ${citation.page}`;
  $("citation-block-label").textContent = citation.block_id;
  $("citation-location-quote").textContent = citation.quote;
  const indexes = citationLineIndexes(lines, citation.quote);
  const highlights = [...indexes].flatMap((index) => {
    const box = lines[index]?.bbox;
    if (!box || ![box.x, box.y, box.width, box.height].every(Number.isFinite)) return [];
    return [element("span", {
      className: "pdf-highlight",
      attributes: { style: `left:${box.x * 100}%;top:${box.y * 100}%;width:${box.width * 100}%;height:${box.height * 100}%` },
    })];
  });
  $("pdf-highlights").replaceChildren(...highlights);
  $("citation-match-label").textContent = highlights.length
    ? "Matching text highlighted on this page"
    : "Page opened; the exact quote could not be matched to the PDF text layer";
}

function clearCitation() {
  state.activeCitation = null;
  renderCitationLocation();
}

async function navigatePdf(source, page) {
  clearCitation();
  state.source = source;
  const requestedPage = Number(page);
  state.page = Number.isFinite(requestedPage) ? Math.max(1, Math.min(state.pageCount, requestedPage)) : 1;
  $("pdf-source").value = state.source;
  try {
    if (await renderPdf()) setStatus(`Showing ${state.source === "main" ? "the main paper" : "the supplement"}, page ${state.page}.`);
  }
  catch (error) { setStatus(`Could not render this PDF page: ${error.message}`, true); }
}

async function focusCitation(citation, selectForCorrection = false, scroll = true) {
  if (!citation?.block_id) return setStatus("This record has no evidence block to show.", true);
  try {
    let block = state.evidenceCache.get(citation.block_id);
    if (!block) {
      block = await request(`/api/evidence-block/${state.split}/${encodeURIComponent(state.paperId)}/${encodeURIComponent(citation.block_id)}`);
      state.evidenceCache.set(citation.block_id, block);
    }
    const page = Number(block.page);
    if (!state.bundle.sources.includes(block.source)) throw new Error(`The ${block.source} PDF is not available for this paper.`);
    if (!Number.isInteger(page) || page < 1) throw new Error(`Evidence block ${block.block_id} has no valid PDF page.`);
    state.source = block.source;
    state.page = page;
    state.activeCitation = { block_id: block.block_id, source: block.source, page, quote: citation.quote || block.text };
    $("pdf-source").value = state.source;
    if (selectForCorrection) {
      $("citation-block").value = block.block_id;
      $("citation-quote").value = citation.quote || block.text.slice(0, 1600);
    }
    if (document.querySelector(".workspace-grid").dataset.view === "review") {
      setWorkspaceView(window.matchMedia("(max-width: 920px)").matches ? "paper" : "split", false);
    }
    if (!await renderPdf()) return false;
    if (scroll) {
      const panel = document.querySelector(".pdf-panel");
      panel.scrollTo({ top: 0, behavior: "smooth" });
      panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
    setStatus(`Showing ${block.block_id} in ${block.source === "main" ? "the main paper" : "the supplement"}, page ${page}.`);
    return true;
  } catch (error) {
    setStatus(`Could not show this citation: ${error.message}`, true);
    return false;
  }
}

function renderRecordEvidence(entry) {
  const citation = entry.item.evidence?.[0];
  if (!citation) return element("section", { className: "record-evidence" }, [element("strong", { text: "No record-level evidence supplied" })]);
  return element("section", { className: "record-evidence" }, [
    element("div", { className: "evidence-heading" }, [
      element("strong", { text: `Supporting evidence · ${citation.block_id}` }),
      element("button", { text: "Show in paper", events: { click: () => focusCitation(citation) } }),
    ]),
    element("blockquote", { text: citation.quote }),
  ]);
}

function currentEntry() {
  const entries = filteredEntries();
  if (state.queueKey) {
    const preserved = entries.findIndex((entry) => entry.key === state.queueKey);
    if (preserved >= 0) state.queueIndex = preserved;
  }
  state.queueIndex = Math.max(0, Math.min(state.queueIndex, entries.length - 1));
  const entry = entries[state.queueIndex] || null;
  state.queueKey = entry?.key || null;
  return { entries, entry };
}

function renderReviewQueue() {
  if ($("record-kind-filter").options.length === 1) {
    $("record-kind-filter").append(...Object.entries(COLLECTIONS).map(([value, text]) => element("option", { text, properties: { value } })));
  }
  const { entries, entry } = currentEntry();
  const total = state.bundle.summary.record_count;
  const decisions = state.bundle.summary.record_decisions?.[state.user.id] || {};
  const completed = Object.values(decisions).filter((value) => value === "verified" || value === "uncertain").length;
  $("queue-progress").textContent = `${completed} of ${total} reviewed · ${total - completed} remaining`;
  $("queue-position").textContent = entries.length ? `${state.queueIndex + 1} / ${entries.length}` : "0 / 0";
  $("previous-record").disabled = state.queueIndex <= 0;
  $("next-record").disabled = state.queueIndex >= entries.length - 1;
  if (!entry) {
    $("review-queue").replaceChildren(element("div", { className: "empty-queue" }, [element("h3", { text: "No records match this view" }), element("p", { text: "Change the filters or continue to the completeness check." })]));
    return;
  }
  const decision = recordDecision(entry.kind, entry.item, entry.index);
  const attention = attentionReasons(entry);
  const flags = attention.map((reason) => element("span", { className: "attention-flag", text: reason.label, attributes: { title: reason.explanation } }));
  const context = relatedContext(entry);
  const actions = element("div", { className: "queue-actions" }, [
    element("div", { className: "decision-actions" }, [
      element("button", { className: decision === "verified" ? "active" : "", text: "All fields match source  V", attributes: { title: DECISION_GUIDANCE.verified }, events: { click: () => decideEntry(entry, "verified") } }),
      element("button", { className: decision === "uncertain" ? "active" : "", text: "Cannot establish from source  U", attributes: { title: DECISION_GUIDANCE.uncertain }, events: { click: () => decideEntry(entry, "uncertain") } }),
      element("button", { className: decision === "needs_correction" ? "active" : "", text: "Correct fields  C", attributes: { title: DECISION_GUIDANCE.needs_correction }, events: { click: () => beginCorrection(entry) } }),
    ]),
    element("div", { className: "record-management-actions" }, [
      element("span", { className: "muted", text: "Record structure" }),
      element("button", { text: "Copy as missing record", events: { click: () => copyMissingRecord(entry) } }),
      element("button", { className: "remove-extra", text: "Remove extra record", events: { click: () => beginRemoval(entry) } }),
    ]),
    ...(context.device ? [element("button", {
      text: "Download Excel for this device",
      attributes: { title: "Includes this device, its family, linked performance, family statistics, and linked stability tests." },
      events: { click: (event) => runDownload(
        event.currentTarget,
        "Preparing the device review workbook…",
        "Downloaded an Excel workbook for this device and its linked context.",
        () => downloadReviewWorkbook(context.device[state.bundle.summary.record_identifiers.individual_devices]),
      ) },
    })] : []),
  ]);
  $("review-queue").replaceChildren(element("article", { className: "queue-card" }, [
    element("div", { className: "queue-heading" }, [
      element("div", {}, [element("span", { className: "eyebrow", text: `${COLLECTIONS[entry.kind]} · ${entityId(entry.kind, entry.item, entry.index)}` }), element("h3", { text: entityTitle(entry.kind, entry.item, entry.index) }), element("p", { text: entityDetail(entry.kind, entry.item) })]),
      element("div", { className: "attention-flags" }, flags),
    ]),
    ...(attention.length ? [element("div", { className: "attention-explanations" }, attention.map((reason) => element("p", {}, [
      element("strong", { text: `${reason.label}: ` }),
      reason.explanation,
    ])))] : []),
    renderReviewTarget(entry),
    renderRecordEvidence(entry),
    renderDeviceContext(entry),
    element("p", { className: "decision-help", text: "Your decision applies to the complete current record above—not only the first number or the linked device context." }),
    element("p", { className: "queue-decision-status", attributes: { id: "queue-decision-status", role: "status" } }),
    actions,
  ]));
}

async function moveQueue(delta) {
  const { entries } = currentEntry();
  state.queueIndex = Math.max(0, Math.min(state.queueIndex + delta, entries.length - 1));
  state.queueKey = entries[state.queueIndex]?.key || null;
  renderReviewQueue();
  const entry = currentEntry().entry;
  if (entry?.item.evidence?.[0]) await focusCitation(entry.item.evidence[0], false, false);
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
  const labels = {
    inventory: ["Mark census reviewed", "Census reviewed"],
    fields: ["Mark all record fields reviewed", "Record fields reviewed"],
    completeness: ["Complete paper review", "Paper review completed"],
    adjudication: ["Complete adjudication", "Adjudication completed"],
  };
  document.querySelectorAll(".complete-stage").forEach((button) => {
    const prerequisites = { inventory: hasAudit(), fields: mine("inventory") && remaining === 0, completeness: mine("fields"), adjudication: mine("completeness") };
    button.disabled = mine(button.dataset.stage) || !prerequisites[button.dataset.stage];
    button.textContent = labels[button.dataset.stage][mine(button.dataset.stage) ? 1 : 0];
    if (button.dataset.stage === "fields" && remaining > 0 && !mine("fields")) button.textContent = `Review ${remaining} remaining record${remaining === 1 ? "" : "s"}`;
  });
  $("complete-adjudication").hidden = state.user.role !== "admin";
  const finalEvent = state.bundle.events.at(-1);
  const canExport = state.user.role === "admin" && finalEvent?.kind === "stage_complete" && finalEvent?.details?.stage === "adjudication";
  $("download-truth").hidden = state.user.role !== "admin";
  $("download-truth").disabled = !canExport;
  $("final-export-help").hidden = state.user.role !== "admin" || canExport;
  $("add-record").disabled = false;
  $("new-record-kind").disabled = false;
  const recordsTab = document.querySelector('[data-tab="records"]');
  recordsTab.disabled = false;
  recordsTab.title = "Review extracted records at any time.";
  const completenessTab = document.querySelector('[data-tab="completeness"]');
  completenessTab.disabled = !hasAudit();
  completenessTab.title = hasAudit() ? "" : "Save the census before opening the final completeness check.";
}

function renderHistory() {
  const events = [...state.bundle.events].reverse().map((event) => {
    const subject = event.path || event.details?.record_key || event.details?.stage || event.note || "";
    const decision = event.details?.decision ? ` · ${DECISION_GUIDANCE[event.details.decision] || humanLabel(event.details.decision)}` : "";
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
  if (!state.paperId || !state.source) return false;
  state.pdfAbortController?.abort();
  const controller = new AbortController();
  state.pdfAbortController = controller;
  const requestId = ++state.pdfRequest;
  const paperId = state.paperId;
  const source = state.source;
  const page = state.page;
  const split = state.split;
  const sourceLabel = source === "supplement" ? "supporting information" : "main paper";
  const query = `source=${source}&split=${split}&page=${page}&scale=1.5&view=${PDF_VIEW_VERSION}`;
  const image = $("pdf-page");
  const message = $("pdf-message");
  const sourceStatus = $("pdf-current-source");
  const displayed = state.pdfDisplayed;
  $("pdf-stage").setAttribute("aria-busy", "true");
  message.hidden = false;
  message.className = "pdf-message";
  message.textContent = `Loading ${sourceLabel}, page ${page}… Large SI files can take a few seconds the first time.`;
  sourceStatus.textContent = `Loading ${sourceLabel} · page ${page}`;
  $("retry-pdf").hidden = true;
  $("pdf-canvas").hidden = !displayed || displayed.paperId !== paperId;
  $("pdf-text").textContent = "";
  for (const control of [$("previous-page"), $("next-page"), $("page-number")]) control.disabled = true;
  $("pdf-source").disabled = false;
  try {
    const loadedPage = await loadPdfPage(
      `/api/pdf-page/${encodeURIComponent(paperId)}?${query}`,
      controller.signal,
    );
    if (requestId !== state.pdfRequest || paperId !== state.paperId || source !== state.source || page !== state.page) {
      URL.revokeObjectURL(loadedPage.objectUrl);
      return false;
    }
    if (state.pdfObjectUrl) URL.revokeObjectURL(state.pdfObjectUrl);
    state.pdfObjectUrl = loadedPage.objectUrl;
    image.src = loadedPage.objectUrl;
    if (loadedPage.pageCount) state.pageCount = loadedPage.pageCount;
    state.pdfDisplayed = { paperId, source, page, pageCount: state.pageCount };
    $("page-number").value = page;
    $("page-number").max = state.pageCount;
    $("page-count").textContent = `/ ${state.pageCount}`;
    sourceStatus.textContent = `${source === "main" ? "Main paper" : "Supporting information"} · page ${page} of ${state.pageCount}`;
    $("pdf-canvas").hidden = false;
    message.hidden = true;
    for (const control of [$("previous-page"), $("next-page"), $("page-number")]) control.disabled = false;

    const text = await requestWithRetry(
      `/api/pdf-text/${encodeURIComponent(paperId)}?source=${source}&split=${split}&page=${page}&view=${PDF_VIEW_VERSION}`,
      { signal: controller.signal },
    ).then((value) => ({ value })).catch((error) => ({ error }));
    if (requestId !== state.pdfRequest || paperId !== state.paperId || source !== state.source || page !== state.page) return false;
    if (text.value) {
      if (text.value.page_count) {
        state.pageCount = text.value.page_count;
        state.pdfDisplayed.pageCount = state.pageCount;
        sourceStatus.textContent = `${source === "main" ? "Main paper" : "Supporting information"} · page ${page} of ${state.pageCount}`;
      }
      $("page-number").max = state.pageCount;
      $("page-count").textContent = `/ ${state.pageCount}`;
      $("pdf-text").textContent = text.value.text;
      renderCitationLocation(text.value.lines || []);
    } else if (text.error?.name !== "AbortError") {
      $("pdf-text").textContent = "Selectable text is temporarily unavailable. The PDF page above is still ready for review.";
      renderCitationLocation([]);
    }
    return true;
  } catch (error) {
    if (error.name === "AbortError" || requestId !== state.pdfRequest) return false;
    controller.abort();
    if (displayed?.paperId === paperId) {
      state.source = displayed.source;
      state.page = displayed.page;
      state.pageCount = displayed.pageCount;
      $("pdf-source").value = displayed.source;
      $("page-number").value = displayed.page;
      $("page-number").max = displayed.pageCount;
      $("page-count").textContent = `/ ${displayed.pageCount}`;
      sourceStatus.textContent = `${displayed.source === "main" ? "Main paper" : "Supporting information"} · page ${displayed.page} of ${displayed.pageCount}`;
      $("pdf-canvas").hidden = false;
    } else {
      sourceStatus.textContent = `${source === "main" ? "Main paper" : "Supporting information"} unavailable`;
    }
    message.className = "pdf-message error";
    message.textContent = `Could not open the ${sourceLabel}. ${error.message}${displayed?.paperId === paperId ? " The previous page is still shown." : ""}`;
    $("retry-pdf").hidden = false;
    throw error;
  } finally {
    if (requestId === state.pdfRequest) {
      $("pdf-stage").setAttribute("aria-busy", "false");
      $("pdf-source").disabled = false;
      if (!$("pdf-canvas").hidden) {
        for (const control of [$("previous-page"), $("next-page"), $("page-number")]) control.disabled = false;
      }
      if (state.pdfAbortController === controller) state.pdfAbortController = null;
    }
  }
}

async function submitAudit() {
  updateCensusDraft();
  const draft = state.censusDraft;
  try {
    state.bundle = await request(`/api/inventory-audits/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ base_revision: state.bundle.revision, review_scope_sources: state.bundle.sources, ...draft }) });
    state.censusDraft = savedCensusDraft();
    state.editingCensus = false;
    renderStudy();
    setStatus("Record and main-text figure census saved.");
  } catch (error) { setStatus(error.message, true); }
}

function openRecord(kind, index = null, template = null, intent = index == null ? "add" : "edit") {
  const source = template || (index == null ? newRecordDraft(kind) : state.bundle.ground_truth[kind][index]);
  const item = index == null && template ? copiedRecordDraft(kind, source) : structuredClone(source);
  const entry = index == null ? null : { kind, index, item, key: recordKey(kind, item, index) };
  const references = entry ? recordReferences(entry) : [];
  state.edit = { kind, index, value: item, intent, references };
  $("record-kind").textContent = COLLECTIONS[kind];
  $("record-title").textContent = index == null
    ? `Add missing ${singularCollection(kind).toLowerCase()}`
    : entityTitle(kind, item, index);
  $("record-dialog-help").textContent = intent === "copy"
    ? "This is a new record copied from the current one as a structurally valid starting point. The original stays unchanged. Check every copied value and its evidence before saving."
    : intent === "remove"
      ? "Remove this record only if it should not exist in the ground truth. Linked records are never deleted automatically."
      : index == null
        ? "Fill the missing record from the paper. The draft shape comes from the current schema; blank required fields must be completed before it can be saved."
        : "Correct this record's scientific fields. Saving replaces this record only; it does not create another device or measurement.";
  $("record-json").value = JSON.stringify(item, null, 2);
  const adding = index == null;
  $("evidence-search-label").textContent = adding ? "Choose evidence for this missing record" : "Choose correction evidence";
  $("mutation-note-label").textContent = intent === "remove" ? "Reason for removal" : adding ? "Review note" : "Reason for correction";
  $("mutation-note-help").textContent = intent === "remove" ? "Required: explain why this record is not supported by the paper." : adding ? "Optional: explain why this record was missing." : "Explain what the extraction got wrong.";
  $("mutation-note").placeholder = intent === "remove" ? "Why should this record not exist?" : adding ? "Why was this record added?" : "What did the extraction get wrong?";
  $("save-record").textContent = adding ? "Add missing record" : "Save field correction";
  $("save-record").hidden = intent === "remove";
  renderStructuredEditor();
  setRecordEditorMode("fields", false);
  const citation = item.evidence?.[0];
  $("citation-block").value = citation?.block_id || "";
  $("citation-quote").value = citation?.quote || "";
  $("mutation-note").value = "";
  $("remove-record").hidden = intent !== "remove";
  $("remove-record").disabled = references.length > 0;
  $("remove-record").textContent = references.length ? "Resolve linked records first" : "Remove extra record";
  const dependency = $("record-dependencies");
  dependency.hidden = intent !== "remove" || references.length === 0;
  dependency.replaceChildren();
  if (intent === "remove" && references.length) {
    const type = singularCollection(kind).toLowerCase();
    dependency.append(
      element("strong", { text: `${references.length} linked record${references.length === 1 ? " must" : "s must"} be handled first` }),
      element("p", { text: `This ${type} is used by ${linkedRecordSummary(references)}. Removing it now would leave ${references.length === 1 ? "that record" : "those records"} pointing to something that no longer exists.` }),
      element("p", { text: "Open each linked record below and change its family or device link. If the linked record is also unsupported by the paper, remove it instead." }),
      element("ul", {}, references.map((reference) => element("li", {}, [element("button", {
        text: `Open ${singularCollection(reference.kind).toLowerCase()} · ${entityId(reference.kind, reference.item, reference.index)}`,
        properties: { type: "button" },
        events: { click: () => reviewReferencedRecord(reference) },
      })]))),
    );
  }
  $("evidence-results").replaceChildren();
  $("dialog-status").textContent = "";
  $("record-dialog").showModal();
  if (intent === "remove") $("mutation-note").focus();
  if (citation) focusCitation(citation, false, false);
}

function humanLabel(value) {
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function recordFieldPath(path) {
  const recordIndex = state.edit.index == null ? "-" : state.edit.index;
  return `/${[state.edit.kind, recordIndex, ...path].map(pointerPart).join("/")}`;
}

function setRecordEditorMode(mode, syncFromJson = true) {
  if (mode === "fields" && syncFromJson && !$("json-editor-panel").hidden) {
    try {
      state.edit.value = JSON.parse($("record-json").value);
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

function singularCollection(kind) {
  const label = COLLECTIONS[kind];
  if (label.endsWith("ies")) return `${label.slice(0, -3)}y`;
  return label.endsWith("s") ? label.slice(0, -1) : label;
}

function updateEditValue(path, rawValue, original) {
  let parent = state.edit.value;
  for (const part of path.slice(0, -1)) parent = parent[part];
  let value = rawValue;
  if (original === null) value = rawValue === "" ? null : rawValue;
  else if (typeof original === "number") value = rawValue === "" ? null : Number(rawValue);
  else if (typeof original === "boolean") value = rawValue === "true";
  parent[path.at(-1)] = value;
  $("record-json").value = JSON.stringify(state.edit.value, null, 2);
}

function structuredLeaf(label, value, path) {
  let input;
  if (path.at(-1) === "material_form") {
    input = element("select", {}, MATERIAL_FORMS.map((form) => element("option", {
      text: humanLabel(form), properties: { value: form, selected: value === form },
    })));
  } else if (typeof value === "boolean") {
    input = element("select", {}, [element("option", { text: "true", properties: { value: "true" } }), element("option", { text: "false", properties: { value: "false" } })]);
    input.value = String(value);
  } else if (typeof value === "string" && value.length > 140) {
    input = element("textarea", { text: value, properties: { rows: 3 } });
  } else {
    input = element("input", { properties: { value: value ?? "", type: typeof value === "number" ? "number" : "text", placeholder: value === null ? "Not reported" : "" } });
  }
  input.addEventListener("input", () => updateEditValue(path, input.value, value));
  return element("label", {}, [
    element("span", { text: humanLabel(label) }),
    element("code", { className: "json-path", text: recordFieldPath(path) }),
    input,
  ]);
}

function structuredNode(label, value, path, open = false) {
  if (value == null || typeof value !== "object") return structuredLeaf(label, value, path);
  const entries = Array.isArray(value)
    ? value.map((item, index) => [item?.name || item?.material || item?.operation || `Item ${index + 1}`, item, index])
    : Object.entries(value).filter(([key]) => key !== "evidence").map(([key, item]) => [key, item, key]);
  const children = entries.map(([childLabel, item, key]) => structuredNode(childLabel, item, [...path, key]));
  const count = Array.isArray(value) ? ` (${value.length})` : "";
  return element("details", { className: "editor-group", properties: { open } }, [
    element("summary", { text: `${humanLabel(label)}${count}` }),
    element("div", { className: "editor-fields" }, children.length ? children : [element("p", { className: "muted", text: "No editable fields" })]),
  ]);
}

function renderStructuredEditor() {
  const fields = Object.entries(state.edit.value).filter(([key]) => key !== "evidence").map(([key, value]) => structuredNode(key, value, [key], true));
  $("structured-editor").replaceChildren(...fields);
}

async function submitDecision(entry, decision) {
  return request(`/api/record-decisions/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ collection: entry.kind, record_id: entityId(entry.kind, entry.item, entry.index), decision, base_revision: state.bundle.revision, note: "" }) });
}

async function decideEntry(entry, decision) {
  const title = entityTitle(entry.kind, entry.item, entry.index);
  const keepPosition = $("record-status-filter").value === "remaining";
  const actionButtons = [...document.querySelectorAll(".queue-actions button")];
  try {
    actionButtons.forEach((button) => { button.disabled = true; });
    $("queue-decision-status").textContent = `Saving “${DECISION_GUIDANCE[decision]}”…`;
    setStatus(`Saving “${DECISION_GUIDANCE[decision]}” for ${title}…`);
    state.bundle = await submitDecision(entry, decision);
    if (!keepPosition) state.queueIndex += 1;
    state.queueKey = null;
    renderStudy();
    const next = currentEntry().entry;
    if (next?.item.evidence?.[0]) await focusCitation(next.item.evidence[0], false, false);
    setStatus(`${title}: ${DECISION_GUIDANCE[decision]}.`);
  } catch (error) {
    $("queue-decision-status").textContent = error.message;
    $("queue-decision-status").className = "queue-decision-status error";
    actionButtons.forEach((button) => { button.disabled = false; });
    setStatus(error.message, true);
  }
}

function beginCorrection(entry) {
  openRecord(entry.kind, entry.index, null, "edit");
}

function beginRemoval(entry) {
  openRecord(entry.kind, entry.index, null, "remove");
}

function copyMissingRecord(entry) {
  openRecord(entry.kind, null, entry.item, "copy");
}

async function addMissingRecord() {
  document.querySelector(".add-record-menu").open = false;
  const button = $("add-record");
  try {
    if (!state.studySchema) {
      button.disabled = true;
      button.textContent = "Preparing…";
      await loadStudySchema();
    }
    openRecord($("new-record-kind").value, null, null, "add");
  } catch (error) { setStatus(`Could not prepare a new record: ${error.message}`, true); }
  finally {
    button.disabled = false;
    button.textContent = "Create draft";
  }
}

async function reviewReferencedRecord(reference) {
  $("record-dialog").close();
  $("record-status-filter").value = "all";
  $("record-kind-filter").value = reference.kind;
  state.queueIndex = 0;
  state.queueKey = reference.key;
  renderReviewQueue();
  if (reference.item.evidence?.[0]) await focusCitation(reference.item.evidence[0], false, false);
  setStatus(`Reviewing the linked ${singularCollection(reference.kind).toLowerCase()} before removing the original record.`);
}

function attachMissingEvidence(value, citation) {
  if (Array.isArray(value)) return value.forEach((item) => attachMissingEvidence(item, citation));
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value.evidence) && value.evidence.length === 0) value.evidence = [structuredClone(citation)];
  Object.values(value).forEach((item) => attachMissingEvidence(item, citation));
}

async function saveRecord() {
  try {
    const value = JSON.parse($("record-json").value);
    const { kind, index } = state.edit;
    const blockId = $("citation-block").value.trim();
    const quote = $("citation-quote").value.trim();
    if (!blockId || !quote) throw new Error("Choose an evidence block and provide an exact quote.");
    const citation = { block_id: blockId, quote };
    attachMissingEvidence(value, citation);
    const payload = { action: index == null ? "add" : "replace", path: `/${pointerPart(kind)}/${index == null ? "-" : index}`, value, evidence: [citation], note: $("mutation-note").value, base_revision: state.bundle.revision };
    state.bundle = await request(`/api/mutations/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify(payload) });
    $("record-dialog").close();
    renderStudy();
    setStatus(index == null ? `Added the missing ${singularCollection(kind).toLowerCase()} and revalidated the study.` : "Correction saved and the complete study schema revalidated.");
  } catch (error) { $("dialog-status").textContent = error.message; }
}

async function removeRecord() {
  const note = $("mutation-note").value.trim();
  if (state.edit.references.length) return $("dialog-status").textContent = "Open and resolve each linked record listed above. This record can then be removed safely.";
  if (!note) {
    $("dialog-status").textContent = "Explain why the paper does not support this record before removing it.";
    $("mutation-note").focus();
    return;
  }
  const title = entityTitle(state.edit.kind, state.edit.value, state.edit.index);
  if (!window.confirm(`Remove ${title}? Only this record will be deleted; linked records are never removed automatically.`)) return;
  try {
    const { kind, index } = state.edit;
    state.bundle = await request(`/api/mutations/${state.split}/${encodeURIComponent(state.paperId)}`, { method: "POST", body: JSON.stringify({ action: "remove", path: `/${pointerPart(kind)}/${index}`, note, evidence: [], base_revision: state.bundle.revision }) });
    $("record-dialog").close();
    renderStudy();
    setStatus(`Removed ${title}. No linked records were deleted.`);
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
    renderPdf().catch((error) => { $("dialog-status").textContent = `Could not render this evidence page: ${error.message}`; });
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
  if (tab === "completeness" && !hasAudit()) {
    setStatus("Save the census before opening the final completeness check.", true);
    return;
  }
  state.tab = tab;
  document.querySelectorAll("[data-tab]").forEach((button) => button.classList.toggle("active", button.dataset.tab === tab));
  for (const name of ["inventory", "records", "completeness", "history"]) $(`${name}-tab`).hidden = name !== tab;
  if (tab === "records") {
    renderReviewQueue();
    const citation = currentEntry().entry?.item.evidence?.[0];
    if (citation) focusCitation(citation, false, false);
  }
}

function showRevisionConflictActions() {
  $("reload-paper").hidden = false;
  $("reload-paper-dialog").hidden = false;
}

function clearRevisionConflictActions() {
  $("reload-paper").hidden = true;
  $("reload-paper-dialog").hidden = true;
}

function setStatus(message, error = false) {
  $("status").textContent = message;
  $("status").className = error ? "error" : "success";
  if (!error) clearRevisionConflictActions();
}

async function reloadLatestPaper() {
  if (!state.paperId) return;
  $("reload-paper").disabled = true;
  $("reload-paper-dialog").disabled = true;
  if ($("record-dialog").open) $("record-dialog").close();
  setStatus("Loading the latest saved version…");
  try {
    await selectPaper(state.paperId);
    setStatus("Latest saved version loaded. Review your change again before saving.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("reload-paper").disabled = false;
    $("reload-paper-dialog").disabled = false;
  }
}

function annotationSubject(event) {
  if (event.kind === "mutation") return event.details?.undoes_event_id
    ? "Reversed an earlier saved correction"
    : `${event.action} ${event.path}`;
  if (event.kind === "spreadsheet_review") {
    if (event.details?.undoes_event_id) return "Reversed an earlier reviewed workbook";
    const corrections = event.details?.changed_fields?.length || 0;
    const decisions = event.details?.decisions?.length || 0;
    const scope = event.details?.scope?.device ? ` for device ${event.details.scope.device}` : "";
    return `${corrections} scalar correction${corrections === 1 ? "" : "s"} and ${decisions} record decision${decisions === 1 ? "" : "s"}${scope}`;
  }
  if (event.kind === "record_decision") return `${event.details.record_key} · ${DECISION_GUIDANCE[event.details.decision] || humanLabel(event.details.decision)}`;
  if (event.kind === "stage_complete") return `${humanLabel(event.details.stage)} stage completed`;
  if (event.kind === "review_reset") {
    const cleared = event.details?.cleared_record_decisions || 0;
    const census = event.details?.cleared_inventory_audit ? " · census cleared" : "";
    const stages = event.details?.cleared_stages?.length || 0;
    return `${cleared} record decision${cleared === 1 ? "" : "s"} cleared${census} · ${stages} completed stage${stages === 1 ? "" : "s"} cleared`;
  }
  if (event.kind === "inventory_audit") {
    const counts = Object.entries(event.details.expected_counts || {}).map(([name, count]) => `${humanLabel(name)}: ${count}`);
    const figures = event.details.main_text_figure_census;
    if (figures) counts.push(`Main-text figure-only values: ${figures.figure_only_atomic_values}`);
    return counts.join(" · ") || "Census saved";
  }
  return event.note || humanLabel(event.kind);
}

function annotationIsCurrent(paper, event) {
  if (["record_decision", "inventory_audit", "stage_complete"].includes(event.kind)) return paper.current_event_ids?.includes(event.event_id) || false;
  return null;
}

function annotationMutationState(paper, event) {
  if (paper.undone_event_ids?.includes(event.event_id)) return "already undone";
  if (paper.undoable_event_ids?.includes(event.event_id)) return "undo available";
  return "kept in history";
}

function annotationValue(value) {
  if (value === undefined) return "Not recorded";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function annotationActionHelp(paper, event, canUndo, current) {
  if (canUndo) return "This correction is still the current version and can be safely reversed.";
  if (event.kind === "record_decision") return current
    ? "To change this decision, open the paper's Records tab and choose a different outcome."
    : "This decision is no longer part of your current review state; it remains here as history.";
  if (event.kind === "inventory_audit") return current
    ? "To change these counts, open the paper's Census tab and choose Edit saved census."
    : "A newer census replaced these counts; this entry remains as history.";
  if (event.kind === "stage_complete") return "Review milestones remain in the audit history and are not field edits.";
  if (event.kind === "review_reset") return "The reset cleared reviewer-specific progress without changing the shared scientific data.";
  if (paper.undone_event_ids?.includes(event.event_id)) return "This correction has already been undone.";
  if (["mutation", "spreadsheet_review"].includes(event.kind)) return "This older correction cannot be safely undone because the affected record changed afterward.";
  return "This saved action is part of the audit history and has no reversible data change.";
}

function annotationEvent(paper, event) {
  const current = annotationIsCurrent(paper, event);
  const stateLabel = ["mutation", "spreadsheet_review"].includes(event.kind)
    ? annotationMutationState(paper, event)
    : event.kind === "record_decision"
      ? current === true ? "current decision" : "no longer current"
      : current === true ? "current" : current === false ? "superseded" : "saved";
  const stateClass = stateLabel.replaceAll(" ", "-");
  const canUndo = paper.undoable_event_ids?.includes(event.event_id);
  const actionHelp = annotationActionHelp(paper, event, canUndo, current);
  return element("article", { className: "annotation-event" }, [
    element("div", { className: "annotation-event-heading" }, [
      element("strong", { text: `r${event.revision} · ${humanLabel(event.kind)}` }),
      element("span", { className: `annotation-state ${stateClass}`, text: stateLabel }),
    ]),
    element("p", { text: annotationSubject(event) }),
    element("span", { className: "muted", text: new Date(event.timestamp).toLocaleString() }),
    ...(event.note ? [element("p", { className: "annotation-note", text: event.note })] : []),
    ...(event.kind === "mutation" ? [element("div", { className: "annotation-change" }, [
      element("div", {}, [element("span", { className: "eyebrow", text: "Before" }), element("pre", { text: annotationValue(event.before) })]),
      element("div", {}, [element("span", { className: "eyebrow", text: "After" }), element("pre", { text: annotationValue(event.after) })]),
    ])] : []),
    ...(event.kind === "spreadsheet_review" && event.details?.record_replacements?.length ? [element("details", { className: "annotation-json" }, [
      element("summary", { text: `Inspect ${event.details.record_replacements.length} corrected record${event.details.record_replacements.length === 1 ? "" : "s"}` }),
      element("pre", { text: JSON.stringify(event.details.record_replacements, null, 2) }),
    ])] : []),
    element("p", { className: "annotation-action-help", text: actionHelp }),
    ...(canUndo ? [element("button", {
      text: "Undo this saved edit",
      properties: { type: "button" },
      events: { click: (clickEvent) => undoAnnotation(paper, event, clickEvent.currentTarget) },
    })] : []),
    element("details", { className: "annotation-json" }, [
      element("summary", { text: "Inspect exact saved event" }),
      element("pre", { text: JSON.stringify(event, null, 2) }),
    ]),
  ]);
}

async function undoAnnotation(paper, event, button) {
  if (!window.confirm("Undo this saved correction? The workbench will preserve both the original edit and the undo in its history.")) return;
  button.disabled = true;
  $("annotation-status").textContent = "Undoing and validating the complete study…";
  $("annotation-status").className = "";
  try {
    const bundle = await request(`/api/mutation-undos/${state.split}/${encodeURIComponent(paper.paper_id)}`, {
      method: "POST",
      body: JSON.stringify({ event_id: event.event_id, base_revision: paper.current_revision }),
    });
    if (state.paperId === paper.paper_id) {
      state.bundle = bundle;
      renderStudy();
    }
    await loadReviewerProgress();
    $("annotation-status").textContent = "Saved edit undone. Both actions remain visible in the audit history.";
    $("annotation-status").className = "success";
  } catch (error) {
    if (error.code === "review_revision_conflict") {
      await loadReviewerProgress();
      if (state.paperId === paper.paper_id) {
        state.bundle = await request(`/api/paper/${state.split}/${encodeURIComponent(paper.paper_id)}`);
        renderStudy();
      }
      clearRevisionConflictActions();
    }
    $("annotation-status").textContent = error.code === "review_revision_conflict"
      ? "This paper changed in another session. The latest activity is loaded; check the edit and try Undo again."
      : error.message;
    $("annotation-status").className = "error";
  } finally {
    button.disabled = false;
  }
}

function paperCurrentCounts(paper) {
  return {
    decisions: Object.keys(paper.current_record_decisions || {}).length,
    census: Number(paper.current_inventory_audit != null),
    stages: paper.completed_stages?.length || 0,
    corrections: paper.undoable_event_ids?.length || 0,
  };
}

async function openProgressPaper(paper, tab = "records") {
  $("annotations-dialog").close();
  try {
    if (state.paperId !== paper.paper_id) await selectPaper(paper.paper_id);
    if (state.paperId !== paper.paper_id) return;
    setTab(tab);
  } catch (error) { setStatus(`Could not open ${paper.paper_id}: ${error.message}`, true); }
}

function currentWorkPaper(paper) {
  const counts = paperCurrentCounts(paper);
  const decisionItems = Object.entries(paper.current_record_decisions || {}).map(([key, decision]) => element("li", {}, [
    element("code", { text: key }),
    element("span", { text: DECISION_GUIDANCE[decision] || humanLabel(decision) }),
  ]));
  const corrections = paper.events.filter((event) => paper.undoable_event_ids?.includes(event.event_id));
  return element("section", { className: "annotation-paper current-work-paper" }, [
    element("div", { className: "annotation-paper-heading" }, [
      element("div", {}, [
        element("strong", { text: paper.paper_id }),
        element("span", { className: "muted", text: `Last saved ${new Date(paper.last_saved_at).toLocaleString()}` }),
      ]),
      element("span", { className: "pill", text: `${counts.decisions + counts.census + counts.stages} progress item${counts.decisions + counts.census + counts.stages === 1 ? "" : "s"}` }),
    ]),
    element("div", { className: "current-work-counts" }, [
      element("span", { text: `${counts.decisions} record decision${counts.decisions === 1 ? "" : "s"}` }),
      element("span", { text: counts.census ? "Census saved" : "No census saved" }),
      element("span", { text: `${counts.stages} stage${counts.stages === 1 ? "" : "s"} completed` }),
      ...(counts.corrections ? [element("span", { text: `${counts.corrections} reversible scientific edit${counts.corrections === 1 ? "" : "s"}` })] : []),
    ]),
    ...(decisionItems.length ? [element("details", { className: "current-decisions" }, [
      element("summary", { text: `Inspect ${decisionItems.length} current record decision${decisionItems.length === 1 ? "" : "s"}` }),
      element("ul", {}, decisionItems),
    ])] : []),
    ...(paper.completed_stages.length ? [element("p", { className: "muted", text: `Completed: ${paper.completed_stages.map(humanLabel).join(" · ")}` })] : []),
    ...(corrections.length ? [element("div", { className: "current-corrections" }, [
      element("strong", { text: "Scientific edits that can still be undone" }),
      ...corrections.map((event) => annotationEvent(paper, event)),
    ])] : []),
    element("div", { className: "annotation-paper-actions" }, [
      element("button", { text: "Continue reviewing records", properties: { type: "button" }, events: { click: () => openProgressPaper(paper, "records") } }),
      element("button", { text: counts.census ? "Open saved census" : "Open census", properties: { type: "button" }, events: { click: () => openProgressPaper(paper, "inventory") } }),
      ...(paper.resettable_review_count ? [element("button", { className: "danger", text: "Reset this paper", properties: { type: "button" }, events: { click: (event) => resetPaperProgress(paper, event.currentTarget) } })] : []),
    ]),
  ]);
}

function historyPaper(paper) {
  return element("section", { className: "annotation-paper" }, [
    element("div", { className: "annotation-paper-heading" }, [
      element("div", {}, [
        element("strong", { text: paper.paper_id }),
        element("span", { className: "muted", text: `Current paper revision ${paper.current_revision}` }),
      ]),
      element("span", { className: "pill", text: `${paper.events.length} saved` }),
    ]),
    ...[...paper.events].reverse().map((event) => annotationEvent(paper, event)),
  ]);
}

function renderReviewerProgress(progress) {
  state.annotations = progress;
  const history = [...progress.papers].sort((left, right) => right.last_saved_at.localeCompare(left.last_saved_at));
  const current = history.filter((paper) => paper.resettable_review_count > 0 || paper.undoable_event_ids?.length);
  const reversibleEdits = current.reduce((count, paper) => count + (paper.undoable_event_ids?.length || 0), 0);
  const showingCurrent = state.annotationView === "current";
  $("show-current-annotations").classList.toggle("active", showingCurrent);
  $("show-current-annotations").setAttribute("aria-pressed", String(showingCurrent));
  $("show-annotation-history").classList.toggle("active", !showingCurrent);
  $("show-annotation-history").setAttribute("aria-pressed", String(!showingCurrent));
  $("annotation-help").textContent = showingCurrent
    ? "Continue a paper or reset reviewer-only progress here. Reset clears your decisions, census, and completed stages; it does not alter scientific corrections or the audit history."
    : "This is the complete append-only history of what you saved. Current and superseded labels describe whether an action still affects your review state.";
  $("annotation-summary").replaceChildren(element("p", { text: showingCurrent
    ? `${progress.resettable_review_count} current decision or progress item${progress.resettable_review_count === 1 ? "" : "s"} and ${reversibleEdits} reversible scientific edit${reversibleEdits === 1 ? "" : "s"} across ${current.length} paper${current.length === 1 ? "" : "s"}.`
    : `${progress.annotation_count} saved action${progress.annotation_count === 1 ? "" : "s"} across ${progress.paper_count} paper${progress.paper_count === 1 ? "" : "s"}.` }));
  $("reset-annotations").hidden = !showingCurrent;
  $("reset-annotations").disabled = progress.resettable_review_count === 0;
  const papers = showingCurrent ? current : history;
  const cards = papers.map(showingCurrent ? currentWorkPaper : historyPaper);
  $("annotation-list").replaceChildren(...(cards.length ? cards : [element("div", { className: "empty-queue" }, [
    element("strong", { text: showingCurrent ? "No current review work" : "No saved activity" }),
    element("p", { text: showingCurrent ? "Your decisions, census, and completed stages are clear in this dataset. Open History to inspect earlier actions." : "You have not saved review activity in this dataset yet." }),
  ])]));
}

function setAnnotationView(view) {
  state.annotationView = view;
  if (state.annotations) renderReviewerProgress(state.annotations);
}

async function applyReviewerResets(papers, button) {
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  const failures = [];
  let resetCount = 0;
  for (const paper of papers) {
    try {
      const bundle = await request(`/api/reviewer-resets/${state.split}/${encodeURIComponent(paper.paper_id)}`, {
        method: "POST",
        body: JSON.stringify({ base_revision: paper.current_revision }),
      });
      resetCount += 1;
      if (state.paperId === paper.paper_id) state.bundle = bundle;
    } catch (error) { failures.push(`${paper.paper_id}: ${error.message}`); }
  }
  await loadReviewerProgress();
  if (state.bundle) renderStudy();
  button.removeAttribute("aria-busy");
  return { resetCount, failures };
}

function reportReviewerReset({ resetCount, failures }) {
  if (failures.length) {
    $("annotation-status").textContent = `${resetCount} paper${resetCount === 1 ? "" : "s"} reset. Could not reset ${failures.join("; ")}`;
    $("annotation-status").className = "error";
  } else {
    $("annotation-status").textContent = `Current review progress reset for ${resetCount} paper${resetCount === 1 ? "" : "s"}. Saved history is unchanged.`;
    $("annotation-status").className = "success";
  }
}

async function resetPaperProgress(paper, button) {
  if (!window.confirm(`Reset your current record decisions, census, and completed stages for ${paper.paper_id}? Scientific corrections and saved history are kept.`)) return;
  $("annotation-status").textContent = `Resetting current progress for ${paper.paper_id}…`;
  $("annotation-status").className = "";
  try { reportReviewerReset(await applyReviewerResets([paper], button)); }
  catch (error) { $("annotation-status").textContent = error.message; $("annotation-status").className = "error"; button.disabled = false; button.removeAttribute("aria-busy"); }
}

async function resetReviewerProgress() {
  const button = $("reset-annotations");
  try {
    const progress = await loadReviewerProgress();
    const papers = progress.papers.filter((paper) => paper.resettable_review_count > 0);
    if (!papers.length) return;
    if (!window.confirm(`Reset all ${progress.resettable_review_count} current decision or progress item${progress.resettable_review_count === 1 ? "" : "s"} across ${papers.length} paper${papers.length === 1 ? "" : "s"} in ${progress.split}? Scientific corrections and saved history are kept.`)) return;
    $("annotation-status").textContent = "Resetting current review progress…";
    $("annotation-status").className = "";
    reportReviewerReset(await applyReviewerResets(papers, button));
  } catch (error) { $("annotation-status").textContent = error.message; $("annotation-status").className = "error"; button.disabled = false; button.removeAttribute("aria-busy"); }
}

function reviewerProgressCacheKey() { return `perla-review-progress:${state.user.id}:${state.split}`; }

function saveReviewerProgressCache(progress) {
  try { sessionStorage.setItem(reviewerProgressCacheKey(), JSON.stringify(progress)); }
  catch (error) { console.warn("Could not cache review progress", error); }
}

function cachedReviewerProgress() {
  try { return JSON.parse(sessionStorage.getItem(reviewerProgressCacheKey()) || "null"); }
  catch { sessionStorage.removeItem(reviewerProgressCacheKey()); return null; }
}

async function loadReviewerProgress() {
  const progress = await request(`/api/reviewer-progress/${encodeURIComponent(state.split)}`);
  state.annotations = progress;
  saveReviewerProgressCache(progress);
  renderReviewerProgress(progress);
  return progress;
}

async function openReviewerProgress() {
  if (!state.user) {
    setStatus("Sign in before opening saved review progress.", true);
    return;
  }
  state.annotationView = "current";
  $("annotations-dialog").showModal();
  const cached = state.annotations || cachedReviewerProgress();
  if (cached) renderReviewerProgress(cached);
  else {
    $("annotation-summary").replaceChildren(element("p", { className: "muted", text: "Loading your current review work…" }));
    $("annotation-list").replaceChildren();
  }
  $("annotation-status").textContent = cached ? "Refreshing saved progress…" : "";
  $("annotation-status").className = "";
  try { await loadReviewerProgress(); $("annotation-status").textContent = ""; }
  catch (error) { $("annotation-status").textContent = error.message; $("annotation-status").className = "error"; }
}

async function downloadReviewerProgress() {
  const progress = await loadReviewerProgress();
  const blob = new Blob([`${JSON.stringify(progress, null, 2)}\n`], { type: "application/json" });
  saveBlob(blob, `perla-${state.user.id}-${state.split}-annotations.json`);
}

function saveBlob(blob, filename) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 0);
}

async function downloadResponse(url, filename) {
  const response = await fetch(url, {
    headers: await authorizationHeaders(),
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  saveBlob(await response.blob(), filename);
}

async function downloadStudyJson() {
  const { paperId, split } = state;
  const bundle = await request(`/api/paper/${split}/${encodeURIComponent(paperId)}`);
  const blob = new Blob([`${JSON.stringify(bundle.ground_truth, null, 2)}\n`], { type: "application/json" });
  saveBlob(blob, `${paperId}.${split}.revision-${bundle.revision}.study.json`);
  return bundle.revision;
}

async function downloadReviewWorkbook(deviceId = null) {
  const { paperId, split } = state;
  const query = deviceId ? `?device=${encodeURIComponent(deviceId)}` : "";
  const scope = deviceId ? `.${deviceId}` : "";
  await downloadResponse(
    `/api/review-workbook/${split}/${encodeURIComponent(paperId)}${query}`,
    `${paperId}${scope}.review.xlsx`,
  );
}

async function uploadReviewWorkbook(file) {
  if (!file) return;
  if (!window.confirm("Upload this reviewed workbook? Valid corrections and record decisions will be saved together as one review revision.")) {
    $("review-workbook-file").value = "";
    return;
  }
  const form = new FormData();
  form.append("workbook", file);
  form.append("filename", file.name);
  setStatus("Validating the workbook and the complete study…");
  try {
    state.bundle = await request(
      `/api/review-workbook/${state.split}/${encodeURIComponent(state.paperId)}`,
      { method: "POST", body: form },
    );
    renderStudy();
    await loadReviewerProgress();
    setStatus("Reviewed workbook saved as one validated revision. The import is visible in My edits & undo.");
  } catch (error) {
    setStatus(error.message, true);
  } finally {
    $("review-workbook-file").value = "";
  }
}

async function downloadPaper(source) {
  const { paperId, split } = state;
  const suffix = source === "supplement" ? ".supplement.pdf" : ".pdf";
  const url = `/api/pdf/${encodeURIComponent(paperId)}?source=${source}&split=${encodeURIComponent(split)}`;
  await downloadResponse(url, `${paperId}${suffix}`);
}

async function runDownload(button, loadingMessage, completeMessage, operation) {
  const label = button.textContent;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  button.textContent = "Preparing…";
  setFileStatus(loadingMessage);
  try {
    const result = await operation();
    setFileStatus(typeof completeMessage === "function" ? completeMessage(result) : completeMessage);
  } catch (error) {
    setFileStatus(error.message, true);
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
    button.textContent = label;
  }
}

function setFileStatus(message, error = false) {
  for (const id of ["file-action-status", "files-status"]) {
    const status = $(id);
    status.hidden = false;
    status.textContent = message;
    status.className = error ? "error" : "success";
  }
  setStatus(message, error);
}

async function downloadGroundTruth() {
  await downloadResponse(
    `/api/ground-truth-export/${state.split}/${encodeURIComponent(state.paperId)}`,
    `${state.paperId}.ground-truth.zip`,
  );
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

$("split").addEventListener("change", async (event) => { state.pdfAbortController?.abort(); state.split = event.target.value; state.papers = []; state.paperId = null; state.bundle = null; state.annotations = null; state.censusDraft = null; state.editingCensus = false; $("workspace").hidden = true; $("empty-title").textContent = "Choose a paper"; $("empty-message").textContent = "Review the extracted records beside the paper, record what is missing, and count information that appears only in main-text figures."; $("empty-state").hidden = false; try { await loadPapers(); } catch (error) { showStartupError(error); } });
$("toggle-paper-list").addEventListener("click", () => setPaperListOpen(document.querySelector("main").classList.contains("paper-list-hidden")));
document.querySelectorAll("[data-workspace-view]").forEach((button) => button.addEventListener("click", () => setWorkspaceView(button.dataset.workspaceView)));
$("paper-filter").addEventListener("input", renderPapers);
$("submit-audit").addEventListener("click", submitAudit);
$("edit-census").addEventListener("click", editSavedCensus);
$("retry-pdf").addEventListener("click", () => navigatePdf(state.source, state.page));
$("pdf-source").addEventListener("change", (event) => navigatePdf(event.target.value, 1));
$("previous-page").addEventListener("click", () => navigatePdf(state.source, state.page - 1));
$("next-page").addEventListener("click", () => navigatePdf(state.source, state.page + 1));
$("page-number").addEventListener("change", (event) => navigatePdf(state.source, Number(event.target.value)));
$("page-number").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    navigatePdf(state.source, Number(event.target.value));
  }
});
$("clear-citation").addEventListener("click", clearCitation);
document.querySelectorAll("[data-tab]").forEach((button) => button.addEventListener("click", () => setTab(button.dataset.tab)));
document.querySelectorAll(".complete-stage").forEach((button) => button.addEventListener("click", () => completeStage(button.dataset.stage)));
$("add-record").addEventListener("click", addMissingRecord);
$("record-status-filter").addEventListener("change", () => { state.queueIndex = 0; state.queueKey = null; renderReviewQueue(); });
$("record-kind-filter").addEventListener("change", () => { state.queueIndex = 0; state.queueKey = null; renderReviewQueue(); });
$("previous-record").addEventListener("click", () => moveQueue(-1));
$("next-record").addEventListener("click", () => moveQueue(1));
$("show-fields-editor").addEventListener("click", () => setRecordEditorMode("fields"));
$("show-json-editor").addEventListener("click", () => setRecordEditorMode("json"));
$("close-record").addEventListener("click", () => $("record-dialog").close());
$("cancel-record").addEventListener("click", () => $("record-dialog").close());
$("save-record").addEventListener("click", saveRecord);
$("remove-record").addEventListener("click", removeRecord);
$("reload-paper").addEventListener("click", reloadLatestPaper);
$("reload-paper-dialog").addEventListener("click", reloadLatestPaper);
$("search-evidence").addEventListener("click", searchEvidence);
$("evidence-query").addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); searchEvidence(); } });
$("open-files").addEventListener("click", () => $("files-dialog").showModal());
$("download-truth").addEventListener("click", (event) => runDownload(
  event.currentTarget,
  "Preparing the adjudicated PR bundle…",
  "Downloaded the adjudicated PR bundle.",
  downloadGroundTruth,
));
$("download-study-json").addEventListener("click", (event) => runDownload(
  event.currentTarget,
  "Preparing the latest validated study JSON…",
  (revision) => `Downloaded study JSON from revision ${revision}. Local changes are not saved in the workbench.`,
  downloadStudyJson,
));
$("download-review-workbook").addEventListener("click", (event) => runDownload(
  event.currentTarget,
  "Preparing an editable workbook for the latest paper revision…",
  "Downloaded the all-record Excel review workbook.",
  () => downloadReviewWorkbook(),
));
$("download-review-workbook-dialog").addEventListener("click", (event) => runDownload(
  event.currentTarget,
  "Preparing an editable workbook for the latest paper revision…",
  "Downloaded the all-record Excel review workbook.",
  () => downloadReviewWorkbook(),
));
$("review-workbook-file").addEventListener("change", (event) => uploadReviewWorkbook(event.target.files[0]));
$("download-main-pdf").addEventListener("click", (event) => runDownload(
  event.currentTarget,
  "Preparing the main paper PDF…",
  "Downloaded the main paper PDF.",
  () => downloadPaper("main"),
));
$("download-supplement-pdf").addEventListener("click", (event) => runDownload(
  event.currentTarget,
  "Preparing the supporting information PDF…",
  "Downloaded the supporting information PDF.",
  () => downloadPaper("supplement"),
));
$("open-annotations").addEventListener("click", openReviewerProgress);
$("show-current-annotations").addEventListener("click", () => setAnnotationView("current"));
$("show-annotation-history").addEventListener("click", () => setAnnotationView("history"));
$("reset-annotations").addEventListener("click", resetReviewerProgress);
$("download-annotations").addEventListener("click", async () => {
  try {
    await downloadReviewerProgress();
    $("annotation-status").textContent = "Downloaded your current persisted annotations.";
    $("annotation-status").className = "success";
  } catch (error) { $("annotation-status").textContent = error.message; $("annotation-status").className = "error"; }
});
$("open-import").addEventListener("click", () => $("import-dialog").showModal());
$("close-import").addEventListener("click", () => $("import-dialog").close());
$("cancel-import").addEventListener("click", () => $("import-dialog").close());
$("import-form").addEventListener("submit", importPaper);
document.addEventListener("keydown", (event) => {
  if (state.tab !== "records" || $("record-dialog").open || ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  const entry = currentEntry().entry;
  if (!entry) return;
  const key = event.key.toLowerCase();
  if (key === "arrowright" || key === "j") moveQueue(1);
  else if (key === "arrowleft" || key === "k") moveQueue(-1);
  else if (key === "v") decideEntry(entry, "verified");
  else if (key === "u") decideEntry(entry, "uncertain");
  else if (key === "c") beginCorrection(entry);
  else return;
  event.preventDefault();
});

function showStartupError(error) {
  if (error.status === 401) {
    state.user = null;
    if (internalSignInEnabled()) localStorage.removeItem(REVIEW_TOKEN_KEY);
    showSignIn("Your session expired. Sign in again to continue; your saved reviews are unchanged.");
    return;
  }
  $("paper-load-status").hidden = true;
  $("empty-title").textContent = "The review workspace did not load";
  $("empty-message").textContent = `${error.message} Your saved reviews are unchanged.`;
  $("retry-startup").hidden = false;
  $("reviewer").textContent = state.user?.name || "Not connected";
}

async function startApp() {
  $("retry-startup").hidden = true;
  $("empty-title").textContent = "Loading review workspace…";
  $("empty-message").textContent = "Signing you in and fetching the paper list.";
  try {
    if (!state.user) await loadSession();
    await loadPapers();
    $("empty-title").textContent = "Choose a paper";
    $("empty-message").textContent = "Review the extracted records beside the paper, record what is missing, and count information that appears only in main-text figures.";
  } catch (error) { showStartupError(error); }
}

$("retry-startup").addEventListener("click", startApp);

$("use-email-sign-in").addEventListener("click", showClerkSignIn);
$("use-project-password").addEventListener("click", showInternalSignIn);

initializeWorkspaceLayout();

$("internal-sign-in").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  submit.disabled = true;
  $("login-status").textContent = "Signing in…";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("login-email").value, password: $("login-password").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Sign-in failed.");
    localStorage.setItem(REVIEW_TOKEN_KEY, payload.token);
    state.user = payload.user;
    $("login-password").value = "";
    showWorkbench();
    await startApp();
  } catch (error) {
    showSignIn(error.message);
  } finally {
    submit.disabled = false;
  }
});

$("sign-out").addEventListener("click", () => {
  localStorage.removeItem(REVIEW_TOKEN_KEY);
  state.user = null;
  state.papers = [];
  showSignIn("Signed out.");
});

try {
  if (await initializeAuthentication()) {
    showWorkbench();
    await startApp();
  }
} catch (error) {
  showSignIn(error.message);
}
