"""Run one bounded, evidence-local recovery pass for visible extraction gaps.

The main extraction remains the source of truth. This module turns claim-coverage
misses and deterministic validation findings into a small worklist, supplies
only the implicated parser text/table blocks, and accepts a proposed patch only when
it does not worsen validation or semantic claim coverage. It never reads rendered
pages or uses a vision model.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from .claims import ClaimLedger
from .correction_checks import (
    RECORD_COLLECTIONS as _COLLECTIONS,
)
from .correction_checks import (
    candidate_quality as candidate_quality,
)
from .correction_checks import (
    is_monotonic_quality as is_monotonic_quality,
)
from .correction_checks import (
    record_for_path as _record_for_path,
)
from .guidance import (
    COMPOSITION_BOUNDARY_POLICY,
    DEVICE_FAMILY_POLICY,
    EVIDENCE_INTERPRETATION_POLICY,
    RECORD_BOUNDARY_POLICY,
    SHARED_QUANTITY_POLICY,
)
from .models import (
    DeviceFamily,
    EvidenceBlock,
    IndividualDevice,
    PerformanceObservation,
    PopulationStatistic,
    ShortText,
    StabilityTest,
    StrictModel,
    StudyExtraction,
)
from .spans import EvidenceSpan, build_evidence_spans, evidence_payload
from .transport import (
    compact_to_span_citations,
    expand_span_citations,
    span_citation_schema,
)

if TYPE_CHECKING:
    from .client import ModelClient

RepairReason = Literal[
    "claim_possible_match",
    "claim_unmatched",
    "unclaimed_record",
    "shared_quantity_scope",
    "validation_issue",
    "absorber_scope",
]

REPAIR_SYSTEM_PROMPT = """Repair only the supplied extraction records using only the
supplied parser text and table blocks. Never infer from an image, general knowledge,
or cited prior literature. Return only complete records that must be added or replaced.
An empty patch is correct when the evidence is insufficient."""

REPAIR_PROMPT = f"""Resolve the supplied worklist conservatively.

{DEVICE_FAMILY_POLICY}
{RECORD_BOUNDARY_POLICY}
{COMPOSITION_BOUNDARY_POLICY}
{EVIDENCE_INTERPRETATION_POLICY}
{SHARED_QUANTITY_POLICY}
Rules:
- Preserve device families, individual devices, population statistics, performance
  observations, and stability tests as different reporting levels.
- A processing arm is not a PopulationStatistic unless the source actually reports a
  population statistic, distribution, or aggregate result for multiple devices. Do
  not create a population merely to retain a group-level process distinction.
- Return a complete replacement record when correcting an existing ID, or a complete
  new record when recovering an omitted entity. Do not return unchanged records.
- Keep every ReportedValue atomic: one semantic quantity per object.
- Put specimen-specific fabrication values in IndividualDevice.reported_properties.
- Put stage-specific aging conditions in StabilityCheckpoint.conditions.
- Scope each formula, constituent, and absorber property to one absorber or subcell.
- Copy raw values from the supplied evidence spans.
- Cite only supplied span IDs. Do not repair unreadable chemistry by guessing.
- Remove a complete top-level record only when the worklist identifies it as
  unclaimed and the supplied evidence establishes that it is a processing arm,
  characterization specimen, duplicate, or otherwise outside the target schema.
- An unclaimed record has no matching reader note; this does not prove it is wrong.
  Missing or uncertain notes are not evidence for deletion. Correct unsupported
  optional fields in place and preserve the record's source-supported values.
- Never return the same record ID in both removals and a replacement collection. If
  its identity is uncertain, leave it unchanged and explain the uncertainty.
