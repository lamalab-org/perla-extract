"""Frozen replay must preserve evidence, environments and one-attempt accounting."""

import csv
import json

import pytest

from examples.scoring import replay as replay_module
from examples.scoring.run import build_example


@pytest.fixture
def saved_case(tmp_path):
    example = tmp_path / "example"
    build_example(example)
    run = example / "predictions/corrected"
    roster = tmp_path / "runs.csv"
    with roster.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["paper_id", "run_directory"])
        writer.writeheader()
        writer.writerow({"paper_id": "10.0000--synthetic", "run_directory": str(run)})
    return roster, example / "truth/dev", run


def test_same_inputs_have_no_changed_decisions_or_undocumented_calls(
    saved_case, tmp_path
):
    roster, references, run = saved_case
    original = (run / "extraction.json").read_bytes()
    first = tmp_path / "baseline"
    baseline = replay_module.replay(roster, references, first)
    revised = tmp_path / "revised"
    result = replay_module.replay(roster, references, revised, first)
    assert baseline["scored"] == result["scored"] == 1
    assert result["unavailable"] == 0
    assert json.loads((revised / "decision-changes.json").read_text()) == []
    score = json.loads((revised / "0000/evaluation.json").read_text())
    assert score["core_facts"]["groups"]["performance"]["matched"] == 2
    assert score["run_efficiency"]["live_calls"] == 0
    assert (run / "extraction.json").read_bytes() == original


def test_replay_rejects_an_environment_change_before_writing(
    saved_case, tmp_path, monkeypatch
):
    roster, references, _ = saved_case
    first = tmp_path / "baseline"
    replay_module.replay(roster, references, first)
    monkeypatch.setattr(replay_module, "_environment", lambda: {"pint": "different"})
    destination = tmp_path / "changed"
    with pytest.raises(ValueError, match="same dependency environment"):
        replay_module.replay(roster, references, destination, first)
    assert not destination.exists()


def test_replay_retains_an_unavailable_attempt_when_its_seal_is_broken(
    saved_case, tmp_path
):
    roster, references, run = saved_case
    path = run / "extraction.json"
    path.write_text(path.read_text().replace('"20%"', '"21%"'))
    output = tmp_path / "replay"
    result = replay_module.replay(roster, references, output)
    assert result["scored"] == 0 and result["unavailable"] == 1
    assert "differs from manifest" in result["entries"][0]["error"]
    assert json.loads((output / "manifest.json").read_text())["unavailable"] == 1


def test_replay_rejects_a_changed_baseline_report(saved_case, tmp_path):
    roster, references, _ = saved_case
    first = tmp_path / "baseline"
    replay_module.replay(roster, references, first)
    report = first / "0000/evaluation.json"
    report.write_text(report.read_text() + "\n")
    result = replay_module.replay(roster, references, tmp_path / "revised", first)
    assert result["unavailable"] == 1
    assert "baseline evaluation differs" in result["entries"][0]["error"]


def test_copied_rows_cannot_be_counted_as_independent_attempts(saved_case, tmp_path):
    roster, references, _ = saved_case
    with roster.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    with roster.open("a", newline="", encoding="utf-8") as stream:
        csv.DictWriter(stream, fieldnames=list(rows[0])).writerow(rows[0])
    result = replay_module.replay(roster, references, tmp_path / "revised")
    assert result["scored"] == 1 and result["unavailable"] == 1
    assert "same saved run_id" in result["entries"][1]["error"]
