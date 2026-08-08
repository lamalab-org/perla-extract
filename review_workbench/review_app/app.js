const state = {
  split: "test", papers: [], selected: null, sources: [], tab: "fields",
  paperData: null, reviewData: null, quantityData: null, evidenceDirty: false,
  users: [], reviewerProgress: [], comments: [], issues: [], figureAudits: {}, corpusSummary: null, currentUser: null,
  clerk: null, pdfPage: 1, pdfPageCount: 0, pdfImageUrl: null,
  pdfNavigationId: 0, pdfPageText: "", pdfTextLines: [], pdfQuote: "", pendingCursor: null,
  internalToken: null,
};
const $ = (id) => document.getElementById(id);

async function authorizationHeaders(headers = {}) {
  if (state.internalToken) return { ...headers, Authorization: `Bearer ${state.internalToken}` };
  if (!state.clerk?.session) return headers;
  const token = await state.clerk.session.getToken();
  return token ? { ...headers, Authorization: `Bearer ${token}` } : headers;
}

async function request(url, options = {}) {
  const headers = await authorizationHeaders({
    "Content-Type": "application/json", ...(options.headers || {}),
  });
  const response = await fetch(url, { ...options, headers });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

function escapeHtml(value) {
  return (value == null ? "" : String(value)).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

const SAFE_FRAGMENT_TAGS = new Set([
  "article", "b", "button", "code", "div", "h3", "i", "input", "label", "mark",
  "option", "p", "pre", "select", "span", "strong", "textarea", "time",
]);
const SAFE_FRAGMENT_ATTRIBUTES = new Set([
  "checked", "class", "disabled", "min", "placeholder", "rows", "selected",
  "type", "value",
]);

function renderSafeHtml(element, html) {
  const parsed = new DOMParser().parseFromString(`<body>${html}</body>`, "text/html");
  parsed.body.querySelectorAll("*").forEach((node) => {
    if (!SAFE_FRAGMENT_TAGS.has(node.tagName.toLowerCase())) {
      node.replaceWith(document.createTextNode(node.textContent || ""));
      return;
    }
    [...node.attributes].forEach((attribute) => {
      if (!SAFE_FRAGMENT_ATTRIBUTES.has(attribute.name) && !attribute.name.startsWith("data-")) {
        node.removeAttribute(attribute.name);
      }
    });
  });
  const fragment = document.createDocumentFragment();
  [...parsed.body.childNodes].forEach((node) => fragment.append(document.importNode(node, true)));
  element.replaceChildren(fragment);
}

function included(paper) { return paper.exclusion_reasons.length === 0; }
function currentPaper() { return state.papers.find((paper) => paper.id === state.selected); }
function reviewerId() { return state.currentUser?.id || "reviewer"; }
function reviewerName(id) { return state.users.find((user) => user.id === id)?.name || id; }

function renderPapers() {
  const filter = $("filter").value;
  const visible = state.papers.filter((paper) =>
    filter === "all" ||
    (filter === "issues" && paper.open_issues > 0) ||
    (filter === "pending" && paper.metadata.review_status === "pending") ||
    (filter === "excluded" && !included(paper)) ||
    (filter === "included" && included(paper))
  );
  const reviewed = state.papers.filter((p) => p.metadata.review_status === "reviewed").length;
  const excluded = state.papers.filter((p) => !included(p)).length;
  $("summary").textContent = `${reviewed}/${state.papers.length} classified · ${excluded} excluded`;
  renderSafeHtml($("paper-list"), visible.map((paper) => {
    const progress = paper.field_review || { reviewed: 0, total: 0 };
    const percent = progress.total ? Math.round(100 * progress.reviewed / progress.total) : 0;
    return `
      <button class="paper-item ${paper.id === state.selected ? "selected" : ""}" data-paper="${paper.id}">
        <span class="paper-id">${paper.id.replace("--", "/")}</span>
        <span class="paper-meta"><span class="split-tag ${state.split}">${state.split === "test" ? "test" : "dev"}</span><span>${paper.cell_count} cells</span><span class="dot">·</span><span class="${included(paper) ? "include-text" : "exclude-text"}">${included(paper) ? "include" : "exclude"}</span>${paper.open_issues ? `<span class="issue-count">${paper.open_issues} issue${paper.open_issues === 1 ? "" : "s"}</span>` : ""}</span>
        <span class="mini-progress"><i data-progress="${percent}"></i></span>
        <span class="paper-meta">${progress.reviewed}/${progress.total} fields reviewed</span>
      </button>`;
  }).join(""));
  document.querySelectorAll("[data-progress]").forEach((bar) => {
    bar.style.width = `${Math.max(0, Math.min(100, Number(bar.dataset.progress)))}%`;
  });
  document.querySelectorAll("[data-paper]").forEach((button) => button.addEventListener("click", () => selectPaper(button.dataset.paper)));
}

function renderReviewerProgress() {
  const reviewers = state.reviewerProgress || [];
  renderSafeHtml($("reviewer-progress"), reviewers.length ? reviewers.map((reviewer) => `
    <div class="reviewer-progress-item">
      <div class="reviewer-progress-heading"><strong>${escapeHtml(reviewer.name)}${reviewer.id === reviewerId() ? " (you)" : ""}</strong><span>${reviewer.percent}%</span></div>
      <div class="reviewer-progress-track"><i data-reviewer-progress="${reviewer.percent}"></i></div>
      <div class="reviewer-progress-meta"><span>${reviewer.reviewed}/${reviewer.total} fields</span><span>${reviewer.papers_completed}/${reviewer.paper_count} papers</span></div>
      ${reviewer.needs_followup || reviewer.incorrect ? `<div class="reviewer-progress-meta"><span>${reviewer.needs_followup} follow-up</span><span>${reviewer.incorrect} incorrect</span></div>` : ""}
    </div>`).join("") : `<span>No reviewer accounts found.</span>`);
  document.querySelectorAll("[data-reviewer-progress]").forEach((bar) => {
    bar.style.width = `${Math.max(0, Math.min(100, Number(bar.dataset.reviewerProgress)))}%`;
  });
}

async function loadReviewerProgress() {
  const payload = await request(`/api/reviewer-progress?split=${state.split}`);
  state.reviewerProgress = payload.reviewers;
  renderReviewerProgress();
}

async function loadPapers() {
  const [payload, userPayload, corpusSummary, reviewerProgress] = await Promise.all([
    request(`/api/papers?split=${state.split}`), request("/api/users"), request("/api/corpus-summary"),
    request(`/api/reviewer-progress?split=${state.split}`),
  ]);
  state.papers = payload.papers;
  state.sources = payload.sources;
  state.users = userPayload.users;
  state.corpusSummary = corpusSummary;
  state.reviewerProgress = reviewerProgress.reviewers;
  $("test-count").textContent = corpusSummary.test.papers;
  $("dev-count").textContent = corpusSummary.dev.papers;
  document.querySelectorAll("[data-split]").forEach((button) => button.classList.toggle("active", button.dataset.split === state.split));
  renderSafeHtml($("source"), `<option value="">Choose extraction…</option>` + state.sources.map((source) => `<option value="${escapeHtml(source)}">${escapeHtml(source)}</option>`).join(""));
  renderPapers();
  renderReviewerProgress();
  const preferred = state.papers.find((paper) => paper.id === state.selected) || state.papers[0];
  if (preferred) await selectPaper(preferred.id);
}

function metadataPayload() {
  return { article_type: $("article-type").value, tandem_scope: $("tandem-scope").value, review_status: $("review-status").value, notes: $("review-notes").value };
}

function renderEligibility(reasons) {
  const badge = $("eligibility");
  if (reasons.length) { badge.className = "pill excluded"; badge.textContent = "Excluded from scoring"; }
  else { badge.className = "pill included"; badge.textContent = "Included in scoring"; }
}

function renderProgress(progress = { reviewed: 0, total: 0 }) {
  const badge = $("field-progress");
  badge.textContent = `${progress.reviewed}/${progress.total} fields`;
  badge.className = `pill ${progress.total && progress.reviewed === progress.total ? "included" : "neutral"}`;
}

function summarizeCells(data) {
  const cells = data?.cells || [];
  if (!cells.length) return "No cells in this result";
  const pces = cells.map((cell) => cell?.pce?.value).filter((value) => value != null);
  return `${cells.length} cell${cells.length === 1 ? "" : "s"}${pces.length ? ` · PCE ${pces.slice(0, 8).join(", ")}%` : ""}`;
}

async function selectPaper(paperId) {
  if (state.evidenceDirty && !window.confirm("Discard unsaved field-review changes?")) return;
  state.selected = paperId;
  state.quantityData = null;
  state.evidenceDirty = false;
  state.pendingCursor = null;
  $("paper-title").textContent = paperId.replace("--", "/");
  await jumpToPage(1);
  $("search-results").hidden = true;
  $("pdf-search").value = "";
  renderPapers();
  await loadPaperData();
}

async function loadPaperData() {
  const source = $("source").value;
  const params = new URLSearchParams({ split: state.split });
  if (source) params.set("source", source);
  const [paperData, reviewData, commentData, issueData, figureAuditData] = await Promise.all([
    request(`/api/paper/${encodeURIComponent(state.selected)}?${params}`),
    request(`/api/review/${state.split}/${encodeURIComponent(state.selected)}?reviewer=${encodeURIComponent(reviewerId())}`),
    request(`/api/comments/${state.split}/${encodeURIComponent(state.selected)}`),
    request(`/api/issues/${state.split}/${encodeURIComponent(state.selected)}`),
    request(`/api/figure-audits/${state.split}/${encodeURIComponent(state.selected)}`),
  ]);
  state.paperData = paperData;
  state.reviewData = reviewData;
  state.comments = commentData.comments;
  state.issues = issueData.issues;
  state.figureAudits = figureAuditData.audits;
  const meta = paperData.metadata;
  $("article-type").value = meta.article_type;
  $("tandem-scope").value = meta.tandem_scope;
  $("review-status").value = meta.review_status;
  $("review-notes").value = meta.notes || "";
  renderEligibility(currentPaper()?.exclusion_reasons || []);
  renderProgress(reviewData.progress);
  populateCellFilter();
  renderView();
}

function populateCellFilter() {
  const indexes = [...new Set(state.reviewData.facts.map((fact) => fact.path.match(/^\/cells\/(\d+)/)?.[1]).filter((x) => x != null))];
  renderSafeHtml($("field-cell"), `<option value="all">All cells</option>` + indexes.map((index) => `<option value="${index}">Cell ${Number(index) + 1}</option>`).join(""));
  renderSafeHtml($("issue-cell"), `<option value="">Paper-level / unknown</option>` + indexes.map((index) => `<option value="${index}">Cell ${Number(index) + 1}</option>`).join(""));
  if (indexes.length) $("field-cell").value = indexes[0];
}

function cellContextHtml(index) {
  const cell = state.paperData?.ground_truth?.cells?.[Number(index)];
  if (!cell) return "";
  const composition = cell.perovskite_composition || {};
  const metric = (key, suffix = "") => cell[key]?.value == null ? null : `${key.toUpperCase()} ${cell[key].value}${cell[key].unit || suffix}`;
  const metrics = [metric("pce"), metric("jsc"), metric("voc"), metric("ff", "%"), metric("active_area")].filter(Boolean);
  const stack = (cell.layers || []).map((layer) => layer.name).filter(Boolean);
  return `<div class="cell-context-heading"><div><span class="cell-number">Cell ${Number(index) + 1}</span><strong>${escapeHtml(composition.formula || "Composition not recorded")}</strong></div><span>${escapeHtml(cell.device_architecture || "architecture unknown")}</span></div>
    <div class="cell-context-metrics">${metrics.map((item) => `<span>${escapeHtml(item)}</span>`).join("")}</div>
    <div class="cell-context-details"><span><b>Type</b> ${escapeHtml(composition.sample_type || "—")}</span><span><b>Dimensionality</b> ${escapeHtml(composition.dimensionality || "—")}</span><span class="stack"><b>Stack</b> ${escapeHtml(stack.join(" / ") || "not recorded")}</span></div>`;
}

function renderCellContext() {
  const selected = $("field-cell").value;
  if (selected === "all") {
    const count = state.paperData?.ground_truth?.cells?.length || 0;
    renderSafeHtml($("cell-context"), `<div class="review-hint">${count} cells in this paper. Select a cell to keep its composition, metrics, and stack visible while reviewing.</div>`);
  } else renderSafeHtml($("cell-context"), cellContextHtml(selected));
}

function pathLabel(path) {
  const parts = path.split("/").filter(Boolean);
  const labels = [];
  for (let i = 0; i < parts.length; i += 1) {
    const part = parts[i];
    if (part === "cells" && /^\d+$/.test(parts[i + 1] || "")) { labels.push(`Cell ${Number(parts[++i]) + 1}`); continue; }
    if (/^\d+$/.test(part)) labels.push(`#${Number(part) + 1}`);
    else labels.push(part.replaceAll("_", " "));
  }
  return labels.join(" › ");
}

function statusLabel(status) {
  return { pending: "Pending", verified: "Verified", incorrect: "Incorrect JSON", not_in_paper: "Not found in paper", needs_followup: "Needs follow-up" }[status] || status;
}

const VALUE_RELATIONS = [
  ["unspecified", "Not specified"], ["exact", "Exact"],
  ["approximately", "Approximately"], ["lower_bound", "Lower bound / at least"],
  ["upper_bound", "Upper bound / at most"], ["range", "Range"],
];
const AGGREGATIONS = [
  ["unspecified", "Not specified"], ["single_measurement", "Single measurement"],
  ["mean", "Average / mean"], ["median", "Median"], ["champion", "Champion"],
  ["stabilized", "Stabilized"], ["distribution", "Distribution"],
];

function selectOptions(options, selected) {
  return options.map(([value, label]) => `<option value="${value}" ${selected === value ? "selected" : ""}>${label}</option>`).join("");
}

function highlightedSnippet(suggestion) {
  if (!suggestion) return "";
  const before = suggestion.snippet.slice(0, suggestion.match_start);
  const match = suggestion.snippet.slice(suggestion.match_start, suggestion.match_end);
  const after = suggestion.snippet.slice(suggestion.match_end);
  return `${escapeHtml(before)}<mark>${escapeHtml(match)}</mark>${escapeHtml(after)}`;
}

function matchedText(suggestion, fallback = "") {
  if (!suggestion) return fallback;
  return suggestion.snippet.slice(suggestion.match_start, suggestion.match_end) || fallback;
}

function jumpToFactEvidence(fact) {
  if (fact.suggestion) {
    return jumpToPage(
      fact.suggestion.page,
      matchedText(fact.suggestion, fact.suggestion.query),
      null,
      fact.suggestion.snippet,
    );
  }
  return searchValue(fact);
}

function renderFields() {
  if (!state.reviewData) return;
  const cell = $("field-cell").value;
  const status = $("field-status").value;
  const query = $("field-query").value.trim().toLowerCase();
  const facts = state.reviewData.facts.filter((fact) => {
    const factCell = fact.path.match(/^\/cells\/(\d+)/)?.[1];
    const statusMatch = status === "all" || fact.evidence.status === status || (status === "issues" && !["pending", "verified"].includes(fact.evidence.status)) || (status === "disagreements" && fact.disagreement);
    return (cell === "all" || factCell === cell) && statusMatch && (!query || pathLabel(fact.path).toLowerCase().includes(query) || String(fact.value).toLowerCase().includes(query));
  });
  renderCellContext();
  renderSafeHtml($("field-list"), facts.length ? facts.map((fact) => {
    const suggestion = fact.suggestion;
    const fieldComments = state.comments.filter((comment) => comment.field_path === fact.path);
    const peerReviews = Object.entries(fact.reviews || {}).filter(([id]) => id !== reviewerId());
    return `<article class="fact-card ${fact.evidence.status} ${fact.disagreement ? "has-disagreement" : ""}" data-fact-path="${escapeHtml(fact.path)}">
      <div class="fact-topline">
        <label class="check-label"><input class="verify-field" type="checkbox" ${fact.evidence.status === "verified" ? "checked" : ""} /> <span>verified</span></label>
        <span class="fact-path">${escapeHtml(pathLabel(fact.path))}</span>
        <button class="value-chip" data-action="search-value">${escapeHtml(JSON.stringify(fact.value))}</button>
      </div>
      ${peerReviews.length ? `<div class="peer-reviews">${peerReviews.map(([id, review]) => `<span class="peer-review ${review.status}">${escapeHtml(reviewerName(id))}: ${escapeHtml(statusLabel(review.status))}${review.value_relation && review.value_relation !== "unspecified" ? ` · ${escapeHtml(review.value_relation.replaceAll("_", " "))}` : ""}${review.aggregation && review.aggregation !== "unspecified" ? ` · ${escapeHtml(review.aggregation.replaceAll("_", " "))}` : ""}</span>`).join("")}${fact.disagreement ? `<strong>disagreement</strong>` : ""}</div>` : ""}
      ${suggestion ? `<div class="suggestion"><button data-action="jump-suggestion">p. ${suggestion.page}</button><p>${highlightedSnippet(suggestion)}<span class="suggestion-reason">${escapeHtml(suggestion.rationale || "Exact text match")}</span></p><button data-action="use-quote">Use as quote</button></div>` : `<div class="no-suggestion">No exact text match suggested — click the value to search variants.</div>`}
      <div class="evidence-fields">
        <label>Decision <select class="fact-status">${["pending", "verified", "incorrect", "not_in_paper", "needs_followup"].map((item) => `<option value="${item}" ${fact.evidence.status === item ? "selected" : ""}>${statusLabel(item)}</option>`).join("")}</select></label>
        <label>Value relation <select class="fact-value-relation">${selectOptions(VALUE_RELATIONS, fact.evidence.value_relation || "unspecified")}</select></label>
        <label>Page <input class="fact-page" type="number" min="1" value="${fact.evidence.page || suggestion?.page || ""}" /></label>
        <label>Aggregation <select class="fact-aggregation">${selectOptions(AGGREGATIONS, fact.evidence.aggregation || "unspecified")}</select></label>
        <label class="wide">Evidence quote <textarea class="fact-quote" rows="2" placeholder="Paste a short supporting quote…">${escapeHtml(fact.evidence.quote || "")}</textarea></label>
        <label class="wide">Reviewer note <input class="fact-notes" value="${escapeHtml(fact.evidence.notes || "")}" placeholder="Why is this ambiguous or incorrect?" /></label>
      </div>
      <div class="field-discussion">
        ${fieldComments.map((comment) => `<p><strong>${escapeHtml(reviewerName(comment.author_id))}</strong> ${escapeHtml(comment.body)}</p>`).join("")}
        <div><input class="field-comment" placeholder="Discuss this field with other reviewers…" /><button data-action="add-field-comment">Comment</button></div>
      </div>
    </article>`;
  }).join("") : `<div class="empty-state">No fields match these filters.</div>`);
  bindFieldEvents();
}

function factForCard(card) { return state.reviewData.facts.find((fact) => fact.path === card.dataset.factPath); }

function updateFactFromCard(card) {
  const fact = factForCard(card);
  fact.evidence.status = card.querySelector(".fact-status").value;
  fact.evidence.value_relation = card.querySelector(".fact-value-relation").value;
  fact.evidence.aggregation = card.querySelector(".fact-aggregation").value;
  fact.evidence.page = Number(card.querySelector(".fact-page").value) || null;
  fact.evidence.quote = card.querySelector(".fact-quote").value;
  fact.evidence.notes = card.querySelector(".fact-notes").value;
  card.querySelector(".verify-field").checked = fact.evidence.status === "verified";
  card.className = `fact-card ${fact.evidence.status}`;
  state.evidenceDirty = true;
  $("evidence-status").textContent = "Unsaved review changes";
}

function bindFieldEvents() {
  document.querySelectorAll(".fact-card").forEach((card) => {
    const fact = factForCard(card);
    card.querySelector(".verify-field").addEventListener("change", (event) => {
      card.querySelector(".fact-status").value = event.target.checked ? "verified" : "pending";
      updateFactFromCard(card);
    });
    card.querySelectorAll(".fact-status,.fact-value-relation,.fact-aggregation,.fact-page,.fact-quote,.fact-notes").forEach((input) => input.addEventListener("change", () => updateFactFromCard(card)));
    card.querySelector('[data-action="search-value"]').addEventListener("click", () => jumpToFactEvidence(fact));
    card.querySelector('[data-action="jump-suggestion"]')?.addEventListener("click", () => jumpToFactEvidence(fact));
    card.querySelector('[data-action="use-quote"]')?.addEventListener("click", () => {
      card.querySelector(".fact-page").value = fact.suggestion.page;
      card.querySelector(".fact-quote").value = fact.suggestion.snippet;
      updateFactFromCard(card);
    });
    card.querySelector('[data-action="add-field-comment"]').addEventListener("click", async () => {
      const input = card.querySelector(".field-comment");
      if (!input.value.trim()) return;
      await addComment(input.value, fact.path);
      input.value = "";
      renderFields();
    });
  });
}

function nextPendingField() {
  const facts = state.reviewData?.facts || [];
  if (!facts.length) return;
  const start = Math.max(-1, facts.findIndex((fact) => fact.path === state.pendingCursor));
  const ordered = facts.slice(start + 1).concat(facts.slice(0, start + 1));
  const fact = ordered.find((item) => item.evidence.status === "pending");
  if (!fact) {
    $("evidence-status").textContent = "All fields in this paper have been reviewed";
    return;
  }
  state.pendingCursor = fact.path;
  const cell = fact.path.match(/^\/cells\/(\d+)/)?.[1];
  $("field-cell").value = cell == null ? "all" : cell;
  $("field-status").value = "pending";
  $("field-query").value = "";
  renderFields();
  requestAnimationFrame(() => {
    const card = [...document.querySelectorAll(".fact-card")].find((item) => item.dataset.factPath === fact.path);
    card?.scrollIntoView({ behavior: "smooth", block: "center" });
    if (fact.suggestion) jumpToPage(fact.suggestion.page, matchedText(fact.suggestion, fact.suggestion.query), null, fact.suggestion.snippet);
  });
}

async function searchValue(fact) {
  const query = fact.suggestion?.query || String(fact.value);
  $("pdf-search").value = query;
  await searchPdf(true);
}

async function loadQuantities() {
  if (state.quantityData) return renderQuantities();
  $("quantity-summary").textContent = "Scanning PDF quantities…";
  state.quantityData = await request(`/api/quantities/${encodeURIComponent(state.selected)}?split=${state.split}`);
  renderQuantities();
}

function renderQuantities() {
  const query = $("quantity-query").value.trim().toLowerCase();
  const category = $("quantity-category").value;
  const groups = (state.quantityData.candidate_groups || []).filter((group) =>
    (category === "all" || group.category === category) &&
    (!query || `${group.category} ${group.context} ${group.mentions.map((item) => item.text).join(" ")}`.toLowerCase().includes(query))
  );
  $("quantity-summary").textContent = `${state.quantityData.unmapped_count} unmatched mentions are grouped into ${state.quantityData.candidate_groups.length} evidence candidates and ranked by likely schema relevance. Review grouped device tuples before isolated process quantities; plot-only values belong in Figure audit.`;
  renderSafeHtml($("quantity-list"), groups.length ? groups.slice(0, 200).map((group, index) => `<article class="quantity-card" data-page="${group.page}" data-query="${escapeHtml(group.mentions[0].raw_value)}" data-quantity-index="${index}">
    <button class="quantity-jump"><span class="quantity-value">${group.mentions.map((item) => escapeHtml(item.text)).join(" · ")}</span><span class="schema-tags"><span>${escapeHtml(group.category)}</span></span><span class="quantity-page">p. ${group.page}</span><span class="quantity-context">${escapeHtml(group.context)}</span></button>
    <button class="report-quantity">Propose correction</button>
  </article>`).join("") : `<div class="empty-state">No unmatched quantities match this filter.</div>`);
  document.querySelectorAll(".quantity-jump").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".quantity-card");
    const group = groups[Number(card.dataset.quantityIndex)];
    jumpToPage(card.dataset.page, card.dataset.query, null, group.context);
    $("pdf-search").value = card.dataset.query;
  }));
  document.querySelectorAll(".report-quantity").forEach((button) => button.addEventListener("click", async () => {
    const card = button.closest(".quantity-card");
    const group = groups[Number(card.dataset.quantityIndex)];
    const values = group.mentions.map((item) => item.text).join(", ");
    await createIssue({ type: "missing_value", description: `${group.category} candidate (${values}) appears in one evidence passage but is not matched to ground truth. Verify device linkage and eligibility before applying.`, suggested_value: values, source_page: group.page, source_text: group.context });
    state.tab = "issues"; renderView();
  }));
}

