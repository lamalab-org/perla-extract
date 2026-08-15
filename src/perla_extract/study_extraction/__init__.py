"""Public API for evidence-complete, device-centered study records."""

from .compatibility import ReducedExport, to_reduced, to_reduced_with_report
from .merge import merge_candidates, namespace_candidates
from .models import EvidenceBlock, StudyExtraction
from .partitioning import EvidenceWindow, WindowPlan, plan_windows
from .reconciliation import ReconciliationResult, attach_valid_equivalences

__all__ = [
    "EvidenceBlock",
    "EvidenceWindow",
    "ReducedExport",
    "ReconciliationResult",
    "StudyExtraction",
    "WindowPlan",
    "attach_valid_equivalences",
    "merge_candidates",
    "namespace_candidates",
    "plan_windows",
    "to_reduced",
    "to_reduced_with_report",
]
