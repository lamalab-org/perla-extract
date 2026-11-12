import { pendingRecordAction, resolvePendingAction } from "./review-state.js";

const pointerPart = value => String(value).replaceAll("~", "~0").replaceAll("/", "~1");

function attachMissingEvidence(value, citation) {
  if (Array.isArray(value)) return value.forEach(item => attachMissingEvidence(item, citation));
  if (!value || typeof value !== "object") return;
  if (Array.isArray(value.evidence) && value.evidence.length === 0) value.evidence = [structuredClone(citation)];
  Object.values(value).forEach(item => attachMissingEvidence(item, citation));
}

/** Selected paper, saved revision and deferred structural action have one owner.
 * Failed saves leave the current bundle untouched. An editor's base revision is
 * retained so intervening saves cannot silently bless a stale record draft.
 */
export function createReviewSession(api, initialSplit = "dev") {
  let split = initialSplit;
  let paperId = null;
  let bundle = null;
  let pendingAction = null;
  let selection = 0;

  function replaceBundle(next) {
    if (!next || !Number.isInteger(next.revision)) throw new Error("The server returned an invalid review revision.");
    bundle = next;
    return bundle;
  }

  async function saveRecord({ edit, value, citation, note = "" }) {
    if (!bundle || !paperId) throw new Error("Choose a paper before saving a record.");
    const currentSelection = selection;
    const base_revision = edit.baseRevision ?? bundle.revision;
    const preparedValue = structuredClone(value);
    attachMissingEvidence(preparedValue, citation);
    const shared = { value: preparedValue, evidence: [citation], note, base_revision };
    const reclassifying = edit.intent === "reclassify";
    const next = reclassifying
      ? await api.reclassify(split, paperId, { ...shared,
        source_collection: edit.source.kind, source_record_id: edit.source.recordId,
        target_collection: edit.kind })
      : await api.mutate(split, paperId, { ...shared,
        action: edit.index == null ? "add" : "replace",
        path: `/${pointerPart(edit.kind)}/${edit.index == null ? "-" : edit.index}` });
    if (currentSelection !== selection) return next;
    replaceBundle(next);
    if (reclassifying && pendingAction?.key === `${edit.source.kind}:${edit.source.recordId}`) pendingAction = null;
    return next;
  }

  return {
    get split() { return split; },
    get paperId() { return paperId; },
    get bundle() { return bundle; },
    get pendingAction() { return pendingAction; },
    reset(nextSplit = split) {
      split = nextSplit; paperId = null; bundle = null; pendingAction = null; selection += 1;
    },
    select(id, next) {
      replaceBundle(next);
      paperId = id; pendingAction = null; selection += 1;
    },
    replaceBundle,
    beginPending(operation, entry) { pendingAction = pendingRecordAction(operation, entry); },
    resolvePending(entries, references) { return resolvePendingAction(pendingAction, entries, references); },
    cancelPending() { pendingAction = null; },
    async removeRecord(edit, note) {
      const currentSelection = selection;
      const next = await api.mutate(split, paperId, {
        action: "remove", path: `/${pointerPart(edit.kind)}/${edit.index}`,
        note, evidence: [], base_revision: edit.baseRevision ?? bundle.revision,
      });
      if (selection === currentSelection) {
        replaceBundle(next);
        const identifier = next.summary.record_identifiers[edit.kind];
        if (pendingAction?.key === `${edit.kind}:${edit.value[identifier]}`) pendingAction = null;
      }
      return next;
    },
    saveRecord,
  };
}