function renderJson() {
  const data = state.tab === "truth" ? state.paperData?.ground_truth : state.paperData?.extraction;
  $("json-editor").value = data ? JSON.stringify(data, null, 2) : "";
  $("json-editor").readOnly = state.tab !== "truth";
  $("save-json").disabled = state.tab !== "truth";
  $("cell-summary").textContent = summarizeCells(data);
}

function renderDiscussion() {
  const disagreements = state.reviewData?.disagreement_paths || [];
  renderSafeHtml($("disagreement-summary"), `<strong>${disagreements.length} field disagreement${disagreements.length === 1 ? "" : "s"}</strong> across reviewer decisions. Use the Field review status filter to inspect them beside the PDF.`);
  const paperComments = state.comments.filter((comment) => !comment.field_path);
  const fieldComments = state.comments.filter((comment) => comment.field_path);
  renderSafeHtml($("discussion-list"), `
    <h3>Paper discussion</h3>
    ${paperComments.length ? paperComments.map(commentHtml).join("") : `<div class="empty-state">No paper-level comments yet.</div>`}
    <h3>Field comments</h3>
    ${fieldComments.length ? fieldComments.map((comment) => `${commentHtml(comment)}<button class="comment-path" data-comment-path="${escapeHtml(comment.field_path)}">${escapeHtml(pathLabel(comment.field_path))}</button>`).join("") : `<div class="empty-state">No field comments yet.</div>`}`);
  document.querySelectorAll("[data-comment-path]").forEach((button) => button.addEventListener("click", () => {
    state.tab = "fields"; $("field-query").value = button.dataset.commentPath.split("/").pop(); renderView();
  }));
}

