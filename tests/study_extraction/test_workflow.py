import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.candidate_selection import (
    _gate_final_candidate_against_draft,
    _select_refinement_candidate,
)
from perla_extract.study_extraction.claims import ClaimLedger, ExperimentalObject
from perla_extract.study_extraction.configuration import ExtractionConfig
from perla_extract.study_extraction.extraction_prompts import (
    _claim_ledger_prompt,
    prompt_sha256,
)
from perla_extract.study_extraction.guidance import (
    COMPOSITION_BOUNDARY_POLICY,
    DEVICE_FAMILY_POLICY,
    EVIDENCE_INTERPRETATION_POLICY,
    RECORD_BOUNDARY_POLICY,
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
from perla_extract.study_extraction.repair import REPAIR_PROMPT
from perla_extract.study_extraction.workflow import _run_model_calls, run_extraction

FIXTURE = Path(__file__).parents[1] / "test_files" / "nat_comm_7139.pdf"


class RecordingClient:
    """Return distinct valid drafts while exposing prompts to workflow assertions."""

    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.kinds: list[str] = []
        self.requests: list[dict] = []

    def complete(self, **request):
        self.requests.append(request)
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


def test_model_requests_include_shared_scientific_policies_once(tmp_path):
    block = EvidenceBlock(
        block_id="source",
        source="main",
        page=1,
        kind="text",
        text="Control device reached 20%.",
    )
    client = RecordingClient()
    _run_model_calls(
        ExtractionConfig(pdf=FIXTURE, output_dir=tmp_path),
        client,
        [block],
        ClaimLedger(objects=[], claims=[]),
    )
    for prompt in [*client.prompts, _claim_ledger_prompt([block]), REPAIR_PROMPT]:
        for policy in (
            DEVICE_FAMILY_POLICY,
            RECORD_BOUNDARY_POLICY,
            COMPOSITION_BOUNDARY_POLICY,
            EVIDENCE_INTERPRETATION_POLICY,
            SHARED_QUANTITY_POLICY,
        ):
            assert prompt.count(policy) == 1


def test_recall_pass_repeats_source_without_treating_prior_model_as_evidence():
    """A second reading changes instructions but carries no previous model ledger."""

    block = EvidenceBlock(
        block_id="b", source="main", page=1, kind="text", text="Device reached 20%."
    )
    prompt = _claim_ledger_prompt([block], recall_pass=2)

    assert "INDEPENDENT OBJECT-FIRST READING" in prompt
    assert "Device reached 20%." in prompt
    assert "fallible ledger came from an earlier" not in prompt
    assert "INDEPENDENT CLAIM-FIRST READING" in _claim_ledger_prompt(
        [block], recall_pass=3
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
    assert plan == {
        "mode": "disabled",
        "recall_passes": 0,
        "approximate_request_tokens": 0,
        "windows": [],
    }


def test_oversized_assembly_fails_before_any_provider_request(tmp_path):
    """A context overflow must be explicit and must not fall through to repair calls."""

    report = run_extraction(
        ExtractionConfig(
            pdf=FIXTURE,
            supplement=None,
            output_dir=tmp_path / "output",
            parser="pymupdf",
            document_cache_dir=tmp_path / "documents",
            model_cache_dir=tmp_path / "models",
            use_claim_ledger=False,
            assembly_max_input_tokens=1,
        )
    )

    assert report["status"] == "failed"
    assert report["budget"]["provider_requests"] == 0
    extraction = json.loads((tmp_path / "output/extraction.json").read_text())
    assert "assembly request estimate" in extraction["unresolved_notes"][0]


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
        use_refinement=True,
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
    refinement = candidate([("60 ms", 60), ("80 ms", 80)])

    selected, audit = _select_refinement_candidate(draft, refinement, [block], None)

    assert selected == draft
    assert audit["selected"] == "draft"
    assert audit["draft_quality"]["source_verified_values"] == 1
    assert audit["refinement_quality"]["source_verified_values"] == 0
    assert audit["refinement_quality"]["reported_values"] == 2


@pytest.mark.parametrize(
    "original,revised,accepted,lost",
    [
        (["40 ms", "80 ms"], ["40 ms"], False, 1),
        (["40 ms"], ["80 ms"], False, 1),
        (["40 ms", "40 ms"], ["40 ms"], True, 0),
    ],
)
def test_refinement_preserves_distinct_source_values_but_allows_exact_deduplication(
    original, revised, accepted, lost
):
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

    draft = candidate(original)
    refinement = candidate(revised)

    selected, selection = _select_refinement_candidate(draft, refinement, [block], None)
    final, audit = _gate_final_candidate_against_draft(
        draft, selected, [block], None, selection
    )

    assert final == (refinement if accepted else draft)
    assert audit["selected"] == ("refinement" if accepted else "draft")
    assert selection["refinement_quality"]["lost_verified_values"] == lost
    # The final gate also protects callers that provisionally selected a bad edit.
    guarded, _ = _gate_final_candidate_against_draft(
        draft, refinement, [block], None, {"selected": "refinement"}
    )
    assert guarded == final


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


@pytest.mark.parametrize("use_claim_ledger", [False, True])
def test_direct_and_empty_reading_send_full_source_at_provider_boundary(
    tmp_path, monkeypatch, use_claim_ledger
):
    from perla_extract.study_extraction import workflow
    from perla_extract.study_extraction.client import ModelClient
    from perla_extract.study_extraction.run_artifacts import verify_run_manifest
    from perla_extract.study_extraction.spans import build_evidence_spans

    blocks = [
        EvidenceBlock(
            block_id="title",
            source="main",
            page=1,
            kind="text",
            text="Photovoltaic study title.",
        ),
        EvidenceBlock(
            block_id="result",
            source="main",
            page=2,
            kind="text",
            text="Control efficiency was 20.1%.",
        ),
        EvidenceBlock(
            block_id="recipe",
            source="supplement",
            page=9,
            kind="text",
            text="Annealing lasted 45 minutes.",
        ),
    ]
    monkeypatch.setattr(
        workflow, "parse_documents", lambda *args, **kwargs: (blocks, [])
    )
    outgoing = []

    def live(self, body, failure):
        outgoing.append(body)
        properties = body["response_format"]["json_schema"]["schema"]["properties"]
        if "claims" in properties:
            value = {"objects": [], "claims": []}
        else:
            value = StudyExtraction(
                paper=PaperMetadata(title=None, doi=None),
                device_families=[],
                individual_devices=[],
                performance_observations=[],
                population_statistics=[],
                stability_tests=[],
                unresolved_notes=[],
            ).model_dump(mode="json")
        return value, {
            "prompt_tokens": 10,
            "completion_tokens": 10,
            "total_tokens": 20,
            "cost": 0.01,
        }

    monkeypatch.setattr(ModelClient, "_live", live)
    output = tmp_path / "output"
    report = run_extraction(
        ExtractionConfig(
            pdf=FIXTURE,
            output_dir=output,
            use_claim_ledger=use_claim_ledger,
            use_refinement=True,
            max_model_calls=6,
            use_targeted_repair=False,
            use_enrichment=False,
            model_cache_dir=tmp_path / "cache",
            heartbeat_seconds=0,
        )
    )
    study_calls = [
        body
        for body in outgoing
        if "device_families"
        in body["response_format"]["json_schema"]["schema"]["properties"]
    ]
    assert len(study_calls) == 2
    span_ids = {span.span_id for span in build_evidence_spans(blocks)}
    for body in study_calls:
        for block in blocks:
            assert block.text in body["messages"][1]["content"]
        citation_schema = body["response_format"]["json_schema"]["schema"]["$defs"][
            "EvidenceCitation"
        ]
        assert citation_schema["type"] == "integer"
        assert "enum" not in citation_schema
        assert citation_schema["minimum"] == min(span_ids)
        assert citation_schema["maximum"] == max(span_ids)
        payload, _ = json.JSONDecoder().raw_decode(
            body["messages"][1]["content"].split("COMPLETE SOURCE EVIDENCE:\n", 1)[1]
        )
        assert {int(key) for block in payload for key in block["spans"]} == span_ids
    manifest, _ = verify_run_manifest(output)
    assert manifest.run_id == report["run_id"]
    events = [
        json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()
    ]
    assert all(event["run_id"] == manifest.run_id for event in events)
    assert [
        event["strategy"] for event in events if event["event"] == "evidence.selected"
    ] == ["complete_source"]


@pytest.mark.parametrize("dry_run", [False, True])
def test_nonempty_run_directory_is_untouched_even_for_dry_run(
    tmp_path, monkeypatch, dry_run
):
    from perla_extract.study_extraction import workflow

    output = tmp_path / "output"
    output.mkdir()
    (output / "extraction.json").write_text('{"old": "prediction"}')
    (output / "claim_ledger.json").write_text('{"old": "evidence"}')
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    monkeypatch.setattr(
        workflow,
        "parse_documents",
        lambda *a, **kw: pytest.fail("must reject before parsing"),
    )
    with pytest.raises(FileExistsError, match="new directory"):
        run_extraction(
            ExtractionConfig(pdf=FIXTURE, output_dir=output, dry_run=dry_run)
        )
    assert {path.name: path.read_bytes() for path in output.iterdir()} == before


def test_claim_windows_fit_the_serialized_provider_payload(tmp_path):
    from perla_extract.study_extraction.deployment import (
        estimate_request_input,
        strict_response_schema,
    )
    from perla_extract.study_extraction.source_reading import (
        _collect_claim_ledger,
        _plan_claim_collection,
    )

    blocks = [
        EvidenceBlock(
            block_id=f"b-{i}",
            source="supplement",
            page=i // 10 + 1,
            kind="text",
            text=f"Source record {i}: " + "long source passage " * 5,
        )
        for i in range(200)
    ]
    config = ExtractionConfig(
        pdf=FIXTURE,
        output_dir=tmp_path,
        claim_mode="windowed",
        claim_window_input_tokens=6_000,
    )
    _, _, plan = _plan_claim_collection(config, blocks)
    assert len(plan) > 1
    assert [block.block_id for _, primary, _ in plan for block in primary] == [
        block.block_id for block in blocks
    ]
    requests = []

    class Reader:
        def complete(self, **request):
            requests.append(request)
            return ClaimLedger(objects=[], claims=[])

    _collect_claim_ledger(config, Reader(), blocks)
    for request in requests:
        size = estimate_request_input(
            request["system"],
            request["prompt"],
            strict_response_schema(
                request["response_model"], request["request_schema"]
            ),
        )
        assert size["estimated_input_tokens"] <= config.claim_window_input_tokens


def test_indivisible_source_block_cannot_silently_exceed_reading_budget(tmp_path):
    from perla_extract.study_extraction.source_reading import _plan_claim_collection

    block = EvidenceBlock(
        block_id="oversized-table",
        source="supplement",
        page=1,
        kind="table",
        text="A measured property\n" * 5_000,
    )
    config = ExtractionConfig(
        pdf=FIXTURE,
        output_dir=tmp_path,
        claim_mode="windowed",
        claim_window_input_tokens=6_000,
    )
    with pytest.raises(
        ValueError, match="oversized-table.*exceeding the reading limit"
    ):
        _plan_claim_collection(config, [block])


def test_unexpected_downstream_failure_preserves_known_charges(tmp_path, monkeypatch):
    from perla_extract.study_extraction import workflow
    from perla_extract.study_extraction.client import ModelClient
    from perla_extract.study_extraction.run_artifacts import (
        load_run_efficiency,
        verify_run_manifest,
    )

    block = EvidenceBlock(
        block_id="source",
        source="main",
        page=1,
        kind="text",
        text="Control device was measured.",
    )
    monkeypatch.setattr(workflow, "parse_documents", lambda *a, **kw: ([block], []))
    value = StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    ).model_dump(mode="json")
    monkeypatch.setattr(
        ModelClient,
        "_live",
        lambda *a, **kw: (value, {"total_tokens": 40, "cost": 0.25}),
    )

    def fail(*args):
        raise RuntimeError("finalization failed")

    monkeypatch.setattr(workflow, "remove_unsupported_optional_claims", fail)
    output = tmp_path / "output"
    with pytest.raises(RuntimeError, match="finalization failed"):
        run_extraction(
            ExtractionConfig(
                pdf=FIXTURE,
                output_dir=output,
                use_claim_ledger=False,
                use_refinement=False,
                use_targeted_repair=False,
                use_enrichment=False,
                model_cache_dir=tmp_path / "cache",
                heartbeat_seconds=0,
            )
        )
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["usage"]["cost"] == 0.25
    assert report["usage"]["total_tokens"] == 40
    verify_run_manifest(output)
    efficiency = load_run_efficiency(output / "report.json")
    assert efficiency.cost_usd == 0.25
    assert efficiency.provider_requests == 1
    assert efficiency.elapsed_seconds >= 0


def test_dry_run_cli_fails_known_direct_context_overflow_without_a_provider_call(
    tmp_path, monkeypatch
):
    from click.testing import CliRunner

    from perla_extract.study_extraction import workflow
    from perla_extract.study_extraction.cli import main

    block = EvidenceBlock(
        block_id="source",
        source="main",
        page=1,
        kind="text",
        text="Device was measured.",
    )
    monkeypatch.setattr(workflow, "parse_documents", lambda *a, **kw: ([block], []))
    monkeypatch.setattr(
        workflow,
        "ModelClient",
        lambda *a, **kw: pytest.fail("dry run must not construct a model client"),
    )
    settings = tmp_path / "settings.toml"
    settings.write_text(
        "[stages.assemble]\nmax_input_tokens = 1\ncontext_tokens = 2\nmax_output_tokens = 1\n"
    )
    output = tmp_path / "direct"
    result = CliRunner().invoke(
        main,
        [
            "--pdf",
            str(FIXTURE),
            "--output-dir",
            str(output),
            "--config",
            str(settings),
            "--no-claims",
            "--dry-run",
        ],
    )
    assert result.exit_code == 1
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "failed"
    assert report["request_feasibility"]["assembly"] == "infeasible"
    assert report["errors"]
    assert report["budget"]["provider_requests"] == 0
    assert (output / "run_manifest.json").exists()
    assert (output / "document.json").exists()


def test_reader_dry_run_does_not_claim_unknown_assembly_input_fits(
    tmp_path, monkeypatch
):
    from perla_extract.study_extraction import workflow

    block = EvidenceBlock(
        block_id="source",
        source="main",
        page=1,
        kind="text",
        text="Device was measured.",
    )
    monkeypatch.setattr(workflow, "parse_documents", lambda *a, **kw: ([block], []))
    report = run_extraction(
        ExtractionConfig(
            pdf=FIXTURE,
            output_dir=tmp_path / "output",
            dry_run=True,
            use_claim_ledger=True,
        )
    )
    assert report["status"] == "dry_run"
    assert report["request_feasibility"]["assembly"] == "conditional_on_reader_output"
