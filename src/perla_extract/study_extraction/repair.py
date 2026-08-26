"""Run one bounded, evidence-local recovery pass for visible extraction gaps.

The main extraction remains the source of truth.  This module turns independent
coverage misses and deterministic validation findings into a small worklist, supplies
only the implicated parser text/table blocks, and accepts a proposed patch only when
it does not reduce grounded content or coverage.  It never reads rendered pages or
uses a vision model.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from .guidance import DEVICE_FAMILY_POLICY, SHARED_QUANTITY_POLICY
from .inventory import EvidenceInventory, audit_inventory_coverage
from .models import (
    CrossWindowIdentityLink,
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
from .validation import validate_study

if TYPE_CHECKING:
    from .client import ModelClient

RepairReason = Literal[
    "inventory_possible_match",
    "inventory_unmatched",
    "validation_issue",
    "absorber_scope",
]

REPAIR_SYSTEM_PROMPT = """Repair only the supplied extraction records using only the
supplied parser text and table blocks. Never infer from an image, general knowledge,
or cited prior literature. Return only complete records that must be added or replaced.
An empty patch is correct when the evidence is insufficient."""

REPAIR_PROMPT = f"""Resolve the supplied worklist conservatively.

{DEVICE_FAMILY_POLICY}
{SHARED_QUANTITY_POLICY}
Rules:
- Preserve device families, individual devices, population statistics, performance
  observations, and stability tests as different reporting levels.
- Return a complete replacement record when correcting an existing ID, or a complete
  new record when recovering an omitted entity. Do not return unchanged records.
- Keep every ReportedValue atomic: one semantic quantity per object.
- Put specimen-specific fabrication values in IndividualDevice.reported_properties.
- Put stage-specific aging conditions in StabilityCheckpoint.conditions.
- Scope each formula, constituent, and absorber property to one absorber or subcell.
- Copy raw values from the supplied evidence spans.
- Cite only supplied span IDs. Do not repair unreadable chemistry by guessing.
- Do not delete records. Explain unresolved gaps in unresolved_notes.
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
    identity_links: list[CrossWindowIdentityLink]
    unresolved_notes: Annotated[list[ShortText], Field(max_length=100)]


class RepairAudit(StrictModel):
    """Explain whether a proposed patch was applied and how quality changed."""

    status: Literal["not_needed", "no_change", "accepted", "rejected", "failed"]
    worklist: RepairWorklist
    selected_block_ids: list[str]
    proposed_record_counts: dict[str, int]
    before_quality: dict[str, int]
    after_quality: dict[str, int]
    reason: str | None


_COLLECTIONS = {
    "device_family": ("device_families", "family_id"),
    "individual_device": ("individual_devices", "device_id"),
    "performance_observation": ("performance_observations", "observation_id"),
    "population_statistic": ("population_statistics", "population_id"),
    "stability_test": ("stability_tests", "test_id"),
    "identity_link": ("identity_links", "link_id"),
}


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


def _record_for_path(study: StudyExtraction, path: str) -> tuple[str, object] | None:
    """Resolve a validation JSON path to its top-level scientific record."""

    match = re.match(r"^\$\.([a-z_]+)\[(\d+)\]", path)
    if not match or not hasattr(study, match.group(1)):
        return None
    collection = getattr(study, match.group(1))
    index = int(match.group(2))
    if not isinstance(collection, list) or index >= len(collection):
        return None
    kind = next(
        (
            candidate
            for candidate, (name, _) in _COLLECTIONS.items()
            if name == match.group(1)
        ),
        match.group(1),
    )
    return kind, collection[index]


def build_repair_worklist(
    study: StudyExtraction,
    coverage: dict[str, object] | None,
    validation: dict[str, object],
) -> RepairWorklist:
    """Turn independent gaps into deduplicated work items with local block IDs."""

    items: list[RepairWorkItem] = []
    for candidate in (coverage or {}).get("items", []):
        if not isinstance(candidate, dict) or candidate.get("status") == "covered":
            continue
        evidence = candidate.get("evidence", [])
        candidate_ids = [
            str(item) for item in candidate.get("candidate_record_ids", [])
        ]
        current_blocks: set[str] = set()
        collection_details = _COLLECTIONS.get(str(candidate.get("kind", "")))
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
                    "inventory_possible_match"
                    if candidate.get("status") == "possible_match"
                    else "inventory_unmatched"
                ),
                record_kind=str(candidate.get("kind", "unknown")),
                record_ids=candidate_ids,
                block_ids=sorted(
                    {
                        str(item["block_id"])
                        for item in evidence
                        if isinstance(item, dict) and item.get("block_id")
                    }
                    | current_blocks
                ),
                detail=f"Inventory candidate {candidate.get('item_id')}: {candidate.get('label')}",
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


