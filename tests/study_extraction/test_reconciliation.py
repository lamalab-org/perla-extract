"""Tests for explicit, non-destructive identity reconciliation."""

from perla_extract.study_extraction.merge import merge_candidates
from perla_extract.study_extraction.models import (
    DeviceFamily,
    EquivalenceGroup,
    EvidenceRef,
    Paper,
    StudyExtraction,
)
from perla_extract.study_extraction.reconciliation import (
    ReconciliationResult,
    attach_valid_equivalences,
)


def extraction(family_id: str) -> StudyExtraction:
    """Build one minimal window result."""

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


def proposal(*member_ids: str) -> ReconciliationResult:
    """Build one evidence-backed family-equivalence proposal."""

    return ReconciliationResult(
        equivalence_groups=[
            EquivalenceGroup(
                equivalence_id="eq1",
                entity_kind="device_family",
                member_ids=list(member_ids),
                rationale="Both candidates explicitly use the control label.",
                evidence=[EvidenceRef(block_id="b1", quote="reported stack")],
            )
        ],
        unresolved_notes=[],
    )


def test_valid_equivalence_is_attached_without_deleting_candidates():
    candidates = merge_candidates(
        [("w1", extraction("f1")), ("w2", extraction("f1"))]
    )

    reconciled, audit = attach_valid_equivalences(
        candidates, proposal("w1:f1", "w2:f1")
    )

    assert len(reconciled.device_families) == 2
    assert reconciled.equivalence_groups[0].member_ids == ["w1:f1", "w2:f1"]
    assert audit.accepted_group_ids == ["eq1"]
    assert audit.issues == []


def test_unknown_member_is_rejected_but_preserved_in_audit():
    candidates = merge_candidates(
        [("w1", extraction("f1")), ("w2", extraction("f1"))]
    )

    reconciled, audit = attach_valid_equivalences(
        candidates, proposal("w1:f1", "missing:f1")
    )

    assert reconciled.equivalence_groups == []
    assert audit.proposed_groups[0].member_ids == ["w1:f1", "missing:f1"]
    assert "unknown device_family member IDs" in audit.issues[0].reason
