"""Reference compatibility follows data constraints, not wording in model prompts."""

import json
from pathlib import Path

import pytest

from perla_extract.study_extraction.models import (
    StudyExtraction,
    compatible_study_schema,
    study_schema_prose_sha256,
    study_schema_sha256,
)
from perla_extract.study_extraction.reference_io import load_reference


def test_schema_prose_changes_leave_reference_contract_compatible(monkeypatch):
    original = StudyExtraction.model_json_schema()
    structural, prose = study_schema_sha256(), study_schema_prose_sha256()
    changed = json.loads(json.dumps(original))
    changed["description"] = (
        "Clearer instructions for extracting this scientific record."
    )
    monkeypatch.setattr(
        StudyExtraction, "model_json_schema", classmethod(lambda cls: changed)
    )
    assert study_schema_sha256() == structural
    assert study_schema_prose_sha256() != prose
    assert compatible_study_schema(
        "0f242f5cb719b47ae6bbcd1818ce8bd382b358136334ecc2abfb9de9db011413"
    )


def test_structural_change_rejects_archived_compatibility(monkeypatch):
    changed = StudyExtraction.model_json_schema()
    changed["additionalProperties"] = True
    monkeypatch.setattr(
        StudyExtraction, "model_json_schema", classmethod(lambda cls: changed)
    )
    assert not compatible_study_schema(
        "0f242f5cb719b47ae6bbcd1818ce8bd382b358136334ecc2abfb9de9db011413"
    )


def test_annotation_named_fields_are_still_part_of_schema_contract():
    from perla_extract.study_extraction.models import _schema_contract

    assert _schema_contract(
        {"description": "prose", "properties": {"description": {"type": "string"}}}
    ) == {"properties": {"description": {"type": "string"}}}


def test_all_frozen_references_remain_readable_without_rewriting():
    root = Path(__file__).resolve().parents[2] / "data/study_extraction/ground_truth"
    manifests = sorted(root.glob("v*/dev/*/manifest.json"))
    assert len(manifests) == 60
    for path in manifests:
        before = path.read_bytes()
        truth, provenance = load_reference(path.parent)
        assert truth and provenance is not None
        assert path.read_bytes() == before


@pytest.mark.parametrize("version", [2, 3])
def test_only_current_reference_artifact_format_is_loaded(tmp_path, version):
    (tmp_path / "ground_truth.json").write_text("{}")
    (tmp_path / "manifest.json").write_text(
        json.dumps({"artifact_format_version": version})
    )
    with pytest.raises(ValueError, match="format is unsupported"):
        load_reference(tmp_path)
