from pathlib import Path

import pymupdf
import pytest

from perla_extract.study_extraction.configuration import ExtractionConfig
from perla_extract.study_extraction.figure_tables import (
    SECTION_LABEL,
    _label_values,
    _rows,
    figure_tables,
    section_label,
)
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


def _raster_figure_pdf(path: Path) -> Path:
    """The J-V figure above, stored as an image: no text layer at all."""
    vector = pymupdf.open(_figure_pdf(path.with_name("vector.pdf")))
    region = pymupdf.Rect(30, 200, 570, 340)
    image = vector[0].get_pixmap(clip=region, dpi=200).tobytes("png")
    document = pymupdf.open()
    document.new_page(width=595, height=790).insert_image(region, stream=image)
    document.save(path)
    return path


def test_image_figures_are_read_by_ocr_only_when_enabled(tmp_path):
    pytest.importorskip("rapidocr")
    pdf = _raster_figure_pdf(tmp_path / "raster.pdf")
    region = [{"page": 1, "bbox": [30, 200, 570, 340]}]

    assert figure_tables(pdf, region) == []
    items = figure_tables(pdf, region, ocr=True)

    assert len(items) == 1 and items[0]["read_by_ocr"] is True
    assert "FS 1.216 26.48 85.26 27.45" in items[0]["text"].splitlines()
    metadata = items[0]["metadata"]
    assert metadata["derived_from"] == "docling_figure_region+rapidocr_words"
    assert 0 < metadata["ocr_min_word_confidence"] <= 1
    colours = metadata["row_text_colours"]
    assert colours[1] == colours[2] != colours[3] == colours[4]


def test_text_layer_is_preferred_over_ocr(tmp_path):
    pdf = _figure_pdf(tmp_path / "figure.pdf")

    items = figure_tables(pdf, [{"page": 1, "bbox": [30, 200, 570, 340]}], ocr=True)

    assert [item["read_by_ocr"] for item in items] == [False]


def test_ocr_labels_and_cache_identity():
    common = {"source_hash": "0" * 64, "source": "main", "parser": "docling", "dependency_versions": {}}

    assert "misread" in section_label("Fig. 3 | x", ocr=True)
    assert section_label("Fig. 3 | x", ocr=True).startswith("Fig. 3:")
    assert "rapidocr" not in _cache_identity(**common)
    assert "rapidocr" in _cache_identity(**common, figure_table_parsing=True)


def test_ocr_resolution_is_part_of_the_cache_key_and_validated(tmp_path):
    common = {"source_hash": "0" * 64, "source": "main", "parser": "docling", "dependency_versions": {}}

    assert "figure_table_ocr_dpi" not in _cache_identity(**common, figure_table_ocr_dpi=600)
    assert _cache_identity(**common, figure_table_parsing=True)["figure_table_ocr_dpi"] == 300
    assert _cache_identity(**common, figure_table_parsing=True, figure_table_ocr_dpi=600)[
        "figure_table_ocr_dpi"
    ] == 600
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF")
    assert ExtractionConfig(pdf=pdf, figure_table_ocr_dpi=600).figure_table_ocr_dpi == 600
    for wrong in (50, 2000, 300.0):
        with pytest.raises(ValueError, match="figure_table_ocr_dpi"):
            ExtractionConfig(pdf=pdf, figure_table_ocr_dpi=wrong)


def test_legend_with_numbers_beside_the_table_stays_out_of_its_rows(tmp_path):
    # a legend left of the table, its entries on the table's lines and numbered like
    # the row labels ("FACl 20 mol%"), separated from the table by an empty plot area
    items = [(300, 192, "mol%"), (350, 192, "Jsc"), (400, 192, "Voc"), (450, 192, "PCE")]
    values = (("24.90", "1.016", "19.29"), ("23.46", "1.039", "18.19"), ("23.97", "1.030", "19.32"),
              ("24.36", "1.092", "20.28"))
    for index, (jsc, voc, pce) in enumerate(values):
        y = 200 + 10 * index
        items += [(285, y, f"FACl {10 * index} mol%"), (350, y, jsc), (400, y, voc), (450, y, pce)]
        items += [(130, y + 5, f"FACl {10 * index + 10} mol%")]  # legend, between the rows
    for index in range(1, 4):
        items.append((130, 200 + 10 * index, f"FACl {10 * index} mol%"))  # legend, on the rows
    pdf = _page_pdf(tmp_path / "f.pdf", items)

    (table,) = _table(pdf)
    assert table[1:] == [
        "FACl 0 mol% 24.90 1.016 19.29",
        "FACl 10 mol% 23.46 1.039 18.19",
        "FACl 20 mol% 23.97 1.030 19.32",
        "FACl 30 mol% 24.36 1.092 20.28",
    ]


