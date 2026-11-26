import {
  citationLineIndexes, doiFromPaperId, paperFromCrossref, renderProse, scrollAnchor,
  scrollTarget, sourceName,
} from "./simple-view.js";

const $ = (id) => document.getElementById(id);
const TOKEN_KEY = "review-token";
const PAPER_HEIGHT_KEY = "simple-comparison-paper-height";
const LABELS = ["A", "B"];
const state = {
  authMode: "local", clerk: null, clerkListenerAdded: false, starting: false, user: null,
  comparisons: [], current: null, source: "main", page: 1, pageCount: 1, pdfUrl: null,
  pageLines: null, activeCitation: null, activeSeconds: 0, lastTick: Date.now(),
  scrollLock: null, pageRequest: 0,
};

// ---- Transport and sign-in (same contract as the other review pages) ----

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
  return response;
}

const requestJson = async (url, options) => (await request(url, options)).json();

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

const internalSignInEnabled = () => state.authMode === "internal" || state.authMode === "internal_or_clerk";
const clerkSignInEnabled = () => state.authMode === "clerk" || state.authMode === "internal_or_clerk";

async function initializeAuth() {
  const response = await fetch("/api/auth/config");
  if (!response.ok) throw new Error("The sign-in service is temporarily unavailable.");
  const config = await response.json();
  if (!config.enabled && config.mode !== "local") {
    throw new Error("Authentication is not completely configured for this deployment.");
  }
  state.authMode = config.enabled ? config.mode : "local";
  if (!localStorage.getItem(TOKEN_KEY) && clerkSignInEnabled() && !state.clerk) {
    await loadScript(`${config.frontend_api}/npm/@clerk/ui@1/dist/ui.browser.js`, { crossorigin: "anonymous" });
    await loadScript(`${config.frontend_api}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`, {
      crossorigin: "anonymous", "data-clerk-publishable-key": config.publishable_key,
    });
    await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
    state.clerk = window.Clerk;
    if (!state.clerkListenerAdded) {
      state.clerk.addListener(({ session }) => {
        if (session && !state.user) start().catch(showAuthenticationError);
      });
      state.clerkListenerAdded = true;
    }
  }
  try {
    state.user = (await requestJson("/api/session")).user;
  } catch (error) {
    if (error.status === 401 && localStorage.getItem(TOKEN_KEY)) {
      localStorage.removeItem(TOKEN_KEY);
      return initializeAuth();
    }
    if (error.status === 401) { showSignIn(); return false; }
    throw error;
  }
  $("auth-gate").hidden = true;
  $("simple-app").hidden = false;
  $("reviewer").textContent = state.user.name;
  const admin = state.user.role === "admin";
  $("open-create").hidden = !admin;
  $("download-export").hidden = !admin;
  return true;
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
  $("auth-help").textContent = "Use an available email sign-in method to continue.";
  state.clerk?.mountSignIn($("clerk-sign-in"));
}

function showSignIn(message = "") {
  $("simple-app").hidden = true;
  $("auth-gate").hidden = false;
  $("retry-auth").hidden = true;
  $("login-status").textContent = message;
  if (internalSignInEnabled()) showInternalSignIn();
  else showClerkSignIn();
}

function showAuthenticationError(error) {
  $("simple-app").hidden = true;
  $("auth-gate").hidden = false;
  $("auth-title").textContent = "The sign-in service did not load";
  $("auth-help").textContent = "Your answers are unchanged. Check your connection and try again.";
  for (const id of ["internal-sign-in", "clerk-sign-in", "use-email-sign-in", "use-project-password"]) $(id).hidden = true;
  $("retry-auth").hidden = false;
  $("login-status").textContent = error?.message || String(error);
}

// ---- Assignment list ----

async function refreshList() {
  state.comparisons = (await requestJson("/api/simple-comparisons")).comparisons;
  renderList();
}

