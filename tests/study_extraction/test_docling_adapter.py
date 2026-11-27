"""Parser regressions independent of downloaded Docling models or paid inference."""

from types import SimpleNamespace as Node

import pytest

from perla_extract.study_extraction.claims import (
    ClaimLedger,
    ExperimentalObject,
    assembly_blocks,
    assembly_evidence_payload,
    assembly_spans,
)
from perla_extract.study_extraction.models import EvidenceBlock, EvidenceCitation
from perla_extract.study_extraction.source import (
    _attach_explicit_table_notes,
    _docling_bbox,
    _docling_table,
    _parse_docling,
)
from perla_extract.study_extraction.spans import evidence_payload


class BottomLeftBox(Node):
    def __init__(self):
        super().__init__(l=10, t=780, r=110, b=760)

    def to_top_left_origin(self, *, page_height):
        assert page_height == 800
        return Node(l=10, t=20, r=110, b=40)


def test_docling_geometry_uses_the_provenance_page_height():
    document = Node(pages={2: Node(size=Node(height=800))})
    item = Node(prov=[Node(page_no=2, bbox=BottomLeftBox())])
    assert _docling_bbox(item, document) == [10, 20, 110, 40]
    assert _docling_bbox(Node(prov=[]), document) is None
    assert _docling_bbox(item, Node(pages={})) is None


def cell(text, row, column, *, rows=1, columns=1, header=False):
    return Node(
        text=text,
        row_span=rows,
        col_span=columns,
        start_row_offset_idx=row,
        end_row_offset_idx=row + rows,
        start_col_offset_idx=column,
        end_col_offset_idx=column + columns,
        column_header=header,
        row_header=False,
        row_section=False,
    )


def table():
    device = cell("Device", 0, 0, rows=2, header=True)
    measurement = cell("J-V", 0, 1, columns=2, header=True)
    voltage, efficiency = (
        cell("Voc (V)", 1, 1, header=True),
        cell("PCE (%)", 1, 2, header=True),
    )
    first = [cell("Control", 2, 0), cell("1.044", 2, 1), cell("19.70", 2, 2)]
    second = [cell("", 3, 0), cell("0.680", 3, 1), cell("0.31", 3, 2)]
    cells = [device, measurement, voltage, efficiency, *first, *second]
    return Node(
        data=Node(
            grid=[
                [device, measurement, measurement],
                [device, voltage, efficiency],
                first,
                second,
            ],
            table_cells=cells,
        )
    )


def test_table_preserves_multirow_headers_decimal_text_and_actual_blanks():
    text, metadata = _docling_table(table())
    assert text.splitlines() == [
        "Device | J-V | J-V",
        "Device | Voc (V) | PCE (%)",
        "Control | 1.044 | 19.70",
        " | 0.680 | 0.31",
    ]
    assert metadata["rows"][-1][0] is None
    assert metadata["table_layout"][0]["row_end"] == 2
    assert metadata["table_layout"][1]["column_end"] == 3
    assert metadata["table_layout"][0]["column_header"]


def test_adapter_links_actual_caption_and_footnote_and_keeps_geometry_missing(
    monkeypatch,
):
    from perla_extract.study_extraction import source

    caption = Node(
        self_ref="caption",
        text="Table 1. J-V measurements.",
        label="caption",
        content_layer="body",
        prov=[],
    )
    note = Node(
        self_ref="note",
        text="Values measured under 100 mW/cm2.",
        label="footnote",
        content_layer="body",
        prov=[],
    )
    item = table()
    item.self_ref, item.text, item.label, item.content_layer, item.prov = (
        "table",
        "",
        "table",
        "body",
        [],
    )
    item.captions, item.footnotes = [Node(cref="caption")], [Node(cref="note")]
    document = Node(
        pages={}, iterate_items=lambda **_: [(caption, 1), (item, 1), (note, 1)]
    )
    monkeypatch.setattr(
        source,
        "_docling_runtime",
        lambda: (
            Node(convert=lambda _: Node(document=document)),
            Node(BODY="body", FURNITURE="furniture"),
            Node(REFERENCE="reference", PAGE_HEADER="header", PAGE_FOOTER="footer"),
        ),
    )
    monkeypatch.setattr(source, "_native_typography_blocks", lambda *a, **k: [])
    blocks = _parse_docling(None, "main")
    assert blocks[1].metadata["context_block_ids"] == [
        blocks[0].block_id,
        blocks[2].block_id,
    ]
    assert all(block.bbox is None for block in blocks)
    payload = evidence_payload(blocks)
    assert payload[1]["table_layout"] == blocks[1].metadata["table_layout"]
    assert payload[1]["context_block_ids"] == [blocks[0].block_id, blocks[2].block_id]

    # Exercise the actual converter-adapter → selection → span transport chain.
    # A span-only test would not detect context discarded by block selection.
    from perla_extract.study_extraction.transport import expand_span_citations

    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="control",
                label="Control",
                role="individual_device",
                scope="target",
                evidence=[
                    EvidenceCitation(
                        block_id=blocks[1].block_id, quote="Control | 1.044 | 19.70"
                    )
                ],
            )
        ],
        claims=[],
    )
    selected = assembly_blocks(blocks, ledger)
    spans = assembly_spans(selected, ledger)
    assembled = assembly_evidence_payload(selected, ledger)
    assert [b.block_id for b in selected] == [b.block_id for b in blocks]
    assert assembled[1]["context_block_ids"] == [blocks[0].block_id, blocks[2].block_id]
    note_span = next(s for s in spans if s.text == note.text)
    assert expand_span_citations({"evidence": [note_span.span_id]}, spans) == {
        "evidence": [{"block_id": blocks[2].block_id, "quote": note.text}]
    }


