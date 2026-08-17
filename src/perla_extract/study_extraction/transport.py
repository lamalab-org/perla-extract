"""Compact model responses without changing the public study schema.

The public schema keeps evidence beside every claim because that is convenient for
review and export. Repeating the same quotation for every value is wasteful during
generation, however. This module gives the model a normalized citation catalog and
expands its citation references before Pydantic validates ``StudyExtraction``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from .models import StudyExtraction


def _identifier_schema() -> dict[str, object]:
    """Return the same bounded identifier contract used by the public models."""

    return {"type": "string", "minLength": 1, "maxLength": 200}


def compact_study_schema(block_ids: Iterable[str]) -> dict[str, object]:
    """Build a study schema with shared citations and request-local block IDs.

    The transformation is deliberately mechanical: every nested ``evidence`` field
    already refers to the shared ``EvidenceCitation`` definition, so replacing that
    definition with a citation ID normalizes the whole response without duplicating
    the scientific entity models.
    """

    schema = deepcopy(StudyExtraction.model_json_schema())
    definitions = schema["$defs"]
    definitions["EvidenceCitation"] = _identifier_schema()
    definitions["EvidenceCatalogEntry"] = {
        "type": "object",
        "properties": {
            "citation_id": _identifier_schema(),
            "block_id": {
                **_identifier_schema(),
                "enum": sorted(set(block_ids)),
            },
            "quote": {"type": "string", "minLength": 1, "maxLength": 1600},
        },
        "required": ["citation_id", "block_id", "quote"],
        "additionalProperties": False,
    }
    schema["properties"]["evidence_catalog"] = {
        "type": "array",
        "items": {"$ref": "#/$defs/EvidenceCatalogEntry"},
    }
    schema["required"] = [*schema.get("required", []), "evidence_catalog"]
    return schema


def expand_compact_study(payload: object) -> dict[str, object]:
    """Replace citation IDs with full citations before public-schema validation.

    Unknown or duplicate citation IDs are rejected instead of becoming missing
    evidence. Expansion changes representation only; it never edits a claim, quote,
    value, or source pointer.
    """

    if not isinstance(payload, dict):
        raise ValueError("compact study response must be an object")
    data = deepcopy(payload)
    entries = data.pop("evidence_catalog", None)
    if not isinstance(entries, list):
        raise ValueError("compact study response requires evidence_catalog")
    catalog: dict[str, dict[str, str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("evidence catalog entries must be objects")
        citation_id = entry.get("citation_id")
        block_id = entry.get("block_id")
        quote = entry.get("quote")
        if not all(
            isinstance(item, str) and item for item in (citation_id, block_id, quote)
        ):
            raise ValueError("evidence catalog entries require non-empty strings")
        if citation_id in catalog:
            raise ValueError(f"duplicate citation_id: {citation_id}")
        catalog[citation_id] = {"block_id": block_id, "quote": quote}

    def expand(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence":
                    if not isinstance(item, list) or not all(
                        isinstance(citation_id, str) for citation_id in item
                    ):
                        raise ValueError("evidence must contain citation IDs")
                    try:
                        value[key] = [catalog[citation_id] for citation_id in item]
                    except KeyError as error:
                        raise ValueError(
                            f"unknown citation_id: {error.args[0]}"
                        ) from error
                else:
                    expand(item)
        elif isinstance(value, list):
            for item in value:
                expand(item)

    expand(data)
    return data


def compact_study(study: StudyExtraction) -> dict[str, object]:
    """Deduplicate an existing study's citations for another model request.

    This is the mechanical inverse of ``expand_compact_study``. Refinement needs the
    whole draft, but repeating a long table row beside every atomic value can dominate
    its input. Stable encounter-order IDs retain all evidence while making that input
    reproducible and substantially smaller.
    """

    data = study.model_dump(mode="json")
    citation_ids: dict[tuple[str, str], str] = {}
    catalog: list[dict[str, str]] = []

    def compact(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence" and isinstance(item, list):
                    references: list[str] = []
                    for reference in item:
                        identity = (str(reference["block_id"]), str(reference["quote"]))
                        citation_id = citation_ids.get(identity)
                        if citation_id is None:
                            citation_id = f"citation-{len(catalog) + 1}"
                            citation_ids[identity] = citation_id
                            catalog.append(
                                {
                                    "citation_id": citation_id,
                                    "block_id": identity[0],
                                    "quote": identity[1],
                                }
                            )
                        references.append(citation_id)
                    value[key] = references
                else:
                    compact(item)
        elif isinstance(value, list):
            for item in value:
                compact(item)

    compact(data)
    data["evidence_catalog"] = catalog
    return data


def constrain_evidence_block_ids(
    schema: dict[str, object], block_ids: Iterable[str]
) -> dict[str, object]:
    """Constrain ordinary evidence citations to IDs present in one model request."""

    constrained = deepcopy(schema)
    citation = constrained.get("$defs", {}).get("EvidenceCitation", {})
    block = citation.get("properties", {}).get("block_id")
    if isinstance(block, dict):
        block["enum"] = sorted(set(block_ids))
    return constrained
