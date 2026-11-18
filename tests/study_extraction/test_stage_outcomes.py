"""A fallback must remain visible in both local events and the saved report."""

import json
from pathlib import Path

import pytest

from perla_extract.study_extraction import workflow
from perla_extract.study_extraction.enrichment import EnrichmentAudit
from perla_extract.study_extraction.models import EvidenceBlock
from perla_extract.study_extraction.observability import EventJournal, read_events


def test_handled_stage_failure_has_one_canonical_outcome(tmp_path):
    journal = EventJournal(tmp_path)
    with journal.stage("refine") as outcome:
        outcome.record("failed", errors=["invalid response"], fallback="retain_draft")
    terminal = read_events(journal.path)[-1]
    assert terminal["event"] == "stage.finished"
    assert {key: terminal[key] for key in outcome.to_dict()} == outcome.to_dict()
    assert journal.stages == [outcome]
    assert journal.stage_id is None


def test_interrupted_stage_is_preserved_and_parent_context_restored(tmp_path):
    journal = EventJournal(tmp_path)
    with journal.stage("outer") as parent:
        with pytest.raises(KeyboardInterrupt):
            with journal.stage("read"):
                raise KeyboardInterrupt("cancelled")
        assert journal.stage_id == parent.stage_id
    assert [stage.status for stage in journal.stages] == ["interrupted", "complete"]


@pytest.mark.parametrize("failure", ["refine", "enrich"])
def test_handled_optional_failure_is_shared_by_report_and_events(
    tmp_path, monkeypatch, failure
):
    from perla_extract.study_extraction import study_outputs
    from perla_extract.study_extraction.client import ModelClient

    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"synthetic; parser is injected")
    block = EvidenceBlock(
        block_id="b", source="main", page=1, kind="text", text="Synthetic study."
    )
    monkeypatch.setattr(workflow, "parse_documents", lambda *a, **kw: ([block], []))
    monkeypatch.setattr(
        ModelClient,
        "_live",
        lambda *a, **kw: (
            workflow._empty_extraction("Synthetic").model_dump(mode="json"),
            {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
                "cost": 0.01,
            },
        ),
    )
    monkeypatch.setattr(
        workflow,
        "refine_draft",
        lambda client, **kw: (kw["draft"], "synthetic refinement failure"),
    )
    monkeypatch.setattr(
        study_outputs,
        "run_enrichment",
        lambda **kw: EnrichmentAudit(errors=["synthetic interpretation failure"]),
    )
    report = workflow.run_extraction(
        workflow.ExtractionConfig(
            pdf=pdf,
            output_dir=tmp_path / "run",
            model_cache_dir=tmp_path / "cache",
            use_claim_ledger=False,
            use_targeted_repair=False,
            use_refinement=failure == "refine",
            use_enrichment=failure == "enrich",
            heartbeat_seconds=0,
        )
    )
    terminal = [
        event
        for event in read_events(tmp_path / "run/events.jsonl")
        if event["event"] == "stage.finished" and event["stage"] == failure
    ][0]
    reported = [stage for stage in report["stages"] if stage["stage"] == failure][0]
    assert reported["status"] == terminal["status"] == "failed"
    assert reported["errors"] == terminal["errors"]
    assert report["usage"]["cost"] == 0.01
    saved = json.loads((tmp_path / "run/report.json").read_text())
    assert saved["stages"] == report["stages"]
    if failure == "enrich":
        assert report["enrichment_status"] == "failed"