def test_reader_row_keeps_distant_headers_and_linked_notes_without_unrelated_prose():
    table_block = EvidenceBlock(
        block_id="table",
        source="supplement",
        page=2,
        kind="table",
        text="Device | PCE (%)\nA | 10\nB | 11\nC | 12\nD | 13",
        metadata={"context_block_ids": ["note"]},
    )
    note = EvidenceBlock(
        block_id="note",
        source="supplement",
        page=3,
        kind="text",
        text="All J-V measurements used 100 mW/cm2.",
    )
    unrelated = EvidenceBlock(
        block_id="unrelated",
        source="supplement",
        page=4,
        kind="text",
        text="Different experiment: 50 mW/cm2.",
    )
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="device",
                label="device",
                role="individual_device",
                scope="target",
                evidence=[EvidenceCitation(block_id="table", quote="D | 13")],
            )
        ],
        claims=[],
    )
    spans = assembly_spans([table_block, note, unrelated], ledger)
    assert {s.block_id for s in spans} == {"table", "note"}
    assert spans[0].text == "Device | PCE (%)"
    payload = assembly_evidence_payload([table_block, note, unrelated], ledger)
    assert payload[0]["context_block_ids"] == ["note"]
    assert {key for entry in payload for key in entry["spans"]} == {
        s.span_id for s in spans
    }


@pytest.mark.parametrize("ambiguous", [False, True])
def test_explicit_table_notes_require_a_unique_source_caption_target(ambiguous):
    caption = EvidenceBlock(
        block_id="caption",
        source="main",
        page=2,
        kind="text",
        text="Table S1. Reverse scans.",
    )
    table_block = EvidenceBlock(
        block_id="table",
        source="main",
        page=2,
        kind="table",
        text="A | 20",
        metadata={"context_block_ids": ["caption"]},
    )
    note = EvidenceBlock(
        block_id="note",
        source="main",
        page=3,
        kind="text",
        text="Note to Table S1: Irradiance was 100 mW/cm2.",
    )
    generic = EvidenceBlock(
        block_id="generic",
        source="main",
        page=2,
        kind="text",
        text="Note: All values use 50 mW/cm2.",
    )
    discussion = EvidenceBlock(
        block_id="discussion",
        source="main",
        page=4,
        kind="text",
        text="The results in Table S1 show an improvement.",
    )
    other_source = EvidenceBlock(
        block_id="other-source",
        source="supplement",
        page=1,
        kind="text",
        text="Table S1 note: A different experiment used 80 mW/cm2.",
    )
    other_table = EvidenceBlock(
        block_id="other-table-note",
        source="main",
        page=3,
        kind="text",
        text="Note to Table S2: A different experiment used 60 mW/cm2.",
    )
    blocks = [
        caption,
        table_block,
        note,
        generic,
        discussion,
        other_source,
        other_table,
    ]
    if ambiguous:
        blocks.append(
            table_block.model_copy(deep=True, update={"block_id": "second-table"})
        )

    _attach_explicit_table_notes(blocks)

    assert table_block.metadata["context_block_ids"] == (
        ["caption"] if ambiguous else ["caption", "note"]
    )
    assert table_block.metadata.get("explicit_table_note_ids", []) == (
        [] if ambiguous else ["note"]
    )
    # All source statements remain available. Unqualified notes and discussion
    # references do not acquire an inferred table owner.
    assert generic in blocks and discussion in blocks and other_source in blocks
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="A",
                label="A",
                role="individual_device",
                scope="target",
                evidence=[EvidenceCitation(block_id="table", quote="A | 20")],
            )
        ],
        claims=[],
    )
    # Use an SI copy to avoid the deliberate main opening-page metadata fallback.
    supplement = [
        b.model_copy(update={"source": "supplement"})
        for b in blocks
        if b != other_source
    ]
    selected = assembly_blocks(supplement, ledger)
    assert "generic" not in {b.block_id for b in selected}
    assert "discussion" not in {b.block_id for b in selected}
    assert "other-table-note" not in {b.block_id for b in selected}


@pytest.mark.parametrize("bad_metadata", [None, 7, {"note": True}, [None, {}]])
def test_malformed_optional_context_metadata_cannot_invent_evidence_links(bad_metadata):
    block = EvidenceBlock(
        block_id="table",
        source="main",
        page=1,
        kind="table",
        text="A | 10",
        metadata={"context_block_ids": bad_metadata},
    )
    assert evidence_payload([block])[0]["context_block_ids"] == []
