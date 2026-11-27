"""Scientific comparisons must bind outputs, retain attempts, and resample papers."""

import csv
import json

import pytest
from click.testing import CliRunner

from examples.scoring.run import quantity, seed_study, synthetic_coverage
from perla_extract.study_extraction.comparison import main, paired_interval, summarize
from perla_extract.study_extraction.evaluation import (
    BenchmarkProvenance,
    EvaluationReport,
    PredictionValidation,
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
                        coverage=synthetic_coverage(),
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
    path.with_suffix(".study.json").write_text(
        json.dumps(
            {
                "paper_ids": ["paper-a", "paper-b"],
                "repeats": 2,
                "factors": {"claim_recall_passes": {"control": 2, "treatment": 1}},
            }
        )
    )
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(
            stream, fieldnames=["paper_id", "arm", "repeat", "run_dir"]
        )
        writer.writeheader()
        writer.writerows(rows)
    return path


def test_complete_comparison_preserves_pairing_and_cost(roster):
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality_status"] == "ready"
    assert result["quality"]["mean_paired_fact_f1_delta"] == -0.5
    assert result["recorded_cost_saving_fraction"] == 0.5
    assert result["accounting"]["control"]["planned_runs"] == 4
    assert result["quality"]["paired_fact_f1_delta_interval"]["paper_count"] == 2
    assert result["reference_agreement"]["status"] == "complete_roster"
    assert result["reference_agreement"]["mean_paired_fact_f1_delta"] == -0.5


def test_unreviewed_benchmark_supports_automatic_diagnostic_comparison(roster):
    for path in roster.parent.glob("paper-*/*/*/evaluation.json"):
        report = json.loads(path.read_text())
        report["benchmark"]["coverage"] = {}
        report["interpretation"] = "reference_agreement"
        path.write_text(json.dumps(report))
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    diagnostic = result["reference_agreement"]
    assert diagnostic["status"] == "complete_roster"
    assert diagnostic["complete_pair_count"] == 4
    assert diagnostic["mean_paired_fact_f1_delta"] == -0.5
    assert diagnostic["paired_fact_f1_delta_interval"]["paper_count"] == 2
    for strict, code in ((False, 0), (True, 1)):
        output = roster.parent / f"diagnostic-{strict}.json"
        args = [
            str(roster),
            "--study",
            str(roster.with_suffix(".study.json")),
            "--output",
            str(output),
        ]
        if strict:
            args.append("--require-reviewed-quality")
        run = CliRunner().invoke(main, args)
        assert run.exit_code == code, run.output
        assert "Reference agreement: complete_roster" in run.output


def test_diagnostic_comparison_rejects_mixed_reference_coverage(roster):
    path = roster.parent / "paper-a/control/1/evaluation.json"
    report = json.loads(path.read_text())
    report["benchmark"]["coverage"] = {}
    report["interpretation"] = "reference_agreement"
    path.write_text(json.dumps(report))
    diagnostic = summarize(roster, study=roster.with_suffix(".study.json"))[
        "reference_agreement"
    ]
    assert diagnostic["status"] == "blocked"
    assert diagnostic["mean_paired_fact_f1_delta"] is None


def test_unreviewed_reference_retains_counts_but_cannot_supply_verified_quality(roster):
    path = roster.parent / "paper-a/control/1/evaluation.json"
    report = json.loads(path.read_text())
    report["benchmark"]["coverage"] = {}
    report["interpretation"] = "reference_agreement"
    path.write_text(json.dumps(report))
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert any("coverage" in issue for issue in result["quality_issues"])
    row = next(
        row
        for row in result["runs"]
        if row.get("interpretation") == "reference_agreement"
    )
    assert row["scientific_scores"]["micro"]["truth"] > 0
    assert not row["quality_available"]


def test_sealed_partial_output_cannot_turn_a_failed_attempt_into_completed_quality(
    roster,
):
    directory = roster.parent / "paper-a/control/1"
    path = directory / "report.json"
    raw = json.loads(path.read_text())
    raw["status"] = "failed"
    path.write_text(json.dumps(raw))
    (directory / "run_manifest.json").unlink()
    write_run_manifest(directory)
    _, digest = verify_run_manifest(directory)
    score_path = directory / "evaluation.json"
    score = EvaluationReport.model_validate_json(score_path.read_bytes())
    score.run_manifest_sha256 = digest
    score.run_efficiency = load_run_efficiency(path)
    score_path.write_text(score.model_dump_json())
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert result["accounting"]["control"]["total_cost_usd"] == 8
    assert result["accounting"]["control"]["completion_fraction"] == 0.75
    assert any(
        "partial output is diagnostic" in issue for issue in result["quality_issues"]
    )
    diagnostic = result["reference_agreement"]
    assert diagnostic["status"] == "conditional_on_complete_pairs"
    assert diagnostic["complete_pair_count"] == 3
    assert diagnostic["omitted_pairs"] == [dict(paper_id="paper-a", repeat=1)]
    assert diagnostic["papers"][0]["repeats"] == [2]


@pytest.mark.parametrize(
    "category,ready",
    [("structural", False), ("unspecified", False), ("citation", True)],
)
@pytest.mark.parametrize("status", ["complete", "complete_needs_review"])
def test_validation_categories_are_visible_and_only_safe_diagnostics_allow_comparison(
    roster, category, ready, status
):
    path = roster.parent / "paper-a/control/1/evaluation.json"
    score = EvaluationReport.model_validate_json(path.read_bytes())
    report_path = path.with_name("report.json")
    report = json.loads(report_path.read_text())
    report["status"] = status
    report_path.write_text(json.dumps(report))
    path.with_name("run_manifest.json").unlink()
    write_run_manifest(path.parent)
    score.run_manifest_sha256 = verify_run_manifest(path.parent)[1]
    score.run_efficiency = load_run_efficiency(report_path)
    score.prediction_validation = PredictionValidation(
        status="needs_review",
        counts={},
        issues=[dict(path="/some/field", reason="Test issue", category=category)],
    )
    path.write_text(score.model_dump_json())
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert (result["quality"] is not None) is ready
    assert (result["reference_agreement"]["status"] == "complete_roster") is ready
    assert result["accounting"]["control"]["completion_fraction"] == 1
    assert result["validation_coverage"] == dict(
        checked_runs=1, unavailable_runs=7, issues=1
    )


def test_completed_warning_status_requires_validation_details(roster):
    directory = roster.parent / "paper-a/control/1"
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text())
    report["status"] = "complete_needs_review"
    report_path.write_text(json.dumps(report))
    (directory / "run_manifest.json").unlink()
    write_run_manifest(directory)
    path = directory / "evaluation.json"
    score = EvaluationReport.model_validate_json(path.read_bytes())
    score.run_manifest_sha256 = verify_run_manifest(directory)[1]
    score.run_efficiency = load_run_efficiency(report_path)
    assert score.prediction_validation is None
    path.write_text(score.model_dump_json())
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["reference_agreement"]["status"] == "blocked"
    assert any("no validation details" in issue for issue in result["quality_issues"])
    assert result["accounting"]["control"]["completion_fraction"] == 1


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
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert result["reference_agreement"]["status"] == "blocked"
    assert result["reference_agreement"]["mean_paired_fact_f1_delta"] is None
    assert result["issues"]
    if filename in {"report.json", "run_configuration.json"}:
        assert result["accounting"]["control"]["total_cost_usd"] is None
    else:
        assert result["accounting"]["control"]["total_cost_usd"] == 8