# OCR words (page points) of a J-V figure with an inset table in panel A, recorded from
# a real image figure: the y axes of panels A and B and B's second axis run past it.
INSET_BESIDE_AXES = [
    (305, 79, 318, 93, 'B'),
    (89, 81, 100, 93, 'A'),
    (316, 91, 329, 101, '100'),
    (507, 91, 517, 101, '25'),
    (480, 104, 498, 114, '21.23'),
    (104, 110, 114, 120, '20'),
    (320, 115, 330, 125, '80'),
    (509, 115, 516, 125, '20'),
    (480, 127, 499, 136, '19.07'),
    (104, 136, 114, 146, '15'),
    (320, 139, 330, 149, '60'),
    (509, 139, 515, 149, '15'),
    (104, 161, 114, 171, '10'),
    (320, 163, 330, 173, '40'),
    (504, 163, 517, 173, '-10'),
    (172, 173, 182, 183, 'Voc'),
    (195, 174, 203, 183, 'Jsc'),
    (236, 174, 246, 181, 'PCE'),
    (219, 175, 224, 182, 'FF'),
    (172, 180, 180, 188, '(V)'),
    (187, 180, 196, 188, '(mA'),
    (198, 180, 210, 188, 'cm{2)'),
    (236, 181, 246, 188, '(%)'),
    (107, 186, 115, 196, '5'),
    (320, 187, 330, 197, '20'),
    (506, 187, 513, 197, '5'),
    (411, 189, 471, 198, 'ZnAl-MMO/TiO,'),
    (170, 190, 181, 199, '0.94'),
    (190, 190, 207, 199, '21.75'),
    (216, 190, 228, 199, '0.71'),
    (233, 190, 248, 199, '14.45'),
    (122, 191, 165, 198, 'ZnAl-MMO/TiO2'),
    (410, 202, 428, 212, 'Bare'),
    (430, 202, 445, 212, 'TiO2'),
    (131, 203, 143, 211, 'Bare'),
    (144, 203, 156, 211, 'TiO2'),
    (170, 203, 182, 212, '0.82'),
    (190, 203, 207, 212, '20.88'),
    (216, 203, 228, 212, '0.70'),
    (233, 203, 248, 212, '11.94'),
    (107, 211, 115, 220, '0'),
    (505, 211, 513, 220, '0'),
    (324, 212, 330, 220, '0'),
    (112, 219, 122, 229, '0.0'),
    (148, 219, 158, 229, '0.2'),
    (183, 219, 193, 229, '0.4'),
    (218, 219, 228, 229, '0.6'),
    (253, 219, 263, 229, '0.8'),
    (289, 219, 299, 229, '1.0'),
    (344, 220, 357, 230, '400'),
    (377, 220, 392, 230, '500'),
    (412, 220, 426, 230, '600'),
    (447, 220, 459, 230, '700'),
    (479, 220, 493, 230, '800'),
    (184, 232, 214, 244, 'Voltage'),
    (215, 232, 228, 244, '(V)'),
    (387, 235, 431, 245, 'Wavelength'),
    (433, 235, 452, 245, '(nm)'),
]


def test_tick_lines_of_neighbouring_axes_are_neither_rows_nor_headers():
    (table,) = _rows(INSET_BESIDE_AXES)

    assert [" ".join(w[4] for w in row) for row in table["rows"]] == [
        "Voc Jsc FF PCE",
        "(V) (mA cm{2) (%)",
        "ZnAl-MMO/TiO2 0.94 21.75 0.71 14.45",
        "Bare TiO2 0.82 20.88 0.70 11.94",
    ]


def test_legends_repeating_one_value_per_line_are_no_table(tmp_path):
    items = []
    for index, label in enumerate(("2 days", "6 days", "12 days", "20 days")):
        y = 200 + 9 * index
        items += [(80, y, label), (200, y, label), (320, y, label)]  # the same legend in three panels
    pdf = _page_pdf(tmp_path / "f.pdf", items)

    assert _table(pdf) == []


def _stack(lines, x=100, y=250):
    return [(x, y + 10 * i, text) for i, text in enumerate(lines)]


def test_label_value_stacks_of_one_device_are_read(tmp_path):
    pdf = _page_pdf(tmp_path / "stack.pdf", [
        (100, 240, "Champion"),
        *_stack(["PCE: 20.1%", "Voc: 1.15 V", "Jsc: 23.5 mA/cm2", "FF: 78.2%"]),
    ])

    assert _table(pdf) == [["Champion", "PCE: 20.1%", "Voc: 1.15 V", "Jsc: 23.5 mA/cm2", "FF: 78.2%"]]


def test_label_value_stacks_side_by_side_are_read_separately(tmp_path):
    left = _stack(["PCE = 20.1%", "Voc = 1.12 V", "Jsc = 23.1 mA/cm2", "FF = 77.5%"])
    right = _stack(["PCE = 22.4%", "Voc = 1.16 V", "Jsc = 23.9 mA/cm2", "FF = 80.6%"], x=200)
    pdf = _page_pdf(tmp_path / "pair.pdf", [(100, 240, "Control"), (200, 240, "Target"), *left, *right])

    assert [rows[0] for rows in _table(pdf)] == ["Control", "Target"]
    assert [len(rows) for rows in _table(pdf)] == [5, 5]


def test_attached_separators_and_split_exponents_from_ocr_are_read():
    def words(lines):
        found = []
        for i, line in enumerate(lines):
            x = 100
            for text in line.split():
                found.append((x, 250 + 10 * i, x + 6 * len(text), 258 + 10 * i, text))
                x += 6 * len(text) + 4
        return found

    tables = _label_values(words(["PCE:20.1%", "Voc=1.15V", "Jsc = 23.5 mA/cm 2", "FF:78.2%"]))

    assert [" ".join(w[4] for w in row) for row in tables[0]["rows"]] == [
        "PCE:20.1%", "Voc=1.15V", "Jsc = 23.5 mA/cm 2", "FF:78.2%",
    ]


def test_prose_expressions_and_repeated_labels_are_no_label_value_stack(tmp_path):
    pdf = _page_pdf(tmp_path / "noise.pdf", [
        *_stack(["a PCE of 20.05% (VOC: 1.22 V, JSC:", "20.71 mA cm-2, FF: 79.36%) was", "determined here"]),
        *_stack(["y = 0.52x + 2.1", "R2 = 0.993"], x=300),
        *_stack(["t = 0 h", "t = 100 h", "t = 500 h"], y=400),
        *_stack(["PCE: 20.1%", "Voc: 1.1"], x=300, y=400),  # two lines only
    ])

    assert _table(pdf) == []
