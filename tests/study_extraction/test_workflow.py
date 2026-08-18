import json
from pathlib import Path

from perla_extract.study_extraction.inventory import InventoryItem
from perla_extract.study_extraction.models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    EvidenceCitation,
    IndividualDevice,
    PaperMetadata,
    ReportedValue,
    StudyExtraction,
    study_schema_sha256,
)
from perla_extract.study_extraction.workflow import (
    ExtractionConfig,
    _run_model_calls,
    _select_refinement_candidate,
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


def test_refinement_is_rejected_when_the_grounded_draft_strictly_dominates():
    block = EvidenceBlock(
        block_id="result",
        source="main",
        page=1,
        kind="text",
        text="Device A used a 40 ms delay.",
    )
    citation = EvidenceCitation(block_id="result", quote="Device A")

    def candidate(values: list[tuple[str, float]]) -> StudyExtraction:
        return StudyExtraction(
            paper=PaperMetadata(title=None, doi=None),
            device_families=[],
            individual_devices=[
                IndividualDevice(
                    device_id="device-a",
                    family_id=None,
                    label="Device A",
                    variant=None,
                    champion_status="not_reported",
                    selection_basis="not_reported",
                    reported_properties=[
                        ReportedValue(
                            name="delay",
                            raw_value=raw_value,
                            value_number=value_number,
                            unit="ms",
                            evidence=[citation],
                        )
                        for raw_value, value_number in values
                    ],
                    evidence=[citation],
                )
            ],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[],
        )

    draft = candidate([("40 ms", 40)])
    refinement = candidate([("60 ms", 60), ("80 ms", 80)])

    selected, audit = _select_refinement_candidate(
        draft, refinement, [block], None
    )

    assert selected == draft
    assert audit["selected"] == "draft"
    assert audit["draft_quality"]["source_verified_values"] == 1
    assert audit["refinement_quality"]["source_verified_values"] == 0
    assert audit["refinement_quality"]["reported_values"] == 2
