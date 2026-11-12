"""Interpret optional composition/process details and write downstream formats.

Enrichment proposals never overwrite the source-reported extraction. Failed or
unresolved proposals stay in their audit and the stage outcome for review.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from .artifacts import write_json_atomic
from .client import ModelClient
from .compatibility import to_reduced_with_report
from .configuration import ExtractionConfig
from .enrichment import EnrichmentAudit, run_enrichment
from .logging import logger
from .models import EvidenceBlock
from .nomad import NOMADExport, to_nomad_with_report
from .observability import EventJournal
from .stage_results import StageStatus
from .workflow_results import ExportResult, FinalStudy


def _write_nomad_artifacts(output_dir: Path, exported: NOMADExport) -> None:
    """Write uploadable archives separately while keeping one inspectable manifest."""

    payload = exported.model_dump(mode="json", exclude_none=True)
    write_json_atomic(output_dir / "nomad_export.json", payload)
    write_json_atomic(
        output_dir / "composition_projection.json",
        {
            "target_package": exported.target_package,
            "target_version": exported.target_version,
            "target_commit": exported.target_commit,
            "projections": [
                item.model_dump(mode="json", exclude_none=True)
                for item in exported.composition_projections
            ],
        },
    )
    archive_dir = output_dir / "nomad"
    for mapping in exported.mappings:
        archive = exported.archives[mapping.archive_index]
        write_json_atomic(
            archive_dir / mapping.archive_file,
            archive.model_dump(mode="json", exclude_none=True),
        )
    write_json_atomic(
        archive_dir / "manifest.json",
        {
            "target_package": exported.target_package,
            "target_version": exported.target_version,
            "target_commit": exported.target_commit,
            "mappings": [item.model_dump(mode="json") for item in exported.mappings],
            "issues": [
                item.model_dump(mode="json", exclude_none=True)
                for item in exported.issues
            ],
        },
    )


def _enrichment_status(enrichment: EnrichmentAudit) -> StageStatus:
    result_statuses = [result.status for result in enrichment.composition_results] + [
        result.status for result in enrichment.processing_results
    ]
    return (
        "failed"
        if enrichment.errors and not result_statuses
        else (
            "needs_review"
            if enrichment.errors
            or any(status != "accepted" for status in result_statuses)
            or enrichment.unresolved_absorber_ids
            or enrichment.unresolved_processing_step_ids
            else "complete"
        )
    )


def _interpret_and_export(
    config: ExtractionConfig,
    client: ModelClient,
    final: FinalStudy,
    blocks: list[EvidenceBlock],
    assembly_blocked: bool,
    journal: EventJournal,
) -> ExportResult:
    """Keep optional interpretations beside the reported study and export both."""
    extraction = final.extraction
    nomad: NOMADExport | None = None
    enrichment_status = None
    enrichment: EnrichmentAudit | None = None
    if config.use_enrichment and not assembly_blocked:
        logger.info("Interpreting composition and processing from local evidence")
        with journal.stage("enrich") as outcome:
            enrichment = run_enrichment(
                client=client,
                study=extraction,
                blocks=blocks,
                model=config.effective_model("enrich"),
                reasoning_effort=config.reasoning_effort,
                max_output_tokens=config.enrichment_max_output_tokens,
            )
            enrichment_status = _enrichment_status(enrichment)
            outcome.record(enrichment_status, errors=enrichment.errors)
            write_json_atomic(
                config.output_dir / "enrichment.json",
                enrichment.model_dump(mode="json"),
            )
            write_json_atomic(
                config.output_dir / "composition_proposals.json",
                {
                    "results": [
                        result.model_dump(mode="json")
                        for result in enrichment.composition_results
                    ]
                },
            )
            write_json_atomic(
                config.output_dir / "processing_proposals.json",
                {
                    "results": [
                        result.model_dump(mode="json")
                        for result in enrichment.processing_results
                    ]
                },
            )
    nomad_error: str | None = None
    try:
        with journal.stage("export"):
            nomad = to_nomad_with_report(
                extraction,
                model=config.effective_model("assemble"),
                enrichment=enrichment,
                evidence_blocks=blocks,
            )
            _write_nomad_artifacts(config.output_dir, nomad)
    except (ValidationError, ValueError) as exc:
        nomad = None
        nomad_error = str(exc)
        write_json_atomic(
            config.output_dir / "nomad_conversion_failure.json",
            {"error": nomad_error},
        )

    reduced_error: str | None = None
    if config.reduced_export:
        try:
            with journal.stage("export_reduced"):
                reduced = to_reduced_with_report(extraction)
                write_json_atomic(
                    config.output_dir / "reduced.json",
                    reduced.cells.model_dump(mode="json"),
                )
                write_json_atomic(
                    config.output_dir / "reduced_conversion.json",
                    reduced.model_dump(mode="json"),
                )
        except (ValidationError, ValueError) as exc:
            reduced_error = str(exc)
            write_json_atomic(
                config.output_dir / "reduced_conversion_failure.json",
                {"error": reduced_error},
            )

    return ExportResult(
        enrichment, enrichment_status, nomad, nomad_error, reduced_error
    )
