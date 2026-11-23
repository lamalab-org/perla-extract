"""A single-file experiment must preserve pairing, inputs and failed work."""

import csv
import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from examples.scoring.run import build_example
from perla_extract.study_extraction import experiment
from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.configuration import resolve_config, tomllib
from perla_extract.study_extraction.experiment_plan import (
    ExperimentSettings,
    _arm_settings,
)
from perla_extract.study_extraction.run_artifacts import file_digest, write_run_manifest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    original = tmp_path / "example"
    build_example(original)
    reference = original / "truth/dev/10.0000--synthetic"
    pdf = tmp_path / "paper with spaces.pdf"
    pdf.write_bytes(b"Synthetic placeholder; parsing is replaced in this unit test")
    manifest_path = reference / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest"]["source_sha256"] = [file_digest(pdf)]
    manifest_path.write_text(json.dumps(manifest))
    path = tmp_path / "experiment.toml"
    path.write_text(f"""
output = "results"
repeats = 2
[stages.default]
model = "openai/synthetic"
[extraction]
parser = "pymupdf"
max_model_calls = 3
use_claim_ledger = true
[control]
claim_recall_passes = 1
[treatment]
claim_recall_passes = 2
[[papers]]
pdf = {json.dumps(pdf.name)}
reference = {json.dumps(str(reference.relative_to(tmp_path)))}
""")
    calls = []

    def extract(config, *, fail=False):
        if not config.dry_run:
            calls.append(config)
        output = config.output_dir
        # The complete roster must already exist before observing any outcome.
        with (tmp_path / "results/runs.csv").open() as stream:
            settings = ExperimentSettings.model_validate(
                tomllib.loads(path.read_text())
            )
            assert len(list(csv.DictReader(stream))) == settings.repeats * len(
                settings.methods
            )
        output.mkdir(parents=True)
        report = {
            "status": "dry_run"
            if config.dry_run
            else ("failed" if fail else "complete"),
            "errors": ["Provider request timed out"] if fail else [],
            "elapsed_seconds": 1,
            "usage": {
                "live_calls": 1,
                "cache_hits": 0,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost": 1,
            },
            "budget": {
                "provider_requests": 1,
                "cost_tracking_complete": True,
                "planned_calls_fit": True,
            },
        }
        write_json_exclusive(output / "report.json", report)
        write_json_exclusive(
            output / "run_configuration.json",
            {
                "scientific_settings": config.scientific_settings(),
                "source_sha256": [
                    file_digest(source)
                    for source in (config.pdf, config.supplement)
                    if source is not None
                ],
                "evidence_spans_sha256": "a" * 64,
            },
        )
        if not fail or config.dry_run:
            for name in ("extraction.json", "document.json"):
                shutil.copyfile(
                    original / "predictions/corrected" / name, output / name
                )
        write_run_manifest(output)
        return report

    monkeypatch.setattr(experiment, "run_extraction", extract)
    return path, calls, extract


def test_check_explains_change_without_running_or_creating_output(setup):
    path, calls, _ = setup
    result = CliRunner().invoke(experiment.main, [str(path), "--check"])
    assert result.exit_code == 0, result.output
    assert "4 runs" in result.output
    assert "claim_recall_passes: 1 → 2" in result.output
    assert calls == []
    assert not (path.parent / "results").exists()


@pytest.mark.parametrize(
    "filename,paper_count", [("experiment.toml", 30), ("examples/experiment.toml", 1)]
)
def test_shipped_experiments_parse_and_use_docling_by_default(filename, paper_count):
    path = Path(__file__).resolve().parents[2] / filename
    settings = ExperimentSettings.model_validate(tomllib.loads(path.read_text()))
    assert (
        len(settings.papers) == paper_count
        if not settings.dataset
        else len(json.loads((path.parent / settings.dataset).read_text())["papers"])
        == paper_count
    )
    for arm in ("control", "treatment"):
        config = resolve_config(
            pdf=Path("paper.pdf"),
            **_arm_settings(settings, arm, path.parent),
        )
        assert config.parser == "docling"


def test_check_accepts_an_explicit_parser_ablation(setup):
    path, calls, _ = setup
    path.write_text(
        path.read_text().replace(
            "[treatment]\nclaim_recall_passes = 2",
            '[treatment]\nclaim_recall_passes = 1\nparser = "docling"',
        )
    )
    result = CliRunner().invoke(experiment.main, [str(path), "--check"])
    assert result.exit_code == 0, result.output
    assert 'parser: "pymupdf" → "docling"' in result.output
    assert calls == []
    assert not (path.parent / "results").exists()


def test_unreviewed_benchmark_finishes_with_an_automatic_diagnostic_result(setup):
    path, calls, _ = setup
    manifest_path = path.parent / "example/truth/dev/10.0000--synthetic/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["coverage"] = {}
    manifest_path.write_text(json.dumps(manifest))
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 4
    output = path.parent / "results"
    report = json.loads((output / "comparison.json").read_text())
    assert report["quality"] is None
    assert report["reference_agreement"]["status"] == "complete_roster"
    assert report["reference_agreement"]["mean_paired_fact_f1_delta"] == 0
    summary = (output / "summary.md").read_text()
    assert "Automatic reference-agreement difference" in summary
    assert "Comparison unavailable" not in summary
    assert "Reviewed-scope conclusion unavailable" in summary
    assert (output / "figure-data/paired_reference_agreement.csv").exists()
    assert json.loads((output / "errors.json").read_text()) == []


