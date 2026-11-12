"""Scientific comparisons must bind outputs, retain attempts, and resample papers."""

import csv
import json

import pytest
from click.testing import CliRunner

from examples.scoring.run import quantity, seed_study
from perla_extract.study_extraction.comparison import main, paired_interval, summarize
from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    evaluate_study,
)
from perla_extract.study_extraction.run_artifacts import (
    load_run_efficiency,
    verify_run_manifest,
    write_run_manifest,
)


@pytest.fixture
def roster(tmp_path):
    rows = []
    for i, paper in enumerate(("paper-a", "paper-b")):
        for repeat in (1, 2):
            for arm in ("control", "treatment"):
                directory = tmp_path / paper / arm / str(repeat)
                directory.mkdir(parents=True)
                source = ("a" if i == 0 else "b") * 64
                truth = seed_study()
                prediction = truth.model_copy(deep=True)
                if arm == "treatment":
                    prediction.performance_observations[0].metrics[0] = quantity(
                        "PCE", 22
                    )
                (directory / "extraction.json").write_text(prediction.model_dump_json())
                config = dict(
                    model="provider/model",
                    source_sha256=[source],
                    evidence_spans_sha256=source,
                    claim_recall_passes=2 if arm == "control" else 1,
                )
                (directory / "run_configuration.json").write_text(json.dumps(config))
                raw = dict(
                    status="complete",
                    run_id=f"{paper}-{arm}-{repeat}",
                    usage=dict(
                        live_calls=1,
                        cache_hits=0,
                        prompt_tokens=10,
                        completion_tokens=5,
                        total_tokens=15,
                        cost=2 if arm == "control" else 1,
                    ),
                    budget=dict(provider_requests=1, cost_tracking_complete=True),
                    elapsed_seconds=3,
                )
                (directory / "report.json").write_text(json.dumps(raw))
                manifest = write_run_manifest(directory)
                _, digest = verify_run_manifest(directory)
                score = evaluate_study(
                    truth,
                    prediction,
                    benchmark=BenchmarkProvenance(
                        paper_id=paper,
                        split="dev",
                        ground_truth_sha256="0" * 64,
                        source_manifest_sha256=source,
                        source_sha256=[source],
                    ),
                    run_efficiency=load_run_efficiency(directory / "report.json"),
                    run_manifest_sha256=digest,
                    run_id=manifest.run_id,
                )
                (directory / "evaluation.json").write_text(score.model_dump_json())
                rows.append(
                    dict(
                        paper_id=paper,
                        arm=arm,
                        repeat=repeat,
                        run_dir=str(directory.relative_to(tmp_path)),
                    )
                )
    path = tmp_path / "runs.csv"
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["paper_id", "arm", "repeat", "run_dir"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_complete_comparison_preserves_pairing_and_cost(roster):
    result = summarize(roster, factors=("claim_recall_passes",))
    assert result["quality_status"] == "ready"
    assert result["quality"]["mean_paired_macro_f1_delta"] == -0.5
    assert result["recorded_cost_saving_fraction"] == 0.5
    assert result["accounting"]["control"]["planned_runs"] == 4
    assert result["quality"]["paired_macro_f1_delta_interval"]["paper_count"] == 2


@pytest.mark.parametrize(
    "filename",
    ["evaluation.json", "extraction.json", "report.json", "run_configuration.json"],
)
def test_swapping_any_bound_artifact_cannot_reverse_conclusion(roster, filename):
    a = roster.parent / "paper-a" / "control" / "1" / filename
    b = roster.parent / "paper-a" / "treatment" / "1" / filename
    original_a, original_b = a.read_bytes(), b.read_bytes()
    a.write_bytes(original_b)
    b.write_bytes(original_a)
    result = summarize(roster)
    assert result["quality"] is None
    assert result["issues"]
    if filename in {"report.json", "run_configuration.json"}:
        assert result["accounting"]["control"]["total_cost_usd"] is None
    else:
        assert result["accounting"]["control"]["total_cost_usd"] == 8


def test_missing_score_retains_known_paid_cost(roster):
    (roster.parent / "paper-a/control/1/evaluation.json").unlink()
    result = summarize(roster)
    assert result["quality"] is None
    assert result["accounting"]["control"]["total_cost_usd"] == 8
    assert result["recorded_cost_saving_fraction"] == 0.5
    assert len(result["runs"]) == 8


def test_incomplete_accounting_is_not_free_or_a_complete_total(roster):
    directory = roster.parent / "paper-a/control/1"
    path = directory / "report.json"
    raw = json.loads(path.read_text())
    raw["budget"]["cost_tracking_complete"] = False
    path.write_text(json.dumps(raw))
    (directory / "run_manifest.json").unlink()
    write_run_manifest(directory)
    (directory / "evaluation.json").unlink()
    result = summarize(roster)
    assert result["accounting"]["control"]["known_cost_usd"] == 8
    assert result["accounting"]["control"]["total_cost_usd"] is None
    assert result["recorded_cost_saving_fraction"] is None


def test_factor_check_rejects_unrelated_intervention(roster):
    result = summarize(roster, factors=("use_refinement",))
    assert result["quality"] is None
    assert any("undeclared configuration" in issue for issue in result["issues"])


def test_repeat_means_do_not_create_extra_papers():
    assert paired_interval([0, 0], samples=100)["ci95_upper"] == 0
    assert paired_interval([0.3], samples=100)["status"] == "insufficient_papers"
    assert paired_interval([-0.2, 0.4], samples=0)["mean"] == pytest.approx(0.1)


def test_cli_retains_diagnostics_and_refuses_overwrite(roster):
    output = roster.parent / "comparison.json"
    runner = CliRunner()
    assert runner.invoke(main, [str(roster), "--output", str(output)]).exit_code == 0
    assert runner.invoke(main, [str(roster), "--output", str(output)]).exit_code != 0


def test_missing_pair_is_rejected_instead_of_dropped(roster):
    lines = roster.read_text().splitlines()
    roster.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(ValueError, match="both arms"):
        summarize(roster)


def test_plot_tables_retain_counts_pairs_and_missing_measurements(roster):
    from perla_extract.study_extraction.figure_data import export_tables

    result = summarize(roster)
    output = roster.parent / "tables"
    export_tables(result, output)
    with (output / "runs.csv").open() as stream:
        runs = list(csv.DictReader(stream))
    with (output / "paired_papers.csv").open() as stream:
        pairs = list(csv.DictReader(stream))
    with (output / "group_scores.csv").open() as stream:
        groups = list(csv.DictReader(stream))
    assert len(runs) == 8
    assert all(row["total_tokens"] == "" for row in runs)
    assert all(row["known_total_tokens"] == "15" for row in runs)
    assert len(pairs) == 2
    assert {float(row["mean_macro_f1_delta"]) for row in pairs} == {-0.5}
    assert len(groups) == 8 * 3 * 6
    (roster.parent / "paper-a/control/1/evaluation.json").unlink()
    export_tables(summarize(roster), roster.parent / "partial")
    with (roster.parent / "partial/runs.csv").open() as stream:
        partial = list(csv.DictReader(stream))
    missing = next(row for row in partial if row["quality_available"] == "False")
    assert missing["macro_f1"] == ""
    assert missing["known_cost_usd"] == "2.0"


@pytest.mark.parametrize("complete", [True, False, None])
def test_run_token_completeness_is_preserved_without_inference(tmp_path, complete):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "status": "partial",
                "elapsed_seconds": 1,
                "usage": {
                    "live_calls": 1,
                    "cache_hits": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "cost": 0,
                    "token_tracking_complete": complete,
                },
                "budget": {"cost_tracking_complete": False},
            }
        )
    )
    accounting = load_run_efficiency(report)
    assert accounting.token_tracking_complete is complete
    assert (
        accounting.total_tokens == 0
    )  # Retained known subtotal, never evidence of completeness.


