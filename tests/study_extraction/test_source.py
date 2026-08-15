from pathlib import Path

from perla_extract.study_extraction.source import parse_pdf

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


def test_invalid_document_cache_is_rebuilt(tmp_path):
    """Recover from an interrupted parser-cache write using the source PDF."""

    blocks, _ = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)
    next(tmp_path.glob("*.json")).write_text("{", encoding="utf-8")

    rebuilt, event = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)

    assert event["cache_hit"] is False
    assert [block.block_id for block in rebuilt] == [block.block_id for block in blocks]
