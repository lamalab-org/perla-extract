"""Create a compact, independent inventory for routing and recall review.

The inventory is intentionally shallower than the final extraction. It identifies
which present-study records exist and where, without extracting their values. This
makes it cheap enough to run first, useful for excluding clearly irrelevant blocks,
and independent enough to reveal candidates the detailed extraction may miss.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Annotated, Literal

from pydantic import Field

from .evidence import normalized_source_text, source_contains_text
from .models import (
    EvidenceBlock,
    EvidenceCitation,
    Identifier,
    ShortText,
    StrictModel,
    StudyExtraction,
)

InventoryKind = Literal[
    "device_family",
    "individual_device",
    "performance_observation",
    "population_statistic",
    "stability_test",
]


class InventoryItem(StrictModel):
    """Identify one present-study record without extracting its detailed values."""

    item_id: Identifier
    kind: InventoryKind
    label: ShortText
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=4)]


class EvidenceExclusion(StrictModel):
    """Mark one block as clearly irrelevant to the requested study extraction."""

    evidence: EvidenceCitation
    reason: ShortText


class EvidenceInventory(StrictModel):
    """Carry independent record candidates and conservative routing decisions."""

    items: list[InventoryItem]
    exclusions: list[EvidenceExclusion]


def routed_blocks(
    blocks: list[EvidenceBlock], inventory: EvidenceInventory
) -> tuple[list[EvidenceBlock], dict[str, object]]:
    """Exclude only valid, explicitly listed blocks that support no inventory item.

    Candidate evidence always wins over an exclusion. Unknown IDs are ignored and
    reported, making a weak inventory reduce optimization rather than reduce recall.
    """

    block_by_id = {block.block_id: block for block in blocks}
    known = set(block_by_id)
    protected = {
        citation.block_id
        for item in inventory.items
        for citation in item.evidence
        if citation.block_id in known
    }
    requested: set[str] = set()
    for item in inventory.exclusions:
        block = block_by_id.get(item.evidence.block_id)
        if block is not None and source_contains_text(block.text, item.evidence.quote):
            requested.add(item.evidence.block_id)
    invalid_exclusions = {
        item.evidence.block_id
        for item in inventory.exclusions
        if (block := block_by_id.get(item.evidence.block_id)) is None
        or not source_contains_text(block.text, item.evidence.quote)
    }
    excluded = (requested & known) - protected
    selected = [block for block in blocks if block.block_id not in excluded]
    fallback_reason = None
    if blocks and not selected:
        selected = blocks
        excluded = set()
        fallback_reason = "inventory attempted to exclude every block"
    return selected, {
        "input_block_count": len(blocks),
        "selected_block_count": len(selected),
        "excluded_block_ids": sorted(excluded),
        "protected_block_ids": sorted(protected & requested),
        "invalid_exclusion_block_ids": sorted(invalid_exclusions),
        "fallback_reason": fallback_reason,
        "decisions": [item.model_dump(mode="json") for item in inventory.exclusions],
    }


def _record_evidence(record: object) -> tuple[set[str], set[str]]:
    """Collect block IDs and normalized quotes from one extracted top-level record."""

    blocks: set[str] = set()
    quotes: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            if {"block_id", "quote"} <= value.keys():
                blocks.add(str(value["block_id"]))
                quote = normalized_source_text(value["quote"])
                if quote:
                    quotes.add(quote)
            else:
                for item in value.values():
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    if hasattr(record, "model_dump"):
        walk(record.model_dump(mode="json"))
    return blocks, quotes


def audit_inventory_coverage(
    inventory: EvidenceInventory, extraction: StudyExtraction
) -> dict[str, object]:
    """Compare independent candidates with extraction evidence conservatively.

    Exact quotation overlap counts as covered. Sharing only a source block is a
    possible match because one table or paragraph may describe several devices.
    Everything else remains unmatched for human review; this audit never inserts or
    deletes scientific records.
    """

    records_by_kind: dict[str, list[object]] = defaultdict(list)
    records_by_kind["device_family"].extend(extraction.device_families)
    records_by_kind["individual_device"].extend(extraction.individual_devices)
    records_by_kind["performance_observation"].extend(
        extraction.performance_observations
    )
    records_by_kind["population_statistic"].extend(extraction.population_statistics)
    records_by_kind["stability_test"].extend(extraction.stability_tests)
    identifier_fields = {
        "device_family": "family_id",
        "individual_device": "device_id",
        "performance_observation": "observation_id",
        "population_statistic": "population_id",
        "stability_test": "test_id",
    }
    evidence_by_kind = {
        kind: [
            (
                str(getattr(record, identifier_fields[kind])),
                *_record_evidence(record),
            )
            for record in records
        ]
        for kind, records in records_by_kind.items()
    }
    results: list[dict[str, object]] = []
    counts = {"covered": 0, "possible_match": 0, "unmatched": 0}
    for item in inventory.items:
        item_blocks = {citation.block_id for citation in item.evidence}
        item_quotes = {
            quote
            for citation in item.evidence
            if (quote := normalized_source_text(citation.quote))
        }
        exact = []
        possible = []
        for record_id, blocks, quotes in evidence_by_kind[item.kind]:
            quote_overlap = any(
                len(candidate) >= 12
                and len(extracted) >= 12
                and (candidate in extracted or extracted in candidate)
                for candidate in item_quotes
                for extracted in quotes
            )
            if quote_overlap:
                exact.append(record_id)
            elif item_blocks & blocks:
                possible.append(record_id)
        status = "covered" if exact else "possible_match" if possible else "unmatched"
        counts[status] += 1
        results.append(
            {
                **item.model_dump(mode="json"),
                "status": status,
                "candidate_record_ids": exact or possible,
            }
        )
    fully_covered = counts["possible_match"] == counts["unmatched"] == 0
    return {
        "status": "complete" if fully_covered else "needs_review",
        "counts": counts,
        "items": results,
    }
