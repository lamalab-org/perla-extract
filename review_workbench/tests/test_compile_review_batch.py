from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from click.testing import CliRunner
from openpyxl import load_workbook
from openpyxl.comments import Comment

from perla_extract.study_extraction.models import study_schema_sha256
from review_workbench.compile_review_batch import compile_workbook, main
from review_workbench.spreadsheet_review import create_review_workbook
from review_workbench.study_review import (
    RECORD_IDENTIFIERS,
    RECORD_LABELS,
    RecordMergeRequest,
    StudyReviewStore,
)


@pytest.fixture
def batch(tmp_path, empty_study, document_payload):
    study = copy.deepcopy(empty_study)
    citation = {"block_id": "main_p1_text_1", "quote": "champion device"}
    study["device_families"] = [
        {
            "family_id": "family-1",
            "label": "Control",
            "variant": None,
            "architecture": None,
            "polarity": "not_reported",
            "full_stack_raw": None,
            "layers": [],
            "absorbers": [],
            "processing_steps": [],
            "evidence": [citation],
        }
    ]
    study["individual_devices"] = [
        {
            "device_id": "device-1",
            "family_id": "family-1",
            "label": "Champion",
            "variant": None,
            "champion_status": "yes",
            "selection_basis": "champion",
            "reported_properties": [],
            "evidence": [citation],
        }
    ]
    paper_id = "10.0000--example"
    run_dir = tmp_path / "runs" / paper_id
    run_dir.mkdir(parents=True)
    (run_dir / "extraction.json").write_text(json.dumps(study))
    (run_dir / "document.json").write_text(json.dumps(document_payload))
    workbook = create_review_workbook(
        truth=study,
        identifiers=RECORD_IDENTIFIERS,
        labels=RECORD_LABELS,
        paper_id=paper_id,
        split="dev",
        revision=1,
        schema_sha256=study_schema_sha256(),
        current_decisions={},
    )
    book = load_workbook(BytesIO(workbook))
    book["Record review"]["A2"] = "All fields match source"
    book["Record review"]["B2"] = "ok"
    book["Record review"]["A3"] = "All fields match source"
    book["Record review"]["B3"] = "Likely the same champion, but not explicit."
    output = BytesIO()
    book.save(output)
    workbook_path = tmp_path / "review.xlsx"
    workbook_path.write_bytes(output.getvalue())
    return study, run_dir, workbook_path


def test_stale_acceptance_does_not_verify_a_new_record_with_the_same_id(
    tmp_path, batch
):
    study, run_dir, workbook_path = batch
    newer_study = copy.deepcopy(study)
    newer_study["device_families"][0]["label"] = "Renamed control"
    (run_dir / "extraction.json").write_text(json.dumps(newer_study))

    manifest = compile_workbook(
        workbook_path, (tmp_path / "runs",), tmp_path / "compiled"
    )

    assessment = json.loads(
        (Path(manifest["output_path"]) / "adjudication.json").read_text()
    )
    assert manifest["record_counts"] == {
        "needs_adjudication": 2,
    }
    assert manifest["workbook_match"] == "older_seed_feedback_only"
    assert [
        item["provisional_status"] for item in assessment["record_assessments"]
    ] == ["needs_adjudication", "needs_adjudication"]
    assert assessment["record_assessments"][0]["adjudication_reason"] == (
        "stale_workbook_requires_reconciliation"
    )
    archived = Path(manifest["output_path"]) / "reviewer_workbook.xlsx"
    assert (
        hashlib.sha256(archived.read_bytes()).digest()
        == hashlib.sha256(workbook_path.read_bytes()).digest()
    )


def test_exact_workbook_acceptance_is_only_provisional(tmp_path, batch):
    _, _, workbook = batch
    result = compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    assert result["workbook_match"] == "exact_seed"
    assert result["record_counts"] == {"verified": 1, "needs_adjudication": 1}
    assert result["status"] == "provisional_needs_adjudication"


