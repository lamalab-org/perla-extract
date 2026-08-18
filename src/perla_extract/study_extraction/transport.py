"""Translate compact model evidence references into the public study schema.

The public schema keeps exact quotations beside every claim for review and export.
The model-facing schema instead accepts only precomputed evidence-span identifiers.
Python expands those identifiers after generation, so the model chooses evidence but
never spends tokens copying it or introduces a subtly altered quotation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Iterable

from pydantic import BaseModel

from .evidence import source_contains_text
from .spans import EvidenceSpan


def _identifier_schema(values: Iterable[str] | None = None) -> dict[str, object]:
    """Return the bounded identifier contract, optionally constrained to known IDs."""

    schema: dict[str, object] = {
        "type": "string",
        "minLength": 1,
        "maxLength": 200,
    }
    if values is not None:
        schema["enum"] = sorted(set(values))
    return schema


def span_citation_schema(
    response_model: type[BaseModel], spans: Iterable[EvidenceSpan]
) -> dict[str, object]:
    """Replace every model-facing citation object with a known evidence-span ID.

    All response models share the same ``EvidenceCitation`` definition, including the
    inventory, full extraction, identity-link, and targeted-repair calls. Applying one
    mechanical schema transformation keeps citation behavior consistent everywhere.
    """

    span_list = list(spans)
    if not span_list:
        raise ValueError("at least one evidence span is required")
    schema = deepcopy(response_model.model_json_schema())
    definitions = schema.get("$defs", {})
    if "EvidenceCitation" not in definitions:
        raise ValueError("response schema does not contain EvidenceCitation")
    definitions["EvidenceCitation"] = _identifier_schema(
        span.span_id for span in span_list
    )
    return schema


def expand_span_citations(
    payload: object, spans: Iterable[EvidenceSpan]
) -> dict[str, object]:
    """Restore exact public citations for model-selected evidence-span identifiers."""

    if not isinstance(payload, dict):
        raise ValueError("model response must be an object")
    data = deepcopy(payload)
    catalog = {
        span.span_id: {"block_id": span.block_id, "quote": span.text}
        for span in spans
    }

    def citation(reference: object) -> dict[str, str]:
        if not isinstance(reference, str):
            raise ValueError("model-facing evidence must contain span identifiers")
        try:
            return catalog[reference]
        except KeyError as error:
            raise ValueError(f"unknown evidence span: {reference}") from error

    def expand(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence":
                    value[key] = (
                        [citation(reference) for reference in item]
                        if isinstance(item, list)
                        else citation(item)
                    )
                else:
                    expand(item)
        elif isinstance(value, list):
            for item in value:
                expand(item)

    expand(data)
    return data


def compact_to_span_citations(
    payload: BaseModel | dict[str, object], spans: Iterable[EvidenceSpan]
) -> dict[str, object]:
    """Replace exact public citations with span IDs for a subsequent model prompt.

    Refinement and identity linking receive records produced from the same span
    catalog. Failing on a non-matching citation is intentional: silently dropping or
    approximating evidence would make the next model call less auditable.
    """

    data = deepcopy(
        payload.model_dump(mode="json")
        if isinstance(payload, BaseModel)
        else payload
    )
    span_list = list(spans)
    identities = {
        (span.block_id, span.text): span.span_id for span in span_list
    }

    def compact_reference(reference: object) -> str:
        if not isinstance(reference, dict):
            raise ValueError("public evidence citation must be an object")
        identity = (str(reference.get("block_id")), str(reference.get("quote")))
        exact = identities.get(identity)
        if exact is not None:
            return exact
        matches = [
            span.span_id
            for span in span_list
            if span.block_id == identity[0]
            and source_contains_text(span.text, identity[1])
        ]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(
            f"citation does not resolve to one generated evidence span: {identity[0]}"
        )

    def compact(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "evidence":
                    value[key] = (
                        [compact_reference(reference) for reference in item]
                        if isinstance(item, list)
                        else compact_reference(item)
                    )
                else:
                    compact(item)
        elif isinstance(value, list):
            for item in value:
                compact(item)

    compact(data)
    return data
