from __future__ import annotations

from types import SimpleNamespace

import pymupdf

from review_workbench.figure_images import (
    FigureRegion,
    geometry_match_score,
    pdf_rect_from_docling_bbox,
    render_figure_regions,
)


def test_docling_bottom_left_box_converts_to_pdf_top_left_coordinates():
    bbox = SimpleNamespace(l=10, t=180, r=90, b=120, coord_origin="BOTTOMLEFT")

    assert pdf_rect_from_docling_bbox(bbox, page_height=200) == [10, 20, 90, 80]


def test_rendered_crop_has_stable_provenance(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    document = pymupdf.open()
    page = document.new_page(width=200, height=200)
    page.draw_rect(pymupdf.Rect(20, 30, 120, 130), color=(0, 0, 0), fill=(1, 1, 1))
    page.insert_text((30, 70), "Figure data")
    document.save(pdf_path)
    document.close()
    region = FigureRegion(
        figure_number="1",
        page=1,
        bbox=[20.0, 30.0, 120.0, 130.0],
        caption="Fig. 1. Example.",
        caption_block_id="main-p1-caption",
        localization_method="docling_picture",
    )

    first = render_figure_regions(
        pdf_path, [region], tmp_path / "first", dpi=144, margin_points=0
    )[0]
    second = render_figure_regions(
        pdf_path, [region], tmp_path / "second", dpi=144, margin_points=0
    )[0]

    assert (first.width_pixels, first.height_pixels) == (200, 200)
    assert first.image_sha256 == second.image_sha256
    assert first.caption_block_id == "main-p1-caption"


def test_geometry_match_requires_caption_below_and_horizontal_overlap():
    picture = [70.0, 70.0, 525.0, 460.0]

    assert (
        geometry_match_score(picture, [72.0, 462.0, 527.0, 490.0], page_height=842)
        is not None
    )
    assert (
        geometry_match_score(picture, [72.0, 300.0, 527.0, 330.0], page_height=842)
        is None
    )
    assert (
        geometry_match_score(picture, [540.0, 462.0, 590.0, 490.0], page_height=842)
        is None
    )