function commentHtml(comment) {
  return `<article class="comment"><div><strong>${escapeHtml(reviewerName(comment.author_id))}</strong><time>${escapeHtml(new Date(comment.created_at).toLocaleString())}</time></div><p>${escapeHtml(comment.body)}</p></article>`;
}

async function addComment(body, fieldPath = null) {
  const payload = await request(`/api/comments/${state.split}/${encodeURIComponent(state.selected)}`, {
    method: "POST", body: JSON.stringify({ author_id: reviewerId(), body, field_path: fieldPath }),
  });
  state.comments.push(payload.comment);
}

function issueTypeLabel(type) {
  return { missing_cell: "Missing cell / device", missing_value: "Missing value", missing_layer: "Missing layer", missing_composition: "Missing composition", mixed_device: "Values mixed across devices", out_of_scope_tandem: "Out-of-scope tandem device", schema_limitation: "Schema limitation / uncertainty", wrong_value: "Wrong value", other: "Other" }[type] || type;
}

function renderFigureAudit() {
  const mine = state.figureAudits[reviewerId()];
  $("total-figures").value = mine?.total_figures ?? "";
  $("schema-figures").value = mine?.schema_relevant_figures ?? "";
  $("figure-only-figures").value = mine?.figure_only_schema_figures ?? "";
  $("unlinked-statistic-figures").value = mine?.unlinked_device_statistic_figures ?? "";
  $("figure-notes").value = mine?.notes || "";
  $("figure-audit-status").textContent = mine
    ? `Your audit saved ${new Date(mine.updated_at).toLocaleString()}`
    : "Not yet reviewed by you";
  const peers = Object.values(state.figureAudits).filter((audit) => audit.reviewer_id !== reviewerId());
  renderSafeHtml($("peer-figure-audits"), peers.length ? `<h3>Other reviewers and seeded suggestions</h3>${peers.map((audit) => `<article class="peer-figure-audit">
    <strong>${escapeHtml(reviewerName(audit.reviewer_id))}</strong>
    <span>${audit.total_figures} total · ${audit.schema_relevant_figures} schema-relevant · ${audit.figure_only_schema_figures} figure-only · ${audit.unlinked_device_statistic_figures || 0} with unlinked device statistics</span>
    ${audit.notes ? `<p>${escapeHtml(audit.notes)}</p>` : ""}
    <button type="button" data-use-figure-audit="${escapeHtml(audit.reviewer_id)}">Use these counts and notes</button>
  </article>`).join("")}` : `<div class="empty-state">No other reviewer has audited the figures yet.</div>`);
  $("peer-figure-audits").querySelectorAll("[data-use-figure-audit]").forEach((button) => button.addEventListener("click", () => {
    const audit = state.figureAudits[button.dataset.useFigureAudit];
    if (!audit) return;
    $("total-figures").value = audit.total_figures;
    $("schema-figures").value = audit.schema_relevant_figures;
    $("figure-only-figures").value = audit.figure_only_schema_figures;
    $("unlinked-statistic-figures").value = audit.unlinked_device_statistic_figures || 0;
    $("figure-notes").value = audit.notes || "";
    $("figure-audit-status").textContent = "Suggestion copied — review it, then save";
  }));
}

