from __future__ import annotations

from review_workbench.figure_census_evaluation import evaluate_panels


def test_panel_detection_is_separate_from_matched_attribute_accuracy():
    proposed = [
        (
            "paper",
            {
                "figure_number": "1",
                "panel_label": "a",
                "figure_class": "jv",
                "schema_relevant": True,
            },
        ),
        (
            "paper",
            {
                "figure_number": "2",
                "panel_label": "",
                "figure_class": "other",
                "schema_relevant": False,
            },
        ),
    ]
    gold = [
        (
            "paper",
            {
                "figure_number": "1",
                "panel_label": "A",
                "figure_class": "eqe",
                "schema_relevant": True,
            },
        ),
        (
            "paper",
            {
                "figure_number": "3",
                "panel_label": "b",
                "figure_class": "stability",
                "schema_relevant": True,
            },
        ),
    ]

    result = evaluate_panels(proposed, gold)

    assert result["panel_detection"] == {
        "true_positive": 1,
        "false_positive": 1,
        "false_negative": 1,
        "precision": 0.5,
        "recall": 0.5,
        "f1": 0.5,
    }
    assert result["matched_panel_attributes"]["figure_class"]["accuracy"] == 0.0
    assert result["matched_panel_attributes"]["schema_relevant"]["accuracy"] == 1.0
    assert result["class_confusion"] == {"eqe -> jv": 1}


def test_duplicate_panel_identity_is_rejected():
    import pytest

    rows = [("paper", {"figure_number": "1", "panel_label": "a"})] * 2

    with pytest.raises(ValueError, match="duplicate panel"):
        evaluate_panels(rows, [])
