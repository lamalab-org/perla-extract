"""Keep cheap-workflow comparisons paired, complete, and honest about missing cost."""

import json

import pytest
from click.testing import CliRunner

from examples.ablations.report import main, paired_interval, summarize
from examples.scoring.run import quantity, seed_study
from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    RunEfficiency,
    evaluate_study,
)


@pytest.fixture
def plan(tmp_path):
    jobs = []
    references = {}
    for paper in ("paper-a", "paper-b"):
        for repeat in (1, 2):
            for arm in ("control", "treatment"):
                truth = seed_study()
                truth.performance_observations[0].metrics.append(quantity("FF", 80))
                prediction = truth.model_copy(deep=True)
                if arm == "control" or repeat == 1:
                    prediction.performance_observations[0].metrics[0] = quantity(
                        "PCE", 22
                    )
                if arm == "treatment" and repeat == 1:
                    prediction.performance_observations[0].metrics[1] = quantity(
                        "FF", 81
                    )
                report = evaluate_study(truth, prediction)
                report.benchmark = BenchmarkProvenance(
                    paper_id=paper,
                    split="dev",
                    ground_truth_sha256="0" * 64,
                    source_manifest_sha256=("a" if paper == "paper-a" else "b") * 64,
                )
                references[paper] = {
                    "benchmark": report.benchmark.model_dump(mode="json"),
                    "truth_content_sha256": report.truth_content_sha256,
                }
                report.run_efficiency = RunEfficiency(
                    status="complete",
                    live_calls=2,
                    cache_hits=0,
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                    cost_usd=2 if arm == "control" else 1,
                    cost_tracking_complete=True,
                    elapsed_seconds=3,
                )
                path = tmp_path / f"{paper}-{repeat}-{arm}-é.json"
                path.write_text(report.model_dump_json(), encoding="utf-8")
                configuration = {
                    "model": "provider/strong",
                    "claim_recall_passes": 2 if arm == "control" else 1,
                    "source_sha256": ["a" * 64, "b" * 64],
                }
                configuration_path = path.with_suffix(".configuration.json")
                configuration_path.write_text(
                    json.dumps(configuration), encoding="utf-8"
                )
                jobs.append(
                    {
                        "paper_id": paper,
                        "repeat": repeat,
                        "arm": arm,
                        "score": ["scorer", "--output", str(path)],
                        "score_output": str(path),
                        "run_configuration": str(configuration_path),
                        "expected_configuration": configuration,
                    }
                )
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 3,
                "jobs": jobs,
                "repeats": 2,
                "study": {"name": "test"},
                "revision": "test",
                "references": references,
                "reference_release": None,
                "scorer": {
                    "format_version": report.format_version,
                    "matcher_version": report.matcher_version,
                    "study_schema_sha256": report.study_schema_sha256,
                    "config": report.config.model_dump(mode="json"),
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def change_score(plan, edit):
    from pathlib import Path

    job = json.loads(plan.read_text(encoding="utf-8"))["jobs"][0]
    path = Path(job["score_output"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    edit(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pair_repeats_within_papers_and_report_actual_cost(plan):
    report = summarize(plan)
    assert report["quality_status"] == "ready"
    assert report["paper_count"] == 2  # Four pairs are not four independent papers.
    assert report["quality"]["mean_paired_macro_f1_delta"] == pytest.approx(0)
    assert len(report["quality"]["by_repeat"]) == 2
    assert report["quality"]["papers"][0]["repeat_deltas"] == pytest.approx(
        [-0.25, 0.25]
    )
    assert report["quality"]["paired_macro_f1_delta_interval"]["paper_count"] == 2
    assert report["accounting"]["control"]["total_cost_usd"] == 8
    assert report["recorded_cost_saving_fraction"] == 0.5


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_malformed_report_blocks_headline_not_diagnostics(
    plan, tmp_path, missing
):
    from pathlib import Path

    job = json.loads(plan.read_text(encoding="utf-8"))["jobs"][0]
    path = Path(job["score_output"])
    if missing:
        path.unlink()
    else:
        path.write_text("not json", encoding="utf-8")
    output = tmp_path / "summary.json"
    result = CliRunner().invoke(main, [str(plan), "--output", str(output)])
    assert result.exit_code != 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert len(report["runs"]) == 8
    assert report["quality"] is None
    assert report["accounting"]["control"]["total_cost_usd"] is None
    assert report["recorded_cost_saving_fraction"] is None


@pytest.mark.parametrize("field", ["truth_content_sha256", "study_schema_sha256"])
def test_incompatible_truth_or_schema_not_paired(plan, field):
    change_score(plan, lambda data: data.update({field: "c" * 64}))
    report = summarize(plan)
    assert report["issues"]
    assert report["quality"] is None


def test_incompatible_tolerances_block_headline(plan):
    change_score(
        plan, lambda data: data["config"].update(numeric_relative_tolerance=0.1)
    )
    assert summarize(plan)["quality"] is None


def test_unresolved_matching_blocks_headline(plan):
    change_score(
        plan, lambda data: data["core_facts"].update(scoring_status="needs_review")
    )
    assert summarize(plan)["quality"] is None


def test_cli_does_not_overwrite_original_artifacts(plan):
    original = plan.read_bytes()
    result = CliRunner().invoke(main, [str(plan), "--output", str(plan)])
    assert result.exit_code != 0
    assert plan.read_bytes() == original


@pytest.mark.parametrize("complete", [None, False])
def test_unknown_cost_is_not_zero_cost(plan, complete):
    change_score(
        plan,
        lambda data: data["run_efficiency"].update(cost_tracking_complete=complete),
    )
    report = summarize(plan)
    assert report["quality"] is not None
    assert report["accounting"]["control"]["recorded_scored_cost_usd"] == 8
    assert report["accounting"]["control"]["total_cost_usd"] is None
    assert report["recorded_cost_saving_fraction"] is None


def test_duplicate_or_unpaired_job_rejected(plan):
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["jobs"].pop()
    plan.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="both arms"):
        summarize(plan)


@pytest.mark.parametrize("field", ["truth_content_sha256", "tolerance"])
def test_agreement_between_arms_does_not_override_prepared_contract(plan, field):
    from pathlib import Path

    for job in json.loads(plan.read_text(encoding="utf-8"))["jobs"]:
        path = Path(job["score_output"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        if field == "tolerance":
            payload["config"]["numeric_relative_tolerance"] = 0.1
        else:
            payload[field] = "f" * 64
        path.write_text(json.dumps(payload), encoding="utf-8")
    result = summarize(plan)
    assert result["quality"] is None
    assert any("prepared" in issue for issue in result["issues"])


def test_paired_interval_has_known_limits_and_is_reproducible():
    identical = paired_interval([0.125, 0.125, 0.125], 500, 9)
    assert (
        identical["mean"] == identical["ci95_lower"] == identical["ci95_upper"] == 0.125
    )
    result = paired_interval([-0.25, 0.25], 500, 9)
    assert result == paired_interval([-0.25, 0.25], 500, 9)
    assert result["mean"] == 0
    assert result["ci95_lower"] == -0.25 and result["ci95_upper"] == 0.25
    assert paired_interval([0.125], 500, 9)["status"] == "insufficient_papers"
    assert paired_interval([0.125, 0.25], 0, 9)["status"] == "disabled"


def test_legacy_unvalidated_plan_cannot_claim_comparable_results(plan):
    payload = json.loads(plan.read_text(encoding="utf-8"))
    del payload["format_version"]
    plan.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="validated references"):
        summarize(plan)


def test_explicit_output_paths_do_not_depend_on_command_layout(plan):
    before = summarize(plan)
    payload = json.loads(plan.read_text(encoding="utf-8"))
    for job in payload["jobs"]:
        # Reporting reads its declared artifact, not the shape of a shell command.
        job["score"] = ["different-launcher", "--unrelated-flag"]
    plan.write_text(json.dumps(payload), encoding="utf-8")
    after = summarize(plan)
    before.pop("plan_sha256")
    after.pop("plan_sha256")
    assert after == before


def test_missing_explicit_output_path_is_a_clear_error(plan):
    payload = json.loads(plan.read_text(encoding="utf-8"))
    del payload["jobs"][0]["score_output"]
    plan.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="explicit score_output"):
        summarize(plan)


@pytest.mark.parametrize(
    "change", ["model", "claim_recall_passes", "source_sha256", "missing"]
)
def test_changed_or_missing_execution_configuration_blocks_comparison(plan, change):
    from pathlib import Path

    job = json.loads(plan.read_text(encoding="utf-8"))["jobs"][0]
    path = Path(job["run_configuration"])
    if change == "missing":
        path.unlink()
    else:
        configuration = json.loads(path.read_text(encoding="utf-8"))
        configuration[change] = {
            "model": "provider/other",
            "claim_recall_passes": 3,
            "source_sha256": ["c" * 64],
        }[change]
        path.write_text(json.dumps(configuration), encoding="utf-8")
    result = summarize(plan)
    assert result["quality"] is None
    assert any("configuration" in issue for issue in result["issues"])


def test_source_hash_order_does_not_change_configuration_identity(plan):
    from pathlib import Path

    job = json.loads(plan.read_text(encoding="utf-8"))["jobs"][0]
    path = Path(job["run_configuration"])
    configuration = json.loads(path.read_text(encoding="utf-8"))
    configuration["source_sha256"].reverse()
    path.write_text(json.dumps(configuration), encoding="utf-8")
    assert summarize(plan)["quality_status"] == "ready"