function renderIssues() {
  const open = state.issues.filter((issue) => issue.status === "open");
  renderSafeHtml($("issue-summary"), `<strong>${open.length} open issue${open.length === 1 ? "" : "s"}</strong>. Missing items remain separate from the JSON until reviewed and resolved.`);
  renderSafeHtml($("issue-list"), state.issues.length ? state.issues.map((issue) => {
    const proposal = issue.schema_proposal || {};
    const proposalParts = [proposal.value_relation, proposal.aggregation, proposal.measurement_context].filter((value) => value && value !== "unspecified");
    return `<article class="issue ${issue.status}">
    <div class="issue-heading"><span>${escapeHtml(issueTypeLabel(issue.type))}</span><strong>${escapeHtml(issue.status)}</strong></div>
    <p>${escapeHtml(issue.description)}</p>
    <div class="issue-meta">${issue.cell_index == null ? "Paper-level" : `Cell ${issue.cell_index + 1}`}${issue.source_page ? ` · p. ${issue.source_page}` : ""}${issue.suggested_value ? ` · suggested: ${escapeHtml(issue.suggested_value)}` : ""}</div>
    ${proposalParts.length || proposal.uncertainty ? `<div class="schema-tags">${proposalParts.map((value) => `<span>${escapeHtml(value.replaceAll("_", " "))}</span>`).join("")}${proposal.uncertainty ? `<span>${escapeHtml(proposal.uncertainty)}</span>` : ""}</div>` : ""}
    ${issue.proposed_patch?.length ? `<details class="proposed-patch"><summary>Proposed correction (${issue.proposed_patch.length} operation${issue.proposed_patch.length === 1 ? "" : "s"})</summary><pre><code>${escapeHtml(JSON.stringify(issue.proposed_patch, null, 2))}</code></pre><button type="button" data-copy-patch="${issue.id}">Copy patch</button></details>` : ""}
    ${issue.source_page ? `<button class="issue-jump" data-issue-jump="${issue.id}">Jump to evidence</button>` : ""}
    ${issue.status === "open" ? `<div class="issue-resolution"><input placeholder="Resolution note…" /><button data-resolve-issue="${issue.id}">Resolve</button></div>` : `<div class="issue-meta">Resolved by ${escapeHtml(reviewerName(issue.resolved_by))}: ${escapeHtml(issue.resolution || "no note")}</div>`}
  </article>`; }).join("") : `<div class="empty-state">No missing-item reports yet.</div>`);
  document.querySelectorAll("[data-issue-jump]").forEach((button) => button.addEventListener("click", () => {
    const issue = state.issues.find((item) => item.id === button.dataset.issueJump);
    if (!issue) return;
    if (issue.source_text) $("pdf-search").value = issue.source_text;
    jumpToPage(issue.source_page, issue.source_text || "", null, issue.source_text || "");
  }));
  document.querySelectorAll("[data-copy-patch]").forEach((button) => button.addEventListener("click", async () => {
    const issue = state.issues.find((item) => item.id === button.dataset.copyPatch);
    if (!issue) return;
    await navigator.clipboard.writeText(JSON.stringify(issue.proposed_patch, null, 2));
    button.textContent = "Copied";
  }));
  document.querySelectorAll("[data-resolve-issue]").forEach((button) => button.addEventListener("click", async () => {
    const resolution = button.previousElementSibling.value;
    const payload = await request(`/api/issues/${state.split}/${encodeURIComponent(state.selected)}/${button.dataset.resolveIssue}`, { method: "PUT", body: JSON.stringify({ reviewer_id: reviewerId(), resolution }) });
    const index = state.issues.findIndex((issue) => issue.id === payload.issue.id); state.issues[index] = payload.issue; renderIssues();
  }));
}

