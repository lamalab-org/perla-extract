"""Create stable, directly citable passages from parser evidence blocks.

Models need to choose supporting evidence, but they do not need to reproduce text we
already own.  This module divides parser blocks into sentence-, row-, or bounded
passages and gives each passage a content-derived identifier.  Model responses can
therefore cite identifiers while Python restores exact quotations deterministically.
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
    """Identify one exact, bounded passage within a parser evidence block."""

    span_id: Identifier
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
    """Return stable citation passages for the supplied blocks in document order."""

    spans: list[EvidenceSpan] = []
    seen_ids: set[str] = set()
    for block in blocks:
        for ordinal, text in enumerate(_passages(block.text, block.kind), start=1):
            digest = hashlib.sha256(
                f"{block.block_id}\0{ordinal}\0{text}".encode("utf-8")
            ).hexdigest()[:12]
            span_id = f"span-{digest}"
            if span_id in seen_ids:
                raise ValueError(f"duplicate evidence span identifier: {span_id}")
            seen_ids.add(span_id)
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


def evidence_payload(blocks: Iterable[EvidenceBlock]) -> list[dict[str, object]]:
    """Serialize source locations once and nest their directly citable passages."""

    block_list = list(blocks)
    spans_by_block: dict[str, list[EvidenceSpan]] = {}
    for span in build_evidence_spans(block_list):
        spans_by_block.setdefault(span.block_id, []).append(span)
    return [
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
