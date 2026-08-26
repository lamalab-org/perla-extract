import json
from pathlib import Path

from perla_extract.study_extraction.claims import ClaimLedger, ExperimentalObject
from perla_extract.study_extraction.guidance import (
    DEVICE_FAMILY_POLICY,
    SHARED_QUANTITY_POLICY,
)
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
from perla_extract.study_extraction.refinement import REFINEMENT_PROMPT
from perla_extract.study_extraction.repair import REPAIR_PROMPT
from perla_extract.study_extraction.workflow import (
    CLAIM_LEDGER_PROMPT,
    EXTRACTION_PROMPT,
    ExtractionConfig,
    _gate_final_candidate_against_draft,
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
        self.kinds: list[str] = []

    def complete(self, **request):
        self.prompts.append(request["prompt"])
        self.kinds.append(request["kind"])
        return StudyExtraction(
            paper=PaperMetadata(title=None, doi=None),
            device_families=[],
            individual_devices=[],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[f"pass {len(self.prompts)}"],
        )


def test_every_semantic_pass_uses_the_same_device_family_boundary():
    """Keep high-recall passes from silently reintroducing treatment-arm families."""

    for prompt in (
        EXTRACTION_PROMPT,
        CLAIM_LEDGER_PROMPT,
        REFINEMENT_PROMPT,
        REPAIR_PROMPT,
    ):
        assert DEVICE_FAMILY_POLICY in prompt
    assert "processing/composition variant" not in EXTRACTION_PROMPT
    assert "processing/composition variants" not in CLAIM_LEDGER_PROMPT
    assert "characterization-only partial structures" in REFINEMENT_PROMPT


def test_value_producing_passes_share_the_atomic_shared_quantity_rule():
    """Keep equal list-scoped values distinct without inferring missing quantities."""

    for prompt in EXTRACTION_PROMPT, REFINEMENT_PROMPT, REPAIR_PROMPT:
        assert SHARED_QUANTITY_POLICY in prompt
    assert (
        "Equal values for different materials are not duplicates" in EXTRACTION_PROMPT
    )
    assert "kind=reported_quantity" in CLAIM_LEDGER_PROMPT
    assert 'raw_value="1.4 M"' in CLAIM_LEDGER_PROMPT


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
    assert (tmp_path / "output" / "evidence_spans.json").exists()
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
        for field in (
            "schema_sha256",
            "prompt_sha256",
            "evidence_spans_sha256",
            "configuration_sha256",
        )
    )


def test_no_claims_dry_run_reports_the_stage_as_disabled(tmp_path):
    report = run_extraction(
        ExtractionConfig(
            pdf=FIXTURE,
            supplement=None,
            output_dir=tmp_path / "output",
            parser="pymupdf",
            document_cache_dir=tmp_path / "documents",
            model_cache_dir=tmp_path / "models",
            use_claim_ledger=False,
            use_refinement=False,
            use_enrichment=False,
            use_targeted_repair=False,
            dry_run=True,
        )
    )

    assert report["claim_mode"] == "disabled"
    assert report["planned_claim_calls"] == 0
    plan = json.loads((tmp_path / "output" / "claim_window_plan.json").read_text())
    assert plan == {"mode": "disabled", "approximate_request_tokens": 0, "windows": []}


def test_quality_pass_receives_grounded_claims_and_retains_draft(tmp_path):
    block = EvidenceBlock(
        block_id="result",
        source="main",
        page=1,
        kind="text",
        text="The control device reached 20.1% efficiency.",
    )
    ledger = ClaimLedger(
        objects=[
            ExperimentalObject(
                object_id="candidate-1",
                role="device_design",
                scope="target",
                label="control device",
                evidence=[EvidenceCitation(block_id="result", quote="control device")],
            )
        ],
        claims=[],
    )
    client = RecordingClient()
    config = ExtractionConfig(
        pdf=FIXTURE,
        supplement=None,
        output_dir=tmp_path,
        parser="pymupdf",
    )

    result, draft_result, errors = _run_model_calls(config, client, [block], ledger)

    assert errors == []
    assert result.unresolved_notes == ["pass 2"]
    assert draft_result is not None
    assert draft_result.unresolved_notes == ["pass 1"]
    assert client.kinds == ["complete_study", "study_refinement"]
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

    selected, audit = _select_refinement_candidate(draft, refinement, [block], None)

    assert selected == draft
    assert audit["selected"] == "draft"
    assert audit["draft_quality"]["source_verified_values"] == 1
    assert audit["refinement_quality"]["source_verified_values"] == 0
    assert audit["refinement_quality"]["reported_values"] == 2


def test_refinement_may_remove_grounded_values_to_correct_entity_precision():
    block = EvidenceBlock(
        block_id="result",
        source="main",
        page=1,
        kind="text",
        text="The same device was reported at 40 ms and 80 ms.",
    )
    citation = EvidenceCitation(block_id="result", quote=block.text)

    def candidate(raw_values: list[str]) -> StudyExtraction:
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
                            value_number=float(raw_value.split()[0]),
                            unit="ms",
                            evidence=[citation],
                        )
                        for raw_value in raw_values
                    ],
                    evidence=[citation],
                )
            ],
            performance_observations=[],
            population_statistics=[],
            stability_tests=[],
            unresolved_notes=[],
        )

    draft = candidate(["40 ms", "80 ms"])
    refinement = candidate(["40 ms"])

    selected, selection = _select_refinement_candidate(draft, refinement, [block], None)
    final, audit = _gate_final_candidate_against_draft(
        draft, selected, [block], None, selection
    )

    assert final == refinement
    assert audit["selected"] == "refinement"
    assert audit["draft_quality"]["source_verified_values"] == 2
    assert audit["final_candidate_quality"]["source_verified_values"] == 1


def test_final_candidate_cannot_trade_validation_for_more_values():
    block = EvidenceBlock(
        block_id="result",
        source="main",
        page=1,
        kind="text",
        text="Device A used a 40 ms delay.",
    )
    citation = EvidenceCitation(block_id="result", quote="Device A used a 40 ms delay.")

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
    candidate_with_extra_unsupported_value = candidate([("40 ms", 40), ("80 ms", 80)])
    selected, audit = _gate_final_candidate_against_draft(
        draft,
        candidate_with_extra_unsupported_value,
        [block],
        None,
        {"selected": "refinement", "reason": "provisional"},
    )

    assert selected == draft
    assert audit["pre_repair_selected"] == "refinement"
    assert audit["selected"] == "draft"
    assert audit["final_candidate_quality"]["reported_values"] == 2
    assert audit["final_candidate_quality"]["validation_issues"] == 1


def test_final_candidate_is_kept_when_all_grounded_signals_are_non_worsening():
    block = EvidenceBlock(
        block_id="result",
        source="main",
        page=1,
        kind="text",
        text="Device A used delays of 40 ms and 80 ms.",
    )
    citation = EvidenceCitation(
        block_id="result", quote="Device A used delays of 40 ms and 80 ms."
    )

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
    richer = candidate([("40 ms", 40), ("80 ms", 80)])
    selected, audit = _gate_final_candidate_against_draft(
        draft,
        richer,
        [block],
        None,
        {"selected": "refinement", "reason": "provisional"},
    )

    assert selected == richer
    assert audit["selected"] == "refinement"
    assert audit["final_candidate_quality"]["source_verified_values"] == 2