async function createIssue(overrides = {}) {
  let proposedPatch = overrides.proposed_patch || [];
  if (!overrides.proposed_patch && $("issue-proposed-patch").value.trim()) {
    proposedPatch = JSON.parse($("issue-proposed-patch").value);
  }
  const payload = {
    reporter_id: reviewerId(), type: overrides.type || $("issue-type").value,
    description: overrides.description || $("issue-description").value,
    cell_index: overrides.cell_index ?? ($("issue-cell").value === "" ? null : Number($("issue-cell").value)),
    suggested_value: overrides.suggested_value || $("issue-suggested-value").value,
    source_page: overrides.source_page || null, source_text: overrides.source_text || "",
    value_relation: overrides.value_relation || $("issue-value-relation").value,
    aggregation: overrides.aggregation || $("issue-aggregation").value,
    measurement_context: overrides.measurement_context || $("issue-measurement-context").value,
    uncertainty: overrides.uncertainty || $("issue-uncertainty").value,
    proposed_patch: proposedPatch,
  };
  const result = await request(`/api/issues/${state.split}/${encodeURIComponent(state.selected)}`, { method: "POST", body: JSON.stringify(payload) });
  state.issues.push(result.issue); currentPaper().open_issues = (currentPaper().open_issues || 0) + 1; renderPapers();
  return result.issue;
}

