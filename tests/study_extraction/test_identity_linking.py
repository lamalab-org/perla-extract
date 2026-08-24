"""Tests for explicit, non-destructive cross-window identity links."""

import pytest

from perla_extract.study_extraction.candidate_collection import (
    combine_window_candidates,
)
from perla_extract.study_extraction.identity_linking import (
    IdentityLinkProposal,
    attach_valid_identity_links,
)
from perla_extract.study_extraction.models import (
    CrossWindowIdentityLink,
    DeviceFamily,
    EvidenceCitation,
    PaperMetadata,
    StudyExtraction,
)


def extraction(family_id: str) -> StudyExtraction:
    """Build one minimal window result."""

    evidence = [EvidenceCitation(block_id="b1", quote="reported stack")]
    return StudyExtraction(
        paper=PaperMetadata(title="PaperMetadata", doi="10.1/test"),
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


def proposal(*candidate_ids: str) -> IdentityLinkProposal:
    """Build one evidence-backed family identity-link proposal."""

    return IdentityLinkProposal(
        identity_links=[
            CrossWindowIdentityLink(
                link_id="eq1",
                entity_kind="device_family",
                candidate_ids=list(candidate_ids),
                rationale="Both candidates explicitly use the control label.",
                evidence=[EvidenceCitation(block_id="b1", quote="reported stack")],
            )
        ],
        unresolved_notes=[],
    )


def test_valid_identity_link_is_attached_without_deleting_candidates():
    candidates = combine_window_candidates(
        [("w1", extraction("f1")), ("w2", extraction("f1"))]
    )

    linked_study, audit = attach_valid_identity_links(
        candidates, proposal("w1:f1", "w2:f1")
    )

    assert len(linked_study.device_families) == 2
    assert linked_study.identity_links[0].candidate_ids == ["w1:f1", "w2:f1"]
    assert audit.accepted_link_ids == ["eq1"]
    assert audit.issues == []


def test_unknown_candidate_is_rejected_but_preserved_in_audit():
    candidates = combine_window_candidates(
        [("w1", extraction("f1")), ("w2", extraction("f1"))]
    )

    linked_study, audit = attach_valid_identity_links(
        candidates, proposal("w1:f1", "missing:f1")
    )

    assert linked_study.identity_links == []
    assert audit.proposed_links[0].candidate_ids == ["w1:f1", "missing:f1"]
    assert "unknown device_family candidate IDs" in audit.issues[0].reason


def test_same_window_identity_link_is_rejected():
    candidates = extraction("f1")
    candidates.device_families.append(
        candidates.device_families[0].model_copy(update={"family_id": "f2"})
    )
    candidates = combine_window_candidates([("w1", candidates)])

    linked_study, audit = attach_valid_identity_links(
        candidates, proposal("w1:f1", "w1:f2")
    )

    assert linked_study.identity_links == []
    assert "different windows" in audit.issues[0].reason


def test_identity_linking_rejects_ambiguous_source_ids():
    candidates = combine_window_candidates([("w1", extraction("f1"))])
    candidates.device_families.append(candidates.device_families[0].model_copy())

    with pytest.raises(ValueError, match="ambiguous entity IDs"):
        attach_valid_identity_links(candidates, proposal("w1:f1", "w2:f1"))
