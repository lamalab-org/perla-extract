/** State shared by review cards, relationship corrections and finalization controls. */

export function pendingRecordAction(operation, entry) {
  if (!['remove', 'reclassify'].includes(operation)) throw new Error('Unknown record action');
  return { operation, key: entry.key, kind: entry.kind };
}

export function resolvePendingAction(pending, entries, references) {
  if (!pending) return null;
  const entry = entries.find(candidate => candidate.key === pending.key);
  if (!entry) return { ...pending, entry: null, references: [], ready: false };
  const linked = references(entry);
  return { ...pending, entry, references: linked, ready: linked.length === 0 };
}

export function schemaCompatibilityMessage(compatibility) {
  if (!compatibility || compatibility.exact_match !== false) return '';
  if (!compatibility.readable_by_current_schema) {
    return 'This draft cannot be read by the installed study schema. Import a compatible extraction before review.';
  }
  if (compatibility.structurally_compatible) {
    return 'The stored schema identifier differs, but its data contract is compatible with this reviewer. Scientific fields still require source review.';
  }
  return 'This draft is readable by the installed study schema. Its stored schema identifier differs; that alone does not identify which scientific fields changed. Check the source and import provenance when reviewing it.';
}

export function reviewProgress(summary, events, user) {
  const decisions = Object.values(summary.record_decisions?.[user.id] || {});
  const reviewed = decisions.filter(value => ['verified', 'uncertain'].includes(value)).length;
  const verified = decisions.filter(value => value === 'verified').length;
  const uncertain = decisions.filter(value => value === 'uncertain').length;
  const mine = stage => (summary.completed_stages?.[stage] || []).includes(user.id);
  const finalEvent = events.at(-1);
  const finalDecisions = Object.values(summary.record_decisions?.[finalEvent?.reviewer_id] || {});
  const finalized = Boolean(finalEvent?.kind === 'stage_complete'
    && finalEvent?.details?.stage === 'adjudication'
    && finalDecisions.filter(value => value === 'verified').length === summary.record_count);
  return {
    total: summary.record_count, reviewed, verified, uncertain,
    remaining: summary.record_count - reviewed,
    unresolved: summary.record_count - verified,
    paperChecked: mine('inventory'), reviewCompleted: mine('completeness'), finalized,
    canFinalize: Boolean(user.can_finalize && mine('completeness') && verified === summary.record_count),
    canExport: Boolean(user.can_finalize && finalized),
  };
}

/** Render every record-level citation; value-level evidence stays with its value. */
export function recordEvidenceSection(citations, element, onSelect, lookupBlock = () => null) {
  if (!citations?.length) return element('section', { className: 'record-evidence' }, [
    element('strong', { text: 'No record-level evidence supplied' }),
  ]);
  return element('section', { className: 'record-evidence' }, [
    element('strong', { text: `Record evidence · ${citations.length} citation${citations.length === 1 ? '' : 's'}` }),
    ...citations.map((citation, index) => {
      const block = lookupBlock(citation.block_id);
      const location = block
        ? `${block.source === 'main' ? 'Main paper' : 'Supporting information'}, page ${block.page}`
        : citation.block_id;
      return element('div', { className: 'record-citation' }, [
        element('div', { className: 'evidence-heading' }, [
          element('code', { text: location }),
          element('button', { text: `Show citation ${index + 1}`, properties: { type: 'button' },
            events: { click: () => onSelect(citation) } }),
        ]),
        element('blockquote', { text: citation.quote }),
      ]);
    }),
  ]);
}

/** Offer existing scientific owners while retaining their exact stored IDs. */
export function relationshipChoices(field, kind, truth, identifiers, current) {
  const target = ['device_families', 'individual_devices'].find(collection =>
    identifiers[collection] === field && collection !== kind);
  if (!target) return null;
  const choices = (truth[target] || []).map(item => ({
    value: item[identifiers[target]], label: item.label || item[identifiers[target]],
  }));
  if (current && !choices.some(choice => choice.value === current)) {
    choices.push({ value: current, label: `Unavailable record (${current})` });
  }
  return { target, choices };
}
