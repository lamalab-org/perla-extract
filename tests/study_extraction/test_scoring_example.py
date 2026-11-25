"""Keep the public worked example executable through the real review/export path."""

import csv
import json
import subprocess
import sys

import pytest

from examples.scoring.run import build_example


def test_public_example_preserves_corrections_and_expected_scores(tmp_path):
    output = tmp_path / "example"
    summary = build_example(output)
    assert summary["passed"]
    assert summary["case_count"] == 5
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
        assert report["prediction_validation"]["status"] == (
            "needs_review" if name == "raw_parsed_conflict" else "verified"
        )
        assert report["run_efficiency"]["live_calls"] == 0
        nomad = json.loads(
            (output / "predictions" / name / "nomad_export.json").read_text()
        )
        mapping = next(
            item
            for item in nomad["mappings"]
            if item["source_kind"] == "performance_observation"
        )
        cell = nomad["archives"][mapping["archive_index"]]["data"]
        if name == "equivalent_units":
            assert cell["pce"] == 20
        if name == "raw_parsed_conflict":
            assert cell["pce"] is None
            assert cell["ff"] == 80
            value = json.loads(cell["additional_notes"])["reported_metrics"][0]
            assert (value["raw_value"], value["value_number"], value["unit"]) == (
                "20%",
                2,
                "%",
            )
            assert value["interpretation"]["status"] == "contradiction"
    comparison = json.loads((output / "comparison.json").read_text())
    assert comparison["quality_status"] == "ready"
    assert comparison["issues"] == []
    assert comparison["quality"]["mean_paired_fact_f1_delta"] == 1
    interval = comparison["quality"]["paired_fact_f1_delta_interval"]
    assert interval["status"] == "insufficient_papers"
    assert interval["ci95_lower"] is None and interval["ci95_upper"] is None
    with (output / "figure-data" / "paired_papers.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1
    assert rows[0]["paper_id"] == "10.0000--synthetic"
    with (output / "figure-data/subfield_scores.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        case = "seed" if row["arm"] == "control" else "corrected"
        report = json.loads(
            (output / "predictions" / case / "evaluation.json").read_text()
        )
        view = report["core_facts"]
        if row["view"] != "attributed":
            view = view[row["view"]]
        counts = view["subfields"][row["subfield"]]
        for key in ("matched", "predicted", "truth"):
            assert int(row[key]) == counts[key]
        assert row["quality_available"] == "True"
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
    with pytest.raises(
        ValueError, match="Scientific checks differ from expected results"
    ):
        build_example(tmp_path / "example")
    summary = json.loads((tmp_path / "example" / "summary.json").read_text())
    assert not summary["passed"]


def test_example_rejects_an_incorrect_export_expectation(tmp_path, monkeypatch):
    """An apparently correct score cannot hide an incorrect exported quantity."""
    from examples.scoring import run

    expected = json.loads(run.EXPECTED.read_text())
    expected["checks"]["raw_parsed_conflict"]["nomad_pce"] = 20
    changed = tmp_path / "expected.json"
    changed.write_text(json.dumps(expected))
    monkeypatch.setattr(run, "EXPECTED", changed)
    with pytest.raises(
        ValueError, match="Scientific checks differ from expected results"
    ):
        build_example(tmp_path / "example")
    summary = json.loads((tmp_path / "example" / "summary.json").read_text())
    assert summary["checks"]["raw_parsed_conflict"]["nomad_pce"] is None
    assert not summary["passed"]


def test_example_needs_no_provider_or_pdf_sdks(tmp_path):
    """Keep the documented lightweight environment sufficient for the full example."""
    script = """
import importlib.abc
import sys
from pathlib import Path

class NoOptionalSDKs(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        blocked = {
            'litellm', 'openai', 'docling', 'fitz', 'pymupdf', 'langfuse',
            'nomad', 'perovskite_solar_cell_database',
        }
        if fullname.split('.')[0] in blocked:
            raise ImportError('The offline example imported an optional SDK: ' + fullname)

sys.meta_path.insert(0, NoOptionalSDKs())
from examples.scoring.run import build_example
summary = build_example(Path(sys.argv[1]))
assert summary['passed'] and summary['case_count'] == 5
"""
    subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "example")],
        check=True,
        capture_output=True,
        text=True,
    )


def test_cli_rejects_reference_sources_that_do_not_match_the_sealed_run(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    from perla_extract.study_extraction import evaluation_cli
    from perla_extract.study_extraction.reference_io import load_reference

    example = tmp_path / "example"
    build_example(example)
    truth = example / "truth/dev/10.0000--synthetic"
    expected, benchmark = load_reference(truth)
    benchmark = benchmark.model_copy(update={"source_sha256": ["0" * 64]})
    monkeypatch.setattr(
        evaluation_cli, "load_reference", lambda _: (expected, benchmark)
    )
    output = tmp_path / "mismatched.json"
    result = CliRunner().invoke(
        evaluation_cli.main,
        [
            "--truth",
            str(truth),
            "--prediction",
            str(example / "predictions/corrected"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code != 0
    assert "source hashes differ" in result.output
    assert not output.exists()