@pytest.mark.parametrize("feedback_kind", ["cell_comment", "note_only", "scalar_edit"])
def test_field_feedback_prevents_automatic_acceptance(tmp_path, batch, feedback_kind):
    _, _, workbook = batch
    book = load_workbook(workbook)
    sheet = book["Device Families"]
    headers = {cell.value: cell.column for cell in sheet[1]}
    if feedback_kind == "cell_comment":
        sheet.cell(2, headers["Extracted value"]).comment = Comment(
            "Check this", "Expert"
        )
    elif feedback_kind == "note_only":
        sheet.cell(2, headers["Reviewer note"], "This is not supported")
    else:
        # The family label is editable text; find it without depending on row order.
        row = next(
            row
            for row in sheet.iter_rows(min_row=2)
            if str(row[headers["Schema path"] - 1].value).endswith("/label")
        )
        sheet.cell(row[0].row, headers["Corrected value"], "New label")
        sheet.cell(row[0].row, headers["Reviewer note"], "Correct the label")
        sheet.cell(row[0].row, headers["Evidence block"], "main_p1_text_1")
        sheet.cell(row[0].row, headers["Evidence quote"], "champion device")
    book.save(workbook)
    result = compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    assert result["record_counts"] == {"needs_adjudication": 2}


def test_unreviewed_notes_are_preserved_and_packages_are_not_overwritten(
    tmp_path, batch
):
    _, _, workbook = batch
    first = compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    assert first == compile_workbook(
        workbook, (tmp_path / "runs",), tmp_path / "compiled"
    )
    book = load_workbook(workbook)
    book["Record review"]["A2"] = "Not reviewed"
    book["Record review"]["B2"] = "Please merge this with the other device"
    book.save(workbook)
    second = compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    assert first["output_path"] != second["output_path"]
    assert Path(first["output_path"]).is_dir()
    target = Path(second["output_path"])
    feedback = json.loads((target / "adjudication.json").read_text())
    assert any(
        c["text"] == "Please merge this with the other device"
        for c in feedback["workbook_comments"]
    )
    for filename, digest in second["files"].items():
        assert hashlib.sha256((target / filename).read_bytes()).hexdigest() == digest


def test_current_browser_merge_and_its_evidence_are_preserved(
    tmp_path, batch, document_payload
):
    study, _, workbook = batch
    study["individual_devices"].append(
        {**study["individual_devices"][0], "device_id": "duplicate"}
    )
    store = StudyReviewStore(tmp_path / "review")
    paper_id = "10.0000--example"
    store.import_seed(
        "dev",
        paper_id,
        study,
        document=document_payload,
        manifest={},
        reviewer_id="expert",
    )
    store.merge_records(
        "dev",
        paper_id,
        RecordMergeRequest(
            base_revision=1,
            collection="individual_devices",
            source_record_id="duplicate",
            target_record_id="device-1",
            note="Same device measured twice",
        ),
        "expert",
    )
    before = store.storage.load_revision("dev", paper_id).model_dump(mode="json")
    result = compile_workbook(
        workbook, (), tmp_path / "compiled", review_data=tmp_path / "review"
    )
    target = Path(result["output_path"])
    draft = json.loads((target / "provisional_ground_truth.json").read_text())
    assert len(draft["individual_devices"]) == 1
    assert json.loads((target / "review_revision.json").read_text()) == before
    assert json.loads((target / "document.json").read_text()) == document_payload
    assert (
        store.storage.load_revision("dev", paper_id).model_dump(mode="json") == before
    )
    assert result["source"] == {
        "kind": "saved_review",
        "revision": 2,
        "evidence_version": 1,
    }


def test_cli_rejects_ambiguous_input(tmp_path, batch):
    _, _, workbook = batch
    result = CliRunner().invoke(
        main,
        [
            "--workbook",
            str(workbook),
            "--run-root",
            str(tmp_path),
            "--review-data",
            str(tmp_path),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    assert result.exit_code == 1
    assert "choose either" in result.output


def test_failed_evidence_validation_prevents_provisional_verification(
    tmp_path, batch, document_payload
):
    _, run_dir, workbook = batch
    document_payload["blocks"][0]["text"] = "Unrelated source text."
    (run_dir / "document.json").write_text(json.dumps(document_payload))
    result = compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    assert result["workbook_match"] == "exact_seed"
    assert result["seed_validation"]["status"] != "verified"
    assert result["record_counts"] == {"needs_adjudication": 2}


def test_corrupted_package_is_not_silently_overwritten(tmp_path, batch):
    _, _, workbook = batch
    result = compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    original = Path(result["output_path"]) / "reviewer_workbook.xlsx"
    original.write_bytes(b"damaged archive")
    with pytest.raises(OSError):
        compile_workbook(workbook, (tmp_path / "runs",), tmp_path / "compiled")
    assert original.read_bytes() == b"damaged archive"