def test_method_fingerprints_are_checked_with_nested_settings():
    from perla_extract.study_extraction.comparison import _settings

    before = {"scientific_settings": {"model": "provider/model"}, "prompt_sha256": "a"}
    after = {**before, "prompt_sha256": "b"}
    assert _settings(before)["prompt_sha256"] != _settings(after)["prompt_sha256"]


def test_copied_run_folder_is_not_an_independent_repeat(roster):
    import shutil

    source = roster.parent / "paper-a/control/1"
    target = roster.parent / "paper-a/control/2"
    shutil.rmtree(target)
    shutil.copytree(source, target)
    result = summarize(roster)
    assert result["quality"] is None
    assert any("duplicate run_id" in issue for issue in result["issues"])
    assert result["accounting"]["control"]["total_cost_usd"] is None
    assert result["accounting"]["control"]["known_cost_usd"] == 6


def add_accounting_attempt(
    roster, directory, *, paper="paper-a", arm="treatment", repeat=""
):
    with roster.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        row.setdefault("role", "paired")
    rows.append(
        {
            "paper_id": paper,
            "arm": arm,
            "repeat": repeat,
            "run_dir": str(directory.relative_to(roster.parent)),
            "role": "accounting_only",
        }
    )
    with roster.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["paper_id", "arm", "repeat", "run_dir", "role"]
        )
        writer.writeheader()
        writer.writerows(rows)


