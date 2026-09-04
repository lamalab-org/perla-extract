from __future__ import annotations

import json

import pytest

from review_workbench.figure_census import (
    CaptionPanelProposal,
    FigureProposalBatch,
    PaperFigureProposal,
    _validate_batch,
    caption_blocks,
)


def test_caption_blocks_selects_only_numbered_main_text_captions(tmp_path):
    document = {
        "blocks": [
            {
                "block_id": "main-1",
                "source": "main",
                "page": 3,
                "text": "Figure 2 | (a) J-V curves. (b) EQE spectra.",
            },
            {
                "block_id": "si-1",
                "source": "supplement",
                "page": 9,
                "text": "Fig. S1. Supporting spectrum.",
            },
            {
                "block_id": "reference-1",
                "source": "main",
                "page": 8,
                "text": "As shown in Fig. 2, performance improved.",
            },
        ]
    }
    path = tmp_path / "document.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    assert caption_blocks(path) == [
        {
            "caption_block_id": "main-1",
            "figure_number": "2",
            "page": 3,
            "caption": "Figure 2 | (a) J-V curves. (b) EQE spectra.",
        }
    ]


def test_classifier_batch_validation_rejects_missing_caption():
    panel = CaptionPanelProposal(
        caption_block_id="main-1",
        figure_number="1",
        panel_label="a",
        figure_class="jv",
        description="Current-density voltage curves.",
        x_axis_label=None,
        y_axis_label=None,
        data_presentation="plotted_values_only",
        extraction_feasibility="requires_digitization",
        schema_relevant=True,
    )
    result = FigureProposalBatch(
        papers=[PaperFigureProposal(paper_id="paper", panels=[panel])]
    )
    batch = [
        {
            "paper_id": "paper",
            "captions": [
                {
                    "caption_block_id": "main-1",
                    "figure_number": "1",
                    "page": 2,
                    "caption": "Fig. 1. (A) J-V curves.",
                },
                {
                    "caption_block_id": "main-2",
                    "figure_number": "2",
                    "page": 3,
                    "caption": "Fig. 2. Stability.",
                },
            ],
        }
    ]

    with pytest.raises(ValueError, match="omitted or invented captions"):
        _validate_batch(result, batch)
