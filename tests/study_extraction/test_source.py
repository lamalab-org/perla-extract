import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.source import (
    _cache_identity,
    _deduplicate_evidence_blocks,
    _scientific_evidence_blocks,
    _table_cell_texts,
    parse_pdf,
)

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


@pytest.mark.parametrize("damaged", [False, True])
def test_table_adapter_keeps_native_decimals_when_detector_splits_them(
    tmp_path, monkeypatch, damaged
):
    from types import SimpleNamespace

    import pymupdf

    from perla_extract.study_extraction.source import _pymupdf_layout_items

    path = tmp_path / "table.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 60), "Control 3.9 0.67")
        page.insert_text((50, 80), "Treated 19.72 29.7")
        document.save(path)
    rows = [["Control", "3.9", "0.67"], ["Treated", "19.72", "29.7"]]
    if damaged:
        rows = [["Control", "39\n.", "067\n."], ["Treated", "1972\n.", "297\n."]]
    table = SimpleNamespace(bbox=(40, 40, 250, 90), extract=lambda: rows)
    monkeypatch.setattr(
        pymupdf.Page,
        "find_tables",
        lambda *args, **kwargs: SimpleNamespace(tables=[table]),
    )
    items = _pymupdf_layout_items(path)
    text = "\n".join(item["text"] for item in items)
    for number in ("3.9", "0.67", "19.72", "29.7"):
        assert number in text
    assert any(item["kind"] == "table" for item in items) is not damaged


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


def test_docling_cache_identity_includes_native_typography_dependency():
    """Invalidate Docling evidence when its PyMuPDF supplement changes."""

    shared = {
        "source_hash": "source",
        "source": "main",
        "parser": "docling",
    }
    first = _cache_identity(
        **shared, dependency_versions={"docling": "2.0", "PyMuPDF": "1.24"}
    )
    upgraded = _cache_identity(
        **shared, dependency_versions={"docling": "2.0", "PyMuPDF": "1.25"}
    )

    assert first != upgraded


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


@pytest.mark.parametrize("first, second", [("Co", "CO"), ("cm²", "cm2"), ("mM", "mm")])
def test_duplicate_supplement_detection_preserves_scientific_case_and_notation(
    first, second
):
    template = (
        "The precursor contains {} and was added before the final annealing step."
    )
    blocks = [
        EvidenceBlock(
            block_id="main",
            source="main",
            page=1,
            kind="text",
            text=template.format(first),
        ),
        EvidenceBlock(
            block_id="si",
            source="supplement",
            page=1,
            kind="text",
            text=template.format(second),
        ),
    ]
    retained, skipped = _deduplicate_evidence_blocks(blocks)
    assert retained == blocks
    assert skipped == 0


def test_duplicate_supplement_retains_original_source_location():
    text = "The precursor contains Co and was added before the final annealing step."
    blocks = [
        EvidenceBlock(block_id="main", source="main", page=1, kind="text", text=text),
        EvidenceBlock(
            block_id="si", source="supplement", page=4, kind="text", text=text
        ),
    ]
    retained, skipped = _deduplicate_evidence_blocks(blocks)
    assert len(retained) == skipped == 1
    assert retained[0].metadata["duplicate_locations"] == [
        {"source": "supplement", "page": 4, "block_id": "si"}
    ]


class _Row:
    def __init__(self, cells):
        self.cells = cells


class _Table:
    """Stand-in for a PyMuPDF table: cell geometry plus what ``extract()`` returns."""

    def __init__(self, cells, extracted):
        self.rows = [_Row(row) for row in cells]
        self._extracted = extracted

    def extract(self):
        return self._extracted


def _word(text, x0, y0, x1, y1):
    return (x0, y0, x1, y1, text, 0, 0, 0)


def test_table_cells_keep_decimal_points_that_extract_splits_off():
    table = _Table(
        cells=[[(0, 0, 50, 20), (50, 0, 120, 20)]],
        extracted=[["Ref\n.", "2638±012\n. ."]],
    )
    words = [_word("Ref.", 2, 5, 20, 15), _word("26.38±0.12", 55, 5, 110, 15)]

    assert _table_cell_texts(table, words) == [["Ref.", "26.38±0.12"]]


def test_table_cells_keep_raised_glyphs_on_their_line_and_order_lines():
    table = _Table(cells=[[(0, 0, 200, 40)]], extracted=[["unused"]])
    words = [
        _word("Hope", 40, 22, 70, 32),
        _word("Zhao", 40, 5, 70, 15),
        _word("1,2", 71, 3, 80, 10),  # superscript, higher than the line
        _word("Chenxu", 2, 5, 38, 15),
        _word("Michael", 2, 22, 38, 32),
    ]

    assert _table_cell_texts(table, words) == [["Chenxu Zhao 1,2\nMichael Hope"]]


def test_table_cells_fall_back_to_extract_for_merged_or_wordless_cells():
    table = _Table(
        cells=[[(0, 0, 50, 20), None, (100, 0, 150, 20)]],
        extracted=[["vector text", None, "kept"]],
    )
    words = [_word("kept", 105, 5, 130, 15)]

    assert _table_cell_texts(table, words) == [["vector text", None, "kept"]]


def test_table_cells_use_extract_when_row_geometry_does_not_match():
    table = _Table(cells=[[(0, 0, 50, 20)]], extracted=[["a"], ["b"]])

    assert _table_cell_texts(table, [_word("x", 1, 1, 5, 5)]) == [["a"], ["b"]]


def test_pymupdf_table_blocks_keep_word_spacing_from_the_pdf(tmp_path):
    blocks, _ = parse_pdf(FIXTURE, "main", parser="pymupdf", cache_dir=tmp_path)
    tables = [block.text for block in blocks if block.kind == "table"]

    assert any("Received: 4 March 2024" in text for text in tables)
    assert not any("Received:4March2024" in text for text in tables)
