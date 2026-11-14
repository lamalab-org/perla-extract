"""Expert sampling must preserve denied credit, provenance and unknown judgments."""

import csv
import json

import pytest
from click.testing import CliRunner

from examples.scoring.run import build_example
from examples.scoring.sample_review import LABEL_COLUMNS, main, prepare


@pytest.fixture
def inputs(tmp_path):
    example = tmp_path / "example"
    build_example(example)
    return (
        example / "truth/dev/10.0000--synthetic",
        example / "predictions/wrong_scan",
    )


def test_sample_covers_both_credit_decisions_without_revealing_them(inputs, tmp_path):
    truth, prediction = inputs
    output = tmp_path / "sample"
    assert prepare(truth, prediction, output, per_stratum=1, seed=17) == 4
    with (output / "review/judgments.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert set(rows[0]) == set(LABEL_COLUMNS)
    assert all(not row[name] for row in rows for name in LABEL_COLUMNS[4:])
    assert "scorer_awarded_credit" not in (output / "review/judgments.csv").read_text()
    assert {path.name for path in (output / "review").iterdir()} == {
        "candidate.json",
        "reference.json",
        "judgments.csv",
    }
    key = json.loads((output / "analyst-key.json").read_text())
    assert {item["scorer_awarded_credit"] for item in key["items"]} == {True, False}
    assert {item["inclusion_probability"] for item in key["items"]} == {1, 0.5}
    assert sum(stratum["population"] for stratum in key["strata"]) == 6
    assert {item["side"] for item in key["items"]} == {"truth", "prediction"}
    assert any(
        item["side"] == "truth" and not item["scorer_awarded_credit"]
        for item in key["items"]
    )
    assert key["score_sha256"] and key["run_manifest_sha256"]
    assert {row["item_id"] for row in rows} == {
        item["item_id"] for item in key["items"]
    }
    second = tmp_path / "same-seed"
    prepare(truth, prediction, second, per_stratum=1, seed=17)
    assert (second / "analyst-key.json").read_bytes() == (
        output / "analyst-key.json"
    ).read_bytes()
    assert (second / "review/judgments.csv").read_bytes() == (
        output / "review/judgments.csv"
    ).read_bytes()


def test_stale_score_is_rejected_before_creating_review_material(inputs, tmp_path):
    truth, prediction = inputs
    path = prediction / "evaluation.json"
    score = json.loads(path.read_text())
    score["prediction_content_sha256"] = "0" * 64
    path.write_text(json.dumps(score))
    output = tmp_path / "stale"
    with pytest.raises(ValueError, match="Score does not match"):
        prepare(truth, prediction, output, per_stratum=2, seed=17)
    assert not output.exists()


def test_review_labels_are_never_overwritten(inputs, tmp_path):
    truth, prediction = inputs
    output = tmp_path / "sample"
    prepare(truth, prediction, output, per_stratum=2, seed=0)
    labels = output / "review/judgments.csv"
    labels.write_text("expert work already saved")
    result = CliRunner().invoke(
        main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(prediction),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "choose a new directory" in result.output
    assert "Traceback" not in result.output
    assert labels.read_text() == "expert work already saved"


def test_fresh_score_can_be_sampled_without_replacing_the_historical_report(
    inputs, tmp_path
):
    truth, prediction = inputs
    original = prediction / "evaluation.json"
    fresh = tmp_path / "rescored.json"
    fresh.write_bytes(original.read_bytes())
    original.write_text("Historical report kept separately")
    assert prepare(truth, prediction, tmp_path / "sample", 1, 17, fresh) == 4
    assert original.read_text() == "Historical report kept separately"
