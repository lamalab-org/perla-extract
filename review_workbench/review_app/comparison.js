const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "review-token";
const state = {
  clerk: null, user: null, assignments: [], current: null, source: "main", page: 1,
  pageCount: 1, pdfUrl: null, activeSeconds: 0, lastTick: Date.now(), judgments: new Map(),
  missingFacts: [], native: null, nativeSeconds: 0, preference: null,
  preferenceSeconds: 0,
};
async function headers(extra = {}) {
  const local = localStorage.getItem(TOKEN_KEY);
  if (local) return { ...extra, Authorization: `Bearer ${local}` };
  const token = await state.clerk?.session?.getToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function request(url, options = {}) {
  const response = await fetch(url, { ...options, headers: await headers(options.headers || {}) });
  if (!response.ok) {
    const value = response.headers.get("content-type")?.includes("json") ? await response.json() : await response.text();
    const error = new Error(value.error || `Request failed (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return options.blob ? response.blob() : response.json();
}

function loadScript(src, attributes = {}) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    Object.entries(attributes).forEach(([key, value]) => script.setAttribute(key, value));
    script.onload = resolve;
    script.onerror = reject;
    document.head.append(script);
  });
}

async function initializeAuth() {
  const config = await fetch("/api/auth/config").then((response) => response.json());
  if (!localStorage.getItem(TOKEN_KEY) && ["clerk", "internal_or_clerk"].includes(config.mode)) {
    await loadScript(`${config.frontend_api}/npm/@clerk/ui@1/dist/ui.browser.js`, { crossorigin: "anonymous" });
    await loadScript(`${config.frontend_api}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`, {
      crossorigin: "anonymous", "data-clerk-publishable-key": config.publishable_key,
    });
    await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
    state.clerk = window.Clerk;
  }
  try {
    state.user = (await request("/api/session")).user;
  } catch (error) {
    if (error.status === 401) window.location.assign("/");
    throw error;
  }
  $("reviewer").textContent = state.user.name;
  $("open-import").hidden = state.user.role !== "admin";
}

function updateTimer() {
  const now = Date.now();
  if (state.current && !state.current.review.submitted_at && document.visibilityState === "visible") {
    state.activeSeconds += Math.min(5, Math.round((now - state.lastTick) / 1000));
  } else if (state.native && !state.native.review && document.visibilityState === "visible") {
    state.nativeSeconds += Math.min(5, Math.round((now - state.lastTick) / 1000));
  } else if (state.preference && !state.preference.review && document.visibilityState === "visible") {
    state.preferenceSeconds += Math.min(5, Math.round((now - state.lastTick) / 1000));
  }
  state.lastTick = now;
}
setInterval(updateTimer, 1000);
document.addEventListener("visibilitychange", () => { state.lastTick = Date.now(); });

function optionValues(select) {
  for (let value = 1; value <= 5; value += 1) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = String(value);
    select.append(option);
  }
}
[
  "chemical-detail", "relationships", "verification-ease", "nomad-usefulness", "confidence",
  "preference-confidence",
].forEach((id) => optionValues($(id)));

function renderList() {
  const list = $("comparison-list");
  list.replaceChildren();
  if (!state.assignments.length) {
    const text = document.createElement("p");
    text.className = "muted";
    text.textContent = "No comparison papers are assigned to you yet.";
    list.append(text);
    return;
  }
  state.assignments.forEach((assignment) => {
    const card = document.createElement(assignment.assigned ? "button" : "article");
    card.className = `paper-card${state.current?.comparison_id === assignment.comparison_id ? " selected" : ""}`;
    card.innerHTML = `<strong></strong><span></span>`;
    card.querySelector("strong").textContent = assignment.title;
    card.querySelector("span").textContent = assignment.assigned
      ? `${assignment.status.replaceAll("_", " ")} · Candidate ${assignment.blind_label}`
      : `Administrator view · ${assignment.status.replaceAll("_", " ")}`;
    if (assignment.assigned) card.onclick = () => openComparison(assignment.comparison_id);
    if (!assignment.assigned && assignment.batch_ready) {
      const download = document.createElement("button");
      download.type = "button";
      download.textContent = "Download completed analysis";
      download.onclick = () => downloadAnalysis(assignment.comparison_id);
      card.append(download);
    }
    list.append(card);
  });
}

async function downloadAnalysis(comparisonId) {
  try {
    const payload = await request(`/api/comparison-export/${encodeURIComponent(comparisonId)}`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${comparisonId}.expert-comparison.json`;
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) { $("reviewer").textContent = error.message; }
}

function fieldNode(field, locked) {
  const row = document.createElement("div");
  row.className = "atomic-field";
  const value = document.createElement("div");
  value.className = "atomic-value";
  const label = document.createElement("span");
  label.textContent = field.label;
  const strong = document.createElement("strong");
  strong.textContent = String(field.value);
  const path = document.createElement("code");
  path.textContent = field.path;
  value.append(label, strong, path);
  const controls = document.createElement("div");
  controls.className = "verdicts";
  const existing = state.judgments.get(field.field_key);
  [
    ["correct", "Correct"], ["incorrect", "Incorrect"],
    ["unsupported", "Unsupported"], ["cannot_determine", "Cannot tell"],
  ].forEach(([verdict, text]) => {
    const choice = document.createElement("label");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = `verdict-${field.field_key}`;
    input.value = verdict;
    input.checked = existing?.verdict === verdict;
    input.disabled = locked;
    choice.append(input, text);
    controls.append(choice);
    input.onchange = () => {
      const current = state.judgments.get(field.field_key) || { field_key: field.field_key };
      current.verdict = verdict;
      if (["incorrect", "unsupported"].includes(verdict)) {
        current.reference ||= { source: state.source, page: state.page, quote: "" };
        source.value = current.reference.source;
        page.value = current.reference.page;
        quote.value = current.reference.quote;
      } else delete current.reference;
      state.judgments.set(field.field_key, current);
      evidence.hidden = !["incorrect", "unsupported"].includes(verdict);
      updateProgress();
    };
  });
  const evidence = document.createElement("div");
  evidence.className = "negative-evidence";
  evidence.hidden = !["incorrect", "unsupported"].includes(existing?.verdict);
  const source = document.createElement("select");
  source.innerHTML = '<option value="main">Main paper</option><option value="supplement">Supporting information</option>';
  source.value = existing?.reference?.source || state.source;
  const page = document.createElement("input");
  page.type = "number"; page.min = "1"; page.value = existing?.reference?.page || state.page;
  const quote = document.createElement("input");
  quote.placeholder = "Short supporting quote (optional)";
  quote.value = existing?.reference?.quote || "";
  [source, page, quote].forEach((input) => { input.disabled = locked; });
  const saveEvidence = () => {
    const current = state.judgments.get(field.field_key);
    if (current && ["incorrect", "unsupported"].includes(current.verdict)) {
      current.reference = { source: source.value, page: Number(page.value), quote: quote.value.trim() };
    }
  };
  [source, page, quote].forEach((input) => input.addEventListener("change", saveEvidence));
  evidence.append(source, page, quote);
  row.append(value, controls, evidence);
  return row;
}

function renderRecords() {
  const container = $("records");
  container.replaceChildren();
  const locked = Boolean(state.current.review.submitted_at);
  state.current.records.forEach((record) => {
    const article = document.createElement("article");
    article.className = "neutral-record";
    const heading = document.createElement("header");
    const title = document.createElement("h4");
    title.textContent = `${record.record_key} · ${record.summary}`;
    heading.append(title);
    article.append(heading, ...record.fields.map((field) => fieldNode(field, locked)));
    if (!record.fields.length) {
      const note = document.createElement("p");
      note.className = "repeated-record-note";
      note.textContent = "This row repeats scalar claims already shown. Count it as a record when checking missing or extra rows.";
      article.append(note);
    }
    container.append(article);
  });
  updateProgress();
}

function updateProgress() {
  if (!state.current) return;
  const total = state.current.records.reduce((sum, record) => sum + record.fields.length, 0);
  $("progress").textContent = `${state.judgments.size} of ${total} claims judged`;
}

function renderMissing() {
  const container = $("missing-facts");
  container.replaceChildren();
  state.missingFacts.forEach((fact, index) => {
    const row = document.createElement("div");
    row.className = "missing-fact";
    const description = document.createElement("input");
    description.placeholder = "Missing fact"; description.value = fact.description;
    const source = document.createElement("select");
    source.innerHTML = '<option value="main">Main</option><option value="supplement">SI</option>';
    source.value = fact.reference.source;
    const page = document.createElement("input"); page.type = "number"; page.min = "1"; page.value = fact.reference.page;
    const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "Remove";
    description.oninput = () => { fact.description = description.value; };
    source.onchange = () => { fact.reference.source = source.value; };
    page.onchange = () => { fact.reference.page = Number(page.value); };
    remove.onclick = () => { state.missingFacts.splice(index, 1); renderMissing(); };
    row.append(description, source, page, remove);
    container.append(row);
  });
}

function hydrateReview(review) {
  state.judgments = new Map(review.judgments.map((item) => [item.field_key, item]));
  state.missingFacts = structuredClone(review.missing_facts);
  state.activeSeconds = review.active_seconds;
  $("extra-records").value = review.extra_records;
  $("missing-records").value = review.missing_records;
  $("wrong-links").value = review.wrong_links;
  $("confidence").value = review.confidence || "";
  $("notes").value = review.notes;
  const locked = Boolean(review.submitted_at);
  renderMissing();
  document.querySelectorAll(".neutral-record input,.omission-review input,.omission-review select,.omission-review textarea,#add-missing").forEach((input) => { input.disabled = locked; });
  $("save-draft").hidden = locked;
  $("submit-review").hidden = locked;
  $("save-state").textContent = locked ? "Submitted · locked" : `Draft revision ${review.revision}`;
  $("utility-review").hidden = !locked;
  if (locked) loadNative();
}

async function loadNative() {
  try {
    state.native = await request(`/api/native-comparisons/${encodeURIComponent(state.current.comparison_id)}`);
    $("native-output").textContent = JSON.stringify(state.native.native_payload, null, 2);
    const review = state.native.review;
    state.nativeSeconds = review?.active_seconds || 0;
    $("chemical-detail").value = review?.ratings.chemical_detail || "";
    $("relationships").value = review?.ratings.relationships || "";
    $("verification-ease").value = review?.ratings.verification_ease || "";
    $("nomad-usefulness").value = review?.ratings.nomad_usefulness || "";
    $("curation-suitability").value = review?.suitable_as_curation_start || "";
    $("utility-notes").value = review?.notes || "";
    document.querySelectorAll("#utility-review select,#utility-review textarea,#submit-utility").forEach((input) => { input.disabled = Boolean(review); });
    $("submit-utility").textContent = review ? "Native-output review submitted" : "Submit native-output review";
    if (review) await loadPreference();
    else $("preference-review").hidden = true;
  } catch (error) { $("save-state").textContent = error.message; }
}

function renderPreferenceRubrics(review) {
  const container = $("preference-rubrics");
  container.replaceChildren();
  state.preference.rubrics.forEach((rubricDefinition) => {
    const {
      key, label, question, minimum_acceptable: minimumBar, preference_rule: preferenceRule,
    } = rubricDefinition;
    const row = document.createElement("label");
    row.className = "preference-rubric";
    const copy = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = label;
    const help = document.createElement("small");
    help.textContent = question;
    const rubric = document.createElement("details");
    rubric.className = "criterion-rubric";
    const rubricSummary = document.createElement("summary");
    rubricSummary.textContent = "Criterion rubric";
    const minimum = document.createElement("p");
    minimum.textContent = `Minimum acceptable: ${minimumBar}`;
    const rule = document.createElement("p");
    rule.textContent = `Preference rule: ${preferenceRule}`;
    rubric.append(rubricSummary, minimum, rule);
    copy.append(title, help, rubric);
    const select = document.createElement("select");
    select.id = `preference-${key}`;
    [
      ["", "Choose…"], ["A", "Candidate A"], ["B", "Candidate B"],
      ["tie", "Tie"], ["both_inadequate", "Both inadequate"],
      ["cannot_judge", "Cannot judge"],
    ].forEach(([value, text]) => {
      const option = document.createElement("option");
      option.value = value; option.textContent = text; select.append(option);
    });
    select.value = review?.preferences[key] || "";
    select.disabled = Boolean(review);
    row.append(copy, select);
    container.append(row);
  });
}

async function loadPreference() {
  try {
    state.preference = await request(`/api/pairwise-comparisons/${encodeURIComponent(state.current.comparison_id)}`);
    $("preference-review").hidden = false;
    $("pairwise-a").textContent = JSON.stringify(state.preference.candidates.A.native_payload, null, 2);
    $("pairwise-b").textContent = JSON.stringify(state.preference.candidates.B.native_payload, null, 2);
    const review = state.preference.review;
    state.preferenceSeconds = review?.active_seconds || 0;
    renderPreferenceRubrics(review);
    $("preference-confidence").value = review?.confidence || "";
    $("preference-rationale").value = review?.rationale || "";
    $("preference-confidence").disabled = Boolean(review);
    $("preference-rationale").disabled = Boolean(review);
    $("submit-preference").disabled = Boolean(review);
    $("submit-preference").textContent = review ? "A/B preferences submitted" : "Submit A/B preferences";
  } catch (error) { $("save-state").textContent = error.message; }
}

async function loadPdf() {
  if (!state.current) return;
  $("pdf-status").textContent = "Loading source…";
  $("pdf-status").hidden = false;
  try {
    const query = new URLSearchParams({ source: state.source, page: String(state.page), split: state.current.split, scale: "1.35" });
    const response = await fetch(`/api/pdf-page/${encodeURIComponent(state.current.paper_id)}?${query}`, { headers: await headers() });
    if (!response.ok) throw new Error(response.status === 404 ? "This source is not available." : "The source page could not be loaded.");
    const blob = await response.blob();
    if (state.pdfUrl) URL.revokeObjectURL(state.pdfUrl);
    state.pdfUrl = URL.createObjectURL(blob);
    $("pdf-page").src = state.pdfUrl;
    state.pageCount = Number(response.headers.get("X-PDF-Pages")) || 1;
    $("page-count").textContent = `/ ${state.pageCount}`;
    $("page-number").value = state.page;
    $("pdf-status").hidden = true;
  } catch (error) {
    $("pdf-status").textContent = error.message;
  }
}

async function openComparison(id) {
  $("save-state").textContent = "Loading…";
  try {
    state.current = await request(`/api/comparisons/${encodeURIComponent(id)}`);
    state.source = "main"; state.page = 1; state.lastTick = Date.now();
    state.native = null; state.preference = null;
    $("preference-review").hidden = true;
    $("comparison-empty").hidden = true;
    $("comparison-workspace").hidden = false;
    $("comparison-workspace").scrollTop = 0;
    document.querySelector(".comparison-review").scrollTop = 0;
    $("comparison-title").textContent = state.current.title;
    $("comparison-meta").textContent = state.current.paper_id;
    $("candidate-label").textContent = `Candidate ${state.current.blind_label}`;
    hydrateReview(state.current.review);
    renderRecords(); renderList();
    $("download-analysis").hidden = !(
      state.user.role === "admin"
      && state.assignments.find((item) => item.comparison_id === id)?.batch_ready
    );
    await loadPdf();
  } catch (error) { $("save-state").textContent = error.message; }
}

function submissionPayload(submit) {
  updateTimer();
  const rating = (id) => Number($(id).value) || null;
  return {
    revision: state.current.review.revision,
    submit,
    active_seconds: state.activeSeconds,
    judgments: [...state.judgments.values()],
    missing_facts: state.missingFacts,
    extra_records: Number($("extra-records").value),
    missing_records: Number($("missing-records").value),
    wrong_links: Number($("wrong-links").value),
    confidence: rating("confidence"),
    notes: $("notes").value.trim(),
  };
}

async function submitUtility() {
  updateTimer();
  const rating = (id) => Number($(id).value) || null;
  $("save-state").textContent = "Submitting native-output review…";
  try {
    await request(`/api/native-utility-reviews/${encodeURIComponent(state.current.comparison_id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active_seconds: state.nativeSeconds,
        ratings: {
          chemical_detail: rating("chemical-detail"), relationships: rating("relationships"),
          verification_ease: rating("verification-ease"), nomad_usefulness: rating("nomad-usefulness"),
        },
        suitable_as_curation_start: $("curation-suitability").value,
        notes: $("utility-notes").value.trim(),
      }),
    });
    await loadNative();
    state.assignments = (await request("/api/comparisons")).comparisons;
    renderList();
    $("download-analysis").hidden = !(
      state.user.role === "admin"
      && state.assignments.find((item) => item.comparison_id === state.current.comparison_id)?.batch_ready
    );
    $("save-state").textContent = "Comparison complete";
  } catch (error) { $("save-state").textContent = error.message; }
}

async function submitPreference() {
  updateTimer();
  const preferences = Object.fromEntries(
    state.preference.rubrics.map(({ key }) => [key, $(`preference-${key}`).value]),
  );
  $("save-state").textContent = "Submitting A/B preferences…";
  try {
    await request(`/api/pairwise-preference-reviews/${encodeURIComponent(state.current.comparison_id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        active_seconds: state.preferenceSeconds,
        preferences,
        confidence: Number($("preference-confidence").value) || null,
        rationale: $("preference-rationale").value.trim(),
      }),
    });
    await loadPreference();
    state.assignments = (await request("/api/comparisons")).comparisons;
    renderList();
    $("download-analysis").hidden = !(
      state.user.role === "admin"
      && state.assignments.find((item) => item.comparison_id === state.current.comparison_id)?.batch_ready
    );
    $("save-state").textContent = "Comparison complete";
  } catch (error) { $("save-state").textContent = error.message; }
}

async function save(submit) {
  $("save-state").textContent = submit ? "Submitting…" : "Saving…";
  try {
    const review = await request(`/api/comparison-reviews/${encodeURIComponent(state.current.comparison_id)}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(submissionPayload(submit)),
    });
    state.current.review = review;
    hydrateReview(review); renderRecords();
    state.assignments = (await request("/api/comparisons")).comparisons;
    renderList();
  } catch (error) { $("save-state").textContent = error.message; }
}

$("pdf-source").onchange = () => { state.source = $("pdf-source").value; state.page = 1; loadPdf(); };
$("previous-page").onclick = () => { if (state.page > 1) { state.page -= 1; loadPdf(); } };
$("next-page").onclick = () => { if (state.page < state.pageCount) { state.page += 1; loadPdf(); } };
$("page-number").onchange = () => { state.page = Math.max(1, Math.min(state.pageCount, Number($("page-number").value))); loadPdf(); };
$("add-missing").onclick = () => { state.missingFacts.push({ description: "", value: "", reference: { source: state.source, page: state.page, quote: "" } }); renderMissing(); };
$("save-draft").onclick = () => save(false);
$("submit-review").onclick = () => {
  if (confirm("Submit and lock this review? It cannot be edited or revealed early.")) save(true);
};
$("submit-utility").onclick = submitUtility;
$("submit-preference").onclick = () => {
  if (confirm("Submit and lock these A/B preferences? They cannot be edited later.")) submitPreference();
};
$("download-analysis").onclick = () => downloadAnalysis(state.current.comparison_id);
$("open-import").onclick = () => $("import-dialog").showModal();
$("close-import").onclick = $("cancel-import").onclick = () => $("import-dialog").close();
$("import-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  $("import-status").textContent = "Validating and freezing comparison…";
  try {
    const [historical, extracted] = await Promise.all([
      JSON.parse(await form.get("historical").text()), JSON.parse(await form.get("extracted").text()),
    ]);
    await request("/api/comparisons", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        comparison_id: form.get("comparison_id"), paper_id: form.get("paper_id"),
        title: form.get("title"), split: form.get("split"), historical, extracted,
        reviewer_ids: form.get("reviewer_ids").split(",").map((value) => value.trim()).filter(Boolean),
        randomization_seed: form.get("randomization_seed"),
      }),
    });
    $("import-status").textContent = "Comparison created.";
    state.assignments = (await request("/api/comparisons")).comparisons;
    renderList();
  } catch (error) { $("import-status").textContent = error.message; }
};

try {
  await initializeAuth();
  state.assignments = (await request("/api/comparisons")).comparisons;
  renderList();
} catch (error) {
  $("reviewer").textContent = "Could not connect";
  $("comparison-empty").innerHTML = `<h2>Comparison workspace unavailable</h2><p></p><a href="/">Return to sign in</a>`;
  $("comparison-empty").querySelector("p").textContent = error.message;
}
