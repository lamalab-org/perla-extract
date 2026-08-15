"""Audit explicit identity links between candidates from different windows."""

from __future__ import annotations

from pydantic import Field, model_validator

from .models import EquivalenceGroup, ShortText, StrictModel, StudyExtraction


class ReconciliationResult(StrictModel):
    """Hold model-proposed equivalence links without changing source candidates."""

    equivalence_groups: list[EquivalenceGroup]
    unresolved_notes: list[ShortText]

    @model_validator(mode="after")
    def validate_group_ids(self) -> ReconciliationResult:
        """Keep proposal identifiers unique so reports can address each group."""

        identifiers = [group.equivalence_id for group in self.equivalence_groups]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("equivalence_id values must be unique")
        return self


class ReconciliationIssue(StrictModel):
    """Explain why one proposed link was not attached to the extraction."""

    equivalence_id: str
    reason: str


class ReconciliationAudit(StrictModel):
    """Preserve every proposal together with accepted links and semantic issues."""

    proposed_groups: list[EquivalenceGroup]
    accepted_group_ids: list[str]
    issues: list[ReconciliationIssue]
    unresolved_notes: list[ShortText] = Field(default_factory=list)


def _entity_ids(study: StudyExtraction) -> dict[str, set[str]]:
    """Index valid identifiers by entity kind for semantic link validation."""

    return {
        "device_family": {item.family_id for item in study.device_families},
        "individual_device": {item.device_id for item in study.individual_devices},
        "performance_observation": {
            item.observation_id for item in study.performance_observations
        },
        "population_statistic": {
            item.population_id for item in study.population_statistics
        },
        "stability_test": {item.test_id for item in study.stability_tests},
    }


def attach_valid_equivalences(
    study: StudyExtraction, result: ReconciliationResult
) -> tuple[StudyExtraction, ReconciliationAudit]:
    """Attach only well-formed identity sets and report every rejected proposal.

    A member must exist in the declared entity collection and may occur in at most
    one accepted group of that kind. Rejected proposals remain visible in the audit;
    no candidate record is deleted or rewritten.
    """

    valid_ids = _entity_ids(study)
    claimed: set[tuple[str, str]] = set()
    accepted: list[EquivalenceGroup] = []
    issues: list[ReconciliationIssue] = []
    for group in result.equivalence_groups:
        unknown = sorted(set(group.member_ids) - valid_ids[group.entity_kind])
        overlap = sorted(
            member
            for member in group.member_ids
            if (group.entity_kind, member) in claimed
        )
        if unknown:
            issues.append(
                ReconciliationIssue(
                    equivalence_id=group.equivalence_id,
                    reason=f"unknown {group.entity_kind} member IDs: {unknown}",
                )
            )
            continue
        if overlap:
            issues.append(
                ReconciliationIssue(
                    equivalence_id=group.equivalence_id,
                    reason=f"members already claimed by another group: {overlap}",
                )
            )
            continue
        accepted.append(group)
        claimed.update((group.entity_kind, member) for member in group.member_ids)

    audit = ReconciliationAudit(
        proposed_groups=result.equivalence_groups,
        accepted_group_ids=[group.equivalence_id for group in accepted],
        issues=issues,
        unresolved_notes=result.unresolved_notes,
    )
    reconciled = study.model_copy(
        update={
            "equivalence_groups": accepted,
            "unresolved_notes": [
                *study.unresolved_notes,
                *result.unresolved_notes,
            ],
        }
    )
    return reconciled, audit