function renderView() {
  ["fields", "gaps", "figures", "discussion", "issues", "json"].forEach((panel) => $(`${panel}-panel`).hidden = true);
  $("source").closest("label").hidden = state.tab !== "extraction";
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === state.tab));
  if (state.tab === "fields") { $("fields-panel").hidden = false; $("cell-summary").textContent = "Review each non-null scalar ground-truth field against the paper."; renderFields(); }
  else if (state.tab === "gaps") { $("gaps-panel").hidden = false; $("cell-summary").textContent = "Candidate quantities in the paper that may be missing from the JSON."; loadQuantities().catch((error) => $("quantity-summary").textContent = error.message); }
  else if (state.tab === "figures") { $("figures-panel").hidden = false; $("cell-summary").textContent = "Separate accounting for schema-relevant and figure-only evidence."; renderFigureAudit(); }
  else if (state.tab === "discussion") { $("discussion-panel").hidden = false; $("cell-summary").textContent = "Compare reviewer decisions and discuss scoring differences."; renderDiscussion(); }
  else if (state.tab === "issues") { $("issues-panel").hidden = false; $("cell-summary").textContent = "Structured reports for cells, values, or layers missing from the ground truth."; renderIssues(); }
  else { $("json-panel").hidden = false; renderJson(); }
}

async function findHighlight(page, text) {
  if (!text) return null;
  try {
    const payload = await request(`/api/search/${encodeURIComponent(state.selected)}?q=${encodeURIComponent(text)}`);
    return payload.results.find((result) => result.page === page && result.bbox)?.bbox || null;
  } catch (_) { return null; }
}

function showPdfHighlight(bbox, text = "", quote = "") {
  const highlight = $("pdf-highlight");
  state.pdfQuote = (quote || text).trim();
  $("copy-pdf-quote").disabled = !state.pdfQuote;
  if (!bbox) {
    highlight.hidden = true;
    $("pdf-scroll").scrollTop = 0;
    $("pdf-location").textContent = `Page ${state.pdfPage}`;
    return;
  }
  const paddingX = 0.004;
  const paddingY = 0.003;
  highlight.style.left = `${100 * Math.max(0, bbox.x - paddingX)}%`;
  highlight.style.top = `${100 * Math.max(0, bbox.y - paddingY)}%`;
  highlight.style.width = `${100 * Math.min(1, bbox.width + 2 * paddingX)}%`;
  highlight.style.height = `${100 * Math.min(1, bbox.height + 2 * paddingY)}%`;
  highlight.hidden = false;
  const scroller = $("pdf-scroll");
  const stage = $("pdf-page-stage");
  scroller.scrollTop = Math.max(0, stage.offsetTop + bbox.y * stage.clientHeight - scroller.clientHeight / 3);
  $("pdf-location").textContent = text ? `Highlighted “${text}”` : `Page ${state.pdfPage}`;
}

