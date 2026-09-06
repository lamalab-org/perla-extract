"""Inventory source claims before deciding how they map to study records.

The claim ledger is deliberately not a smaller ``StudyExtraction``. It records what
the paper says, which experimental object it concerns, and whether that object belongs
to the photovoltaic study. A later document-level model call performs entity
resolution and schema assembly. This separation prevents every treatment label or
characterization specimen from becoming a device family merely because it was
mentioned in a locally valid passage.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from .evidence import normalized_source_text, source_contains_text
from .models import (
    EvidenceBlock,
    EvidenceCitation,
    Identifier,
    ShortText,
    StrictModel,
    StudyExtraction,
)
from .spans import EvidenceSpan, build_evidence_spans

ObjectRole = Literal[
    "device_design",
    "individual_device",
    "processing_arm",
    "characterization_specimen",
    "population",
    "performance_measurement",
    "stability_experiment",
    "other",
]
ClaimKind = Literal[
    "identity",
    "composition",
    "processing",
    "performance",
    "population",
    "stability",
    "measurement_condition",
    "reported_quantity",
    "other",
]
StudyScope = Literal["target", "context", "uncertain"]


class ExperimentalObject(StrictModel):
    """Describe a source-mentioned object without promoting it to a schema record.

    ``role`` captures what the authors used the object for. ``scope`` records whether
    the evidence establishes that its facts belong in the photovoltaic extraction.
    Both are revisited during global assembly; neither is trusted merely because a
    model emitted it.
    """

    object_id: Identifier
    label: ShortText
    role: ObjectRole
    scope: StudyScope
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=4)]


class SourceClaim(StrictModel):
    """Preserve one evidence-backed assertion independently of the output schema.

    A coordinated quantity uses ``shared_targets`` to retain its grammatical scope.
    The final extraction must then represent each target atomically or explicitly
    leave the claim unresolved; equal values are never collapsed as duplicates.
    """

    claim_id: Identifier
    kind: ClaimKind
    label: ShortText
    subject_object_ids: Annotated[list[Identifier], Field(max_length=20)]
    scope: StudyScope
    raw_value: Annotated[
        str | None,
        Field(
            max_length=500,
            description=(
                "Exact source text of one atomic value or outcome, without its "
                "surrounding sentence or a second quantity"
            ),
        ),
    ] = None
    shared_targets: Annotated[list[ShortText], Field(max_length=20)]
    evidence: Annotated[list[EvidenceCitation], Field(min_length=1, max_length=4)]


class ClaimLedger(StrictModel):
    """Carry neutral experimental objects and atomic source claims."""

    objects: list[ExperimentalObject]
    claims: list[SourceClaim]

    @model_validator(mode="after")
    def validate_identifiers(self) -> ClaimLedger:
        """Keep references unambiguous before the ledger guides another model call."""

        object_ids = [item.object_id for item in self.objects]
        known_object_ids = set(object_ids)
        claim_ids = [item.claim_id for item in self.claims]
        if len(object_ids) != len(known_object_ids):
            raise ValueError("experimental object IDs must be unique")
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("source claim IDs must be unique")
        overlap = known_object_ids & set(claim_ids)
        if overlap:
            raise ValueError(
                f"object and claim IDs must be disjoint: {sorted(overlap)}"
            )
        unknown = sorted(
            {
                object_id
                for claim in self.claims
                for object_id in claim.subject_object_ids
                if object_id not in known_object_ids
            }
        )
        if unknown:
            raise ValueError(
                f"claims reference unknown experimental objects: {unknown}"
            )
        return self


def _namespace(identifier: str, namespace: str) -> str:
    """Make a window-local ledger identifier unique without interpreting it."""

    return f"{namespace}:{identifier}"


def namespace_ledger(ledger: ClaimLedger, namespace: str) -> ClaimLedger:
    """Prefix window-local IDs while preserving references between claims and objects."""

    object_ids = {
        item.object_id: _namespace(item.object_id, namespace) for item in ledger.objects
    }
    return ledger.model_copy(
        update={
            "objects": [
                item.model_copy(update={"object_id": object_ids[item.object_id]})
                for item in ledger.objects
            ],
            "claims": [
                item.model_copy(
                    update={
                        "claim_id": _namespace(item.claim_id, namespace),
                        "subject_object_ids": [
                            object_ids[object_id]
                            for object_id in item.subject_object_ids
                        ],
                    }
                )
                for item in ledger.claims
            ],
        }
    )


def combine_ledgers(parts: Sequence[tuple[str, ClaimLedger]]) -> ClaimLedger:
    """Join lossless window ledgers for one global entity-resolution call."""

    namespaced = [namespace_ledger(ledger, window_id) for window_id, ledger in parts]
    return ClaimLedger(
        objects=[item for ledger in namespaced for item in ledger.objects],
        claims=[item for ledger in namespaced for item in ledger.claims],
    )


def grounded_ledger(
    blocks: list[EvidenceBlock], ledger: ClaimLedger
) -> tuple[ClaimLedger, dict[str, object]]:
    """Remove ledger entries whose claimed quotations do not occur in the document."""

    block_by_id = {block.block_id: block for block in blocks}

    def grounded_citations(
        citations: Iterable[EvidenceCitation],
    ) -> list[EvidenceCitation]:
        return [
            citation
            for citation in citations
            if (block := block_by_id.get(citation.block_id)) is not None
            and source_contains_text(block.text, citation.quote)
        ]

    objects: list[ExperimentalObject] = []
    rejected: list[dict[str, str]] = []
    for item in ledger.objects:
        citations = grounded_citations(item.evidence)
        if citations:
            objects.append(item.model_copy(update={"evidence": citations}))
        else:
            rejected.append(
                {"source_id": item.object_id, "reason": "unresolved evidence"}
            )
    known_objects = {item.object_id for item in objects}
    claims: list[SourceClaim] = []
    for item in ledger.claims:
        citations = grounded_citations(item.evidence)
        subjects = [
            value for value in item.subject_object_ids if value in known_objects
        ]
        if citations and len(subjects) == len(item.subject_object_ids):
            claims.append(
                item.model_copy(
                    update={"evidence": citations, "subject_object_ids": subjects}
                )
            )
        else:
            rejected.append(
                {"source_id": item.claim_id, "reason": "unresolved evidence or subject"}
            )
    result = ClaimLedger(objects=objects, claims=claims)
    return result, {
        "input_object_count": len(ledger.objects),
        "grounded_object_count": len(objects),
        "input_claim_count": len(ledger.claims),
        "grounded_claim_count": len(claims),
        "rejected_count": len(rejected),
        "rejected": rejected,
    }


def assembly_blocks(
    blocks: Sequence[EvidenceBlock], ledger: ClaimLedger
) -> list[EvidenceBlock]:
    """Return claim-cited source blocks plus the main-paper opening page.

    Long supplements are read completely during claim collection. Schema assembly
    then receives the compact ledger and only its cited source passages, preventing a
    second full long-document call while keeping every final value tied to original
    parser evidence. The opening page is inexpensive context for title and DOI.
    """

    cited = {
        citation.block_id
        for item in [*ledger.objects, *ledger.claims]
        for citation in item.evidence
    }
    main_pages = [block.page for block in blocks if block.source == "main"]
    first_main_page = min(main_pages) if main_pages else None
    selected = [
        block
        for block in blocks
        if block.block_id in cited
        or (block.source == "main" and block.page == first_main_page)
    ]
    return selected or list(blocks)


def assembly_spans(
    blocks: Sequence[EvidenceBlock], ledger: ClaimLedger
) -> list[EvidenceSpan]:
    """Expose cited passages, their immediate context, and paper-header context.

    Claim collection has already read every source block. Sending only the exact
    passages that support retained claims and one adjacent span on each side keeps the
    global assembly feasible while preserving local grammar needed to resolve scope.
    The fixed neighborhood is structural rather than property- or chemistry-specific.
    """

    block_list = list(blocks)
    cited = {
        (citation.block_id, citation.quote)
        for item in [*ledger.objects, *ledger.claims]
        for citation in item.evidence
    }
    main_pages = [block.page for block in block_list if block.source == "main"]
    first_main_page = min(main_pages) if main_pages else None
    spans = build_evidence_spans(block_list)
    span_positions = {span.span_id: index for index, span in enumerate(spans)}
    cited_positions = {
        span_positions[span.span_id]
        for span in spans
        if (span.block_id, span.text) in cited
    }
    neighborhood_positions = {
        neighbor
        for position in cited_positions
        for neighbor in (position - 1, position, position + 1)
        if 0 <= neighbor < len(spans)
        and spans[neighbor].block_id == spans[position].block_id
    }
    header_block_ids = {
        block.block_id
        for block in block_list
        if block.source == "main" and block.page == first_main_page
    }
    selected = [
        span
        for index, span in enumerate(spans)
        if index in neighborhood_positions or span.block_id in header_block_ids
    ]
    return selected or spans


def assembly_evidence_payload(
    blocks: Sequence[EvidenceBlock], ledger: ClaimLedger
) -> list[dict[str, object]]:
    """Serialize the compact passage catalog used by global schema assembly."""

    block_by_id = {block.block_id: block for block in blocks}
    grouped: dict[str, dict[str, str]] = defaultdict(dict)
    for span in assembly_spans(blocks, ledger):
        grouped[span.block_id][span.span_id] = span.text
    return [
        {
            "block_id": block.block_id,
            "source": block.source,
            "page": block.page,
            "section": block.section_path[-1] if block.section_path else None,
            "kind": block.kind,
            "spans": grouped[block_id],
        }
        for block_id, block in block_by_id.items()
        if block_id in grouped
    ]


TOP_LEVEL_RECORD_BY_OBJECT_ROLE = {
    "device_design": ("device_family", "device_families", "family_id"),
    "individual_device": ("individual_device", "individual_devices", "device_id"),
    "population": ("population_statistic", "population_statistics", "population_id"),
    "performance_measurement": (
        "performance_observation",
        "performance_observations",
        "observation_id",
    ),
    "stability_experiment": ("stability_test", "stability_tests", "test_id"),
}


def _record_evidence(record: object) -> tuple[set[str], set[str]]:
    """Collect citation locations recursively from one final top-level record."""

    block_ids: set[str] = set()
    quotes: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            if {"block_id", "quote"} <= value.keys():
                block_ids.add(str(value["block_id"]))
                if quote := normalized_source_text(value["quote"]):
                    quotes.add(quote)
            else:
                for child in value.values():
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(record.model_dump(mode="json") if hasattr(record, "model_dump") else record)
    return block_ids, quotes


def _matches_evidence(
    evidence: list[EvidenceCitation], record_blocks: set[str], record_quotes: set[str]
) -> str | None:
    """Return exact or block-level evidence overlap without inferring identity."""

    source_blocks = {item.block_id for item in evidence}
    source_quotes = {
        quote for item in evidence if (quote := normalized_source_text(item.quote))
    }
    if any(
        len(left) >= 12 and len(right) >= 12 and (left in right or right in left)
        for left in source_quotes
        for right in record_quotes
    ):
        return "covered"
    return "possible_match" if source_blocks & record_blocks else None


def _reported_values(record: object) -> list[tuple[str, str, set[str]]]:
    """Return atomic value names, raw text, and citation blocks from a record."""

    found: list[tuple[str, str, set[str]]] = []

    def walk(value: object, context: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            local_context = context
            if isinstance(value.get("name"), str):
                local_context = (*context, value["name"])
            if isinstance(value.get("raw_value"), str):
                blocks = {
                    str(item["block_id"])
                    for item in value.get("evidence", [])
                    if isinstance(item, dict) and item.get("block_id")
                }
                found.append((" ".join(local_context), value["raw_value"], blocks))
            for key, child in value.items():
                if key != "evidence":
                    walk(child, local_context)
        elif isinstance(value, list):
            for child in value:
                walk(child, context)

    walk(record.model_dump(mode="json") if hasattr(record, "model_dump") else record)
    return found


def _tokens(value: str) -> tuple[str, ...]:
    """Tokenize a source label without chemical dictionaries or name rewriting."""

    return tuple(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_token_sequence(needle: str, haystack: str) -> bool:
    """Match a complete token sequence so short chemical names do not collide."""

    wanted = _tokens(needle)
    available = _tokens(haystack)
    return bool(wanted) and any(
        available[index : index + len(wanted)] == wanted
        for index in range(len(available) - len(wanted) + 1)
    )


def _contains_raw_value(needle: str, haystack: str) -> bool:
    """Match a raw quantity while preventing numeric substring false positives."""

    wanted = re.sub(r"\s+", "", normalized_source_text(needle))
    available = re.sub(r"\s+", "", normalized_source_text(haystack))
    if not wanted:
        return False
    start = available.find(wanted)
    while start >= 0:
        end = start + len(wanted)
        left = available[start - 1] if start else ""
        right = available[end] if end < len(available) else ""
        left_ok = not left or not (
            left.isdigit() or left == "." if wanted[0].isdigit() else left.isalnum()
        )
        right_ok = not right or not (
            right.isdigit() or right == "." if wanted[-1].isdigit() else right.isalnum()
        )
        if left_ok and right_ok:
            return True
        start = available.find(wanted, start + 1)
    return False


def audit_claim_coverage(
    ledger: ClaimLedger, extraction: StudyExtraction
) -> dict[str, Any]:
    """Audit claim recall, shared-value scope, and unsupported record proliferation.

    Exact citation overlap is evidence of representation. Block-only overlap remains
    a possible match for review. Context objects are never demanded as final records,
    and only target device-design objects can justify a device family. This makes the
    audit sensitive to both missing values and over-split families without embedding
    paper-specific materials or processing rules.
    """

    records: dict[str, list[tuple[str, object, set[str], set[str]]]] = defaultdict(list)
    for role, (kind, collection, identifier) in TOP_LEVEL_RECORD_BY_OBJECT_ROLE.items():
        del role
        for record in getattr(extraction, collection):
            blocks, quotes = _record_evidence(record)
            records[kind].append(
                (str(getattr(record, identifier)), record, blocks, quotes)
            )

    items: list[dict[str, object]] = []
    counts = {
        "covered": 0,
        "possible_match": 0,
        "unmatched": 0,
        "context": 0,
        "uncertain": 0,
        "unclaimed_records": 0,
        "missing_shared_targets": 0,
    }
    claimed_record_keys: set[tuple[str, str]] = set()
    object_record_keys: dict[str, set[tuple[str, str]]] = {}
    object_by_id = {item.object_id: item for item in ledger.objects}

    for item in ledger.objects:
        if item.scope != "target" or item.role not in TOP_LEVEL_RECORD_BY_OBJECT_ROLE:
            status = "context" if item.scope == "context" else "uncertain"
            counts[status] += 1
            items.append(
                {
                    **item.model_dump(mode="json"),
                    "status": status,
                    "record_kind": None,
                    "candidate_record_ids": [],
                }
            )
            object_record_keys[item.object_id] = set()
            continue
        record_kind = TOP_LEVEL_RECORD_BY_OBJECT_ROLE[item.role][0]
        exact: list[str] = []
        possible: list[str] = []
        for record_id, _, block_ids, quotes in records[record_kind]:
            match = _matches_evidence(item.evidence, block_ids, quotes)
            if match == "covered":
                exact.append(record_id)
            elif match == "possible_match":
                possible.append(record_id)
        status = "covered" if exact else "possible_match" if possible else "unmatched"
        candidates = exact or possible
        counts[status] += 1
        claimed_record_keys.update((record_kind, record_id) for record_id in candidates)
        object_record_keys[item.object_id] = {
            (record_kind, record_id) for record_id in candidates
        }
        items.append(
            {
                **item.model_dump(mode="json"),
                "status": status,
                "record_kind": record_kind,
                "candidate_record_ids": candidates,
            }
        )

    all_record_values = [
        (kind, record_id, value_name, value_raw, value_blocks)
        for kind, entries in records.items()
        for record_id, record, _, _ in entries
        for value_name, value_raw, value_blocks in _reported_values(record)
    ]
    for claim in ledger.claims:
        if claim.scope != "target":
            status = "context" if claim.scope == "context" else "uncertain"
            counts[status] += 1
            items.append(
                {
                    **claim.model_dump(mode="json"),
                    "status": status,
                    "record_kind": None,
                    "candidate_record_ids": [],
                    "missing_shared_targets": [],
                }
            )
            continue
        role_kinds = {
            TOP_LEVEL_RECORD_BY_OBJECT_ROLE[subject.role][0]
            for object_id in claim.subject_object_ids
            if (subject := object_by_id.get(object_id)) is not None
            and subject.role in TOP_LEVEL_RECORD_BY_OBJECT_ROLE
        }
        claim_record_kind = next(iter(role_kinds)) if len(role_kinds) == 1 else None
        if claim.subject_object_ids and not role_kinds:
            counts["uncertain"] += 1
            items.append(
                {
                    **claim.model_dump(mode="json"),
                    "status": "uncertain",
                    "record_kind": None,
                    "candidate_record_ids": [],
                    "missing_shared_targets": [],
                }
            )
            continue
        candidate_kinds = sorted(role_kinds) or (
            [claim_record_kind] if claim_record_kind else list(records)
        )
        subject_record_keys = {
            key
            for object_id in claim.subject_object_ids
            for key in object_record_keys.get(object_id, set())
        }
        candidate_entries = [
            (kind, record_id, record, block_ids, quotes)
            for kind in candidate_kinds
            for record_id, record, block_ids, quotes in records.get(kind, [])
            if not subject_record_keys or (kind, record_id) in subject_record_keys
        ]
        exact_claims: list[str] = []
        possible_claims: list[str] = []
        source_blocks = {item.block_id for item in claim.evidence}
        for _, record_id, record, block_ids, quotes in candidate_entries:
            match = _matches_evidence(claim.evidence, block_ids, quotes)
            requires_atomic_value = claim.kind == "reported_quantity" or bool(
                claim.shared_targets
            )
            value_supported = (
                not requires_atomic_value
                or claim.raw_value is None
                or any(
                    bool(source_blocks & value_blocks)
                    and _contains_raw_value(claim.raw_value, value_raw)
                    for _, value_raw, value_blocks in _reported_values(record)
                )
            )
            if match == "covered" and value_supported:
                exact_claims.append(record_id)
            elif match is not None:
                possible_claims.append(record_id)
        missing_targets: list[str] = []
        if claim.shared_targets and claim.raw_value:
            eligible_keys = {
                (kind, record_id) for kind, record_id, _, _, _ in candidate_entries
            }
            for target in claim.shared_targets:
                represented = any(
                    kind in candidate_kinds
                    and (kind, record_id) in eligible_keys
                    and bool(source_blocks & value_blocks)
                    and _contains_raw_value(claim.raw_value, value_raw)
                    and _contains_token_sequence(target, value_name)
                    for kind, record_id, value_name, value_raw, value_blocks in all_record_values
                )
                if not represented:
                    missing_targets.append(target)
            counts["missing_shared_targets"] += len(missing_targets)
        status = (
            "covered"
            if exact_claims and not missing_targets
            else "possible_match"
            if (exact_claims or possible_claims)
            else "unmatched"
        )
        candidates = exact_claims or possible_claims
        counts[status] += 1
        items.append(
            {
                **claim.model_dump(mode="json"),
                "status": status,
                "record_kind": claim_record_kind,
                "candidate_record_ids": candidates,
                "missing_shared_targets": missing_targets,
            }
        )

    for record_kind, entries in records.items():
        for record_id, record, _, _ in entries:
            if (record_kind, record_id) in claimed_record_keys:
                continue
            counts["unclaimed_records"] += 1
            items.append(
                {
                    "source_id": f"unclaimed:{record_kind}:{record_id}",
                    "label": f"Final {record_kind} has no matching target object claim",
                    "status": "unclaimed",
                    "record_kind": record_kind,
                    "candidate_record_ids": [record_id],
                    "evidence": [
                        citation.model_dump(mode="json")
                        for citation in getattr(record, "evidence", [])
                    ],
                    "missing_shared_targets": [],
                }
            )

    issue_count = (
        counts["possible_match"]
        + counts["unmatched"]
        + counts["uncertain"]
        + counts["unclaimed_records"]
        + counts["missing_shared_targets"]
    )
    return {
        "status": "complete" if issue_count == 0 else "needs_review",
        "counts": counts,
        "issue_count": issue_count,
        "items": items,
    }
