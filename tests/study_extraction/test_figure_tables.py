from pathlib import Path

import pymupdf
import pytest

from perla_extract.study_extraction.configuration import ExtractionConfig
from perla_extract.study_extraction.figure_tables import SECTION_LABEL, figure_tables, section_label
from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.source import (
    _cache_identity,
    _with_figure_tables,
    parse_pdf,
)

PURPLE = (0.43, 0.0, 0.55)


def _figure_pdf(path: Path) -> Path:
    """One page with a J-V style inset table inside a multi-panel figure."""
    document = pymupdf.open()
    page = document.new_page(width=595, height=790)
    put = lambda x, y, text, color=(0, 0, 0): page.insert_text(  # noqa: E731
        (x, y), text, fontsize=6, color=color
    )
    put(86, 230, "VOC")
    put(105, 230, "JSC")
    put(127, 230, "FF")
    put(141, 230, "PCE")
    rows = [
        ("FS", "1.100", "26.54", "82.89", "24.18", (0, 0, 0)),
        ("RS", "1.110", "26.46", "84.16", "24.72", (0, 0, 0)),
        ("FS", "1.216", "26.48", "85.26", "27.45", PURPLE),
        ("RS", "1.215", "26.49", "85.84", "27.61", PURPLE),
    ]
    for index, (label, voc, jsc, ff, pce, color) in enumerate(rows):
        y = 250 + 8 * index
        put(74, y, label, color)
        put(85, y, voc, color)
        put(104, y, jsc, color)
        put(124, y, ff, color)
        put(141, y, pce, color)
    for index, tick in enumerate(("20", "15", "10", "5")):  # y-axis ticks beside the rows
        put(54, 232 + 20 * index, tick)
    for index, tick in enumerate(("0.2", "0.4", "0.6", "0.8", "1.0")):  # x-axis scale
        put(80 + 18 * index, 320, tick)
    put(300, 250, "1D (MAIm)PbI3 3.1 6.8 9.4")  # neighbouring panel, same height
    page.insert_text((45, 300), "Current density (mA cm-2)", fontsize=6, rotate=90)
    document.save(path)
    return path


def test_figure_table_rebuilds_inset_rows_with_labels_and_colours(tmp_path):
    pdf = _figure_pdf(tmp_path / "figure.pdf")

    items = figure_tables(pdf, [{"page": 1, "bbox": [30, 200, 570, 340]}])

    assert len(items) == 1
    assert items[0]["text"].splitlines() == [
        "VOC JSC FF PCE",
        "FS 1.100 26.54 82.89 24.18",
        "RS 1.110 26.46 84.16 24.72",
        "FS 1.216 26.48 85.26 27.45",
        "RS 1.215 26.49 85.84 27.61",
    ]
    colours = items[0]["metadata"]["row_text_colours"]
    assert colours[1] == colours[2] == "#000000"
    assert colours[3] == colours[4] != "#000000"
    assert items[0]["metadata"]["data_rows"] == 4


def test_figure_table_converts_docling_bottom_left_coordinates(tmp_path):
    pdf = _figure_pdf(tmp_path / "figure.pdf")
    # the same region as above, measured from the bottom of the 790 pt page
    region = {"page": 1, "bbox": [30, 790 - 200, 570, 790 - 340], "bottom_left_origin": True}

    assert len(figure_tables(pdf, [region])) == 1


def test_figures_without_tables_and_axis_scales_yield_nothing(tmp_path):
    pdf = _figure_pdf(tmp_path / "figure.pdf")

    assert figure_tables(pdf, [{"page": 1, "bbox": [30, 300, 570, 340]}]) == []
    assert figure_tables(pdf, [{"page": 5, "bbox": [0, 0, 10, 10]}]) == []


def test_figure_table_sits_after_its_caption_with_an_id_after_all_others(tmp_path):
    pdf = _figure_pdf(tmp_path / "figure.pdf")
    texts = ["paragraph before", "Fig. 2 | J-V curves of the champion devices.", "paragraph after"]
    blocks = [
        EvidenceBlock(
            block_id=f"main_p1_000{i}_text_x", source="main", page=1,
            section_path=["Results"], kind="text", text=text,
        )
        for i, text in enumerate(texts, start=1)
    ]
    figures = [{"page": 1, "bbox": [30, 200, 570, 340], "after": 1, "caption": texts[1]}]

    result = _with_figure_tables(pdf, "main", figures, blocks)

    assert [b.block_id for b in result if b.kind == "text"] == [b.block_id for b in blocks]
    table = result[2]
    assert table.kind == "figure_table"
    assert table.block_id.startswith("main_p1_0004_figure_table_")
    assert table.section_path[0] == "Results"
    assert table.section_path[-1].startswith("Fig. 2: numbers printed inside the figure")
    assert "FS 1.100 26.54 82.89 24.18" in table.text


def test_section_label_names_the_figure_from_its_caption():
    assert section_label("Fig. 3 | DICS architecture").startswith("Fig. 3:")
    assert section_label("Supplementary Figure 12. Stability").startswith("Supplementary Figure 12:")
    assert section_label("Extended Data Fig. 4a shows").startswith("Extended Data Fig. 4a:")
    assert section_label("") == SECTION_LABEL
    assert section_label(None) == SECTION_LABEL