def test_missing_score_retains_known_paid_cost(roster):
    (roster.parent / "paper-a/control/1/evaluation.json").unlink()
    result = summarize(roster, study=roster.with_suffix(".study.json"))
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
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["accounting"]["control"]["known_cost_usd"] == 8
    assert result["accounting"]["control"]["total_cost_usd"] is None
    assert result["accounting"]["control"]["cost_per_completed_extraction_usd"] is None
    assert result["accounting"]["control"]["projected_cost_usd"]["10000"] is None
    assert result["recorded_cost_saving_fraction"] is None


def test_factor_check_rejects_unrelated_intervention(roster):
    wrong_factor(roster)
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert result["reference_agreement"]["status"] == "blocked"
    assert any("undeclared configuration" in issue for issue in result["issues"])


def reseal_configuration(directory, **updates):
    """Bind a synthetic method change without breaking unrelated integrity checks."""
    path = directory / "run_configuration.json"
    configuration = json.loads(path.read_text())
    configuration.update(updates)
    path.write_text(json.dumps(configuration))
    (directory / "run_manifest.json").unlink()
    write_run_manifest(directory)
    score_path = directory / "evaluation.json"
    score = json.loads(score_path.read_text())
    score["run_manifest_sha256"] = verify_run_manifest(directory)[1]
    score_path.write_text(json.dumps(score))


