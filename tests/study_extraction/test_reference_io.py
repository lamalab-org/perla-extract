"""References use one current data model and retain the producer's provenance."""

import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.models import (
    StudyExtraction,
    study_schema_prose_sha256,
    study_schema_sha256,
)
from perla_extract.study_extraction.reference_io import (
    load_reference,
    reference_content_digest,
)


def test_schema_prose_changes_leave_the_data_contract_fingerprint_unchanged(
    monkeypatch,
):
    original = StudyExtraction.model_json_schema()
    structural, prose = study_schema_sha256(), study_schema_prose_sha256()
    changed = json.loads(json.dumps(original))
    changed["description"] = "Clearer scientific extraction instructions."
    monkeypatch.setattr(
        StudyExtraction, "model_json_schema", classmethod(lambda cls: changed)
    )
    assert study_schema_sha256() == structural
    assert study_schema_prose_sha256() != prose


def test_annotation_named_fields_are_still_part_of_schema_contract():
    from perla_extract.study_extraction.models import _schema_contract

    assert _schema_contract(
        {"description": "prose", "properties": {"description": {"type": "string"}}}
    ) == {"properties": {"description": {"type": "string"}}}


def test_frozen_references_validate_without_upgrades_or_rewriting():
    root = (
        Path(__file__).resolve().parents[2]
        / "data/study_extraction/ground_truth/v2/dev"
    )
    manifests = sorted(root.glob("*/manifest.json"))
    assert len(manifests) == 30
    for path in manifests:
        before = path.read_bytes()
        truth, provenance = load_reference(path.parent)
        assert truth and provenance is not None
        assert path.read_bytes() == before


@pytest.fixture
def reference_copy(tmp_path):
    root = (
        Path(__file__).resolve().parents[2]
        / "data/study_extraction/ground_truth/v2/dev"
    )
    original = next(root.glob("*/manifest.json")).parent
    for name in ("manifest.json", "ground_truth.json"):
        (tmp_path / name).write_bytes((original / name).read_bytes())
    return tmp_path


def test_producer_fingerprint_cannot_override_current_payload_validation(
    reference_copy,
):
    path = reference_copy / "ground_truth.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["retired_field"] = []
    path.write_text(json.dumps(payload), encoding="utf-8")
    manifest_path = reference_copy / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"]["ground_truth.json"] = reference_content_digest(payload)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        load_reference(reference_copy)


@pytest.mark.parametrize(
    "field,value",
    [
        ("study_schema_sha256", "unknown"),
        ("study_schema_version", 0),
        ("study_schema_version", True),
    ],
)
def test_reference_requires_valid_producer_metadata(reference_copy, field, value):
    path = reference_copy / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest[field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="producer schema version and SHA-256"):
        load_reference(reference_copy)


@pytest.mark.parametrize("version", [2, 3])
def test_only_current_reference_artifact_format_is_loaded(tmp_path, version):
    (tmp_path / "ground_truth.json").write_text("{}", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifact_format_version": version}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="format is unsupported"):
        load_reference(tmp_path)
