"""Public API for evidence-complete, device-centered study records.

Exports are loaded on first access so lightweight consumers of a single schema or
artifact helper do not import model providers, parsers, or optional export stacks.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "EvidenceBlock": ("models", "EvidenceBlock"),
    "EnrichmentAudit": ("enrichment", "EnrichmentAudit"),
    "EvidenceWindow": ("partitioning", "EvidenceWindow"),
    "EvidenceWindowPlan": ("partitioning", "EvidenceWindowPlan"),
    "IdentityLinkProposal": ("identity_linking", "IdentityLinkProposal"),
    "NOMADExport": ("nomad", "NOMADExport"),
    "ReducedExport": ("compatibility", "ReducedExport"),
    "StudyExtraction": ("models", "StudyExtraction"),
    "attach_valid_identity_links": ("identity_linking", "attach_valid_identity_links"),
    "combine_window_candidates": ("candidate_collection", "combine_window_candidates"),
    "namespace_window_candidates": (
        "candidate_collection",
        "namespace_window_candidates",
    ),
    "plan_evidence_windows": ("partitioning", "plan_evidence_windows"),
    "run_enrichment": ("enrichment", "run_enrichment"),
    "to_nomad_with_report": ("nomad", "to_nomad_with_report"),
    "to_reduced": ("compatibility", "to_reduced"),
    "to_reduced_with_report": ("compatibility", "to_reduced_with_report"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Load one public export without imposing unrelated optional dependencies."""

    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as error:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from error
    value = getattr(import_module(f".{module_name}", __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """Expose lazy names to documentation and interactive discovery tools."""

    return sorted(set(globals()) | set(__all__))
