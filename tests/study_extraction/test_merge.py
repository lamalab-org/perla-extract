import pytest

from perla_extract.study_extraction.merge import merge_candidates
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EvidenceRef,
    Paper,
    StudyExtraction,
)


def extraction(family_id: str) -> StudyExtraction:
    evidence = [EvidenceRef(block_id="b1", quote="reported stack")]
    return StudyExtraction(
        paper=Paper(title="Paper", doi="10.1/test"),
        device_families=[
            DeviceFamily(
                family_id=family_id,
                label="control",
                variant=None,
                architecture=None,
                polarity="not_reported",
                full_stack_raw=None,
                layers=[],
                absorber_formula=None,
                absorber_properties=[],
                absorber_constituents=[],
                processing_steps=[],
                evidence=evidence,
            )
        ],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[],
    )


def test_merge_is_a_lossless_namespaced_union():
    merged = merge_candidates([("w1", extraction("f1")), ("w2", extraction("f1"))])
    assert [item.family_id for item in merged.device_families] == ["w1:f1", "w2:f1"]


def test_merge_rejects_reused_window_namespace():
    with pytest.raises(ValueError, match="window IDs must be unique"):
        merge_candidates([("w1", extraction("f1")), ("w1", extraction("f2"))])


def test_merge_rejects_duplicate_ids_inside_a_window():
    duplicate = extraction("f1")
    duplicate.device_families.append(duplicate.device_families[0].model_copy())

    with pytest.raises(ValueError, match="entity IDs must be unique"):
        merge_candidates([("w1", duplicate)])
