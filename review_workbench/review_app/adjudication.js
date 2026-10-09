// Shared reference finalization, using the workbench's authenticated requests.
const node = (tag, text, className) => {
  const el = document.createElement(tag);
  if (text != null) el.textContent = text;
  if (className) el.className = className;
  return el;
};
const pretty = (value) => value == null ? "—" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);

export function differences(before, after, path = "") {
  if (JSON.stringify(before) === JSON.stringify(after)) return [];
  // A new checkpoint should appear as an addition, not shift every later row.
  if (Array.isArray(before) && Array.isArray(after)) {
    const entries = [...before, ...after];
    const keys = entries.length && entries.every(value => value && typeof value === "object" && !Array.isArray(value))
      ? Object.keys(entries[0]).filter(key => key.endsWith("_id") || key === "name") : [];
    const identity = keys.find(key => [before, after].every(values => values.every(value => typeof value[key] === "string") && new Set(values.map(value => value[key])).size === values.length));
    if (identity) {
      const old = Object.fromEntries(before.map(value => [value[identity], value]));
      const next = Object.fromEntries(after.map(value => [value[identity], value]));
      const oldOrder = before.map(value => value[identity]).filter(key => Object.hasOwn(next, key));
      const newOrder = after.map(value => value[identity]).filter(key => Object.hasOwn(old, key));
      const reordered = JSON.stringify(oldOrder) !== JSON.stringify(newOrder)
        ? [[`${path}/order`, oldOrder.join(" → "), newOrder.join(" → ")]] : [];
      return [...reordered, ...differences(old, next, path)];
    }
  }
  if ((before == null || typeof before === "object") && (after == null || typeof after === "object") && (before || after)) {
    return [...new Set([...Object.keys(before || {}), ...Object.keys(after || {})])]
      .filter(key => key !== "evidence")
      .flatMap(key => differences(before?.[key], after?.[key], `${path}/${key}`));
  }
  return [[path || "Record", pretty(before), pretty(after)]];
}

// Keep the complete record inspectable without making long recipes bury the actions.
function recordFields(record) {
  const wrapper = node("div");
  const populated = value => value != null && value !== "" && value !== "not_reported";
  const table = value => {
    const el = node("table", null, "finalization-changes");
    for (const [path, , text] of differences(null, value)) {
      if (text === "—" || text === "not_reported" || text === "") continue;
      const row = node("tr"); row.append(node("td", path.replaceAll("_", " ")), node("td", text)); el.append(row);
    }
    return el;
  };
  wrapper.append(table(Object.fromEntries(Object.entries(record).filter(([, value]) => populated(value) && typeof value !== "object"))));
  for (const [key, value] of Object.entries(record)) {
    if (key === "evidence" || !value || typeof value !== "object" || !Object.keys(value).length) continue;
    const group = node("details");
    group.append(node("summary", `${key.replaceAll("_", " ")} (${Array.isArray(value) ? value.length : "details"})`), table(value));
    wrapper.append(group);
  }
  return wrapper;
}