def test_one_command_runs_scores_and_compares_with_separate_caches(setup):
    path, calls, _ = setup
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 0, result.output
    assert len(calls) == 4
    assert len({call.model_cache_dir for call in calls}) == 4
    assert len({call.document_cache_dir for call in calls}) == 1
    output = path.parent / "results"
    report = json.loads((output / "comparison.json").read_text(encoding="utf-8"))
    assert report["quality_status"] == "ready"
    assert report["quality"]["mean_paired_fact_f1_delta"] == 0
    assert report["study"]["factors"] == {
        "claim_recall_passes": {"control": 1, "treatment": 2}
    }
    assert report["accounting"]["control"]["total_cost_usd"] == 2
    assert report["accounting"]["control"]["cost_per_completed_extraction_usd"] == 1
    assert report["accounting"]["control"]["projected_cost_usd"]["10000"] == 10000
    assert (output / "figure-data/cost_projection.csv").is_file()
    assert (output / "figure-data/paired_papers.csv").is_file()
    field_rows = list(csv.DictReader((output / "figure-data/field_scores.csv").open()))
    pce = [
        row
        for row in field_rows
        if row["field"] == "measurement:pce" and row["view"] == "attributed"
    ]
    assert len(pce) == 4
    assert all(row["tp"] == "1" and row["fp"] == row["fn"] == "0" for row in pce)
    assert (output / "figure-data/field_strata.csv").is_file()
    important = list(
        csv.DictReader((output / "figure-data/important_fields.csv").open())
    )
    assert [row for row in important if row["field"] == "measurement:pce"] == [
        row for row in field_rows if row["field"] == "measurement:pce"
    ]
    assert any(row["field"] == "material:stack" for row in important)
    assert (output / "experiment.toml").read_bytes() == path.read_bytes()
    summary = (output / "summary.md").read_text(encoding="utf-8")
    assert "Treatment − control: +0.0000" in summary
    assert "only one paper" in summary
    assert "same 2 paper/repetition pairs with completed, scored outputs" in summary
    assert "| control | 2 / 2 | 2 | 2.000000 |" in summary
    assert "Read first:" in result.output
    repeated = CliRunner().invoke(experiment.main, [str(path)])
    assert repeated.exit_code == 1
    assert "Output already exists" in repeated.output
    assert len(calls) == 4


def test_failed_run_stays_in_roster_and_accounting(setup, monkeypatch):
    path, calls, extract = setup
    monkeypatch.setattr(
        experiment, "run_extraction", lambda config: extract(config, fail=not calls)
    )
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 1
    assert len(calls) == 4
    report = json.loads(
        (path.parent / "results/comparison.json").read_text(encoding="utf-8")
    )
    assert len(report["runs"]) == 4
    failed = next(
        row for row in report["runs"] if row["accounting"]["status"] == "failed"
    )
    assert "Provider request timed out" in failed["issues"][0]
    assert "evaluation.json" not in failed["issues"][0]
    assert report["quality"] is None
    assert sum(arm["known_cost_usd"] for arm in report["accounting"].values()) == 4
    assert (
        len(
            json.loads(
                (path.parent / "results/errors.json").read_text(encoding="utf-8")
            )
        )
        == 1
    )
    summary = (path.parent / "results/summary.md").read_text(encoding="utf-8")
    assert "Reviewed-scope conclusion unavailable" in summary
    assert "Treatment − control:" not in summary
    assert "| control | 1 / 2 | 2 | 2.000000 |" in summary
    counts = report["accounting"][failed["arm"]]
    assert counts["cost_per_completed_extraction_usd"] == 2
    assert counts["projected_cost_usd"]["50000"] == 100000


def test_all_failed_attempts_have_no_automatic_effect_estimate(setup, monkeypatch):
    path, calls, extract = setup
    monkeypatch.setattr(
        experiment, "run_extraction", lambda config: extract(config, fail=True)
    )
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 1
    assert len(calls) == 4
    summary = (path.parent / "results/summary.md").read_text()
    assert "Comparison unavailable" in summary
    assert "Automatic reference-agreement difference" not in summary
    assert "Treatment − control:" not in summary
    report = json.loads((path.parent / "results/comparison.json").read_text())
    for counts in report["accounting"].values():
        assert counts["cost_per_completed_extraction_usd"] is None
        assert counts["projected_cost_usd"] == {"10000": None, "50000": None}


def test_source_mismatch_is_rejected_before_any_output_or_paid_work(setup):
    path, calls, _ = setup
    (path.parent / "paper with spaces.pdf").write_bytes(b"different paper")
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 1
    assert "do not match the reference sources" in result.output
    assert calls == []
    assert not (path.parent / "results").exists()


