"""Keep the public worked example executable through the real review/export path."""

import json

import pytest

from examples.scoring.run import build_example


def test_public_example_preserves_corrections_and_expected_scores(tmp_path):
    output = tmp_path / "example"
    summary = build_example(output)
    assert summary["passed"]
    truth_dir = output / "truth" / "dev" / "10.0000--synthetic"
    events = json.loads((truth_dir / "review_events.json").read_text())
    assert {event["path"] for event in events if event.get("path")} >= {
        "/device_families/0/full_stack_raw",
        "/performance_observations/0/metrics/0",
        "/performance_observations/0/metrics/-",
    }
    seed = json.loads((truth_dir / "seed_extraction.json").read_text())
    assert seed["performance_observations"][0]["metrics"][0]["value_number"] == 21
    for name in summary["actual"]:
        report = json.loads(
            (output / "predictions" / name / "evaluation.json").read_text()
        )
        assert report["benchmark"]["paper_id"] == "10.0000--synthetic"
        assert report["prediction_validation"]["status"] == "verified"
        assert report["run_efficiency"]["live_calls"] == 0
    # Re-running must not overwrite the simulated review history or exports.
    with pytest.raises(FileExistsError):
        build_example(output)


def test_example_rejects_wrong_expectations_and_retains_reports(tmp_path, monkeypatch):
    """The demonstration must not generate expectations from its own scored output."""

    from examples.scoring import run

    expected = json.loads(run.EXPECTED.read_text())
    expected["cases"]["wrong_scan"]["performance"]["matched"] = 2
    changed = tmp_path / "expected.json"
    changed.write_text(json.dumps(expected))
    monkeypatch.setattr(run, "EXPECTED", changed)
    with pytest.raises(ValueError, match="Scoring differs from expected counts"):
        build_example(tmp_path / "example")
    summary = json.loads((tmp_path / "example" / "summary.json").read_text())
    assert not summary["passed"]
