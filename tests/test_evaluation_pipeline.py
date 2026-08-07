import json

from perla_extract.pipeline import ExtractionPipeline


def test_missing_prediction_is_scored_as_empty(tmp_path):
    truth_dir = tmp_path / "ground_truth" / "test"
    prediction_dir = tmp_path / "predictions"
    truth_dir.mkdir(parents=True)
    prediction_dir.mkdir()
    truth = {
        "cells": [
            {
                "pce": {"value": 20.0, "unit": "%"},
                "layers": [{"name": "ITO", "functionality": "Substrate"}],
            }
        ]
    }
    (truth_dir / "paper.json").write_text(json.dumps(truth), encoding="utf-8")

    pipeline = ExtractionPipeline.__new__(ExtractionPipeline)
    metrics, recall, precision = pipeline._evaluate_multiple(
        prediction_dir, truth_dir
    )

    assert "paper.json" in metrics
    assert recall == 0.0
    assert precision == 0.0
