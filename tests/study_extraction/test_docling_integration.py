"""Opt-in parser-contract smoke test for the default Docling backend."""

import importlib.util
import os
from pathlib import Path

import pytest

from perla_extract.study_extraction.source import parse_pdf

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None
    or os.environ.get("PERLA_RUN_DOCLING_TESTS") != "1",
    reason="set PERLA_RUN_DOCLING_TESTS=1 to run the slower Docling integration test",
)
def test_docling_parser_satisfies_the_shared_block_and_cache_contract(tmp_path):
    """Ensure an installed Docling release produces backend-independent evidence."""

    blocks, first = parse_pdf(
        FIXTURE,
        "main",
        parser="docling",
        cache_dir=tmp_path,
        heartbeat_seconds=0,
    )
    cached, second = parse_pdf(
        FIXTURE,
        "main",
        parser="docling",
        cache_dir=tmp_path,
        heartbeat_seconds=0,
    )

    assert blocks
    assert all(block.text and block.page >= 1 for block in blocks)
    assert len({block.block_id for block in blocks}) == len(blocks)
    assert [block.block_id for block in cached] == [block.block_id for block in blocks]
    assert first["parsed_block_count"] - first["excluded_block_count"] == len(blocks)
    assert first["parser"] == "docling"
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True
