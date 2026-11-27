"""Check the geometry and evidence boundaries of the local figure experiment."""

import pymupdf
import pytest

from examples.parsing.figure_ocr import require_complete_checkpoint
from examples.parsing.figures import (
    figure_regions,
    probe_comparison,
    recover,
    represented,
)


def document(origin="BOTTOMLEFT"):
    return {
        "pages": {"1": {"size": {"width": 600, "height": 800}}},
        "texts": [{"text": "Figure 1. Forward and reverse scans."}],
        "pictures": [
            {
                "self_ref": "#/pictures/0",
                "captions": [{"$ref": "#/texts/0"}],
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 50,
                            "r": 400,
                            "t": 700 if origin == "BOTTOMLEFT" else 100,
                            "b": 400 if origin == "BOTTOMLEFT" else 400,
                            "coord_origin": origin,
                        },
                    }
                ],
            }
        ],
    }


def test_figure_coordinates_and_explicit_caption():
    bottom = figure_regions(document())[0]
    top = figure_regions(document("TOPLEFT"))[0]
    assert bottom == top
    assert bottom["bbox"] == [50, 100, 400, 400]
    assert bottom["caption"] == "Figure 1. Forward and reverse scans."
    with pytest.raises(ValueError, match="coordinate origin"):
        figure_regions(document("unknown"))


def test_native_recovery_keeps_color_location_and_distinct_occurrences():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=600, height=800)
        page.insert_text((100, 160), "16.22", color=(1, 0, 0))
        page.insert_text((100, 260), "16.22", color=(0, 0, 1))
        figures = recover(pdf, document(), [])
    spans = figures[0]["spans"]
    assert [s["color"] for s in spans] == ["#ff0000", "#0000ff"]
    assert spans[0]["bbox"] != spans[1]["bbox"]
    assert figures[0]["new_spans"] == 2


def test_text_elsewhere_does_not_hide_a_missing_figure_occurrence():
    span = {"text": "16.22", "bbox": [100, 140, 130, 160]}
    assert not represented(span, [{"text": "16.22", "bbox": [100, 240, 130, 260]}])
    assert not represented(span, [{"text": "16.22", "bbox": None}])
    assert represented(span, [{"text": "Forward | 16.22", "bbox": [90, 130, 140, 170]}])


def test_duplicate_picture_boxes_do_not_repeat_native_spans():
    doc = document()
    doc["pictures"].append(doc["pictures"][0] | {"self_ref": "#/pictures/1"})
    with pymupdf.open() as pdf:
        pdf.new_page(width=600, height=800).insert_text((100, 160), "16.22")
        figures = recover(pdf, doc, [])
    assert sum(len(f["spans"]) for f in figures) == 1


def test_recovery_preserves_baseline_and_never_credits_another_page():
    blocks = [{"page": 1, "text": "Original evidence"}]
    figures = [{"page": 2, "new_text": "16.22"}]
    job = {
        "source": "main",
        "probes": [
            {"source": "main", "page": 1, "literal": "16.22"},
            {"source": "main", "page": 1, "literal": "Original"},
        ],
    }
    probes = probe_comparison(blocks, figures, job)
    assert not probes[0]["supplemented"]
    assert probes[1]["baseline"] and probes[1]["supplemented"]
    assert blocks == [{"page": 1, "text": "Original evidence"}]
    assert probe_comparison(blocks, figures, job | {"page_range": [2, 2]}) == []


@pytest.mark.parametrize(
    "key", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"]
)
def test_ocr_cannot_run_with_an_incomplete_checkpoint(key):
    require_complete_checkpoint({"missing_keys": set(), "unexpected_keys": set()})
    with pytest.raises(RuntimeError, match="Incomplete checkpoint"):
        require_complete_checkpoint({key: ["language_model.weight"]})
