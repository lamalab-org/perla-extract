"""Public API for evidence-complete, device-centered study records."""

from .candidate_collection import combine_window_candidates, namespace_window_candidates
from .compatibility import ReducedExport, to_reduced, to_reduced_with_report
from .identity_linking import IdentityLinkProposal, attach_valid_identity_links
from .models import EvidenceBlock, StudyExtraction
from .nomad import NOMADExport, to_nomad_with_report
from .partitioning import EvidenceWindow, EvidenceWindowPlan, plan_evidence_windows

__all__ = [
    "EvidenceBlock",
    "EvidenceWindow",
    "EvidenceWindowPlan",
    "IdentityLinkProposal",
    "NOMADExport",
    "ReducedExport",
    "StudyExtraction",
    "attach_valid_identity_links",
    "combine_window_candidates",
    "namespace_window_candidates",
    "plan_evidence_windows",
    "to_reduced",
    "to_reduced_with_report",
    "to_nomad_with_report",
]
