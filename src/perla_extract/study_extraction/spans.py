"""Create directly citable passages from parser evidence blocks.

Models need to choose supporting evidence, but they do not need to reproduce text we
already own.  This module divides parser blocks into sentence-, row-, or bounded
passages and numbers each request's catalog. Model responses cite short identifiers;
Python restores exact quotations. The catalog fingerprint binds identifiers to text.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable

from pydantic import Field

from .models import EvidenceBlock, Identifier, StrictModel

MAX_SPAN_CHARACTERS = 1200
TARGET_SPAN_CHARACTERS = 700
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[\[('“\"A-Z0-9])")


class EvidenceSpan(StrictModel):
    """Identify one exact passage within the catalog supplied to a model call."""

    span_id: int = Field(ge=1, strict=True)
    block_id: Identifier
    text: str = Field(min_length=1, max_length=MAX_SPAN_CHARACTERS)


def _bounded_passages(text: str) -> list[str]:
    """Split an oversized passage at whitespace without rewriting its contents."""

    remaining = text.strip()
    passages: list[str] = []
    while len(remaining) > MAX_SPAN_CHARACTERS:
        boundary = remaining.rfind(" ", 0, MAX_SPAN_CHARACTERS + 1)
        if boundary < MAX_SPAN_CHARACTERS // 2:
            boundary = MAX_SPAN_CHARACTERS
        passages.append(remaining[:boundary].strip())
        remaining = remaining[boundary:].strip()
    if remaining:
        passages.append(remaining)
    return passages


def _prose_passages(text: str) -> list[str]:
    """Pack adjacent sentences into exact, moderately sized source passages."""

    boundaries = list(_SENTENCE_BOUNDARY.finditer(text))
    ranges = [
        (start, end)
        for start, end in zip(
            [0, *(match.end() for match in boundaries)],
            [*(match.start() for match in boundaries), len(text)],
        )
        if text[start:end].strip()
    ]
    passages: list[str] = []
    current_start: int | None = None
    current_end: int | None = None
    for start, end in ranges:
        if current_start is None:
            current_start, current_end = start, end
            continue
        assert current_end is not None
        if len(text[current_start:end].strip()) <= TARGET_SPAN_CHARACTERS:
            current_end = end
            continue
        passages.extend(_bounded_passages(text[current_start:current_end]))
        current_start, current_end = start, end
    if current_start is not None and current_end is not None:
        passages.extend(_bounded_passages(text[current_start:current_end]))
    return passages


def _passages(text: str, kind: str) -> list[str]:
    """Use table rows or packed prose before applying a hard size ceiling.

    Table backends normally serialize rows as lines, while prose blocks normally
    contain many layout lines. Preserving rows for tables and packing adjacent prose
    sentences avoids both overly broad quotations and thousands of tiny span IDs.
    """

    if "table" not in kind.casefold():
        return _prose_passages(text)
    return [
        passage
        for line in text.splitlines()
        if line.strip()
        for passage in _bounded_passages(line)
    ]


def build_evidence_spans(blocks: Iterable[EvidenceBlock]) -> list[EvidenceSpan]:
    """Number exact passages in input order; IDs are local to this catalog.

    A different subset can number the same passage differently. Prompt construction
    and decoding must use the same catalog. Persisted public citations contain the
    parser block ID and exact quotation, never this temporary shorthand.
    """

    spans: list[EvidenceSpan] = []
    seen_passages: set[tuple[str, int, str]] = set()
    for block in blocks:
        for ordinal, text in enumerate(_passages(block.text, block.kind), start=1):
            identity = (block.block_id, ordinal, text)
            if identity in seen_passages:
                raise ValueError(
                    f"duplicate evidence passage in block: {block.block_id}"
                )
            seen_passages.add(identity)
            span_id = len(spans) + 1
            spans.append(
                EvidenceSpan(span_id=span_id, block_id=block.block_id, text=text)
            )
    return spans


def evidence_spans_sha256(spans: Iterable[EvidenceSpan]) -> str:
    """Fingerprint the exact citation catalog that constrains a model request."""

    encoded = json.dumps(
        [span.model_dump(mode="json") for span in spans],
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def linked_context_ids(block: EvidenceBlock) -> list[str]:
    """Read explicit parser links; unknown or malformed metadata supplies no links."""
    context = block.metadata.get("context_block_ids", [])
    return (
        [value for value in context if isinstance(value, str)]
        if isinstance(context, list)
        else []
    )


def linked_context_closure(
    blocks: Iterable[EvidenceBlock], identifiers: Iterable[str]
) -> set[str]:
    """Keep explicit linked context transitively without guessing from proximity.

    The visited set handles repeated links and cycles. Missing targets remain in
    the result so selection never turns an unresolved link into a valid one;
    serialization reports those unavailable targets separately.
    """

    by_id = {block.block_id: block for block in blocks}
    retained = set(identifiers)
    pending = list(retained)
    while pending:
        identifier = pending.pop()
        block = by_id.get(identifier)
        if block is None:
            continue
        for target in linked_context_ids(block):
            if target not in retained:
                retained.add(target)
                pending.append(target)
    return retained


def evidence_payload(
    blocks: Iterable[EvidenceBlock], spans: Iterable[EvidenceSpan] | None = None
) -> list[dict[str, object]]:
    """Serialize blocks using one catalog, including when displaying its subsets.

    A reader presents context and primary evidence separately but decodes against
    their combined catalog. Passing that catalog prevents reusing 1 for two texts.
    """

    block_list = list(blocks)
    spans_by_block: dict[str, list[EvidenceSpan]] = {}
    for span in build_evidence_spans(block_list) if spans is None else spans:
        spans_by_block.setdefault(span.block_id, []).append(span)
    payload: list[dict[str, object]] = [
        {
            "block_id": block.block_id,
            "source": block.source,
            "page": block.page,
            "section": block.section_path[-1] if block.section_path else None,
            "kind": block.kind,
            "spans": {
                span.span_id: span.text
                for span in spans_by_block.get(block.block_id, [])
            },
        }
        for block in block_list
    ]
    # Primary/context displays can be serialized separately against one combined
    # catalog. A target in that catalog remains available to the model even when
    # it is absent from this display's block subset.
    present = set(spans_by_block)
    for block, entry in zip(block_list, payload):
        if block.kind == "table":
            entry["table_layout"] = block.metadata.get("table_layout", [])
        context = linked_context_ids(block)
        if block.kind == "table" or context:
            entry["context_block_ids"] = [
                identifier for identifier in context if identifier in present
            ]
            missing = [
                identifier for identifier in context if identifier not in present
            ]
            if missing:
                entry["unresolved_context_block_ids"] = missing
    return payload