def test_explicit_paper_si_reference_match_is_shown_and_scored(setup):
    path, calls, _ = setup
    # Deliberately unrelated filenames: the TOML block declares the relationship.
    supplement = path.parent / "supporting measurements.pdf"
    supplement.write_bytes(b"Synthetic SI; parsing is replaced in this unit test")
    reference = path.parent / "example/truth/dev/10.0000--synthetic"
    manifest_path = reference / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest"]["source_sha256"].append(file_digest(supplement))
    manifest_path.write_text(json.dumps(manifest))
    path.write_text(
        path.read_text(encoding="utf-8")
        + f"supplement = {json.dumps(supplement.name)}\n"
    )

    checked = CliRunner().invoke(experiment.main, [str(path), "--check"])
    assert checked.exit_code == 0, checked.output
    assert checked.output.count("Ground truth:") == 1
    assert "Paper: paper with spaces.pdf" in checked.output
    assert "SI: supporting measurements.pdf" in checked.output
    assert f"Ground truth: {reference.relative_to(path.parent)}" in checked.output
    assert calls == []
    assert not (path.parent / "results").exists()

    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 0, result.output
    assert all(config.supplement == supplement for config in calls)
    report = json.loads(
        (path.parent / "results/comparison.json").read_text(encoding="utf-8")
    )
    assert report["quality_status"] == "ready"


@pytest.mark.parametrize("problem", ["missing", "wrong", "omitted"])
def test_bad_si_match_stops_before_any_output_or_paid_work(setup, problem):
    path, calls, _ = setup
    supplement = path.parent / "supporting measurements.pdf"
    supplement.write_bytes(b"Expected synthetic SI")
    manifest_path = path.parent / "example/truth/dev/10.0000--synthetic/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_manifest"]["source_sha256"].append(file_digest(supplement))
    manifest_path.write_text(json.dumps(manifest))
    if problem != "omitted":
        path.write_text(
            path.read_text(encoding="utf-8")
            + f"supplement = {json.dumps(supplement.name)}\n"
        )
    if problem == "missing":
        supplement.unlink()
    elif problem == "wrong":
        supplement.write_bytes(b"SI from a different paper")

    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 1
    expected = "source files missing" if problem == "missing" else "do not match"
    assert expected in result.output
    assert calls == []
    assert not (path.parent / "results").exists()


@pytest.mark.parametrize("change_after", [1, 4])
def test_changed_inputs_stop_remaining_calls_and_preserve_paid_work(
    setup, monkeypatch, change_after
):
    path, calls, extract = setup

    def change_after_first(config):
        report = extract(config)
        if len(calls) == change_after:
            path.write_text(
                path.read_text(encoding="utf-8") + "\n# changed during execution\n"
            )
        return report

    monkeypatch.setattr(experiment, "run_extraction", change_after_first)
    result = CliRunner().invoke(experiment.main, [str(path)])
    assert result.exit_code == 1
    assert "Input changed" in result.output
    assert len(calls) == change_after
    report = json.loads(
        (path.parent / "results/comparison.json").read_text(encoding="utf-8")
    )
    assert report["quality"] is None
    assert (
        sum(arm["known_cost_usd"] for arm in report["accounting"].values())
        == change_after
    )
    assert "Input changed" in (path.parent / "results/errors.json").read_text(
        encoding="utf-8"
    )


def test_endpoint_override_expands_both_arm_defaults_without_false_missing_fields(
    setup,
):
    path, _, _ = setup
    path.write_text(
        path.read_text(encoding="utf-8")
        + '\n[treatment.stages.read]\nmodel = "openai/other-reader"\n'
    )
    prepared = experiment.prepare(path)
    contrast = prepared.study.factors["deployments.read.model"]
    assert contrast.control == "openai/synthetic"
    assert contrast.treatment == "openai/other-reader"
    result = experiment.execute(prepared)
    assert result["quality_status"] == "ready"


def test_arm_model_switch_does_not_keep_shared_endpoint_or_credentials(setup):
    path, _, _ = setup
    text = path.read_text(encoding="utf-8").replace(
        'model = "openai/synthetic"',
        'model = "openai/synthetic"\nbase_url = "http://localhost:8000/v1"\napi_key_env = "LOCAL_KEY"',
    )
    path.write_text(text + '\n[treatment.stages.default]\nmodel = "openrouter/other"\n')
    prepared = experiment.prepare(path)
    control, treatment = prepared.attempts[:2]
    assert control.config.effective_deployment("read").api_key_env == "LOCAL_KEY"
    for stage in ("read", "assemble", "refine", "repair", "enrich"):
        deployment = treatment.config.effective_deployment(stage)
        assert deployment.model == "openrouter/other"
        assert deployment.base_url is None
        assert deployment.api_key_env is None


def test_repeating_the_effective_default_is_not_an_ablation(setup):
    path, _, _ = setup
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "[treatment]\nclaim_recall_passes = 2",
            '[treatment]\nclaim_recall_passes = 1\n[treatment.stages.read]\nmodel = "openai/synthetic"',
        )
    )
    with pytest.raises(ValueError, match="identical"):
        experiment.prepare(path)
