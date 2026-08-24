"""Audit explicit identity links between candidates from different windows."""

from __future__ import annotations

from pydantic import Field, model_validator

from .identifiers import duplicate_entity_ids, entity_id_lists, window_namespace
from .models import CrossWindowIdentityLink, ShortText, StrictModel, StudyExtraction


class IdentityLinkProposal(StrictModel):
    """Hold proposed cross-window identity links without changing candidates."""

    identity_links: list[CrossWindowIdentityLink]
    unresolved_notes: list[ShortText]

    @model_validator(mode="after")
    def validate_link_ids(self) -> IdentityLinkProposal:
        """Keep link identifiers unique so audits can address each proposal."""

        identifiers = [link.link_id for link in self.identity_links]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("link_id values must be unique")
        return self


class IdentityLinkIssue(StrictModel):
    """Explain why one proposed link was not attached to the extraction."""

    link_id: str
    reason: str


class IdentityLinkAudit(StrictModel):
    """Preserve every proposal together with accepted links and semantic issues."""

    proposed_links: list[CrossWindowIdentityLink]
    accepted_link_ids: list[str]
    issues: list[IdentityLinkIssue]
    unresolved_notes: list[ShortText] = Field(default_factory=list)


def attach_valid_identity_links(
    study: StudyExtraction, result: IdentityLinkProposal
) -> tuple[StudyExtraction, IdentityLinkAudit]:
    """Attach only well-formed identity sets and report every rejected proposal.

    A candidate must exist in the declared entity collection and may occur in at most
    one accepted link of that kind. Rejected proposals remain visible in the audit;
    no candidate record is deleted or rewritten.
    """

    duplicates = duplicate_entity_ids(study)
    if duplicates:
        raise ValueError(f"cannot link ambiguous entity IDs: {duplicates}")
    valid_ids = {kind: set(ids) for kind, ids in entity_id_lists(study).items()}
    claimed: set[tuple[str, str]] = set()
    accepted_links: list[CrossWindowIdentityLink] = []
    issues: list[IdentityLinkIssue] = []
    for link in result.identity_links:
        namespaces = {
            window_namespace(candidate_id) for candidate_id in link.candidate_ids
        }
        unknown = sorted(set(link.candidate_ids) - valid_ids[link.entity_kind])
        overlap = sorted(
            candidate_id
            for candidate_id in link.candidate_ids
            if (link.entity_kind, candidate_id) in claimed
        )
        if unknown:
            issues.append(
                IdentityLinkIssue(
                    link_id=link.link_id,
                    reason=f"unknown {link.entity_kind} candidate IDs: {unknown}",
                )
            )
            continue
        if None in namespaces or len(namespaces) < 2:
            issues.append(
                IdentityLinkIssue(
                    link_id=link.link_id,
                    reason="linked candidates must come from different windows",
                )
            )
            continue
        if overlap:
            issues.append(
                IdentityLinkIssue(
                    link_id=link.link_id,
                    reason=f"candidates already claimed by another link: {overlap}",
                )
            )
            continue
        accepted_links.append(link)
        claimed.update(
            (link.entity_kind, candidate_id) for candidate_id in link.candidate_ids
        )

    audit = IdentityLinkAudit(
        proposed_links=result.identity_links,
        accepted_link_ids=[link.link_id for link in accepted_links],
        issues=issues,
        unresolved_notes=result.unresolved_notes,
    )
    linked_study = study.model_copy(
        update={
            "identity_links": accepted_links,
            "unresolved_notes": [
                *study.unresolved_notes,
                *result.unresolved_notes,
            ],
        }
    )
    return linked_study, audit