function renderList() {
  const list = $("comparison-list");
  if (!state.comparisons.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "No comparisons are assigned to you yet.";
    list.replaceChildren(empty);
    return;
  }
  list.replaceChildren(...state.comparisons.map((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-current", String(item.comparison_id === state.current?.comparison_id));
    const title = document.createElement("strong");
    title.textContent = item.title;
    const detail = document.createElement("small");
    const progress = item.progress ? ` · ${item.progress.submitted}/${item.progress.assigned} answered` : "";
    detail.textContent = `${item.assigned ? (item.submitted ? "Answered" : "Open") : "Not assigned to you"}${progress}`;
    button.append(title, detail);
    button.disabled = !item.assigned;
    button.onclick = () => openComparison(item.comparison_id);
    return button;
  }));
}

// ---- Opening one comparison ----

async function openComparison(id) {
  setStatus("Loading…");
  try {
    state.current = await requestJson(`/api/simple-comparisons/${encodeURIComponent(id)}`);
  } catch (error) { setStatus(error.message, "error"); return; }
  state.activeCitation = null;
  state.activeSeconds = 0;
  state.lastTick = Date.now();
  $("simple-empty").hidden = true;
  $("simple-workspace").hidden = false;
  $("download-export").disabled = false;
  const select = $("pdf-source");
  select.replaceChildren(...(state.current.sources.length ? state.current.sources : ["main"]).map((source) => {
    const option = document.createElement("option");
    option.value = source;
    option.textContent = sourceName(source);
    return option;
  }));
  for (const label of LABELS) {
    const pane = $(`prose-${label}`);
    pane.replaceChildren(renderProse(document, state.current.candidates[label].prose, { label, onCite: showCitation }));
    pane.scrollTop = 0;
  }
  renderDecision();
  renderList();
  setCitationStatus("Click a citation in A or B to see its quote in the paper.");
  await showPage("main", 1);
}

function renderDecision() {
  const submitted = state.current.preference;
  $("decision-fields").disabled = Boolean(submitted);
  $("decision").reset();
  $("link-scrolling").checked = true;
  if (submitted) {
    document.querySelector(`input[name="choice"][value="${submitted.choice}"]`).checked = true;
    $("justification").value = submitted.justification;
    $("submit-choice").textContent = "Choice submitted";
    setStatus(`You chose ${submitted.choice}. Submitted choices cannot be changed.`, "done");
  } else {
    $("submit-choice").textContent = "Submit choice";
    setStatus("");
  }
}

function setStatus(text, tone = "") {
  $("decision-status").textContent = text;
  $("decision-status").dataset.tone = tone;
}

// ---- Paper panel and citations ----

function setCitationStatus(text, citation = null) {
  const status = $("citation-status");
  status.replaceChildren();
  if (citation) {
    const strong = document.createElement("strong");
    strong.textContent = `${citation.label} [${citation.number}] `;
    status.append(strong);
  }
  status.append(document.createTextNode(text));
  status.title = status.textContent;
}

async function showPage(source, page) {
  const ticket = ++state.pageRequest;
  state.source = source;
  state.page = page;
  $("pdf-source").value = source;
  $("page-number").value = page;
  $("pdf-status").textContent = "Loading source…";
  $("pdf-status").hidden = false;
  $("pdf-highlights").replaceChildren();
  const { paper_id: paperId, split } = state.current;
  const base = `${encodeURIComponent(paperId)}?${new URLSearchParams({ source, page: String(page), split })}`;
  try {
    const [image, text] = await Promise.all([
      request(`/api/pdf-page/${base}&scale=1.6`),
      requestJson(`/api/pdf-text/${base}`).catch(() => null),
    ]);
    if (ticket !== state.pageRequest) return;
    const blob = await image.blob();
    if (state.pdfUrl) URL.revokeObjectURL(state.pdfUrl);
    state.pdfUrl = URL.createObjectURL(blob);
    $("pdf-page").src = state.pdfUrl;
    state.pageCount = Number(image.headers.get("X-PDF-Pages")) || text?.page_count || 1;
    state.pageLines = text?.lines || [];
    $("page-count").textContent = `/ ${state.pageCount}`;
    $("pdf-status").hidden = true;
    highlightCitation();
  } catch (error) {
    if (ticket !== state.pageRequest) return;
    $("pdf-status").textContent = error.status === 404 ? "This source is not available." : error.message;
  }
}

