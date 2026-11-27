"""Summarize execution outcomes without making new scientific decisions.

The event journal owns stage outcomes; the client owns attempt accounting. Reports
consume those results. A successful optional stage cannot rescue failed assembly.
"""

from __future__ import annotations

import time

from .client import ModelClient, summarize_calls
from .configuration import ExtractionConfig
from .observability import EventJournal
from .workflow_results import (
    BuiltStudy,
    CorrectedStudy,
    ExportResult,
    FinalStudy,
    PreparedSource,
    ReadingResult,
)


def _dry_run_report(
    config: ExtractionConfig, source: PreparedSource, journal: EventJournal
) -> dict[str, object]:
    blocks = source.blocks
    source_events = source.source_events
    claim_mode = source.claim_mode
    approximate_tokens = source.assembly_input_tokens
    known_issues = (
        config.stage_policy("assemble").limit_issues(approximate_tokens)
        if not config.use_claim_ledger
        else []
    )
    if not config.use_claim_ledger and config.max_total_tokens is not None:
        reserved_tokens = (
            approximate_tokens + config.stage_policy("assemble").max_output_tokens
        )
        if reserved_tokens > config.max_total_tokens:
            known_issues.append(
                f"first live request needs {reserved_tokens} estimated input/output tokens; "
                f"exceeds max_total_tokens={config.max_total_tokens}"
            )
    feasibility = {
        "assembly": "conditional_on_reader_output"
        if config.use_claim_ledger
        else ("infeasible" if known_issues else "fits_input_estimate"),
        "later_stages": "conditional_on_generated_records",
        "note": "Input sizes are estimates; later drafts and provider retries are unknown.",
    }
    journal.emit("request.preflight", **feasibility, errors=known_issues)
    extraction_call_count = 1
    claim_call_count = source.planned_readings if config.use_claim_ledger else 0
    planned_calls = (
        extraction_call_count
        + extraction_call_count * int(config.use_refinement)
        + claim_call_count
        + (3 if config.use_enrichment else 0)
        + int(config.use_targeted_repair)
    )
    report: dict[str, object] = {
        "status": "failed" if known_issues else "dry_run",
        "dry_run": True,
        "request_feasibility": feasibility,
        "errors": known_issues,
        "calls": [],
        "usage": summarize_calls([]),
        "claim_mode": claim_mode,
        "assembly_mode": "single",
        "evidence_blocks": len(blocks),
        "selected_evidence_blocks": len(blocks),
        "approximate_request_tokens": approximate_tokens,
        "planned_calls": planned_calls,
        "planned_refinement_calls": (
            extraction_call_count if config.use_refinement else 0
        ),
        "planned_claim_calls": claim_call_count,
        "planned_enrichment_calls_max": 3 if config.use_enrichment else 0,
        "planned_targeted_repair_calls_max": int(config.use_targeted_repair),
        "budget": {
            "max_model_calls": config.max_model_calls,
            "max_cost_usd": config.max_cost_usd,
            "max_total_tokens": config.max_total_tokens,
            "provider_requests": 0,
            "cost_tracking_complete": True,
            "planned_calls_fit": (
                config.max_model_calls is None
                or planned_calls <= config.max_model_calls
            ),
        },
        "source_parsing": source_events,
        "elapsed_seconds": round(time.monotonic() - journal.started_at, 3),
    }
    return report


def _completed_report(
    source: PreparedSource,
    reading: ReadingResult,
    built: BuiltStudy,
    corrected: CorrectedStudy,
    final: FinalStudy,
    exports: ExportResult,
    client: ModelClient,
    journal: EventJournal,
) -> dict[str, object]:
    blocks = source.blocks
    source_events = source.source_events
    claim_mode = source.claim_mode
    selected_blocks = reading.selected_blocks
    approximate_tokens = reading.assembly_input_tokens
    errors = reading.errors + built.errors
    validation = final.validation
    coverage_audit = final.coverage_audit
    quality_comparison = final.quality_comparison
    conservative_finalization = final.conservative_finalization
    citation_repairs = corrected.citation_repairs
    refinement_selection = corrected.refinement_selection
    repair_audit = corrected.repair_audit
    enrichment = exports.enrichment
    enrichment_status = exports.enrichment_status
    nomad = exports.nomad
    nomad_error = exports.nomad_error
    counts = validation["counts"]
    assembled = any(
        outcome.stage == "assemble" and outcome.status == "complete"
        for outcome in journal.stages
    )
    status = (
        "failed"
        if not assembled
        else (
            "partial"
            if errors or nomad_error
            else (
                "complete"
                if validation["status"] == "verified"
                and (coverage_audit is None or coverage_audit["status"] == "complete")
                and enrichment_status not in {"failed", "needs_review"}
                else "complete_needs_review"
            )
        )
    )
    report = {
        "status": status,
        "claim_mode": claim_mode,
        "assembly_mode": "single",
        "evidence_blocks": len(blocks),
        "selected_evidence_blocks": len(selected_blocks),
        "approximate_request_tokens": approximate_tokens,
        **counts,
        "validation_issue_count": len(validation["issues"]),
        "citation_repair_count": citation_repairs["repair_count"],
        "conservative_removal_count": conservative_finalization["removal_count"],
        "refinement_calls": sum(call.get("stage") == "refine" for call in client.calls),
        "targeted_repair_status": repair_audit.status if repair_audit else "disabled",
        "quality_comparison": quality_comparison,
        "refinement_selection": refinement_selection,
        "claim_coverage": coverage_audit["counts"] if coverage_audit else None,
        "enrichment_status": enrichment_status,
        "enrichment_errors": enrichment.errors if enrichment else [],
        "accepted_composition_proposals": sum(
            result.status == "accepted"
            for result in (enrichment.composition_results if enrichment else [])
        ),
        "accepted_processing_proposals": sum(
            result.status == "accepted"
            for result in (enrichment.processing_results if enrichment else [])
        ),
        "unresolved_composition_proposals": (
            len(enrichment.unresolved_absorber_ids) if enrichment else 0
        ),
        "unresolved_processing_proposals": (
            len(enrichment.unresolved_processing_step_ids) if enrichment else 0
        ),
        "nomad_archive_count": len(nomad.archives) if nomad is not None else 0,
        "nomad_issue_count": len(nomad.issues) if nomad is not None else 0,
        "nomad_conversion_error": nomad_error,
        "errors": errors,
        "calls": client.calls,
        "usage": summarize_calls(client.calls),
        "budget": client.budget_status(),
        "source_parsing": source_events,
        "elapsed_seconds": round(time.monotonic() - journal.started_at, 3),
    }
    return report
