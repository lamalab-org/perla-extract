import json
from pathlib import Path

from perla_extract.study_extraction.inventory import InventoryItem
from perla_extract.study_extraction.models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    EvidenceCitation,
    PaperMetadata,
    StudyExtraction,
    study_schema_sha256,
)
from perla_extract.study_extraction.workflow import (
    ExtractionConfig,
    _run_model_calls,
    prompt_sha256,
    run_extraction,
)

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


class RecordingClient:
    """Return distinct valid drafts while exposing prompts to workflow assertions."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def complete(self, **request):
        self.prompts.append(request["prompt"])
        return StudyExtraction(
            paper=PaperMetadata(title=None, doi=None),
            device_families=[],
            individual_devices=[],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[f"pass {len(self.prompts)}"],
        )


def test_dry_run_writes_a_complete_request_plan(tmp_path):
    report = run_extraction(
        ExtractionConfig(
            pdf=FIXTURE,
            supplement=None,
            output_dir=tmp_path / "output",
            parser="pymupdf",
            document_cache_dir=tmp_path / "documents",
            model_cache_dir=tmp_path / "models",
            dry_run=True,
        )
    )

    assert report["status"] == "dry_run"
    assert report["evidence_blocks"] > 0
    assert report["planned_calls"] >= 1
    assert (tmp_path / "output" / "document.json").exists()
    assert (tmp_path / "output" / "extraction.schema.json").exists()
    assert (tmp_path / "output" / "enrichment.schema.json").exists()
    assert (tmp_path / "output" / "report.json").exists()
    configuration = json.loads(
        (tmp_path / "output" / "run_configuration.json").read_text()
    )
    assert configuration["schema_version"] == STUDY_SCHEMA_VERSION
    assert configuration["schema_sha256"] == study_schema_sha256()
    assert configuration["prompt_sha256"] == prompt_sha256()
    assert all(
        len(configuration[field]) == 64
        for field in ("schema_sha256", "prompt_sha256", "configuration_sha256")
    )


def test_quality_pass_receives_grounded_inventory_and_retains_draft(tmp_path):
    block = EvidenceBlock(
        block_id="result",
        source="main",
        page=1,
        kind="text",
        text="The control device reached 20.1% efficiency.",
    )
    inventory = InventoryItem(
        item_id="candidate-1",
        kind="device_family",
        label="control device",
        evidence=[EvidenceCitation(block_id="result", quote="control device")],
    )
    client = RecordingClient()
    config = ExtractionConfig(
        pdf=FIXTURE,
        supplement=None,
        output_dir=tmp_path,
        parser="pymupdf",
    )

    result, draft_result, errors = _run_model_calls(
        config, client, [block], "single", None, [inventory]
    )

    assert errors == []
    assert result.unresolved_notes == ["pass 2"]
    assert draft_result is not None
    assert draft_result.unresolved_notes == ["pass 1"]
    assert "candidate-1" in client.prompts[0]
    assert "DRAFT EXTRACTION" in client.prompts[1]
    draft = StudyExtraction.model_validate_json(
        (tmp_path / "draft_extraction.json").read_text()
    )
    assert draft.unresolved_notes == ["pass 1"]
    audit = json.loads((tmp_path / "refinement_audit.json").read_text())
    assert audit["collections"]["device_families"]["before_count"] == 0
