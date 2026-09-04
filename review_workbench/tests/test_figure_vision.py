from __future__ import annotations

from review_workbench.figure_images import FigureImageManifest, RenderedFigure
from review_workbench.figure_vision import (
    VisibleAtomicValue,
    VisualFigureProposal,
    VisualPanelProposal,
    VisualPaperProposal,
    _validate_visual_response,
    _vision_prompt_content,
    build_review_proposal,
    validate_saved_figure_proposal,
)


def rendered_figure(tmp_path) -> RenderedFigure:
    image = tmp_path / "figure.png"
    image.write_bytes(b"png pixels")
    return RenderedFigure(
        figure_number="2",
        page=3,
        bbox=[10.0, 20.0, 100.0, 120.0],
        caption="Fig. 2. Device performance.",
        caption_block_id="main-caption-2",
        localization_method="docling_picture",
        image_path=str(image),
        image_sha256="a" * 64,
        width_pixels=500,
        height_pixels=400,
    )


def visual_result() -> VisualPaperProposal:
    return VisualPaperProposal(
        paper_id="paper",
        figures=[
            VisualFigureProposal(
                figure_number="2",
                image_sha256="a" * 64,
                panels=[
                    VisualPanelProposal(
                        panel_label="a",
                        panel_bbox_normalized=[0, 0, 500, 1000],
                        figure_class="jv",
                        description="Current-density voltage curve with printed metrics.",
                        x_axis_label="Voltage (V)",
                        y_axis_label="Current density (mA cm-2)",
                        data_presentation="explicit_numeric_labels",
                        extraction_feasibility="straightforward",
                        schema_relevant=True,
                        explicit_values=[
                            VisibleAtomicValue(
                                name="PCE",
                                raw_value="21.4%",
                                context="champion device",
                                schema_target="performance_observation",
                                presentation="printed_label",
                            )
                        ],
                        visual_notes=[],
                    )
                ],
            )
        ],
    )


def test_multimodal_prompt_binds_pixels_to_crop_hash(tmp_path):
    figure = rendered_figure(tmp_path)

    content = _vision_prompt_content("paper", [figure])

    assert content[1]["text"].find(figure.image_sha256) > 0
    assert content[2]["image_url"]["url"].startswith("data:image/png;base64,")


def test_review_proposal_never_declares_unmatched_value_figure_only(tmp_path):
    figure = rendered_figure(tmp_path)
    manifest = FigureImageManifest(
        format_version=1,
        pdf_path="paper.pdf",
        pdf_sha256="b" * 64,
        document_sha256="c" * 64,
        docling_version="test",
        dpi=180,
        margin_points=6,
        figures=[figure],
        captions_without_region=[],
    )
    result = visual_result()
    _validate_visual_response(result, "paper", [figure])

    proposal = build_review_proposal(
        result,
        manifest,
        {
            "blocks": [
                {
                    "block_id": "main-text-1",
                    "text": "The champion PCE was 21.4%.",
                }
            ]
        },
    )

    panel = proposal["panels"][0]
    assert panel["figure_only_atomic_values"] == 0
    assert panel["visual_candidates"][0]["text_comparison"] == "exact_text_match"
    assert panel["visual_candidates"][0]["matching_block_ids"] == ["main-text-1"]


def test_unmatched_visual_value_stays_needs_human_comparison(tmp_path):
    figure = rendered_figure(tmp_path)
    manifest = FigureImageManifest(
        format_version=1,
        pdf_path="paper.pdf",
        pdf_sha256="b" * 64,
        document_sha256="c" * 64,
        docling_version="test",
        dpi=180,
        margin_points=6,
        figures=[figure],
        captions_without_region=[],
    )

    proposal = build_review_proposal(visual_result(), manifest, {"blocks": []})

    candidate = proposal["panels"][0]["visual_candidates"][0]
    assert candidate["text_comparison"] == "needs_human_comparison"
    assert proposal["panels"][0]["figure_only_atomic_values"] == 0


def test_saved_proposal_is_revalidated_before_reuse(tmp_path):
    figure = rendered_figure(tmp_path)
    manifest = FigureImageManifest(
        format_version=1,
        pdf_path="paper.pdf",
        pdf_sha256="b" * 64,
        document_sha256="c" * 64,
        docling_version="test",
        dpi=180,
        margin_points=6,
        figures=[figure],
        captions_without_region=[],
    )
    artifact = {
        "format_version": 1,
        "vision_prompt_version": 1,
        "paper_id": "paper",
        "model": "model",
        "pdf_sha256": "b" * 64,
        "document_sha256": "c" * 64,
        "figures": [item.model_dump(mode="json") for item in visual_result().figures],
        "review_proposal": {"panels": [{"figure_number": "2"}]},
    }

    assert validate_saved_figure_proposal(
        artifact, paper_id="paper", manifest=manifest, model="model"
    ) == artifact["review_proposal"]

    artifact["figures"][0]["image_sha256"] = "d" * 64
    try:
        validate_saved_figure_proposal(
            artifact, paper_id="paper", manifest=manifest, model="model"
        )
    except ValueError as exc:
        assert "omitted, invented, or swapped" in str(exc)
    else:
        raise AssertionError("a crop hash mismatch must prevent reuse")