def _upsert(current: list[object], proposed: list[object], id_field: str) -> list[object]:
    """Replace matching IDs and append new IDs while preserving untouched records."""

    replacements = {str(getattr(item, id_field)): item for item in proposed}
    merged = [replacements.pop(str(getattr(item, id_field)), item) for item in current]
    merged.extend(replacements.values())
    return merged


def apply_repair(study: StudyExtraction, repair: StudyRepair) -> StudyExtraction:
    """Apply a typed patch at record boundaries so partial objects cannot leak in."""

    changes: dict[str, object] = {}
    for _, (collection, id_field) in _COLLECTIONS.items():
        changes[collection] = _upsert(
            list(getattr(study, collection)), list(getattr(repair, collection)), id_field
        )
    changes["unresolved_notes"] = list(
        dict.fromkeys([*study.unresolved_notes, *repair.unresolved_notes])
    )
    return study.model_copy(update=changes)


def candidate_quality(
    study: StudyExtraction,
    blocks: list[EvidenceBlock],
    inventory: EvidenceInventory | None,
) -> dict[str, int]:
    """Summarize grounded signals for comparing two extraction candidates."""

    validation = validate_study(study, blocks)
    uncovered = 0
    if inventory is not None:
        counts = audit_inventory_coverage(inventory, study)["counts"]
        uncovered = int(counts["possible_match"]) + int(counts["unmatched"])
    return {
        "validation_issues": len(validation["issues"]),
        "reported_values": int(validation["counts"]["reported_values"]),
        "source_verified_values": int(validation["counts"]["source_verified_values"]),
        "uncovered_inventory_items": uncovered,
    }


def is_monotonic_quality(before: dict[str, int], after: dict[str, int]) -> bool:
    """Return whether a candidate avoids trading away any grounded signal."""

    return (
        after["validation_issues"] <= before["validation_issues"]
        and after["reported_values"] >= before["reported_values"]
        and after["source_verified_values"] >= before["source_verified_values"]
        and after["uncovered_inventory_items"] <= before["uncovered_inventory_items"]
    )


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
    return True


def run_targeted_repair(
    *,
    client: ModelClient,
    study: StudyExtraction,
    blocks: list[EvidenceBlock],
    inventory: EvidenceInventory | None,
    coverage: dict[str, object] | None,
    validation: dict[str, object],
    model: str,
    reasoning_effort: str | None,
    max_output_tokens: int,
) -> tuple[StudyExtraction, RepairAudit]:
    """Request and gate one text-only patch when deterministic audits expose work."""

    from .client import ModelCallError
    from .spans import build_evidence_spans, evidence_payload
    from .transport import (
        compact_to_span_citations,
        expand_span_citations,
        span_citation_schema,
    )

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
    before = candidate_quality(study, blocks, inventory)
    empty_counts = {name: 0 for name, _ in _COLLECTIONS.values()}
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
    record_ids = {
        record_id for item in worklist.items for record_id in item.record_ids
    }
    current_records = {
        collection: [
            compact_to_span_citations(record, evidence_spans)
            for record in getattr(study, collection)
            if str(getattr(record, id_field)) in record_ids
        ]
        for collection, id_field in _COLLECTIONS.values()
    }
    try:
        proposed = client.complete(
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
            response_model=StudyRepair,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            request_schema=span_citation_schema(StudyRepair, evidence_spans),
            decode=lambda payload: expand_span_citations(payload, evidence_spans),
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
    counts = {
        collection: len(getattr(proposed, collection))
        for collection, _ in _COLLECTIONS.values()
    }
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
    if not _proposal_is_scoped(proposed, worklist):
        return study, RepairAudit(
            status="rejected",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=counts,
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
            before_quality=before,
            after_quality=before,
            reason="the patch did not change the extraction",
        )
    after = candidate_quality(candidate, blocks, inventory)
    accepted = is_monotonic_quality(before, after)
    return (
        candidate if accepted else study,
        RepairAudit(
            status="accepted" if accepted else "rejected",
            worklist=worklist,
            selected_block_ids=selected_ids,
            proposed_record_counts=counts,
            before_quality=before,
            after_quality=after,
            reason=None if accepted else "proposed patch failed monotonic quality gates",
        ),
    )
