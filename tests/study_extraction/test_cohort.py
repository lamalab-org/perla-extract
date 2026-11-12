import hashlib
import json

import pytest
from click.testing import CliRunner

import perla_extract.study_extraction.cohort as cohort
from examples.scoring.run import seed_study
from perla_extract.study_extraction.cohort import (
    CohortManifest,
    _completed_run_matches,
    _load_manifest,
    _supplement_path,
)
from perla_extract.study_extraction.configuration import (
    ExtractionConfig,
    implementation_sha256,
    runtime_versions,
)
from perla_extract.study_extraction.extraction_prompts import prompt_sha256
from perla_extract.study_extraction.models import (
    study_schema_prose_sha256,
    study_schema_sha256,
)
from perla_extract.study_extraction.run_artifacts import write_run_manifest


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


def sealed_run(directory, pdf, value, supplement=None):
    directory.mkdir(parents=True, exist_ok=True)
    events = [
        dict(
            source=role,
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            dependency_versions=cohort._parser_dependency_versions(value.parser),
            parser_implementation_sha256=cohort._parser_implementation_sha256(),
        )
        for role, path in [("main", pdf), ("supplement", supplement)]
        if path is not None
    ]
    (directory / "report.json").write_text(
        json.dumps(
            dict(
                status="complete",
                source_parsing=events
                + [{"operation": "cross_document_deduplication", "skipped": 0}],
            )
        )
    )
    configuration = dict(
        scientific_settings=cohort._paper_config(
            value, pdf, supplement, directory
        ).scientific_settings(),
        schema_sha256=study_schema_sha256(),
        schema_prose_sha256=study_schema_prose_sha256(),
        pipeline_implementation_sha256=implementation_sha256(),
        runtime_versions=runtime_versions(),
        prompt_sha256=prompt_sha256(),
    )
    (directory / "run_configuration.json").write_text(json.dumps(configuration))
    (directory / "extraction.json").write_text(seed_study().model_dump_json())
    (directory / "document.json").write_text(json.dumps({"blocks": []}))
    write_run_manifest(directory)


@pytest.mark.parametrize(
    "change", ["source", "supplement", "settings", "prediction", "document", "parser"]
)
def test_resume_checks_actual_sources_configuration_and_valid_artifacts(
    tmp_path, change, monkeypatch
):
    value = manifest()
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"source one")
    supplement = tmp_path / "si.pdf"
    supplement.write_bytes(b"supplement one")
    directory = tmp_path / "run"
    sealed_run(directory, pdf, value, supplement)
    assert _completed_run_matches(directory, value, pdf, supplement)
    if change == "source":
        pdf.write_bytes(b"source two")
    elif change == "supplement":
        supplement.write_bytes(b"supplement two")
    elif change == "settings":
        value = value.model_copy(update={"claim_recall_passes": 1})
    elif change == "parser":
        monkeypatch.setattr(
            cohort, "_parser_dependency_versions", lambda _: {"changed": "version"}
        )
    else:
        name = "extraction.json" if change == "prediction" else "document.json"
        (directory / name).write_text("{}")
        # Resealing cannot make malformed scientific output valid.
        (directory / "run_manifest.json").unlink()
        write_run_manifest(directory)
    assert not _completed_run_matches(directory, value, pdf, supplement)


def test_new_attempt_retains_previous_artifacts(tmp_path):
    directory = tmp_path / "paper"
    directory.mkdir()
    (directory / "failed-report.json").write_text("paid attempt")
    first = cohort._new_attempt(directory)
    first.mkdir()
    assert cohort._new_attempt(directory).name == "attempt-0002"
    assert (directory / "failed-report.json").read_text() == "paid attempt"


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
    config = ExtractionConfig(
        **{key: val for key, val in captured.items() if key != "env_file"}
    )
    assert {
        config.effective_model(stage)
        for stage in ("read", "assemble", "refine", "repair", "enrich")
    } == {value.model}


def test_shards_select_disjoint_papers(tmp_path, monkeypatch):
    payload = manifest().model_dump(mode="json")
    payload["papers"] = [
        {"paper_id": f"paper-{index}", "double_review": False} for index in range(5)
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
    sealed_run(run_dir, pdf_dir / "paper.pdf", value)
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
        {
            "paper_id": "paper",
            "status": "already_complete",
            "run_dir": str(run_dir),
            "attempt_dirs": [str(run_dir)],
        }
    ]


def test_failed_attempt_path_and_invocation_history_are_retained(tmp_path, monkeypatch):
    value = manifest()
    manifest_path = tmp_path / "cohort.json"
    manifest_path.write_text(value.model_dump_json())
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    (pdf_dir / "paper.pdf").write_bytes(b"synthetic pdf")

    def fail(**options):
        path = options["output_dir"]
        path.mkdir(parents=True)
        (path / "report.json").write_text('{"status":"failed"}')
        raise OSError("synthetic write failure")

    monkeypatch.setattr(cohort, "extract_study", fail)
    args = [
        "--manifest",
        str(manifest_path),
        "--pdf-dir",
        str(pdf_dir),
        "--supplement-dir",
        str(pdf_dir),
        "--output-dir",
        str(tmp_path / "runs"),
    ]
    assert CliRunner().invoke(cohort.main, args).exit_code == 1
    assert CliRunner().invoke(cohort.main, args).exit_code == 1
    history = list((tmp_path / "runs/cohort_runs").glob("*.json"))
    assert len(history) == 2
    latest = json.loads((tmp_path / "runs/cohort_run.json").read_text())["results"][0]
    assert latest["run_dir"].endswith("attempt-0001")
    assert len(latest["attempt_dirs"]) == 2