export function createFinalizationQueue({ request, context, download, openRecord, showCitation }) {
  const dialog = node("dialog", null, "finalization-dialog");
  dialog.setAttribute("aria-labelledby", "finalization-heading");
  const shell = node("div", null, "finalization-shell");
  const header = node("div", null, "finalization-header");
  const intro = node("div");
  const heading = node("h2", "Finalize ground truth"); heading.id = "finalization-heading";
  intro.append(heading, node("p", "Resolve the remaining questions. Keep the review already done."));
  const close = node("button", "Close"); close.onclick = () => dialog.close();
  header.append(intro, close);
  const status = node("p", "", "finalization-status"); status.setAttribute("role", "status");
  const layout = node("div", null, "finalization-layout");
  const rail = node("nav", null, "finalization-papers"); rail.setAttribute("aria-label", "Papers to finalize");
  const content = node("section", null, "finalization-content");
  layout.append(rail, content); shell.append(header, status, layout); dialog.append(shell); document.body.append(dialog);
  let queue = null, paper = null, split = "dev", index = 0, busy = false, generation = 0;
  const base = () => `/api/adjudication/${split}/${encodeURIComponent(paper)}`;
  const message = (text, error = false) => { status.textContent = text; status.classList.toggle("error", error); };
  const button = (text, action, primary = false) => {
    const el = node("button", text, primary ? "primary" : ""); el.type = "button";
    el.onclick = () => perform(action); return el;
  };
  async function perform(action) {
    if (busy) return;
    busy = true; dialog.querySelectorAll("button,input,textarea,select").forEach(el => el.disabled = true);
    message("Working…");
    try { await action(); }
    catch (error) { message(error.message, true); }
    finally { busy = false; dialog.querySelectorAll("button,input,textarea,select").forEach(el => el.disabled = false); }
  }
  async function load(id) {
    const previousCase = id === paper ? queue?.cases[index]?.id : null;
    const ticket = ++generation; paper = id; index = 0; queue = null;
    content.replaceChildren(node("p", "Loading saved review…"));
    const result = await request(base());
    if (ticket !== generation) return;
    queue = result;
    if (previousCase) index = Math.max(0, queue.cases.findIndex(item => item.id === previousCase));
    renderRail(); render(); message("All decisions are saved to the review history.");
  }
  function renderRail() {
    rail.replaceChildren();
    for (const item of context().papers) {
      const entry = button(item.id, () => load(item.id));
      entry.setAttribute("aria-current", String(item.id === paper)); rail.append(entry);
    }
    if (!context().canImportPlan) return;
    const label = node("label", "Load prepared suggestions");
    const upload = node("input"); upload.type = "file"; upload.accept = ".json,application/json";
    upload.onchange = () => { if (upload.files[0]) perform(() => importPlan(upload.files[0])); };
    label.append(upload); rail.append(label);
  }
  async function importPlan(file) {
    const plan = JSON.parse(await file.text());
    if (plan.format_version !== 1 || !Array.isArray(plan.papers)) throw new Error("Choose a PERLA finalization-plan JSON file.");
    let saved = 0; const failed = [];
    for (const item of plan.papers) {
      const url = `/api/adjudication/${encodeURIComponent(item.split)}/${encodeURIComponent(item.paper_id)}`;
      try {
        message(`Loading suggestions: ${saved + failed.length + 1} of ${plan.papers.length}…`);
        const current = await request(url);
        await request(`${url}/plan`, { method: "POST", body: JSON.stringify({ base_revision: current.revision, study_sha256: item.study_sha256, proposals: item.proposals, supersedes: item.supersedes || [], feedback_counts: item.feedback_counts || {} }) });
        saved++;
      } catch (error) { failed.push(`${item.paper_id}: ${error.message}`); }
    }
    if (paper) await load(paper);
    message(`${saved} papers loaded.${failed.length ? ` ${failed.length} not imported; see details below.` : ""}`, !!failed.length);
    if (failed.length) content.prepend(node("pre", failed.join("\n")));
  }
  function details(label, value) {
    const el = node("details"); el.append(node("summary", label), node("pre", pretty(value))); return el;
  }
  function workbookFeedback(entries) {
    const group = node("section", null, "finalization-feedback");
    group.append(node("h5", "Original workbook review"));
    for (const value of entries) {
      const entry = typeof value === "string" ? queue.workbook_feedback.find(item => item.id === value) : value;
      const item = node("details");
      item.append(node("summary", `${entry.old_record_key.split(":").at(-1)} · ${entry.review_outcome || "Comment only"}`));
      item.addEventListener("toggle", () => {
      if (!item.open || item.dataset.loaded) return;
      item.dataset.loaded = "true";
      item.append(node("blockquote", entry.text));
      item.append(node("p", `${entry.filename} · ${entry.sheet}!${entry.cell}`, "finalization-summary"));
      item.append(details("Fields in the reviewed workbook", entry.reviewed_fields));
      item.append(details("Corresponding current records", entry.current_record_keys.length ? entry.current_record_keys : "No current counterpart. This is not proof that the record should be removed."));
      for (const key of entry.current_record_keys) {
        const record = queue.workbook_current_records?.[key];
        if (record) {
          const current = node("details"); current.append(node("summary", `Current: ${key.split(":").at(-1)}`), recordFields(record)); item.append(current);
        } else item.append(node("p", `${key}: removed since this correspondence was prepared.`, "error"));
      }
      item.append(details("Original file SHA-256", entry.workbook_sha256));
      });
      group.append(item);
    }
    return group;
  }
  function render() {
    if (!queue) return;
    index = Math.min(index, Math.max(0, queue.cases.length - 1));
    content.replaceChildren(node("h3", queue.title), node("p", `${queue.own_approved_count} approved by you · ${queue.inherited_count} current reviewer approvals · ${queue.cases.length} decisions left`, "finalization-summary"));
    if (queue.last_decision) content.append(button("Undo my last decision", async () => {
      queue = await request(`${base()}/undo`, { method: "POST", body: JSON.stringify({ base_revision: queue.revision, event_id: queue.last_decision.event_id }) }); render(); message("Decision undone. The original remains in history.");
    }));
    if (queue.workbook_feedback?.length) {
      const ledger = node("details");
      ledger.append(node("summary", `${queue.workbook_feedback.length} original workbook comments accounted for`));
      ledger.append(node("p", "Every comment is preserved below and linked to a prepared decision. Accounting for a comment is not an approval of the current data."));
      ledger.append(workbookFeedback(queue.workbook_feedback)); content.append(ledger);
    }
    if (!queue.cases.length) { renderFinish(); return; }
    const guidance = queue.cases.filter(item => item.id.startsWith("proposal:") && !item.changes.length);
    if (guidance.length) {
      const summary = node("details"); summary.append(node("summary", "What changed and what still needs checking"));
      guidance.forEach(item => summary.append(node("h4", item.title), node("p", item.reason, "finalization-reason")));
      content.append(summary);
    }
    const jump = node("select"); jump.setAttribute("aria-label", "Jump to a review decision");
    queue.cases.forEach((item, i) => {
      const option = node("option", `${i + 1}. ${item.changes.length ? "Correction" : item.record_key ? "Record" : "Review decision"}: ${item.title}`);
      option.value = String(i); jump.append(option);
    });
    jump.value = String(index); jump.onchange = () => { index = Number(jump.value); render(); message("Nothing approved. Showing the selected decision."); };
    content.append(jump);
    const unapproved = queue.cases.filter(item => item.record_key);
    if (unapproved.length > 1) content.append(button(`Check ${unapproved.length} records together`, () => renderBatch(unapproved)));
    const item = queue.cases[index];
    const card = node("article", null, "finalization-card");
    card.append(node("span", `Decision ${index + 1} of ${queue.cases.length}`, "eyebrow"), node("h4", item.title), node("p", item.reason, "finalization-reason"));
    if (item.feedback?.length) {
      card.append(workbookFeedback(item.feedback));
      const linked = new Set(queue.workbook_feedback.filter(entry => item.feedback.includes(entry.id)).flatMap(entry => entry.current_record_keys));
      const records = queue.cases.filter(entry => entry.record_key && linked.has(entry.record_key));
      if (records.length) card.append(button(`Check these ${records.length} linked records together`, () => renderBatch(records)));
    }
    if (item.workbook_feedback?.length) card.append(workbookFeedback(item.workbook_feedback));
    if (item.counts) card.append(details("Reviewer count and current records", item.counts));
    if (item.stale) card.append(node("p", "The saved records changed after this suggestion was prepared. Current values are shown below. Check them or edit in review; the old suggestion cannot be applied.", "error"));
    for (const change of item.current_changes || item.changes || []) {
      card.append(node("h5", `${change.collection.replaceAll("_", " ")} · ${change.record_id}`));
      const table = node("table", null, "finalization-changes");
      const head = node("tr"); ["Field", "Current", "Proposed"].forEach(label => head.append(node("th", label))); table.append(head);
      const rows = differences(Object.hasOwn(change, "current") ? change.current : change.before, change.after);
      for (const row of rows) {
        const tr = node("tr"); row.forEach(value => tr.append(node("td", value))); table.append(tr);
      }
      if (rows.length > 14) {
        const group = node("details"); group.append(node("summary", "Inspect all proposed field changes"), table); card.append(group);
      } else card.append(table);
      card.append(details("Inspect the complete proposed record", change.after), details("Inspect the complete current record", Object.hasOwn(change, "current") ? change.current : change.before));
    }
    if (item.changes?.length) card.append(node("p", "Your decision approves the complete affected records, not only the differences shown above.", "finalization-summary"));
    if (item.record) {
      card.append(recordFields(item.record), details("Full record JSON, including empty fields", item.record));
    }
    for (const citation of item.evidence || []) {
      if (citation.quote.length > 500) {
        const source = node("details"); source.append(node("summary", "Read quoted source evidence"), node("blockquote", citation.quote)); card.append(source);
      } else card.append(node("blockquote", citation.quote));
      card.append(button("Show source in paper", async () => { await showCitation(paper, citation); dialog.close(); }));
    }
    const note = node("textarea"); note.placeholder = "Optional explanation for this decision"; note.setAttribute("aria-label", "Decision note"); card.append(note);
    const save = async (action) => {
      queue = await request(`${base()}/decide`, { method: "POST", body: JSON.stringify({ base_revision: queue.revision, case_id: item.id, action, note: note.value.trim() || (action === "accept" ? "Checked source; accepted the proposed correction." : "Checked the review and source; current records are correct.") }) });
      render(); message("Saved. Next remaining decision."); content.scrollTop = 0;
    };
    const actions = node("div", null, "finalization-actions");
    if (item.changes?.length && !item.stale) actions.append(button("Accept correction & approve", () => save("accept"), true));
    actions.append(button(item.changes?.length ? "Keep current version" : item.record ? "Approve this record" : "Confirm this review decision", () => save("keep"), !item.changes?.length));
    actions.append(button("Edit in review", async () => {
      const existing = (item.current_changes || item.changes || []).find(change => Object.hasOwn(change, "current") ? change.current : change.before);
      const key = item.record_key || (existing ? `${existing.collection}:${existing.record_id}` : null);
      await openRecord(paper, key); dialog.close();
    }));
    actions.append(button("Later →", () => { index = (index + 1) % queue.cases.length; render(); message("Left unresolved. Nothing was approved."); }));
    card.append(actions); content.append(card);
  }
  function renderBatch(items) {
    content.replaceChildren(node("h3", "Confirm records you have checked"), node("p", "Use this for records reviewed together, for example against an earlier workbook. Nothing is selected or approved automatically."));
    const selected = new Set();
    const all = node("input"); all.type = "checkbox";
    const selectAll = node("label", null, "finalization-check"); selectAll.append(all, node("span", "Select all current records below")); content.append(selectAll);
    const inputs = [];
    for (const item of items) {
      const card = node("section", null, "finalization-card");
      const input = node("input"); input.type = "checkbox";
      input.onchange = () => { input.checked ? selected.add(item.record_key) : selected.delete(item.record_key); all.checked = selected.size === items.length; };
      inputs.push({ input, key: item.record_key });
      const label = node("label", null, "finalization-check"); label.append(input, node("strong", item.title));
      card.append(label, node("p", item.record_key));
      if (item.workbook_feedback?.length) card.append(workbookFeedback(item.workbook_feedback));
      const full = node("details"); full.append(node("summary", "Inspect current fields and source"), recordFields(item.record), details("Full record JSON", item.record));
      for (const citation of item.evidence || []) full.append(node("blockquote", citation.quote));
      card.append(full); content.append(card);
    }
    all.onchange = () => { selected.clear(); for (const { input, key } of inputs) { input.checked = all.checked; if (all.checked) selected.add(key); } };
    const explanation = node("textarea"); explanation.placeholder = "Review basis, e.g. checked these current records against the paper and reviewed workbook"; explanation.setAttribute("aria-label", "Batch review basis"); content.append(explanation);
    const actions = node("div", null, "finalization-actions");
    actions.append(button("Approve selected current records", async () => {
      if (!selected.size || !explanation.value.trim()) throw new Error("Select the records you checked and briefly state your review basis.");
      queue = await request(`${base()}/confirm-records`, { method: "POST", body: JSON.stringify({ base_revision: queue.revision, record_keys: [...selected], current_records_checked: true, note: explanation.value.trim() }) });
      render(); content.scrollTop = 0; message("Selected records approved. Other items remain unresolved.");
    }, true), button("Back to decisions", () => { render(); message("No batch approvals saved."); }));
    content.append(actions); content.scrollTop = 0;
  }
  function renderFinish() {
    const card = node("section", null, "finalization-card");
    card.append(node("h4", queue.finalized ? "Final ground truth is ready" : "Ready for your final sign-off"));
    if (queue.finalized) {
      card.append(node("p", "Download the corrected JSON, source version, and complete review history."));
      card.append(button("Download final ground truth", async () => { await download(`/api/ground-truth-export/${split}/${encodeURIComponent(paper)}`, `${paper}.ground-truth.zip`); message("Downloaded final reference."); }, true));
    } else {
      card.append(node("p", `You are approving ${queue.record_count} records, including ${queue.inherited_count} unchanged records already approved by reviewers. Their original decisions remain in the history.`));
      if (queue.source_notes?.length) {
        card.append(node("h5", "Source limitations retained in this reference"));
        const notes = node("ul"); queue.source_notes.forEach(text => notes.append(node("li", text))); card.append(notes);
        card.append(node("p", "If any note describes an unresolved extraction error rather than a limitation of the paper, correct it in review before signing off."));
      }
      const check = node("input"); check.type = "checkbox";
      const label = node("label", null, "finalization-check"); label.append(check, node("span", "I accept these reviewed records and have checked the main paper and available SI for missing records.")); card.append(label);
      card.append(button("Finalize & download", async () => {
        if (!check.checked) throw new Error("Confirm the completeness check before finalizing.");
        queue = await request(`${base()}/finalize`, { method: "POST", body: JSON.stringify({ base_revision: queue.revision, adopt_current_reviews: true, completeness_checked: true }) });
        render(); message("Finalized. Downloading your reference…");
        await download(`/api/ground-truth-export/${split}/${encodeURIComponent(paper)}`, `${paper}.ground-truth.zip`);
        message("Finalized and downloaded. Human edits and review history are preserved.");
      }, true));
    }
    content.append(card);
  }
  return { async open() {
    split = context().split; dialog.showModal(); renderRail();
    const id = context().papers.some(item => item.id === paper) ? paper : context().paperId || context().papers[0]?.id;
    if (id) await perform(() => load(id)); else message("Choose a dataset containing papers first.", true);
  }};
}
