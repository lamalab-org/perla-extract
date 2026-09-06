from __future__ import annotations

import copy
import hashlib
import json
from io import BytesIO

from openpyxl import load_workbook

from perla_extract.study_extraction.models import study_schema_sha256
from review_workbench.compile_review_batch import compile_workbook
from review_workbench.spreadsheet_review import create_review_workbook
from review_workbench.study_review import RECORD_IDENTIFIERS, RECORD_LABELS


def test_compile_workbook_keeps_only_unqualified_acceptance_in_verified_subset(
    tmp_path, empty_study, document_payload
):
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
    newer_study = copy.deepcopy(study)
    newer_study["device_families"][0]["label"] = "Renamed control"
    (run_dir / "extraction.json").write_text(json.dumps(newer_study))

    manifest = compile_workbook(
        workbook_path, (tmp_path / "runs",), tmp_path / "compiled"
    )

    assessment = json.loads(
        (tmp_path / "compiled" / paper_id / "adjudication.json").read_text()
    )
    assert manifest["record_counts"] == {
        "verified": 1,
        "needs_adjudication": 1,
    }
    assert manifest["workbook_match"] == "older_seed_feedback_only"
    assert [
        item["provisional_status"] for item in assessment["record_assessments"]
    ] == ["verified", "needs_adjudication"]
    archived = tmp_path / "compiled" / paper_id / "reviewer_workbook.xlsx"
    assert (
        hashlib.sha256(archived.read_bytes()).digest()
        == hashlib.sha256(workbook_path.read_bytes()).digest()
    )
