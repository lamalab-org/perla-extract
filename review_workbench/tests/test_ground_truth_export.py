from __future__ import annotations

import copy
import io
import json
import zipfile

import pytest
from click.testing import CliRunner

from perla_extract.study_extraction.evaluation_cli import main as evaluate
from perla_extract.study_extraction.models import (
    STUDY_SCHEMA_VERSION,
    study_schema_sha256,
)
from review_workbench.ground_truth_export import (
    GROUND_TRUTH_FILENAMES,
    build_ground_truth_export,
    ground_truth_zip,
    write_ground_truth_export,
)
from review_workbench.study_review import (
    RECORD_IDENTIFIERS,
    InventoryAuditRequest,
    MutationRequest,
    RecordDecisionRequest,
    StageRequest,
    StudyReviewStore,
)

SPLIT = "calibration"
PAPER_ID = "10.0000--example"


def test_adjudicated_app_export_can_be_scored(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path / "review")
    study = _study_with_evidence(empty_study, "champion device")
    study["device_families"][0]["full_stack_raw"] = "ITO/perovskite/Ag"
    _adjudicate(store, study, document_payload)
    export = build_ground_truth_export(store, SPLIT, PAPER_ID)
    target = write_ground_truth_export(export, tmp_path / "frozen")
    output = tmp_path / "evaluation.json"
    result = CliRunner().invoke(
        evaluate,
        [
            "--truth",
            str(target),
            "--prediction",
            str(target / "ground_truth.json"),
            "--output",
            str(output),
        ],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    assert report["core_facts"]["micro"]["f1"] == 1
    assert (
        report["benchmark"]["evidence_document_sha256"]
        == export.manifest.evidence_document_sha256
    )


def _adjudicate(
    store: StudyReviewStore,
    study: dict,
    document: dict,
    *,
    decision: str = "verified",
    correction: MutationRequest | None = None,
) -> dict:
    """Create a fully reviewed fixture through the public state transitions."""

    bundle = store.import_seed(
        SPLIT,
        PAPER_ID,
        study,
        document=document,
        manifest={"model": "frontier"},
        reviewer_id="ada",
    )
    if correction is not None:
        bundle = store.mutate(
            SPLIT,
            PAPER_ID,
            correction.model_copy(update={"base_revision": bundle["revision"]}),
            "ada",
        )
    bundle = store.inventory_audit(
        SPLIT,
        PAPER_ID,
        InventoryAuditRequest(
            base_revision=bundle["revision"],
            searched_sources=["main", "supplement"],
            expected_counts={},
        ),
        "ada",
    )
    bundle = store.complete_stage(
        SPLIT,
        PAPER_ID,
        StageRequest(stage="inventory", base_revision=bundle["revision"]),
        "ada",
    )
    for collection, identifier in RECORD_IDENTIFIERS.items():
        for record in bundle["ground_truth"].get(collection, []):
            bundle = store.decide_record(
                SPLIT,
                PAPER_ID,
                RecordDecisionRequest(
                    collection=collection,
                    record_id=record[identifier],
                    decision=decision,
                    base_revision=bundle["revision"],
                ),
                "ada",
            )
    for stage in ("fields", "completeness", "adjudication"):
        bundle = store.complete_stage(
            SPLIT,
            PAPER_ID,
            StageRequest(stage=stage, base_revision=bundle["revision"]),
            "ada",
        )
    return bundle


def test_saved_scientific_correction_changes_the_reference_not_the_seed(
    tmp_path, empty_study, document_payload
):
    """Exercise correction -> adjudication -> immutable export -> meaningful scores."""

    from perla_extract.study_extraction.evaluation import evaluate_study

    store = StudyReviewStore(tmp_path / "review")
    quote = "The device stack is ITO/perovskite/Ag."
    document = copy.deepcopy(document_payload)
    document["blocks"][0]["text"] += " " + quote
    seed = _study_with_evidence(empty_study, quote)
    seed["device_families"][0]["full_stack_raw"] = "ITO/solvent/perovskite/Ag"
    _adjudicate(
        store,
        seed,
        document,
        correction=MutationRequest(
            action="replace",
            path="/device_families/0/full_stack_raw",
            value="ITO/perovskite/Ag",
            base_revision=1,
            evidence=[{"block_id": "main_p1_text_1", "quote": quote}],
        ),
    )
    export = build_ground_truth_export(store, SPLIT, PAPER_ID)
    assert (
        export.seed_extraction.device_families[0].full_stack_raw
        == "ITO/solvent/perovskite/Ag"
    )
    assert export.ground_truth.device_families[0].full_stack_raw == "ITO/perovskite/Ag"
    assert any(
        event.path == "/device_families/0/full_stack_raw"
        for event in export.review_events
    )
    before = evaluate_study(export.ground_truth, export.seed_extraction)
    after = evaluate_study(export.ground_truth, export.ground_truth)
    assert before.core_facts.groups["stack"].f1 == 0
    assert after.core_facts.groups["stack"].f1 == 1
    assert before.truth_content_sha256 == after.truth_content_sha256
    assert before.prediction_content_sha256 != after.prediction_content_sha256
    target = write_ground_truth_export(export, tmp_path / "frozen")
    output = tmp_path / "score.json"
    result = CliRunner().invoke(
        evaluate,
        [
            "--truth",
            str(target),
            "--prediction",
            str(target / "seed_extraction.json"),
            "--output",
            str(output),
            "--fail-on-scoring-issues",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["core_facts"]["groups"]["stack"]["f1"] == 0


def _study_with_evidence(study: dict, quote: str) -> dict:
    result = copy.deepcopy(study)
    result["device_families"] = [
        {
            "family_id": "family-control",
            "label": "Control",
            "variant": None,
            "architecture": None,
            "polarity": "not_reported",
            "full_stack_raw": None,
            "layers": [],
            "absorber_formula": None,
            "absorber_properties": [],
            "absorber_constituents": [],
            "processing_steps": [],
            "evidence": [{"block_id": "main_p1_text_1", "quote": quote}],
        }
    ]
    return result


def test_export_requires_adjudication_as_the_latest_revision(
    tmp_path, empty_study, document_payload
):
    store = StudyReviewStore(tmp_path / "review")
    store.import_seed(
        SPLIT,
        PAPER_ID,
        empty_study,
        document=document_payload,
        manifest={},
        reviewer_id="ada",
    )
    with pytest.raises(ValueError, match="latest review revision"):
        build_ground_truth_export(store, SPLIT, PAPER_ID)

    bundle = _adjudicate(
        StudyReviewStore(tmp_path / "adjudicated"), empty_study, document_payload
    )
    adjudicated = StudyReviewStore(tmp_path / "adjudicated")
    adjudicated.mutate(
        SPLIT,
        PAPER_ID,
        MutationRequest(
            action="replace",
            path="/unresolved_notes/0",
            value="Edited after adjudication",
            evidence=[{"block_id": "main_p1_text_1", "quote": "champion device"}],
            base_revision=bundle["revision"],
        ),
        "ada",
    )
    with pytest.raises(ValueError, match="latest review revision"):
        build_ground_truth_export(adjudicated, SPLIT, PAPER_ID)


def test_export_revalidates_source_evidence(tmp_path, empty_study, document_payload):
    store = StudyReviewStore(tmp_path / "review")
    study = _study_with_evidence(empty_study, "not present in the source")
    _adjudicate(store, study, document_payload)
    with pytest.raises(ValueError, match="unresolved evidence issues"):
        build_ground_truth_export(store, SPLIT, PAPER_ID)


def test_export_is_deterministic_and_refuses_conflicting_overwrites(
    tmp_path, empty_study, document_payload
):
    store = StudyReviewStore(tmp_path / "review")
    bundle = _adjudicate(store, empty_study, document_payload)
    first = build_ground_truth_export(store, SPLIT, PAPER_ID)
    second = build_ground_truth_export(store, SPLIT, PAPER_ID)

    assert first == second
    assert first.manifest.revision == bundle["revision"]
    assert first.manifest.frozen_at == bundle["events"][-1]["timestamp"]
    assert first.manifest.artifact_format_version == 3
    assert first.manifest.evidence_version == 1
    assert len(first.manifest.evidence_document_sha256) == 64
    assert first.manifest.study_schema_version == STUDY_SCHEMA_VERSION
    assert first.manifest.study_schema_sha256 == study_schema_sha256()
    assert first.manifest.review.uncertain_record_keys == []
    assert set(first.manifest.files) == set(GROUND_TRUTH_FILENAMES) - {"manifest.json"}
    assert all(len(digest) == 64 for digest in first.manifest.files.values())

    target = write_ground_truth_export(first, tmp_path / "dataset" / "v1")
    assert write_ground_truth_export(second, tmp_path / "dataset" / "v1") == target
    assert sorted(path.name for path in target.iterdir()) == sorted(
        GROUND_TRUTH_FILENAMES
    )

    ground_truth_path = target / "ground_truth.json"
    altered = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    altered["unresolved_notes"] = ["Different curated result"]
    ground_truth_path.write_text(json.dumps(altered), encoding="utf-8")
    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_ground_truth_export(first, tmp_path / "dataset" / "v1")


def test_browser_archive_contains_the_same_four_stable_files(
    tmp_path, empty_study, document_payload
):
    store = StudyReviewStore(tmp_path / "review")
    _adjudicate(store, empty_study, document_payload)
    export = build_ground_truth_export(store, SPLIT, PAPER_ID)

    first = ground_truth_zip(export)
    assert first == ground_truth_zip(export)
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        assert sorted(archive.namelist()) == sorted(GROUND_TRUTH_FILENAMES)
        assert (
            json.loads(archive.read("ground_truth.json"))
            == export.files()["ground_truth.json"]
        )


def test_export_preserves_adjudicator_abstentions(
    tmp_path, empty_study, document_payload
):
    """An uncertain record must be masked instead of becoming a benchmark label."""

    store = StudyReviewStore(tmp_path / "review")
    study = _study_with_evidence(empty_study, "champion device")
    _adjudicate(store, study, document_payload, decision="uncertain")

    export = build_ground_truth_export(store, SPLIT, PAPER_ID)

    assert export.manifest.review.uncertain_record_keys == [
        "device_families:family-control"
    ]
