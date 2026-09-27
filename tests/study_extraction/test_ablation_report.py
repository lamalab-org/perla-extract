"""Keep cheap-workflow comparisons paired, complete, and honest about missing cost."""

import json

import pytest
from click.testing import CliRunner

from examples.ablations.report import main, summarize
from examples.scoring.run import seed_study
from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    RunEfficiency,
    evaluate_study,
)


@pytest.fixture
def plan(tmp_path):
    jobs = []
    for paper in ("paper-a", "paper-b"):
        for repeat in (1, 2):
            for arm in ("control", "treatment"):
                report = evaluate_study(seed_study(), seed_study())
                report.benchmark = BenchmarkProvenance(
                    paper_id=paper,
                    split="dev",
                    ground_truth_sha256="0" * 64,
                    source_manifest_sha256=("a" if paper == "paper-a" else "b") * 64,
                )
                report.core_facts.macro_f1 = (
                    0.9 if arm == "control" else (0.8 if repeat == 1 else 1.0)
                )
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
                jobs.append(
                    {
                        "paper_id": paper,
                        "repeat": repeat,
                        "arm": arm,
                        "score": ["scorer", "--output", str(path)],
                    }
                )
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {"jobs": jobs, "repeats": 2, "study": {"name": "test"}, "revision": "test"}
        ),
        encoding="utf-8",
    )
    return path


def change_score(plan, edit):
    from pathlib import Path

    job = json.loads(plan.read_text(encoding="utf-8"))["jobs"][0]
    path = Path(job["score"][-1])
    payload = json.loads(path.read_text(encoding="utf-8"))
    edit(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pair_repeats_within_papers_and_report_actual_cost(plan):
    report = summarize(plan)
    assert report["quality_status"] == "ready"
    assert report["paper_count"] == 2  # Four pairs are not four independent papers.
    assert report["quality"]["mean_paired_macro_f1_delta"] == pytest.approx(0)
    assert len(report["quality"]["by_repeat"]) == 2
    assert report["quality"]["papers"][0]["repeat_deltas"] == pytest.approx([-0.1, 0.1])
    assert report["accounting"]["control"]["total_cost_usd"] == 8
    assert report["recorded_cost_saving_fraction"] == 0.5


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_malformed_report_blocks_headline_not_diagnostics(
    plan, tmp_path, missing
):
    from pathlib import Path

    job = json.loads(plan.read_text(encoding="utf-8"))["jobs"][0]
    path = Path(job["score"][-1])
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