def test_cache_identity_is_unchanged_unless_figure_tables_are_enabled():
    common = {"source_hash": "0" * 64, "source": "main", "parser": "docling", "dependency_versions": {}}

    assert "figure_table_parsing" not in _cache_identity(**common)
    assert _cache_identity(**common, figure_table_parsing=True)["figure_table_parsing"] is True


def test_figure_tables_require_docling(tmp_path):
    pdf = _figure_pdf(tmp_path / "figure.pdf")

    with pytest.raises(ValueError, match="docling"):
        parse_pdf(pdf, "main", parser="pymupdf", figure_table_parsing=True)
    with pytest.raises(ValueError, match="docling"):
        ExtractionConfig(pdf=pdf, parser="pymupdf", figure_table_parsing=True)
    assert ExtractionConfig(pdf=pdf).figure_table_parsing is False


def _page_pdf(path: Path, items) -> Path:
    """A page with text at the given (x, y, text) positions, fontsize 6."""
    document = pymupdf.open()
    page = document.new_page(width=595, height=790)
    for x, y, text in items:
        page.insert_text((x, y), text, fontsize=6)
    document.save(path)
    return path


def _table(pdf: Path, region=(0, 0, 595, 790)) -> list[str]:
    items = figure_tables(pdf, [{"page": 1, "bbox": list(region)}])
    return [item["text"].splitlines() for item in items]


def test_long_row_labels_and_trailing_units_are_kept(tmp_path):
    pdf = _page_pdf(tmp_path / "f.pdf", [
        (60, 200, "MTIm@p/MAIm@n (DICS)"), (150, 200, "26.44"), (175, 200, "1.192"), (200, 200, "27.11 %"),
        (60, 208, "Reference"), (150, 208, "26.38"), (175, 208, "1.102"), (200, 208, "24.08 %"),
    ])

    assert _table(pdf) == [[
        "MTIm@p/MAIm@n (DICS) 26.44 1.192 27.11 %",
        "Reference 26.38 1.102 24.08 %",
    ]]


def test_two_column_inset_table_is_found(tmp_path):
    rows = [("Voc (V)", "1.029", "1.011"), ("Jsc", "23.1", "22.8"), ("FF", "75.2", "74.9"), ("PCE", "17.9", "17.3")]
    items = [(80, 192, "Oven"), (110, 192, "NIR")]
    for index, (label, oven, nir) in enumerate(rows):
        items += [(50, 200 + 8 * index, label), (80, 200 + 8 * index, oven), (110, 200 + 8 * index, nir)]
    pdf = _page_pdf(tmp_path / "f.pdf", items)

    assert _table(pdf) == [["Oven NIR", "Voc (V) 1.029 1.011", "Jsc 23.1 22.8", "FF 75.2 74.9", "PCE 17.9 17.3"]]


def test_evenly_spaced_table_column_is_kept_but_an_axis_beyond_the_table_is_not(tmp_path):
    items = []
    for index, (conc, pce, ff) in enumerate((("0", "20.1", "78.2"), ("0.5", "21.4", "79.9"), ("1.0", "22.0", "80.3"), ("1.5", "21.2", "79.1"))):
        y = 300 + 8 * index
        items += [(100, y, conc), (125, y, pce), (150, y, ff)]
    # a y axis left of the table, continuing far above and below it
    for index, tick in enumerate(("30", "25", "20", "15", "10", "5", "0")):
        items.append((60, 276 + 8 * index, tick))
    pdf = _page_pdf(tmp_path / "f.pdf", items)

    assert _table(pdf) == [["0 20.1 78.2", "0.5 21.4 79.9", "1.0 22.0 80.3", "1.5 21.2 79.1"]]


def test_axis_scales_alone_never_form_a_table(tmp_path):
    items = []
    for row, y in enumerate((300, 308, 316)):  # stacked x axes, linear and logarithmic
        values = ("0.2", "0.4", "0.6", "0.8") if row != 1 else ("1", "10", "100", "1000")
        items += [(80 + 30 * k, y, v) for k, v in enumerate(values)]
    pdf = _page_pdf(tmp_path / "f.pdf", items)

    assert _table(pdf) == []


def test_widely_spaced_columns_stay_one_table_but_neighbouring_panels_split(tmp_path):
    items = []
    for index, values in enumerate((("1.10", "26.5", "82.9"), ("1.21", "26.4", "85.3"))):
        y = 400 + 8 * index
        items += [(60 + 45 * k, y, v) for k, v in enumerate(values)]  # wide but even spacing
    # another panel further right: an annotation and a label on the same baselines
    items += [(420, 400, "1D (MAIm)PbI3 3.1 Å"), (450, 408, "6.8 Å (120)")]
    pdf = _page_pdf(tmp_path / "f.pdf", items)

    assert _table(pdf) == [["1.10 26.5 82.9", "1.21 26.4 85.3"]]