function showCitation(reference, label) {
  document.querySelectorAll(".cite[aria-current], .reference[aria-current]").forEach((node) => node.removeAttribute("aria-current"));
  document.querySelectorAll(`#prose-${label} [data-reference="${reference.number}"]`).forEach((node) => node.setAttribute("aria-current", "true"));
  state.activeCitation = { ...reference, label };
  if (!reference.source || !reference.page) {
    $("paper-panel").classList.remove("citation-active");
    $("pdf-highlights").replaceChildren();
    setCitationStatus(`The cited block is not in this paper's document: “${reference.quote}”`, state.activeCitation);
    return;
  }
  setCitationStatus(`${sourceName(reference.source)}, p. ${reference.page}: “${reference.quote}”`, state.activeCitation);
  if (reference.source === state.source && reference.page === state.page) highlightCitation();
  else showPage(reference.source, reference.page);
}

function highlightCitation() {
  const citation = state.activeCitation;
  const visible = citation && citation.source === state.source && citation.page === state.page;
  $("paper-panel").classList.toggle("citation-active", Boolean(visible));
  if (!visible) { $("pdf-highlights").replaceChildren(); return; }
  const indexes = citationLineIndexes(state.pageLines || [], citation.quote);
  const boxes = indexes.flatMap((index) => {
    const box = state.pageLines[index]?.bbox;
    if (!box || ![box.x, box.y, box.width, box.height].every(Number.isFinite)) return [];
    const node = document.createElement("span");
    node.className = "pdf-highlight";
    node.style.cssText = `left:${box.x * 100}%;top:${box.y * 100}%;width:${box.width * 100}%;height:${box.height * 100}%`;
    return [node];
  });
  $("pdf-highlights").replaceChildren(...boxes);
  if (boxes.length) {
    requestAnimationFrame(() => boxes[0].scrollIntoView({ block: "center", behavior: "smooth" }));
  } else {
    setCitationStatus(`${sourceName(citation.source)}, p. ${citation.page}: the quote could not be matched to the page text. “${citation.quote}”`, citation);
  }
}

// ---- Scrolling A and B together ----

function sectionOffsets(pane) {
  return [...pane.querySelectorAll("[data-section]")].map((node) => ({
    key: node.dataset.section, top: node.offsetTop - pane.firstElementChild.offsetTop, height: node.offsetHeight,
  }));
}

function syncScroll(fromLabel) {
  if (!$("link-scrolling").checked || state.scrollLock === fromLabel) return;
  const toLabel = fromLabel === "A" ? "B" : "A";
  const from = $(`prose-${fromLabel}`);
  const to = $(`prose-${toLabel}`);
  const atEnd = from.scrollTop + from.clientHeight >= from.scrollHeight - 2;
  const target = atEnd
    ? to.scrollHeight
    : scrollTarget(sectionOffsets(to), scrollAnchor(sectionOffsets(from), from.scrollTop));
  if (target == null) return;
  state.scrollLock = toLabel;
  to.scrollTop = target;
  requestAnimationFrame(() => { if (state.scrollLock === toLabel) state.scrollLock = null; });
}

// ---- Layout: resizable paper panel and narrow-screen tabs ----

function setPaperHeight(pixels) {
  const workspace = $("simple-workspace");
  const limit = Math.max(160, window.innerHeight - 280);
  const height = Math.round(Math.min(limit, Math.max(140, pixels)));
  workspace.style.setProperty("--paper-height", `${height}px`);
  try { localStorage.setItem(PAPER_HEIGHT_KEY, String(height)); } catch { /* storage is optional */ }
}

function restorePaperHeight() {
  try {
    const saved = Number(localStorage.getItem(PAPER_HEIGHT_KEY));
    if (saved > 0) setPaperHeight(saved);
  } catch { /* storage is optional */ }
}

function startResize(event) {
  event.preventDefault();
  const startY = event.clientY;
  const startHeight = $("paper-panel").getBoundingClientRect().height;
  const move = (moveEvent) => setPaperHeight(startHeight + moveEvent.clientY - startY);
  const stop = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", stop);
}