- Explain a gap in unresolved_notes when the evidence cannot support a safe repair.
"""


class RepairWorkItem(StrictModel):
    """Describe one concrete omission or inconsistency and its local evidence."""

    reason: RepairReason
    record_kind: str
    record_ids: list[str]
    block_ids: list[str]
    detail: str


class RepairWorklist(StrictModel):
    """Collect the small set of issues that justify an additional model call."""

    items: list[RepairWorkItem]


class StudyRepair(StrictModel):
    """Carry typed top-level records that can be upserted without fieldwise merging."""

    device_families: list[DeviceFamily]
    individual_devices: list[IndividualDevice]
    performance_observations: list[PerformanceObservation]
    population_statistics: list[PopulationStatistic]
    stability_tests: list[StabilityTest]
    removals: list["RecordRemoval"]
    unresolved_notes: Annotated[list[ShortText], Field(max_length=100)]


class RecordRemoval(StrictModel):
    """Request deletion of one complete record exposed as semantically unclaimed."""

    record_kind: str
    record_id: str


class RepairAudit(StrictModel):
    """Explain whether a proposed patch was applied and how quality changed."""

    status: Literal["not_needed", "no_change", "accepted", "rejected", "failed"]
    worklist: RepairWorklist
    selected_block_ids: list[str]
    proposed_record_counts: dict[str, int]
    discarded_record_counts: dict[str, int] = Field(default_factory=dict)
    applied_record_counts: dict[str, int] = Field(default_factory=dict)
    before_quality: dict[str, int]
    after_quality: dict[str, int]
    reason: str | None


def repair_request_schema(worklist: RepairWorklist, spans: list[EvidenceSpan]) -> dict:
    """Expose only requested record types, IDs, and removals to the repair model.

    Full records remain the unit of replacement so dependent fields are validated
    together. The public ``StudyRepair`` stays stable; absent collections are filled
    with empty lists after decoding. Unreachable definitions are omitted from the
    provider schema to avoid describing unrelated scientific tasks.
    """

    schema: dict[str, Any] = span_citation_schema(StudyRepair, spans)
    allowed = {"unresolved_notes"}
    for kind, (collection, id_field) in _COLLECTIONS.items():
        items = [
            item
            for item in worklist.items
            if item.record_kind == kind and item.reason != "unclaimed_record"
        ]
        if not items:
            continue
        allowed.add(collection)
        if all(item.record_ids for item in items):
            reference = schema["properties"][collection]["items"]["$ref"].rsplit(
                "/", 1
            )[-1]
            schema["$defs"][reference]["properties"][id_field]["enum"] = sorted(
                {record_id for item in items for record_id in item.record_ids}
            )
    if any(
        item.reason == "unclaimed_record" and item.record_ids for item in worklist.items
    ):
        allowed.add("removals")
    schema["properties"] = {
        key: value for key, value in schema["properties"].items() if key in allowed
    }
    schema["required"] = [key for key in schema.get("required", []) if key in allowed]
    definitions = schema.get("$defs", {})
    used: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                name = reference.rsplit("/", 1)[-1]
                if name not in used:
                    used.add(name)
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema["properties"])
    schema["$defs"] = {
        name: value for name, value in definitions.items() if name in used
    }
    return schema


def _decode_repair(payload: object, spans: list[EvidenceSpan]) -> dict[str, object]:
    """Restore citations and the empty collections omitted by the request schema."""

    expanded = expand_span_citations(payload, spans)
    for collection, _ in _COLLECTIONS.values():
        expanded.setdefault(collection, [])
    expanded.setdefault("removals", [])
    return expanded


def _citation_block_ids(value: object) -> set[str]:
    """Collect source block IDs from a typed record without knowing its fields."""

    found: set[str] = set()

    def walk(item: object) -> None:
        if isinstance(item, dict):
            if {"block_id", "quote"} <= item.keys():
                found.add(str(item["block_id"]))
            else:
                for child in item.values():
                    walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)

    walk(value.model_dump(mode="json") if hasattr(value, "model_dump") else value)
    return found


def build_repair_worklist(
    study: StudyExtraction,
    coverage: dict[str, Any] | None,
    validation: dict[str, Any],
) -> RepairWorklist:
    """Turn independent gaps into deduplicated work items with local block IDs."""

    items: list[RepairWorkItem] = []
    for candidate in (coverage or {}).get("items", []):
        if not isinstance(candidate, dict) or candidate.get("status") in {
            "covered",
            "context",
            "uncertain",
        }:
            continue
        evidence = candidate.get("evidence", [])
        candidate_ids = [
            str(item) for item in candidate.get("candidate_record_ids", [])
        ]
        current_blocks: set[str] = set()
        record_kind = str(
            candidate.get("record_kind") or candidate.get("kind") or "unknown"
        )
        collection_details = _COLLECTIONS.get(record_kind)
        if collection_details:
            collection, id_field = collection_details
            current_blocks.update(
                block_id
                for record in getattr(study, collection)
                if str(getattr(record, id_field)) in candidate_ids
                for block_id in _citation_block_ids(record)
            )
        items.append(
            RepairWorkItem(
                reason=(
                    "unclaimed_record"
                    if candidate.get("status") == "unclaimed"
                    else (
                        "shared_quantity_scope"
                        if candidate.get("missing_shared_targets")
                        else (
                            "claim_possible_match"
                            if candidate.get("status") == "possible_match"
                            else "claim_unmatched"
                        )
                    )
                ),
                record_kind=record_kind,
                record_ids=candidate_ids,
                block_ids=sorted(
                    {
                        str(item["block_id"])
                        for item in evidence
                        if isinstance(item, dict) and item.get("block_id")
                    }
                    | current_blocks
                ),
                detail=(
                    f"Source claim {candidate.get('claim_id') or candidate.get('object_id') or candidate.get('source_id')}: "
                    f"{candidate.get('label')}"
                    + (
                        f"; missing atomic targets={candidate.get('missing_shared_targets')}"
                        if candidate.get("missing_shared_targets")
                        else ""
                    )
                ),
            )
        )
    for finding in validation.get("issues", []):
        if not isinstance(finding, dict):
            continue
        path = str(finding.get("path", ""))
        resolved = _record_for_path(study, path)
        if resolved is None:
            continue
        kind, record = resolved
        identifier = _COLLECTIONS.get(kind)
        record_id = str(getattr(record, identifier[1])) if identifier else ""
        items.append(
            RepairWorkItem(
                reason=(
                    "absorber_scope"
                    if "absorber" in str(finding.get("reason", ""))
                    else "validation_issue"
                ),
                record_kind=kind,
                record_ids=[record_id] if record_id else [],
                block_ids=sorted(_citation_block_ids(record)),
                detail=f"{path}: {finding.get('reason')}",
            )
        )
    unique: dict[tuple[object, ...], RepairWorkItem] = {}
    for item in items:
        key = (item.reason, item.record_kind, tuple(item.record_ids), item.detail)
        unique[key] = item
    return RepairWorklist(items=list(unique.values()))


def _upsert(
    current: list[object], proposed: list[object], id_field: str
) -> list[object]:
    """Replace matching IDs and append new IDs while preserving untouched records."""

    replacements = {str(getattr(item, id_field)): item for item in proposed}
    merged = [replacements.pop(str(getattr(item, id_field)), item) for item in current]
    merged.extend(replacements.values())
    return merged


def apply_repair(study: StudyExtraction, repair: StudyRepair) -> StudyExtraction:
    """Apply a typed patch at record boundaries so partial objects cannot leak in."""

    changes: dict[str, object] = {}
    removals = {(item.record_kind, item.record_id) for item in repair.removals}
    for kind, (collection, id_field) in _COLLECTIONS.items():
        retained = [
            item
            for item in getattr(study, collection)
            if (kind, str(getattr(item, id_field))) not in removals
        ]
        changes[collection] = _upsert(
            retained, list(getattr(repair, collection)), id_field
        )
    changes["unresolved_notes"] = list(
        dict.fromkeys([*study.unresolved_notes, *repair.unresolved_notes])
    )
    return study.model_copy(update=changes)


def _proposal_is_scoped(proposed: StudyRepair, worklist: RepairWorklist) -> bool:
    """Reject records outside requested kinds and duplicate IDs within a patch."""

    allowed = {item.record_kind for item in worklist.items}
    for kind, (collection, id_field) in _COLLECTIONS.items():
        records = getattr(proposed, collection)
        identifiers = [str(getattr(record, id_field)) for record in records]
        if records and kind not in allowed:
            return False
        if len(identifiers) != len(set(identifiers)):
            return False
    removable = {
        (item.record_kind, record_id)
        for item in worklist.items
        if item.reason == "unclaimed_record"
        for record_id in item.record_ids
    }
    if any(
        (item.record_kind, item.record_id) not in removable
        for item in proposed.removals
    ):
        return False
    proposed_keys = {
        (kind, str(getattr(record, id_field)))
        for kind, (collection, id_field) in _COLLECTIONS.items()
        for record in getattr(proposed, collection)
    }
    if proposed_keys & {
        (item.record_kind, item.record_id) for item in proposed.removals
    }:
        return False
    return True


def _scope_repair_to_worklist(
    proposed: StudyRepair, worklist: RepairWorklist
) -> tuple[StudyRepair, dict[str, int]]:
    """Keep only record changes that the deterministic audit explicitly requested.

    A model may emit additional records beyond its worklist even when the request
    schema forbids them. Retain only permitted IDs, explicitly requested new record
    kinds, and named removals. Rejected changes are counted in the repair audit.
    """

    permitted_removals = {
        (item.record_kind, record_id)
        for item in worklist.items
        if item.reason == "unclaimed_record"
        for record_id in item.record_ids
    }
    permitted_ids: dict[str, set[str]] = {kind: set() for kind in _COLLECTIONS}
    permits_new: set[str] = set()
    for item in worklist.items:
        if item.reason == "unclaimed_record" or item.record_kind not in _COLLECTIONS:
            continue
        if item.record_ids:
            permitted_ids[item.record_kind].update(item.record_ids)
        else:
            permits_new.add(item.record_kind)

    changes: dict[str, object] = {}
    discarded: dict[str, int] = {}
    for kind, (collection, id_field) in _COLLECTIONS.items():
        records = list(getattr(proposed, collection))
        retained = [
            record
            for record in records
            if kind in permits_new
            or str(getattr(record, id_field)) in permitted_ids[kind]
        ]
        changes[collection] = retained
        discarded[collection] = len(records) - len(retained)

    removals = [
        removal
        for removal in proposed.removals
        if (removal.record_kind, removal.record_id) in permitted_removals
    ]
    changes["removals"] = removals
    discarded["removals"] = len(proposed.removals) - len(removals)
    if any(discarded.values()):
        # Notes can describe edits that were just discarded. The repair audit
        # records those proposals; do not attach their claimed outcomes to data.
        changes["unresolved_notes"] = []
    return proposed.model_copy(update=changes), discarded


def _repair_counts(repair: StudyRepair) -> dict[str, int]:
    """Count record-boundary changes in one repair proposal or accepted subset."""

    counts = {
        collection: len(getattr(repair, collection))
        for collection, _ in _COLLECTIONS.values()
    }
    counts["removals"] = len(repair.removals)
    return counts


def _empty_repair() -> StudyRepair:
    """Create an empty typed patch for testing one proposed record in isolation."""

    return StudyRepair(
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        removals=[],
        unresolved_notes=[],
    )


def _monotonic_repair_subset(
    study: StudyExtraction,
    proposed: StudyRepair,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
    before: dict[str, int],
) -> tuple[StudyExtraction, dict[str, int], dict[str, int]]:
    """Salvage independent corrections when a larger repair patch regresses quality.

    Validation fixes and safe removals should not be lost because the same model
    response also contains an unrelated bad edit. Each complete top-level record is
    tried separately and retained only when it strictly reduces validation or
    semantic issues without worsening the other signal. This fallback deliberately
    does not accept mutually dependent additions; those require a coherent patch that
    passes the normal whole-proposal gate.
    """

    candidate = study
    quality = before
    accepted = _empty_repair()
    for collection, _ in _COLLECTIONS.values():
        for record in getattr(proposed, collection):
            patch = _empty_repair().model_copy(update={collection: [record]})
            trial = apply_repair(candidate, patch)
            trial_quality = candidate_quality(trial, blocks, ledger, baseline=candidate)
            if is_monotonic_quality(quality, trial_quality) and (
                trial_quality["validation_issues"] < quality["validation_issues"]
                or trial_quality["semantic_issues"] < quality["semantic_issues"]
            ):
                candidate = trial
                quality = trial_quality
                getattr(accepted, collection).append(record)
    for removal in proposed.removals:
        patch = _empty_repair().model_copy(update={"removals": [removal]})
        trial = apply_repair(candidate, patch)
        trial_quality = candidate_quality(trial, blocks, ledger, baseline=candidate)
        if is_monotonic_quality(quality, trial_quality) and (
            trial_quality["validation_issues"] < quality["validation_issues"]
            or trial_quality["semantic_issues"] < quality["semantic_issues"]
        ):
            candidate = trial
            quality = trial_quality
            accepted.removals.append(removal)
    # A partial patch cannot safely inherit notes describing the whole proposal.
    return candidate, quality, _repair_counts(accepted)


def run_targeted_repair(
    *,
    client: ModelClient,
    study: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
    coverage: dict[str, Any] | None,
    validation: dict[str, Any],
    model: str,
    reasoning_effort: str | None,
    max_output_tokens: int,
) -> tuple[StudyExtraction, RepairAudit]:
    """Request and gate one text-only patch when deterministic audits expose work."""

    from .client import ModelCallError

    worklist = build_repair_worklist(study, coverage, validation)
    known = {block.block_id: block for block in blocks}
    selected_ids = sorted(
        {
            block_id
            for item in worklist.items
            for block_id in item.block_ids
            if block_id in known
        }
    )
    before = candidate_quality(study, blocks, ledger)
    empty_counts = {name: 0 for name, _ in _COLLECTIONS.values()}
    empty_counts["removals"] = 0
    if not worklist.items or not selected_ids:
        return study, RepairAudit(
            status="not_needed",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=empty_counts,
            before_quality=before,
            after_quality=before,
            reason=(
                "no repair work was identified"
                if not worklist.items
                else "repair items had no resolvable parser evidence"
            ),
        )
    selected_blocks = [known[block_id] for block_id in selected_ids]
    evidence = evidence_payload(selected_blocks)
    evidence_spans = build_evidence_spans(selected_blocks)
    requested_ids = {
        kind: {
            record_id
            for item in worklist.items
            if item.record_kind == kind
            for record_id in item.record_ids
        }
        for kind in _COLLECTIONS
    }
    current_records = {
        collection: [
            compact_to_span_citations(record, evidence_spans)
            for record in getattr(study, collection)
            if str(getattr(record, id_field)) in requested_ids[kind]
        ]
        for kind, (collection, id_field) in _COLLECTIONS.items()
    }
    try:
        proposed = client.complete(
            stage="repair",
            kind="targeted_study_repair",
            slug="targeted_study_repair",
            model=model,
            system=REPAIR_SYSTEM_PROMPT,
            prompt=REPAIR_PROMPT
            + "\n\nWORKLIST:\n"
            + json.dumps(worklist.model_dump(mode="json"), ensure_ascii=False)
            + "\n\nCURRENT RECORDS:\n"
            + json.dumps(current_records, ensure_ascii=False)
            + "\n\nPARSER TEXT AND TABLE EVIDENCE:\n"
            + json.dumps(evidence, ensure_ascii=False),
            prompt_components={
                "repair_instructions": REPAIR_PROMPT,
                "worklist": worklist.model_dump(mode="json"),
                "current_records": current_records,
                "source_evidence": evidence,
            },
            response_model=StudyRepair,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            request_schema=repair_request_schema(worklist, evidence_spans),
            decode=lambda payload: _decode_repair(payload, evidence_spans),
        )
    except ModelCallError as exc:
        return study, RepairAudit(
            status="failed",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=empty_counts,
            before_quality=before,
            after_quality=before,
            reason=str(exc),
        )
    counts = _repair_counts(proposed)
    if not any(counts.values()) and not proposed.unresolved_notes:
        return study, RepairAudit(
            status="no_change",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=counts,
            before_quality=before,
            after_quality=before,
            reason="the model returned an empty patch",
        )
    proposed, discarded_counts = _scope_repair_to_worklist(proposed, worklist)
    if not _proposal_is_scoped(proposed, worklist):
        return study, RepairAudit(
            status="rejected",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=counts,
            discarded_record_counts=discarded_counts,
            before_quality=before,
            after_quality=before,
            reason="the patch contains an unrequested record kind or duplicate ID",
        )
    candidate = apply_repair(study, proposed)
    if candidate == study:
        return study, RepairAudit(
            status="no_change",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=counts,
            discarded_record_counts=discarded_counts,
            before_quality=before,
            after_quality=before,
            reason="the patch did not change the extraction",
        )
    after = candidate_quality(candidate, blocks, ledger, baseline=study)
    accepted = is_monotonic_quality(before, after)
    applied_counts = _repair_counts(proposed) if accepted else empty_counts
    rejected_quality = after
    if not accepted:
        candidate, after, applied_counts = _monotonic_repair_subset(
            study, proposed, blocks, ledger, before
        )
        accepted = candidate != study
    return (
        candidate if accepted else study,
        RepairAudit(
            status="accepted" if accepted else "rejected",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=counts,
            discarded_record_counts=discarded_counts,
            applied_record_counts=applied_counts,
            before_quality=before,
            after_quality=after if accepted else rejected_quality,
            reason=(
                "applied only independently quality-improving record corrections"
                if accepted and applied_counts != _repair_counts(proposed)
                else "unrequested model additions were discarded before applying the patch"
                if accepted and any(discarded_counts.values())
                else None
            )
            if accepted
            else (
                "proposed patch would discard source-supported values"
                if rejected_quality["lost_verified_values"]
                else "proposed patch worsened validation or reader-note coverage"
            ),
        ),
    )