function renderPdfTextLayer(lines = []) {
  const layer = $("pdf-text-layer");
  const stage = $("pdf-page-stage");
  layer.replaceChildren();
  lines.forEach((line) => {
    const node = document.createElement("div");
    node.className = "pdf-text-line";
    node.textContent = line.text;
    node.style.left = `${100 * line.bbox.x}%`;
    node.style.top = `${100 * line.bbox.y}%`;
    node.style.fontSize = `${Math.max(1, line.font_size * stage.clientHeight)}px`;
    node.style.height = `${100 * line.bbox.height}%`;
    layer.append(node);
    const targetWidth = line.bbox.width * stage.clientWidth;
    const measuredWidth = node.scrollWidth;
    if (targetWidth > 0 && measuredWidth > 0) {
      node.style.transform = `scaleX(${targetWidth / measuredWidth})`;
    }
  });
}

async function jumpToPage(page, text = "", bbox = null, quote = "") {
  if (!state.selected) return;
  const targetPage = Math.max(1, Math.min(state.pdfPageCount || Infinity, Number(page) || 1));
  const navigationId = ++state.pdfNavigationId;
  const selectedPaper = state.selected;
  $("pdf-location").textContent = `Loading page ${targetPage}…`;
  $("pdf-highlight").hidden = true;
  $("pdf-text-layer").replaceChildren();
  const highlightPromise = bbox ? Promise.resolve(bbox) : findHighlight(targetPage, text);
  const pageTextPromise = request(`/api/pdf-text/${encodeURIComponent(selectedPaper)}?page=${targetPage}`);
  const response = await fetch(
    `/api/pdf-page/${encodeURIComponent(selectedPaper)}?page=${targetPage}&scale=1.6`,
    { headers: await authorizationHeaders() },
  );
  if (!response.ok) {
    let message = `PDF page failed to load (${response.status})`;
    try { message = (await response.json()).error || message; } catch (_) { /* Not JSON. */ }
    throw new Error(message);
  }
  const imageUrl = URL.createObjectURL(await response.blob());
  if (navigationId !== state.pdfNavigationId || selectedPaper !== state.selected) {
    URL.revokeObjectURL(imageUrl);
    return;
  }
  state.pdfPage = targetPage;
  state.pdfPageCount = Number(response.headers.get("X-PDF-Pages")) || state.pdfPageCount;
  if (state.pdfImageUrl) URL.revokeObjectURL(state.pdfImageUrl);
  state.pdfImageUrl = imageUrl;
  const image = $("pdf-page-image");
  image.src = imageUrl;
  try { await image.decode(); } catch (_) { /* The load event still paints the page. */ }
  if (navigationId !== state.pdfNavigationId) return;
  $("pdf-page").value = targetPage;
  $("pdf-page").max = state.pdfPageCount || targetPage;
  $("pdf-page-count").textContent = state.pdfPageCount || "–";
  $("pdf-previous").disabled = targetPage <= 1;
  $("pdf-next").disabled = Boolean(state.pdfPageCount && targetPage >= state.pdfPageCount);
  const pageText = await pageTextPromise;
  state.pdfPageText = pageText.text || "";
  state.pdfTextLines = pageText.lines || [];
  renderPdfTextLayer(state.pdfTextLines);
  $("copy-page-text").disabled = !state.pdfPageText;
  showPdfHighlight(await highlightPromise, text, quote);
}

async function searchPdf(jumpFirst = false) {
  const query = $("pdf-search").value.trim();
  if (!query) return;
  const box = $("search-results");
  box.hidden = false;
  box.textContent = "Searching…";
  const payload = await request(`/api/search/${encodeURIComponent(state.selected)}?q=${encodeURIComponent(query)}`);
  if (!payload.results.length) { box.textContent = "No exact text matches found."; return; }
  renderSafeHtml(box, payload.results.map((result, index) => `<button class="search-hit" data-page="${result.page}" data-result-index="${index}"><strong>Jump · p. ${result.page}</strong><span>${highlightedSnippet(result)}</span></button>`).join(""));
  box.querySelectorAll("[data-page]").forEach((hit) => hit.addEventListener("click", () => {
    const result = payload.results[Number(hit.dataset.resultIndex)];
    box.hidden = true;
    jumpToPage(result.page, matchedText(result, query), result.bbox, result.snippet);
  }));
  if (jumpFirst) {
    box.hidden = true;
    jumpToPage(payload.results[0].page, matchedText(payload.results[0], query), payload.results[0].bbox, payload.results[0].snippet);
  }
}

async function copyPdfText(text, kind) {
  if (!text) return;
  const content = kind === "quote"
    ? `${text.replace(/\s+/g, " ").trim()} (p. ${state.pdfPage})`
    : `Page ${state.pdfPage}\n\n${text.trim()}`;
  try {
    await navigator.clipboard.writeText(content);
    $("pdf-location").textContent = `${kind === "quote" ? "Quote" : "Page text"} copied with page ${state.pdfPage}`;
  } catch (_) {
    $("pdf-location").textContent = "Clipboard access was blocked by the browser";
  }
}

document.querySelectorAll("[data-split]").forEach((button) => button.addEventListener("click", async () => { state.split = button.dataset.split; state.selected = null; await loadPapers(); }));
$("filter").addEventListener("change", renderPapers);
$("source").addEventListener("change", loadPaperData);
$("search-button").addEventListener("click", () => searchPdf(false));
$("pdf-search").addEventListener("keydown", (event) => { if (event.key === "Enter") searchPdf(false); });
$("pdf-previous").addEventListener("click", () => jumpToPage(state.pdfPage - 1));
$("pdf-next").addEventListener("click", () => jumpToPage(state.pdfPage + 1));
$("pdf-page").addEventListener("change", () => jumpToPage($("pdf-page").value));
$("copy-pdf-quote").addEventListener("click", () => copyPdfText(state.pdfQuote, "quote"));
$("copy-page-text").addEventListener("click", () => copyPdfText(state.pdfPageText, "page"));
$("pdf-text-layer").addEventListener("mouseup", () => {
  const selectedText = window.getSelection()?.toString().replace(/\s+/g, " ").trim();
  if (!selectedText) return;
  state.pdfQuote = selectedText;
  $("copy-pdf-quote").disabled = false;
  $("pdf-location").textContent = `Selected quote on page ${state.pdfPage}`;
});
$("next-pending").addEventListener("click", nextPendingField);
window.addEventListener("resize", () => renderPdfTextLayer(state.pdfTextLines));
$("field-cell").addEventListener("change", renderFields);
$("field-status").addEventListener("change", renderFields);
$("field-query").addEventListener("input", renderFields);
$("quantity-query").addEventListener("input", renderQuantities);
$("quantity-category").addEventListener("change", renderQuantities);
document.querySelectorAll(".tab").forEach((tab) => tab.addEventListener("click", () => { state.tab = tab.dataset.tab; renderView(); }));

