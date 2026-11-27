"""Opt-in real Docling checks with independent content expectations.

Prepare parser weights before running offline. Native PDF and raster/OCR checks
are separate: disabling OCR cannot establish the scanned-document contract.
"""

import importlib.util
import os
from pathlib import Path

import pytest

from perla_extract.study_extraction.source import parse_pdf

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


@pytest.fixture(autouse=True)
def independent_parser_runtime():
    """Each integration selects its own OCR policy; production batches cache it."""
    from perla_extract.study_extraction.source import _docling_runtime

    _docling_runtime.cache_clear()
    yield
    _docling_runtime.cache_clear()


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None
    or os.environ.get("PERLA_RUN_DOCLING_TESTS") != "1",
    reason="set PERLA_RUN_DOCLING_TESTS=1 to run the slower Docling integration test",
)
def test_docling_parser_satisfies_the_shared_block_and_cache_contract(
    tmp_path, monkeypatch
):
    """Ensure Docling produces backend-independent evidence from a digital PDF.

    The fixture already contains a text layer. Disabling OCR keeps this contract test
    focused on our Docling adapter and avoids making CI availability depend on a
    runtime download of RapidOCR weights. Production parsing retains Docling's normal
    OCR behavior for scanned papers.
    """

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = False
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )
    monkeypatch.setattr(
        "docling.document_converter.DocumentConverter", lambda: converter
    )

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


def _authored_table_pdf(path: Path, *, rasterized: bool) -> None:
    """Create known cells, spanning headers, a real blank and explicit context."""
    import pymupdf

    with pymupdf.open() as document:
        page = document.new_page(width=440, height=340)
        page.insert_text(
            (45, 45), "Table 1. Reverse-scan device performance.", fontsize=12
        )
        for y in (90, 120, 150, 180, 210):
            start = 145 if y == 120 else 45
            page.draw_line((start, y), (375, y))
        for x in (45, 145, 375):
            page.draw_line((x, 90), (x, 210))
        page.draw_line((260, 120), (260, 210))
        for x, y, text in (
            (55, 124, "Device"),
            (180, 110, "Performance"),
            (160, 140, "Voc (V)"),
            (275, 140, "PCE (%)"),
            (55, 170, "Control"),
            (175, 170, "1.00"),
            (285, 170, "20.00"),
            (55, 200, "Treated"),
            (285, 200, "20.50"),
        ):
            page.insert_text((x, y), text, fontsize=11)
        page.insert_text(
            (45, 230), "Note to Table 1: All measurements used 100 mW/cm2.", fontsize=10
        )
        if rasterized:
            pixels = page.get_pixmap(matrix=pymupdf.Matrix(3, 3))
            with pymupdf.open() as scanned:
                target = scanned.new_page(
                    width=page.rect.width, height=page.rect.height
                )
                target.insert_image(target.rect, pixmap=pixels)
                scanned.save(path)
        else:
            document.save(path)


@pytest.mark.skipif(
    importlib.util.find_spec("docling") is None
    or os.environ.get("PERLA_RUN_DOCLING_TESTS") != "1",
    reason="set PERLA_RUN_DOCLING_TESTS=1 with prepared local parser weights",
)
@pytest.mark.parametrize(
    "rasterized",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.skipif(
                os.environ.get("PERLA_RUN_DOCLING_OCR_TESTS") != "1",
                reason="set PERLA_RUN_DOCLING_OCR_TESTS=1 with prepared local OCR weights",
            ),
        ),
    ],
)
def test_docling_preserves_authored_table_content_and_context(
    tmp_path, monkeypatch, rasterized
):
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    from perla_extract.study_extraction.claims import (
        ClaimLedger,
        ExperimentalObject,
        assembly_blocks,
        assembly_evidence_payload,
        assembly_spans,
    )
    from perla_extract.study_extraction.models import EvidenceCitation
    from perla_extract.study_extraction.transport import expand_span_citations

    path = tmp_path / "authored.pdf"
    _authored_table_pdf(path, rasterized=rasterized)
    options = PdfPipelineOptions(
        do_ocr=rasterized,
        accelerator_options=AcceleratorOptions(device="cpu", num_threads=2),
    )
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
        }
    )
    monkeypatch.setattr(
        "docling.document_converter.DocumentConverter", lambda: converter
    )

    blocks, _ = parse_pdf(
        path,
        "supplement",
        parser="docling",
        cache_dir=tmp_path / "cache",
        heartbeat_seconds=0,
    )
    tables = [b for b in blocks if b.kind == "table"]
    assert len(tables) == 1
    table = tables[0]
    assert table.metadata["rows"][-2:] == [
        ["Control", "1.00", "20.00"],
        ["Treated", None, "20.50"],
    ]
    assert table.metadata["rows"][0] == ["Device", "Performance", "Performance"]
    layout = table.metadata["table_layout"]
    assert any(
        c["row_start"] == 0 and c["column_start"] == 1 and c["column_end"] == 3
        for c in layout
    )
    assert any(
        c["row_start"] == 0 and c["column_start"] == 0 and c["row_end"] == 2
        for c in layout
    )
    linked = {
        b.block_id: b
        for b in blocks
        if b.block_id in table.metadata["context_block_ids"]
    }
    assert any("Reverse-scan" in b.text for b in linked.values())
    assert any("100 mW/cm2" in b.text for b in linked.values())

    row = next(line for line in table.text.splitlines() if line.startswith("Treated"))
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="treated",
                label="Treated",
                role="individual_device",
                scope="target",
                evidence=[EvidenceCitation(block_id=table.block_id, quote=row)],
            )
        ],
        claims=[],
    )
    selected = assembly_blocks(blocks, ledger)
    spans = assembly_spans(selected, ledger)
    payload = assembly_evidence_payload(selected, ledger)
    assert set(linked) <= {b.block_id for b in selected}
    assert all("unresolved_context_block_ids" not in entry for entry in payload)
    passage = next(s for s in spans if s.text == row)
    assert expand_span_citations({"evidence": [passage.span_id]}, spans) == {
        "evidence": [{"block_id": table.block_id, "quote": row}]
    }
