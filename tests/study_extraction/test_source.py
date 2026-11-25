import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.source import (
    _cache_identity,
    _deduplicate_evidence_blocks,
    _scientific_evidence_blocks,
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


@pytest.mark.parametrize("outside_prose", [False, True])
def test_table_adapter_suppresses_only_completely_contained_native_blocks(
    monkeypatch, outside_prose
):
    from types import SimpleNamespace

    from perla_extract.study_extraction.source import _pymupdf_layout_items

    class Page:
        def find_tables(self, **_):
            return SimpleNamespace(
                tables=[
                    SimpleNamespace(
                        bbox=(0, 0, 100, 60),
                        extract=lambda: [["Sample", "PCE"], ["A", "20"]],
                    )
                ]
            )

        def get_text(self, mode, **_):
            if mode == "text":
                return "Sample PCE\nA 20"
            lines = [
                {"spans": [{"text": "Sample PCE A 20", "font": "Regular", "size": 10}]}
            ]
            if outside_prose:
                lines.append(
                    {
                        "spans": [
                            {
                                "text": "The treated device reached 24% PCE.",
                                "font": "Regular",
                                "size": 10,
                            }
                        ]
                    }
                )
            return {
                "blocks": [
                    {
                        "type": 0,
                        "bbox": (0, 0, 100, 100) if outside_prose else (5, 5, 95, 55),
                        "lines": lines,
                    }
                ]
            }

    class PDF(list):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(
        "perla_extract.study_extraction.source.pymupdf.open",
        lambda _: PDF([Page()]),
    )

    layout = _pymupdf_layout_items(Path("not-read.pdf"))

    assert [item["kind"] for item in layout] == (
        ["table", "text"] if outside_prose else ["table"]
    )
    assert ("24%" in "\n".join(item["text"] for item in layout)) is outside_prose


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


def test_document_pair_removes_repeated_prose_and_keeps_all_si_tables(
    monkeypatch, tmp_path
):
    from perla_extract.study_extraction import source
    from perla_extract.study_extraction.models import EvidenceBlock

    def block(identifier, origin, text, kind="table"):
        return EvidenceBlock(
            block_id=identifier, source=origin, page=1, kind=kind, text=text
        )

    article = "Device | Jsc | Voc | FF | PCE\nForward | 19.50 | 1.09 | 0.76 | 16.22"
    distinct = "Device | Jsc | Voc | FF | PCE\nReverse | 19.65 | 1.12 | 0.76 | 16.72"
    prose = "The complete article was included before the supplementary information."

    def parse(path, origin, **kwargs):
        blocks = (
            [
                block("main-text", origin, prose, "text"),
                block("main-table", origin, article),
            ]
            if origin == "main"
            else [
                block("si-text-copy", origin, prose, "text"),
                block("si-copy", origin, article),
                block("si-distinct", origin, distinct),
            ]
        )
        return blocks, {"source": origin}

    monkeypatch.setattr(source, "parse_pdf", parse)
    blocks, events = source.parse_documents(tmp_path / "main.pdf", tmp_path / "si.pdf")
    assert [b.block_id for b in blocks] == [
        "main-text",
        "main-table",
        "si-copy",
        "si-distinct",
    ]
    assert blocks[0].metadata["duplicate_locations"] == [
        {"source": "supplement", "page": 1, "block_id": "si-text-copy"}
    ]
    assert events[-1] == {"operation": "cross_document_deduplication", "skipped": 1}


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


def test_explicit_table_context_survives_furniture_classification():
    table = EvidenceBlock(
        block_id="table",
        source="main",
        page=1,
        kind="table",
        text="A | 20",
        metadata={"context_block_ids": ["note"]},
    )
    note = EvidenceBlock(
        block_id="note",
        source="main",
        page=1,
        kind="text",
        text="Measurements use reverse scans.",
        metadata={"include_in_evidence": False},
    )
    footer = EvidenceBlock(
        block_id="footer",
        source="main",
        page=1,
        kind="text",
        text="Page 1",
        metadata={"include_in_evidence": False},
    )

    evidence, excluded = _scientific_evidence_blocks([table, note, footer])

    assert [block.block_id for block in evidence] == ["table", "note"]
    assert excluded == 1


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


@pytest.mark.parametrize("same_caption", [False, True])
def test_identical_table_grids_keep_their_source_context(same_caption):
    from perla_extract.study_extraction.spans import evidence_payload

    grid = "Device | PCE [%] | FF [%]\nControl | 20 | 80"
    main_caption = "Table 1. Reverse scans measured under one-sun illumination."
    si_caption = (
        main_caption
        if same_caption
        else "Table S1. Forward scans measured under one-sun illumination."
    )
    blocks = [
        EvidenceBlock(
            block_id="main-caption",
            source="main",
            page=2,
            kind="text",
            text=main_caption,
        ),
        EvidenceBlock(
            block_id="main-table",
            source="main",
            page=2,
            kind="table",
            text=grid,
            metadata={"context_block_ids": ["main-caption"]},
        ),
        EvidenceBlock(
            block_id="si-caption",
            source="supplement",
            page=1,
            kind="text",
            text=si_caption,
        ),
        EvidenceBlock(
            block_id="si-table",
            source="supplement",
            page=1,
            kind="table",
            text=grid,
            metadata={"context_block_ids": ["si-caption"]},
        ),
    ]

    retained, skipped = _deduplicate_evidence_blocks(blocks)

    assert retained == blocks
    assert skipped == 0
    tables = [entry for entry in evidence_payload(retained) if entry["kind"] == "table"]
    assert [entry["context_block_ids"] for entry in tables] == [
        ["main-caption"],
        ["si-caption"],
    ]
    assert all("unresolved_context_block_ids" not in entry for entry in tables)
