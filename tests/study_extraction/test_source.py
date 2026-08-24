import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.source import (
    _scientific_evidence_blocks,
    parse_pdf,
)

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


def test_pymupdf_parser_preserves_blocks_and_uses_cache(tmp_path):
    blocks, first = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)
    cached, second = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)

    assert blocks
    assert all(block.text and block.page >= 1 for block in blocks)
    assert len({block.block_id for block in blocks}) == len(blocks)
    assert [block.block_id for block in cached] == [block.block_id for block in blocks]
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
    assert first["cache_format_version"] == 1
    assert (
        first["parser_implementation_sha256"] == second["parser_implementation_sha256"]
    )
    assert first["evidence_schema_sha256"] == second["evidence_schema_sha256"]


@pytest.mark.parametrize("invalid_cache", ["{", "[]"])
def test_invalid_document_cache_is_rebuilt(tmp_path, invalid_cache):
    """Recover from an interrupted parser-cache write using the source PDF."""

    blocks, _ = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)
    next(tmp_path.glob("*.json")).write_text(invalid_cache, encoding="utf-8")

    rebuilt, event = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)

    assert event["cache_hit"] is False
    assert [block.block_id for block in rebuilt] == [block.block_id for block in blocks]

    cached, final_event = parse_pdf(
        FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path
    )
    assert final_event["cache_hit"] is True
    assert [block.block_id for block in cached] == [block.block_id for block in blocks]


def test_mismatched_cache_identity_is_rebuilt(tmp_path):
    """Reject structurally valid cache data produced under a different contract."""

    parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)
    cache_path = next(tmp_path.glob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["cache_identity"]["cache_format_version"] = 0
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    _, event = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)

    assert event["cache_hit"] is False


def test_evidence_view_uses_parser_semantics_without_section_name_rules():
    """Exclude confidently typed furniture while retaining every unknown block."""

    def block(block_id: str, **metadata: object) -> EvidenceBlock:
        return EvidenceBlock(
            block_id=block_id,
            source="main",
            page=1,
            section_path=[],
            kind="text",
            text=f"content for {block_id}",
            bbox=None,
            metadata=metadata,
        )

    blocks = [
        block("body"),
        block("unknown", docling_label="new_scientific_label"),
        block("reference", include_in_evidence=False),
        block("header", include_in_evidence=False),
    ]

    evidence, excluded = _scientific_evidence_blocks(blocks)

    assert [item.block_id for item in evidence] == ["body", "unknown"]
    assert excluded == 2