$("metadata-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("metadata-status"); status.textContent = "Saving…";
  try {
    const payload = await request(`/api/metadata/${state.split}/${encodeURIComponent(state.selected)}`, { method: "PUT", body: JSON.stringify(metadataPayload()) });
    const paper = currentPaper(); paper.metadata = payload.metadata; paper.exclusion_reasons = payload.exclusion_reasons;
    renderEligibility(payload.exclusion_reasons); renderPapers(); status.textContent = "Saved";
  } catch (error) { status.textContent = error.message; }
});

$("save-evidence").addEventListener("click", async () => {
  const status = $("evidence-status"); status.textContent = "Saving…";
  try {
    const fields = Object.fromEntries(state.reviewData.facts.map((fact) => [fact.path, fact.evidence]));
    const payload = await request(`/api/evidence/${state.split}/${encodeURIComponent(state.selected)}`, { method: "PUT", body: JSON.stringify({ reviewer_id: reviewerId(), fields }) });
    state.evidenceDirty = false; state.reviewData.progress = payload.progress;
    currentPaper().field_review = payload.overall_progress; renderProgress(payload.progress); renderPapers(); await loadReviewerProgress(); status.textContent = "Field review saved";
  } catch (error) { status.textContent = error.message; }
});

$("paper-comment-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = $("paper-comment");
  if (!input.value.trim()) return;
  await addComment(input.value); input.value = ""; renderDiscussion();
});

$("issue-form").addEventListener("submit", async (event) => {
  event.preventDefault(); const status = $("issue-status"); status.textContent = "Saving…";
  try { await createIssue(); $("issue-description").value = ""; $("issue-suggested-value").value = ""; $("issue-uncertainty").value = ""; $("issue-proposed-patch").value = ""; status.textContent = "Issue created"; renderIssues(); }
  catch (error) { status.textContent = error.message; }
});

$("figure-audit-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const status = $("figure-audit-status");
  status.textContent = "Saving…";
  try {
    const payload = {
      total_figures: Number($("total-figures").value),
      schema_relevant_figures: Number($("schema-figures").value),
      figure_only_schema_figures: Number($("figure-only-figures").value),
      unlinked_device_statistic_figures: Number($("unlinked-statistic-figures").value),
      notes: $("figure-notes").value,
    };
    const result = await request(`/api/figure-audits/${state.split}/${encodeURIComponent(state.selected)}`, { method: "PUT", body: JSON.stringify(payload) });
    state.figureAudits[reviewerId()] = result.audit;
    renderFigureAudit();
  } catch (error) { status.textContent = error.message; }
});

$("open-import").addEventListener("click", () => $("import-dialog").showModal());
$("cancel-import").addEventListener("click", () => $("import-dialog").close());
$("import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const status = $("import-status"); status.textContent = "Importing paper…";
  try {
    const response = await fetch("/api/papers/import", {
      method: "POST", body: form, headers: await authorizationHeaders(),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Import failed");
    status.textContent = "Imported"; $("import-dialog").close(); state.split = form.get("split"); state.selected = payload.paper.id; await loadPapers(); event.target.reset();
  } catch (error) { status.textContent = error.message; }
});

$("save-json").addEventListener("click", async () => {
  const status = $("json-status");
  try {
    const parsed = JSON.parse($("json-editor").value); status.textContent = "Saving…";
    await request(`/api/ground-truth/${state.split}/${encodeURIComponent(state.selected)}`, { method: "PUT", body: JSON.stringify(parsed) });
    state.paperData.ground_truth = parsed; status.textContent = "Ground truth saved"; await loadPaperData();
  } catch (error) { status.textContent = `Not saved: ${error.message}`; }
});

window.addEventListener("beforeunload", (event) => { if (state.evidenceDirty) event.preventDefault(); });

function loadScript(src, attributes = {}) {
  return new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    Object.entries(attributes).forEach(([name, value]) => script.setAttribute(name, value));
    script.addEventListener("load", resolve, { once: true });
    script.addEventListener("error", () => reject(new Error("Authentication could not be loaded")), { once: true });
    document.head.append(script);
  });
}

async function initialize() {
  const response = await fetch("/api/auth/config");
  const config = await response.json();
  if (config.enabled && config.mode === "internal") {
    state.internalToken = window.localStorage.getItem("perla-review-session");
    if (state.internalToken) {
      try { state.currentUser = (await request("/api/session")).user; }
      catch (_) { window.localStorage.removeItem("perla-review-session"); state.internalToken = null; }
    }
    if (!state.currentUser) {
      $("auth-gate").hidden = false;
      $("internal-sign-in").hidden = false;
      return;
    }
    $("internal-sign-out").hidden = false;
  } else if (config.enabled) {
    await loadScript(`${config.frontend_api}/npm/@clerk/ui@1/dist/ui.browser.js`, { crossorigin: "anonymous" });
    await loadScript(`${config.frontend_api}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`, {
      crossorigin: "anonymous", "data-clerk-publishable-key": config.publishable_key,
    });
    await window.Clerk.load({ ui: { ClerkUI: window.__internal_ClerkUICtor } });
    state.clerk = window.Clerk;
    if (!window.Clerk.isSignedIn) {
      $("auth-gate").hidden = false;
      window.Clerk.mountSignIn($("sign-in"));
      return;
    }
    state.currentUser = (await request("/api/session")).user;
    window.Clerk.mountUserButton($("user-button"));
  } else {
    state.currentUser = { id: "reviewer", name: "Reviewer", role: "admin" };
  }
  $("current-user").textContent = `${state.currentUser.name} · ${state.currentUser.role}`;
  $("workbench").hidden = false;
  await loadPapers();
}

$("internal-sign-in").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("login-error");
  error.textContent = "Signing in…";
  try {
    const response = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: $("login-email").value, password: $("login-password").value }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Sign-in failed");
    window.localStorage.setItem("perla-review-session", payload.token);
    window.location.reload();
  } catch (loginError) { error.textContent = loginError.message; }
});

$("internal-sign-out").addEventListener("click", () => {
  window.localStorage.removeItem("perla-review-session");
  window.location.reload();
});

initialize().catch((error) => {
  $("auth-gate").hidden = false;
  $("sign-in").textContent = error.message;
});