function showTab(label) {
  $("candidate-columns").dataset.show = label;
  document.querySelectorAll(".candidate-tabs [data-show]").forEach((tab) => tab.setAttribute("aria-selected", String(tab.dataset.show === label)));
}

// ---- Decision ----

function tick() {
  const now = Date.now();
  if (state.current && !state.current.preference && document.visibilityState === "visible" && document.hasFocus()) {
    state.activeSeconds += Math.min(5, Math.round((now - state.lastTick) / 1000));
  }
  state.lastTick = now;
}

async function submitChoice(event) {
  event.preventDefault();
  if (!state.current || state.current.preference) return;
  const choice = document.querySelector('input[name="choice"]:checked')?.value;
  const justification = $("justification").value.trim();
  if (!choice) { setStatus("Choose A or B first.", "error"); return; }
  if (justification.length < 10) { setStatus("Explain your choice in at least 10 characters.", "error"); $("justification").focus(); return; }
  if (!confirm(`Submit ${choice} as the better extraction? You cannot change it afterwards.`)) return;
  tick();
  setStatus("Submitting…");
  try {
    const preference = await requestJson(`/api/simple-comparisons/${encodeURIComponent(state.current.comparison_id)}/preference`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        choice, justification, active_seconds: state.activeSeconds,
        candidate_hashes: Object.fromEntries(LABELS.map((label) => [label, state.current.candidates[label].prose_sha256])),
      }),
    });
    state.current.preference = preference;
    renderDecision();
    await refreshList();
  } catch (error) { setStatus(error.message, "error"); }
}

// ---- Administration: create and export ----

async function loadPapers() {
  const split = $("create-split").value;
  const select = $("create-paper");
  select.replaceChildren();
  try {
    const papers = (await requestJson(`/api/papers?split=${encodeURIComponent(split)}`)).papers;
    select.append(...papers.map((paper) => {
      const option = document.createElement("option");
      option.value = paper.id;
      option.textContent = paper.title ? `${paper.id} — ${paper.title}` : paper.id;
      return option;
    }));
    if (!papers.length) $("create-status").textContent = `No papers in ${split}.`;
  } catch (error) { $("create-status").textContent = error.message; }
}

async function lookupDoi() {
  const form = $("create-form");
  const doi = form.elements.doi.value.trim() || doiFromPaperId($("create-paper").value);
  if (!doi) { $("lookup-status").textContent = "Enter a DOI first."; return; }
  $("lookup-status").textContent = "Looking up…";
  try {
    const response = await fetch(`https://api.crossref.org/works/${encodeURIComponent(doi)}`);
    if (!response.ok) throw new Error(response.status === 404 ? "Crossref does not know this DOI." : `Crossref answered ${response.status}.`);
    const paper = paperFromCrossref((await response.json()).message);
    form.elements.title.value = paper.title || "";
    form.elements.authors.value = paper.authors.join("\n");
    form.elements.journal.value = paper.journal || "";
    form.elements.year.value = paper.year || "";
    form.elements.doi.value = paper.doi || doi;
    $("lookup-status").textContent = "Filled in from Crossref. Check before creating.";
  } catch (error) { $("lookup-status").textContent = error.message; }
}

async function readJsonFile(file, name) {
  if (!file || !file.size) throw new Error(`Choose the run's ${name}.`);
  try { return JSON.parse(await file.text()); } catch { throw new Error(`${name} is not valid JSON.`); }
}

async function createComparison(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const data = new FormData(form);
  $("create-status").textContent = "Checking the ground truth and freezing both extractions…";
  try {
    const kind = data.get("extractor_kind");
    const extractor = kind === "run"
      ? { kind, extraction: await readJsonFile(data.get("extraction"), "extraction.json"), document: await readJsonFile(data.get("document"), "document.json") }
      : { kind: "seed" };
    const year = Number(data.get("year"));
    const created = await requestJson("/api/simple-comparisons", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        comparison_id: data.get("comparison_id"), split: data.get("split"), paper_id: data.get("paper_id"),
        extractor,
        paper: {
          title: data.get("title").trim() || null,
          authors: data.get("authors").split("\n").map((name) => name.trim()).filter(Boolean),
          journal: data.get("journal").trim() || null,
          year: Number.isInteger(year) && year > 0 ? year : null,
          doi: data.get("doi").trim() || null,
        },
        reviewer_ids: data.get("reviewer_ids").split(",").map((value) => value.trim()).filter(Boolean),
        randomization_seed: data.get("randomization_seed"),
      }),
    });
    $("create-status").textContent = `Created ${created.comparison_id} for ${created.assigned_reviewers} reviewer(s).`;
    await refreshList();
  } catch (error) { $("create-status").textContent = error.message; }
}

