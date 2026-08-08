import json

from review_workbench.score_revisions import score_model


def test_revision_scoring_reports_fact_and_device_metrics(tmp_path):
    truth_dir = tmp_path / "ground_truth" / "test"
    extraction_dir = tmp_path / "extractions" / "model"
    truth_dir.mkdir(parents=True)
    extraction_dir.mkdir(parents=True)
    payload = {
        "cells": [
            {
                "pce": {"value": 20.0, "unit": "%"},
                "layers": [],
            }
        ]
    }
    (truth_dir / "paper.json").write_text(json.dumps(payload), encoding="utf-8")
    (extraction_dir / "paper.json").write_text(json.dumps(payload), encoding="utf-8")

    result = score_model(extraction_dir, truth_dir)

    assert result["papers"] == 1
    assert result["truth_devices"] == 1
    assert result["matched_devices"] == 1
    assert result["missing_devices"] == 0
    assert result["precision"] == 1
    assert result["recall"] == 1
    assert result["f1"] == 1