def test_offline_planning_example_refuses_oversize_without_model_calls(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    from examples.extraction.plan import main, run
    from perla_extract.study_extraction.client import ModelClient

    monkeypatch.setattr(
        ModelClient,
        "_live",
        lambda *a, **kw: pytest.fail("offline plan called a model"),
    )
    output = tmp_path / "example"
    assert run(output) == {"fits": "dry_run", "blocked": "failed"}
    for name in ("fits", "blocked"):
        report = json.loads((output / name / "report.json").read_text())
        config = json.loads((output / name / "run_configuration.json").read_text())
        assert report["budget"]["provider_requests"] == 0
        policy = config["resolved_stage_policies"]["assemble"]
        assert policy["max_output_tokens"] == 1024
        assert policy["deployment"]["max_input_tokens"] == (
            1 if name == "blocked" else 100_000
        )
        assert Path(output / name / "run_manifest.json").exists()

    original = (output / "fits" / "report.json").read_bytes()
    repeated = CliRunner().invoke(main, ["--output", str(output)])
    assert repeated.exit_code == 1
    assert "choose a new directory" in repeated.output
    assert "Traceback" not in repeated.output
    assert (output / "fits" / "report.json").read_bytes() == original


def test_enrichment_artifact_failure_marks_stage_failed(tmp_path, monkeypatch):
    from perla_extract.study_extraction import study_outputs
    from perla_extract.study_extraction.client import ModelClient

    pdf = tmp_path / "input.pdf"
    pdf.write_bytes(b"synthetic")
    block = EvidenceBlock(
        block_id="b", source="main", page=1, kind="text", text="Synthetic study."
    )
    monkeypatch.setattr(workflow, "parse_documents", lambda *a, **kw: ([block], []))
    monkeypatch.setattr(
        ModelClient,
        "_live",
        lambda *a, **kw: (
            workflow._empty_extraction("Synthetic").model_dump(mode="json"),
            {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
                "cost": 0.01,
            },
        ),
    )
    monkeypatch.setattr(study_outputs, "run_enrichment", lambda **kw: EnrichmentAudit())
    original_write = study_outputs.write_json_atomic

    def fail_enrichment(path, value):
        if path.name == "enrichment.json":
            raise OSError("synthetic full disk")
        return original_write(path, value)

    monkeypatch.setattr(study_outputs, "write_json_atomic", fail_enrichment)
    output = tmp_path / "run"
    with pytest.raises(OSError, match="full disk"):
        workflow.run_extraction(
            workflow.ExtractionConfig(
                pdf=pdf,
                output_dir=output,
                model_cache_dir=tmp_path / "cache",
                use_claim_ledger=False,
                use_refinement=False,
                use_targeted_repair=False,
                use_enrichment=True,
                heartbeat_seconds=0,
            )
        )
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    assert (
        next(stage for stage in report["stages"] if stage["stage"] == "enrich")[
            "status"
        ]
        == "failed"
    )
    assert report["usage"]["cost"] == 0.01


def test_failed_export_does_not_count_unwritten_archives(tmp_path, monkeypatch):
    from perla_extract.study_extraction import study_outputs
    from perla_extract.study_extraction.workflow_results import FinalStudy

    def fail_export(*args):
        raise ValueError("invalid serialized export")

    monkeypatch.setattr(study_outputs, "_write_nomad_artifacts", fail_export)
    journal = EventJournal(tmp_path / "run")
    final = FinalStudy(workflow._empty_extraction("Synthetic"), {}, None, None, {})
    config = workflow.ExtractionConfig(
        pdf=tmp_path / "unused.pdf", output_dir=tmp_path / "run", use_enrichment=False
    )
    result = study_outputs._interpret_and_export(
        config, None, final, [], False, journal
    )
    assert result.nomad is None
    assert result.nomad_error == "invalid serialized export"
    assert journal.stages[-1].status == "failed"


def test_stage_start_write_failure_restores_context(tmp_path, monkeypatch):
    journal = EventJournal(tmp_path)
    original_emit = journal.emit

    def fail_start(event, **fields):
        if event == "stage.started":
            raise OSError("cannot save start")
        return original_emit(event, **fields)

    monkeypatch.setattr(journal, "emit", fail_start)
    with pytest.raises(OSError, match="cannot save start"):
        with journal.stage("read"):
            pytest.fail("stage should not execute without its start event")
    assert journal.stage_id is None
    assert journal.stages[0].status == "failed"
