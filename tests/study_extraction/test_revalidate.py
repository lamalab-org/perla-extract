import json

from click.testing import CliRunner

from perla_extract.study_extraction.models import PaperMetadata, StudyExtraction
from perla_extract.study_extraction.revalidate import main, revalidate_run


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def test_revalidation_refreshes_only_derived_run_artifacts(tmp_path):
    run_dir = tmp_path / "paper"
    run_dir.mkdir()
    extraction = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )
    write_json(run_dir / "extraction.json", extraction.model_dump(mode="json"))
    write_json(run_dir / "document.json", {"blocks": []})
    write_json(run_dir / "claim_coverage_audit.json", {"status": "complete"})
    write_json(
        run_dir / "report.json",
        {
            "status": "complete_needs_review",
            "validation_issue_count": 99,
            "enrichment_status": "complete",
            "usage": {"cost": 1.25},
        },
    )

    report = revalidate_run(run_dir)

    assert report["status"] == "complete"
    assert report["validation_issue_count"] == 0
    assert report["usage"] == {"cost": 1.25}
    assert json.loads((run_dir / "validation.json").read_text())["status"] == "verified"
    assert json.loads((run_dir / "grounded_values.json").read_text()) == []


def test_revalidation_command_reports_bad_run_without_hiding_it(tmp_path):
    run_dir = tmp_path / "paper"
    run_dir.mkdir()
    (run_dir / "extraction.json").write_text("not json", encoding="utf-8")

    result = CliRunner().invoke(main, ["--runs-dir", str(tmp_path)])

    assert result.exit_code == 1
    assert "paper: cannot read" in result.output
