"""Values passed between extraction operations.

Each result records what the next operation needs. The mutable RunState exists
only to preserve paid attempts and parse diagnostics if execution is interrupted.
It is not a second owner of configuration or accounting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .claims import ClaimLedger
from .client import ModelClient
from .enrichment import EnrichmentAudit
from .models import EvidenceBlock, StudyExtraction
from .nomad import NOMADExport
from .repair import RepairAudit


@dataclass
class RunState:
    client: ModelClient | None = None
    source_parsing: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class PreparedSource:
    blocks: list[EvidenceBlock]
    source_events: list[dict]
    claim_mode: str
    planned_readings: int
    assembly_input_tokens: int


@dataclass(frozen=True)
class ReadingResult:
    selected_blocks: list[EvidenceBlock]
    grounded_claims: ClaimLedger | None
    errors: list[str]
    assembly_input_tokens: int


@dataclass(frozen=True)
class BuiltStudy:
    extraction: StudyExtraction
    initial_extraction: StudyExtraction | None
    errors: list[str]
    assembly_blocked: bool


@dataclass(frozen=True)
class CorrectedStudy:
    extraction: StudyExtraction
    citation_repairs: dict[str, Any]
    repair_audit: RepairAudit | None
    refinement_selection: dict[str, object] | None


@dataclass(frozen=True)
class FinalStudy:
    extraction: StudyExtraction
    validation: dict[str, Any]
    coverage_audit: dict[str, Any] | None
    quality_comparison: dict[str, object] | None
    conservative_finalization: dict[str, Any]


@dataclass(frozen=True)
class ExportResult:
    enrichment: EnrichmentAudit | None
    enrichment_status: str | None
    nomad: NOMADExport | None
    nomad_error: str | None
    reduced_error: str | None