@pytest.fixture
def parser_roster(roster):
    for directory in roster.parent.glob("paper-*/*/*"):
        treatment = directory.parent.name == "treatment"
        dependencies = {"PyMuPDF": "1.27.2"}
        if treatment:
            dependencies["docling"] = "2.150.0"
        reseal_configuration(
            directory,
            claim_recall_passes=2,
            parser="docling" if treatment else "pymupdf",
            parser_dependencies=dependencies,
            evidence_spans_sha256=("d" if treatment else "c") * 64,
        )
    path = roster.with_suffix(".study.json")
    study = json.loads(path.read_text())
    study["factors"] = {"parser": {"control": "pymupdf", "treatment": "docling"}}
    path.write_text(json.dumps(study))
    return roster


def test_declared_parser_comparison_uses_same_pdfs_with_different_evidence(
    parser_roster,
):
    result = summarize(parser_roster, study=parser_roster.with_suffix(".study.json"))
    assert result["quality_status"] == "ready"
    assert result["reference_agreement"]["status"] == "complete_roster"
    assert all(
        d["fields"]["parser_dependencies.docling"]["treatment"] == "2.150.0"
        for d in result["configuration_differences"]
    )


@pytest.mark.parametrize(
    "updates,expected",
    [
        ({"evidence_spans_sha256": None}, "evidence fingerprint"),
        ({"parser_dependencies": {}}, "dependency fingerprints"),
        (
            {"parser_dependencies": {"PyMuPDF": "changed", "docling": "2.150.0"}},
            "undeclared configuration",
        ),
        ({"prompt_sha256": "changed"}, "undeclared configuration"),
        ({"source_sha256": ["e" * 64]}, "source"),
    ],
)
def test_parser_contrast_preserves_other_integrity_checks(
    parser_roster, updates, expected
):
    reseal_configuration(parser_roster.parent / "paper-a/treatment/1", **updates)
    result = summarize(parser_roster, study=parser_roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert result["reference_agreement"]["status"] == "blocked"
    assert any(expected in issue.lower() for issue in result["issues"])


def test_distinct_parsed_evidence_requires_a_declared_parser_factor(parser_roster):
    path = parser_roster.with_suffix(".study.json")
    study = json.loads(path.read_text())
    study["factors"] = {}
    path.write_text(json.dumps(study))
    result = summarize(parser_roster, study=path)
    assert result["reference_agreement"]["status"] == "blocked"
    assert any("same evidence_spans_sha256" in issue for issue in result["issues"])


def wrong_factor(roster):
    path = roster.with_suffix(".study.json")
    study = json.loads(path.read_text())
    study["factors"] = {"use_refinement": {"control": True, "treatment": False}}
    path.write_text(json.dumps(study))


def test_repeat_means_do_not_create_extra_papers():
    assert paired_interval([0, 0], samples=100)["ci95_upper"] == 0
    assert paired_interval([0.3], samples=100)["status"] == "insufficient_papers"
    assert paired_interval([-0.2, 0.4], samples=0)["mean"] == pytest.approx(0.1)


def test_cli_retains_diagnostics_and_refuses_overwrite(roster):
    output = roster.parent / "comparison.json"
    runner = CliRunner()
    assert (
        runner.invoke(
            main,
            [
                str(roster),
                "--study",
                str(roster.with_suffix(".study.json")),
                "--output",
                str(output),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            main,
            [
                str(roster),
                "--study",
                str(roster.with_suffix(".study.json")),
                "--output",
                str(output),
            ],
        ).exit_code
        != 0
    )


def test_cli_explains_a_rejected_comparison_without_opening_the_report(roster):
    wrong_factor(roster)
    output = roster.parent / "rejected.json"
    result = CliRunner().invoke(
        main,
        [
            str(roster),
            "--study",
            str(roster.with_suffix(".study.json")),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 1
    assert "study factor use_refinement expected" in result.output
    assert f"Full diagnostics: {output}" in result.output
    saved = json.loads(output.read_text())
    assert any(
        "undeclared configuration differences: claim_recall_passes" in issue
        for issue in saved["issues"]
    )
    assert saved["quality"] is None
    assert saved["accounting"]["control"]["known_cost_usd"] == 8


def test_missing_pair_is_rejected_instead_of_dropped(roster):
    lines = roster.read_text().splitlines()
    roster.write_text("\n".join(lines[:-1]) + "\n")
    with pytest.raises(ValueError, match="both arms"):
        summarize(roster, study=roster.with_suffix(".study.json"))


def test_plot_tables_retain_counts_pairs_and_missing_measurements(roster):
    from perla_extract.study_extraction.figure_data import export_tables

    result = summarize(roster, study=roster.with_suffix(".study.json"))
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
    assert {float(row["mean_fact_f1_delta"]) for row in pairs} == {-0.5}
    assert len(groups) == 8 * 2 * 4
    (roster.parent / "paper-a/control/1/evaluation.json").unlink()
    export_tables(
        summarize(roster, study=roster.with_suffix(".study.json")),
        roster.parent / "partial",
    )
    with (roster.parent / "partial/runs.csv").open() as stream:
        partial = list(csv.DictReader(stream))
    missing = next(row for row in partial if row["quality_available"] == "False")
    assert missing["fact_f1"] == ""
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
    result = summarize(roster, study=roster.with_suffix(".study.json"))
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
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality_status"] == "ready"
    assert result["reference_agreement"]["status"] == "complete_roster"
    assert result["issues"] == []
    assert result["paper_count"] == 2
    assert result["quality"]["mean_paired_fact_f1_delta"] == -0.5
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
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality_status"] == "ready"
    assert result["quality"] is not None
    assert result["accounting"]["treatment"]["known_cost_usd"] == 7
    assert result["accounting"]["treatment"]["total_cost_usd"] is None
    assert result["recorded_cost_saving_fraction"] is None


def test_missing_extra_accounting_is_reported_separately_from_quality(roster):
    add_accounting_attempt(roster, roster.parent / "missing-attempt")
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality_status"] == "ready"
    assert result["quality"] is not None
    assert result["quality_issues"] == []
    assert result["accounting_issues"]
    assert result["issues"]
    assert result["accounting"]["treatment"]["total_cost_usd"] is None


def test_accounting_only_attempt_cannot_introduce_an_unpaired_paper(roster):
    add_accounting_attempt(roster, roster.parent / "extra", paper="unpaired-paper")
    with pytest.raises(ValueError, match="must appear in the paired roster"):
        summarize(roster, study=roster.with_suffix(".study.json"))


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
        summarize(roster, study=roster.with_suffix(".study.json"))


def test_accounting_copy_cannot_double_charge_an_existing_run(roster):
    import shutil

    directory = roster.parent / "copied-attempt"
    shutil.copytree(roster.parent / "paper-a/treatment/1", directory)
    add_accounting_attempt(roster, directory)
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert any("duplicate run_id" in issue for issue in result["issues"])
    assert result["accounting"]["treatment"]["known_cost_usd"] == 4
    assert result["accounting"]["treatment"]["total_cost_usd"] is None
    assert result["quality"] is None


def rewrite_roster(roster, transform):
    with roster.open(newline="") as stream:
        reader = csv.DictReader(stream)
        fields, rows = reader.fieldnames, list(reader)
    with roster.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(transform(rows))


@pytest.mark.parametrize("omission", ["paper", "repeat"])
def test_whole_omitted_pair_blocks_quality_and_complete_cost(roster, omission):
    rewrite_roster(
        roster,
        lambda rows: [
            row
            for row in rows
            if (
                row["paper_id"] != "paper-b"
                if omission == "paper"
                else row["repeat"] != "2"
            )
        ],
    )
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert any(
        "Missing study paper/repeat pairs" in issue for issue in result["issues"]
    )
    assert result["accounting"]["control"]["planned_runs"] == 4
    assert result["accounting"]["control"]["listed_paired_runs"] == 2
    assert result["accounting"]["control"]["known_cost_usd"] == 4
    assert result["accounting"]["control"]["total_cost_usd"] is None
    assert result["recorded_cost_saving_fraction"] is None


@pytest.mark.parametrize("paper", ["paper-b", None])
def test_reversed_arm_direction_is_rejected_for_one_or_all_papers(roster, paper):
    rewrite_roster(
        roster,
        lambda rows: [
            {**row, "arm": "control" if row["arm"] == "treatment" else "treatment"}
            if paper is None or row["paper_id"] == paper
            else row
            for row in rows
        ],
    )
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert result["recorded_cost_saving_fraction"] is None
    assert any(
        "study factor claim_recall_passes expected 2, found 1" in issue
        for issue in result["issues"]
    )
    # Valid expenditure remains observable even when quality is uninterpretable.
    assert result["accounting"]["control"]["known_cost_usd"] > 0


def test_extra_pairs_do_not_silently_expand_the_declared_cohort(roster):
    path = roster.with_suffix(".study.json")
    study = json.loads(path.read_text())
    study["paper_ids"] = ["paper-a"]
    path.write_text(json.dumps(study))
    result = summarize(roster, study=path)
    assert result["quality"] is None
    assert any(
        "Unexpected study paper/repeat pairs: paper-b/1, paper-b/2" in issue
        for issue in result["issues"]
    )


def test_method_drift_in_both_arms_cannot_hide_between_papers(roster):
    for arm in ("control", "treatment"):
        for repeat in (1, 2):
            directory = roster.parent / "paper-b" / arm / str(repeat)
            path = directory / "run_configuration.json"
            configuration = json.loads(path.read_text())
            configuration["model"] = "provider/different-model"
            path.write_text(json.dumps(configuration))
            (directory / "run_manifest.json").unlink()
            write_run_manifest(directory)
            _, digest = verify_run_manifest(directory)
            score_path = directory / "evaluation.json"
            score = json.loads(score_path.read_text())
            score["run_manifest_sha256"] = digest
            score_path.write_text(json.dumps(score))
    result = summarize(roster, study=roster.with_suffix(".study.json"))
    assert result["quality"] is None
    assert all(row["quality_available"] for row in result["runs"])
    assert any(
        "settings differ from paper-a/1/control: model" in issue
        for issue in result["issues"]
    )


def test_study_is_required_and_exported_with_its_hash(roster):
    output = roster.parent / "missing-study.json"
    result = CliRunner().invoke(main, [str(roster), "--output", str(output)])
    assert result.exit_code == 2
    assert "--study" in result.output
    assert not output.exists()
    path = roster.with_suffix(".study.json")
    report = summarize(roster, study=path)
    from perla_extract.study_extraction.run_artifacts import file_digest

    assert report["study"] == json.loads(path.read_text())
    assert report["study_sha256"] == file_digest(path)
    assert report["accounting"]["control"]["completion_fraction"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"paper_ids": ["paper-a", "paper-a"]},
        {"paper_ids": []},
        {"repeats": True},
        {"repeats": 0},
        {"repeat": 2},
        {"factors": {"claim_recall_passes": {"control": 1, "treatment": 1}}},
        {"factors": {"claim_recall_passes": {"control": 2, "treament": 1}}},
    ],
)
def test_invalid_study_is_rejected_without_guessing(roster, change):
    path = roster.with_suffix(".study.json")
    study = json.loads(path.read_text())
    study.update(change)
    path.write_text(json.dumps(study))
    with pytest.raises(ValueError):
        summarize(roster, study=path)


def test_setting_comparison_preserves_types_without_inventing_numeric_changes():
    from perla_extract.study_extraction.comparison_study import (
        FactorContrast,
        same_setting,
    )

    assert same_setting({"temperature": 0}, {"temperature": 0.0})
    assert not same_setting([True], [1])
    assert not same_setting({"enabled": False}, {"enabled": 0})
    with pytest.raises(ValueError, match="must differ"):
        FactorContrast(control=1, treatment=1.0)
    with pytest.raises(ValueError):
        FactorContrast(control=1, treatment=float("nan"))
