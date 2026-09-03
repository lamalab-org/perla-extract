import json

import pytest
from click.testing import CliRunner

import perla_extract.study_extraction.cohort as cohort
from perla_extract.study_extraction.cohort import (
    CohortManifest,
    _completed_run_matches,
    _load_manifest,
    _supplement_path,
)
from perla_extract.study_extraction.models import study_schema_sha256
from perla_extract.study_extraction.workflow import prompt_sha256


def manifest() -> CohortManifest:
    return CohortManifest.model_validate(
        {
            "format_version": 1,
            "name": "review",
            "purpose": "review seeds",
            "split": "dev",
            "model": "openai/model",
            "papers": [{"paper_id": "paper"}],
        }
    )


def test_manifest_rejects_duplicate_papers(tmp_path):
    payload = manifest().model_dump(mode="json")
    payload["papers"].append({"paper_id": "paper", "double_review": False})
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="duplicate paper IDs"):
        _load_manifest(path)


def test_manifest_rejects_included_and_excluded_overlap(tmp_path):
    payload = manifest().model_dump(mode="json")
    payload["exclusions"] = [{"paper_id": "paper", "reason": "not primary"}]
    path = tmp_path / "cohort.json"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="also appear as exclusions"):
        _load_manifest(path)


def test_supplement_resolution_is_explicit(tmp_path):
    supplement = tmp_path / "paper-SI.pdf"
    supplement.write_bytes(b"%PDF")

    assert _supplement_path(tmp_path, "paper") == supplement
    assert _supplement_path(tmp_path, "other") is None


def test_resume_requires_current_scientific_fingerprints(tmp_path):
    value = manifest()
    (tmp_path / "report.json").write_text(json.dumps({"status": "complete"}))
    configuration = {
        "model": value.model,
        "parser": value.parser,
        "claim_recall_passes": value.claim_recall_passes,
        "reasoning_effort": None,
        "max_model_calls": value.max_model_calls_per_paper,
        "max_cost_usd": value.max_cost_usd_per_paper,
        "schema_sha256": study_schema_sha256(),
        "prompt_sha256": prompt_sha256(),
    }
    (tmp_path / "run_configuration.json").write_text(json.dumps(configuration))

    assert _completed_run_matches(tmp_path, value)
    configuration["prompt_sha256"] = "older"
    (tmp_path / "run_configuration.json").write_text(json.dumps(configuration))
    assert not _completed_run_matches(tmp_path, value)


def test_every_semantic_stage_uses_the_frozen_cohort_model(tmp_path, monkeypatch):
    value = manifest()
    manifest_path = tmp_path / "cohort.json"
    manifest_path.write_text(value.model_dump_json())
    pdf_dir = tmp_path / "pdfs"
    supplement_dir = tmp_path / "supplements"
    pdf_dir.mkdir()
    supplement_dir.mkdir()
    (pdf_dir / "paper.pdf").write_bytes(b"%PDF")
    captured = {}

    def fake_extract(**options):
        captured.update(options)
        return {"status": "complete"}

    monkeypatch.setattr(cohort, "extract_study", fake_extract)
    result = CliRunner().invoke(
        cohort.main,
        [
            "--manifest",
            str(manifest_path),
            "--pdf-dir",
            str(pdf_dir),
            "--supplement-dir",
            str(supplement_dir),
            "--output-dir",
            str(tmp_path / "runs"),
        ],
    )

    assert result.exit_code == 0
    assert {
        captured["model"],
        captured["claim_model"],
        captured["enrichment_model"],
        captured["refinement_model"],
        captured["repair_model"],
    } == {value.model}


def test_shards_select_disjoint_papers(tmp_path, monkeypatch):
    payload = manifest().model_dump(mode="json")
    payload["papers"] = [
        {"paper_id": f"paper-{index}", "double_review": False}
        for index in range(5)
    ]
    value = CohortManifest.model_validate(payload)
    manifest_path = tmp_path / "cohort.json"
    manifest_path.write_text(value.model_dump_json())
    pdf_dir = tmp_path / "pdfs"
    supplement_dir = tmp_path / "supplements"
    pdf_dir.mkdir()
    supplement_dir.mkdir()
    for index in range(5):
        (pdf_dir / f"paper-{index}.pdf").write_bytes(b"%PDF")
    seen = []

    def fake_extract(**options):
        seen.append(options["pdf"].stem)
        return {"status": "complete"}

    monkeypatch.setattr(cohort, "extract_study", fake_extract)
    result = CliRunner().invoke(
        cohort.main,
        [
            "--manifest",
            str(manifest_path),
            "--pdf-dir",
            str(pdf_dir),
            "--supplement-dir",
            str(supplement_dir),
            "--output-dir",
            str(tmp_path / "runs"),
            "--shard-count",
            "2",
            "--shard-index",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert seen == ["paper-1", "paper-3"]
    assert (tmp_path / "runs" / "cohort_run.shard-2-of-2.json").is_file()


def test_completed_shard_still_writes_a_full_audit(tmp_path, monkeypatch):
    value = manifest()
    manifest_path = tmp_path / "cohort.json"
    manifest_path.write_text(value.model_dump_json())
    pdf_dir = tmp_path / "pdfs"
    supplement_dir = tmp_path / "supplements"
    run_dir = tmp_path / "runs" / "paper"
    pdf_dir.mkdir()
    supplement_dir.mkdir()
    run_dir.mkdir(parents=True)
    (pdf_dir / "paper.pdf").write_bytes(b"%PDF")
    (run_dir / "report.json").write_text(json.dumps({"status": "complete"}))
    (run_dir / "run_configuration.json").write_text(
        json.dumps(
            {
                "model": value.model,
                "parser": value.parser,
                "claim_recall_passes": value.claim_recall_passes,
                "reasoning_effort": None,
                "max_model_calls": value.max_model_calls_per_paper,
                "max_cost_usd": value.max_cost_usd_per_paper,
                "schema_sha256": study_schema_sha256(),
                "prompt_sha256": prompt_sha256(),
            }
        )
    )
    monkeypatch.setattr(
        cohort,
        "extract_study",
        lambda **_: pytest.fail("a matching completed run must be resumed"),
    )

    result = CliRunner().invoke(
        cohort.main,
        [
            "--manifest",
            str(manifest_path),
            "--pdf-dir",
            str(pdf_dir),
            "--supplement-dir",
            str(supplement_dir),
            "--output-dir",
            str(tmp_path / "runs"),
        ],
    )

    assert result.exit_code == 0
    audit = json.loads((tmp_path / "runs" / "cohort_run.json").read_text())
    assert audit["results"] == [
        {"paper_id": "paper", "status": "already_complete"}
    ]