def failed_attempt(roster, *, complete=True):
    directory = roster.parent / "paper-a/treatment/failed-first-attempt"
    directory.mkdir()
    source = roster.parent / "paper-a/treatment/1"
    configuration = json.loads((source / "run_configuration.json").read_text())
    # Extra accounting is not an additional arm configuration or quality sample.
    configuration["model"] = "provider/earlier-attempt"
    (directory / "run_configuration.json").write_text(json.dumps(configuration))
    report = json.loads((source / "report.json").read_text())
    report.update(status="failed", run_id="failed-treatment-attempt")
    report["usage"]["cost"] = 3
    report["budget"]["cost_tracking_complete"] = complete
    (directory / "report.json").write_text(json.dumps(report))
    write_run_manifest(directory)
    return directory


def test_failed_extra_attempt_adds_spend_without_changing_paired_quality(roster):
    directory = failed_attempt(roster)
    add_accounting_attempt(roster, directory)
    result = summarize(roster, factors=("claim_recall_passes",))
    assert result["quality_status"] == "ready"
    assert result["issues"] == []
    assert result["paper_count"] == 2
    assert result["quality"]["mean_paired_macro_f1_delta"] == -0.5
    assert all(
        len(paper["repeat_deltas"]) == 2 for paper in result["quality"]["papers"]
    )
    assert result["accounting"]["treatment"]["total_cost_usd"] == 7
    assert result["accounting"]["treatment"]["planned_runs"] == 4
    assert result["accounting"]["treatment"]["listed_attempts"] == 5
    assert result["accounting"]["treatment"]["accounting_only_runs"] == 1
    assert result["recorded_cost_saving_fraction"] == 0.125
    extra = result["runs"][-1]
    assert extra["repeat"] is None
    assert extra["quality_applicable"] is False
    assert extra["quality_available"] is False
    assert extra["cost_available"] is True
    assert not (directory / "evaluation.json").exists()
    assert len(result["configuration_differences"]) == 4


def test_unknown_extra_spend_stays_unknown_without_erasing_quality(roster):
    add_accounting_attempt(roster, failed_attempt(roster, complete=False))
    result = summarize(roster)
    assert result["quality_status"] == "ready"
    assert result["quality"] is not None
    assert result["accounting"]["treatment"]["known_cost_usd"] == 7
    assert result["accounting"]["treatment"]["total_cost_usd"] is None
    assert result["recorded_cost_saving_fraction"] is None


def test_missing_extra_accounting_is_reported_separately_from_quality(roster):
    add_accounting_attempt(roster, roster.parent / "missing-attempt")
    result = summarize(roster)
    assert result["quality_status"] == "ready"
    assert result["quality"] is not None
    assert result["quality_issues"] == []
    assert result["accounting_issues"]
    assert result["issues"]
    assert result["accounting"]["treatment"]["total_cost_usd"] is None


def test_accounting_only_attempt_cannot_introduce_an_unpaired_paper(roster):
    add_accounting_attempt(roster, roster.parent / "extra", paper="unpaired-paper")
    with pytest.raises(ValueError, match="must appear in the paired roster"):
        summarize(roster)


def test_accounting_only_cannot_replace_a_missing_quality_pair(roster):
    directory = failed_attempt(roster)
    add_accounting_attempt(roster, directory)
    with roster.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["role"] = "accounting_only"
    with roster.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="both arms"):
        summarize(roster)


def test_accounting_copy_cannot_double_charge_an_existing_run(roster):
    import shutil

    directory = roster.parent / "copied-attempt"
    shutil.copytree(roster.parent / "paper-a/treatment/1", directory)
    add_accounting_attempt(roster, directory)
    result = summarize(roster)
    assert any("duplicate run_id" in issue for issue in result["issues"])
    assert result["accounting"]["treatment"]["known_cost_usd"] == 4
    assert result["accounting"]["treatment"]["total_cost_usd"] is None
    assert result["quality"] is None