async function downloadExport() {
  if (!state.current) return;
  const id = state.current.comparison_id;
  try {
    const payload = await requestJson(`/api/simple-comparison-export/${encodeURIComponent(id)}`);
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `${id}-simple-ab.json`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { setStatus(error.message, "error"); }
}

// ---- Wiring ----

async function start() {
  if (state.starting) return;
  state.starting = true;
  try {
    if (!await initializeAuth()) return;
    await refreshList();
    const requested = new URLSearchParams(window.location.search).get("comparison");
    if (requested && state.comparisons.some((item) => item.comparison_id === requested && item.assigned)) await openComparison(requested);
  } finally { state.starting = false; }
}

$("pdf-source").onchange = () => showPage($("pdf-source").value, 1);
$("previous-page").onclick = () => { if (state.page > 1) showPage(state.source, state.page - 1); };
$("next-page").onclick = () => { if (state.page < state.pageCount) showPage(state.source, state.page + 1); };
$("page-number").onchange = () => showPage(state.source, Math.max(1, Math.min(state.pageCount, Number($("page-number").value) || 1)));
for (const label of LABELS) $(`prose-${label}`).addEventListener("scroll", () => syncScroll(label), { passive: true });
$("link-scrolling").onchange = () => { if ($("link-scrolling").checked) syncScroll("A"); };
$("splitter").addEventListener("pointerdown", startResize);
$("splitter").addEventListener("keydown", (event) => {
  if (event.key !== "ArrowUp" && event.key !== "ArrowDown") return;
  event.preventDefault();
  setPaperHeight($("paper-panel").getBoundingClientRect().height + (event.key === "ArrowDown" ? 40 : -40));
});
document.querySelectorAll(".candidate-tabs [data-show]").forEach((tab) => { tab.onclick = () => showTab(tab.dataset.show); });
$("decision").onsubmit = submitChoice;
setInterval(tick, 1000);
restorePaperHeight();

$("open-create").onclick = () => { $("create-dialog").showModal(); loadPapers(); };
$("close-create").onclick = $("cancel-create").onclick = () => $("create-dialog").close();
$("create-split").onchange = loadPapers;
$("create-form").onsubmit = createComparison;
$("lookup-doi").onclick = lookupDoi;
document.querySelectorAll('input[name="extractor_kind"]').forEach((input) => {
  input.onchange = () => { $("run-files").hidden = document.querySelector('input[name="extractor_kind"]:checked').value !== "run"; };
});
$("download-export").onclick = downloadExport;

$("use-email-sign-in").onclick = showClerkSignIn;
$("use-project-password").onclick = showInternalSignIn;
$("retry-auth").onclick = () => window.location.reload();
$("internal-sign-in").onsubmit = async (event) => {
  event.preventDefault();
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  submit.disabled = true;
  $("login-status").textContent = "Signing in…";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("login-email").value, password: $("login-password").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Sign-in failed.");
    localStorage.setItem(TOKEN_KEY, payload.token);
    $("login-password").value = "";
    await start();
  } catch (error) { showSignIn(error.message); } finally { submit.disabled = false; }
};
$("sign-out").onclick = async () => {
  localStorage.removeItem(TOKEN_KEY);
  try { await state.clerk?.signOut(); } finally {
    state.user = null; state.current = null;
    $("simple-workspace").hidden = true;
    $("simple-empty").hidden = false;
    showSignIn("Signed out.");
  }
};

try { await start(); } catch (error) { showAuthenticationError(error); }
