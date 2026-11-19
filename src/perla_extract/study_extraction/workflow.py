"""Build, check, and export one reported photovoltaic study from a paper and its SI."""

from __future__ import annotations

import time
from contextlib import nullcontext
from typing import Any

from .artifacts import write_json_atomic
from .candidate_selection import (
    _gate_final_candidate_against_draft,
    _select_refinement_candidate,
)
from .claims import (
    ClaimLedger,
    assembly_blocks,
    assembly_spans,
    audit_claim_coverage,
    grounded_ledger,
)
from .client import ModelCallError, ModelClient, summarize_calls
from .configuration import (
    ExtractionConfig,
)
from .deployment import (
    estimate_request_input,
    strict_response_schema,
)
from .enrichment import (
    EnrichmentAudit,
)
from .evidence import (
    repair_noncontiguous_citation_quotes,
    repair_unique_citation_pointers,
)
from .extraction_prompts import (
    SYSTEM_PROMPT,
    _assembly_components,
    _direct_prompt,
)
from .extraction_prompts import (
    prompt_sha256 as prompt_sha256,
)
from .finalization import remove_unsupported_optional_claims
from .logging import logger
from .models import (
    EvidenceBlock,
    PaperMetadata,
    StudyExtraction,
)
from .observability import EventJournal
from .refinement import refine_draft
from .repair import (
    RepairAudit,
    run_targeted_repair,
)
from .run_artifacts import write_run_manifest
from .run_configuration import build_run_configuration as _run_configuration
from .run_reporting import _completed_report, _dry_run_report
from .source import parse_documents
from .source_reading import (
    _collect_claim_ledger,
    _plan_claim_collection,
)
from .spans import build_evidence_spans
from .study_outputs import _interpret_and_export
from .tracing import create_langfuse_adapter
from .transport import (
    expand_span_citations,
    span_citation_schema,
)
from .validation import validate_study
from .workflow_results import (
    BuiltStudy,
    CorrectedStudy,
    FinalStudy,
    PreparedSource,
    ReadingResult,
    RunState,
)


def _empty_ledger() -> ClaimLedger:
    """Represent absence of usable claims without inventing extraction guidance."""

    return ClaimLedger(objects=[], claims=[])


def _approximate_tokens(
    prompt: str, schema: dict, *, tokenizer: str | None = None
) -> int:
    """Use the same serialized-input estimate as the provider request boundary."""

    estimate = estimate_request_input(
        SYSTEM_PROMPT,
        prompt,
        strict_response_schema(StudyExtraction, schema),
        tokenizer=tokenizer,
    )["estimated_input_tokens"]
    assert isinstance(estimate, int)
    return estimate


def _empty_extraction(note: str) -> StudyExtraction:
    """Produce a valid inspectable result even when no model call succeeds."""

    return StudyExtraction(
        paper=PaperMetadata(title=None, doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[note[:500]],
    )


def _summarize_usage(calls: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate charges from live calls; cache hits cost no tokens or money."""

    return summarize_calls(calls)


def _run_model_calls(
    config: ExtractionConfig,
    client: ModelClient,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger,
    journal: EventJournal | None = None,
) -> tuple[StudyExtraction, StudyExtraction | None, list[str]]:
    """Build one complete study, then optionally review it against the same source.

    Without usable reader notes both calls receive the complete parsed source.
    Otherwise they receive combined notes and selected supporting passages. A failed
    first call produces an explicitly empty result; a failed review retains its draft.
    """

    errors: list[str] = []
    initial_extraction: StudyExtraction | None = None
    prompt = _direct_prompt(blocks, ledger)
    spans = assembly_spans(blocks, ledger)
    logger.info("Building the complete study from source evidence")
    with journal.stage("assemble") if journal else nullcontext() as outcome:
        try:
            extraction = client.complete(
                stage="assemble",
                kind="complete_study",
                slug="complete_study",
                system=SYSTEM_PROMPT,
                prompt=prompt,
                prompt_components=_assembly_components(blocks, ledger),
                response_model=StudyExtraction,
                request_schema=span_citation_schema(StudyExtraction, spans),
                decode=lambda payload: expand_span_citations(payload, spans),
            )
        except ModelCallError as exc:
            errors.append(str(exc))
            extraction = _empty_extraction(
                "Complete-study model call failed; inspect requests/ and report.json."
            )
            if outcome is not None:
                outcome.record("failed", errors=[str(exc)], fallback="empty_extraction")
    if config.use_refinement and not errors:
        initial_extraction = extraction
        logger.info("Reconciling the complete draft against claims and source evidence")
        with journal.stage("refine") if journal else nullcontext() as outcome:
            extraction, error = refine_draft(
                client,
                draft=extraction,
                evidence_prompt=prompt,
                evidence_components=_assembly_components(blocks, ledger),
                blocks=blocks,
                model=config.effective_model("refine"),
                reasoning_effort=config.stage_policy("refine").reasoning_effort,
                max_output_tokens=config.stage_policy("refine").max_output_tokens,
                system_prompt=SYSTEM_PROMPT,
                kind="study_refinement",
                slug="study_refinement",
                draft_path=config.output_dir / "draft_extraction.json",
                audit_path=config.output_dir / "refinement_audit.json",
                spans=spans,
            )
            if error and outcome is not None:
                outcome.record("failed", errors=[error], fallback="retain_draft")
        if error:
            errors.append(f"study_refinement: {error}")
    return extraction, initial_extraction, errors


def _plan_extraction(
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger,
    config: ExtractionConfig | None = None,
) -> int:
    """Estimate the one global assembly request after claim-based compaction."""

    request_schema = span_citation_schema(
        StudyExtraction, assembly_spans(blocks, ledger)
    )
    if config is None:
        return _approximate_tokens(_direct_prompt(blocks, ledger), request_schema)
    return config.stage_policy("assemble").estimate(
        SYSTEM_PROMPT,
        _direct_prompt(blocks, ledger),
        strict_response_schema(StudyExtraction, request_schema),
    )["estimated_input_tokens"]


def run_extraction(config: ExtractionConfig) -> dict[str, object]:
    """Execute one immutable run, preserving failures and their known model charges.

    The destination must be absent or empty, including for dry runs. Each attempt
    owns its parse, configuration, prediction, report, and event journal; rerunning
    into another directory prevents stale predictions from acquiring new metadata.
    Optional source-grounding finalization can remove unsupported values, with the
    original candidate and exact removal decisions retained for review.
    """

    for path in (config.pdf, config.supplement):
        if path is not None and not path.is_file():
            raise FileNotFoundError(path)
    if config.output_dir.exists() and any(config.output_dir.iterdir()):
        raise FileExistsError(
            f"output directory is not empty: {config.output_dir}; use a new directory for each run"
        )
    config.output_dir.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also reserves an already-created empty directory atomically.
    (config.output_dir / "events.jsonl").touch(exist_ok=False)
    journal = EventJournal(config.output_dir)
    if config.langfuse:
        try:
            journal.adapter = create_langfuse_adapter(
                enabled=True,
                output_dir=config.output_dir,
                capture_content=config.langfuse_capture_content,
            )
        except Exception as error:
            logger.warning(
                "Trace export unavailable; retaining local events ({})",
                type(error).__name__,
            )
            journal.emit(
                "telemetry.failed", error_type=type(error).__name__, status="disabled"
            )
    journal.emit(
        "run.started",
        dry_run=config.dry_run,
        paper=config.pdf.name,
        supplement=config.supplement.name if config.supplement else None,
        models={
            stage: policy.model for stage, policy in config.stage_policies().items()
        },
        readings=config.claim_recall_passes if config.use_claim_ledger else 0,
    )
    state = RunState()
    try:
        report = _run_extraction(config, journal, state)
    except BaseException as error:
        client = state.client
        calls = client.calls if client is not None else []
        report = {
            "run_id": journal.run_id,
            "stages": [outcome.to_dict() for outcome in journal.stages],
            "status": "interrupted"
            if isinstance(error, (KeyboardInterrupt, SystemExit))
            else "failed",
            "errors": [str(error)],
            "calls": calls,
            "usage": _summarize_usage(calls),
            "budget": client.budget_status()
            if client is not None
            else {
                "provider_requests": 0,
                "cost_tracking_complete": True,
            },
            "elapsed_seconds": round(time.monotonic() - journal.started_at, 3),
            "source_parsing": state.source_parsing,
        }
        if not (config.output_dir / "run_configuration.json").exists():
            write_json_atomic(
                config.output_dir / "run_configuration.json",
                _run_configuration(config, "unplanned", [], []),
            )
        write_json_atomic(config.output_dir / "report.json", report)
        journal.emit(
            "run.finished",
            status=report["status"],
            error_type=type(error).__name__,
            errors=report.get("errors", []),
            usage=report.get("usage", {}),
        )
        write_run_manifest(config.output_dir, run_id=journal.run_id)
        raise
    report["stages"] = [outcome.to_dict() for outcome in journal.stages]
    report["run_id"] = journal.run_id
    write_json_atomic(config.output_dir / "report.json", report)
    journal.emit(
        "run.finished",
        status=report["status"],
        usage=report.get("usage", {}),
        errors=report.get("errors", []),
    )
    write_run_manifest(config.output_dir, run_id=journal.run_id)
    return report


def _prepare_source(
    config: ExtractionConfig, journal: EventJournal, state: RunState
) -> PreparedSource:
    """Parse and save source evidence, then plan calls without invoking a model."""
    logger.info("Parsing main paper{}", " and supplement" if config.supplement else "")
    with journal.stage("parse"):
        blocks, source_events = parse_documents(
            config.pdf,
            config.supplement,
            parser=config.parser,
            cache_dir=config.document_cache_dir,
            refresh_cache=config.refresh_document_cache,
            heartbeat_seconds=config.heartbeat_seconds,
            figure_table_parsing=config.figure_table_parsing,
        )
        state.source_parsing = source_events
        write_json_atomic(
            config.output_dir / "document.json",
            {"blocks": [block.model_dump(mode="json") for block in blocks]},
        )
        write_json_atomic(
            config.output_dir / "evidence_spans.json",
            {
                "spans": [
                    span.model_dump(mode="json")
                    for span in build_evidence_spans(blocks)
                ]
            },
        )
    with journal.stage("plan"):
        write_json_atomic(
            config.output_dir / "run_configuration.json",
            _run_configuration(config, "unplanned", source_events, blocks),
        )
        schema = StudyExtraction.model_json_schema()
        if config.use_claim_ledger:
            claim_mode, claim_tokens, claim_plan = _plan_claim_collection(
                config, blocks
            )
        else:
            claim_mode, claim_tokens, claim_plan = "disabled", 0, []
        approximate_tokens = _plan_extraction(blocks, _empty_ledger(), config)
        run_configuration = _run_configuration(
            config, claim_mode, source_events, blocks
        )
        write_json_atomic(
            config.output_dir / "run_configuration.json", run_configuration
        )
        write_json_atomic(config.output_dir / "extraction.schema.json", schema)
        write_json_atomic(
            config.output_dir / "claim_ledger.schema.json",
            ClaimLedger.model_json_schema(),
        )
        write_json_atomic(
            config.output_dir / "enrichment.schema.json",
            EnrichmentAudit.model_json_schema(),
        )
        logger.info(
            "Prepared {} evidence blocks (claims ~{} tokens, assembly ~{} tokens); claim_mode={}",
            len(blocks),
            claim_tokens,
            approximate_tokens,
            claim_mode,
        )

        claim_window_artifact = {
            "mode": claim_mode,
            "recall_passes": config.claim_recall_passes
            if config.use_claim_ledger
            else 0,
            "approximate_request_tokens": claim_tokens,
            "windows": [
                {
                    "window_id": slug,
                    "primary_block_ids": [block.block_id for block in primary],
                    "context_block_ids": [block.block_id for block in context],
                }
                for slug, primary, context in claim_plan
            ],
        }
        write_json_atomic(
            config.output_dir / "claim_window_plan.json", claim_window_artifact
        )

    return PreparedSource(
        blocks,
        source_events,
        claim_mode,
        len(claim_plan) * config.claim_recall_passes,
        approximate_tokens,
    )


def _read_source(
    config: ExtractionConfig,
    client: ModelClient,
    blocks: list[EvidenceBlock],
    journal: EventJournal,
) -> ReadingResult:
    """Ground reader notes; retain complete source when they cannot guide assembly."""
    errors: list[str] = []
    ledger: ClaimLedger | None = None
    grounded_claims: ClaimLedger | None = None
    selected_blocks = blocks
    if config.use_claim_ledger:
        logger.info("Collecting source claims before schema assembly")
        with journal.stage("read") as outcome:
            ledger, claim_errors, _ = _collect_claim_ledger(config, client, blocks)
            errors.extend(claim_errors)
            write_json_atomic(
                config.output_dir / "claim_ledger.json", ledger.model_dump(mode="json")
            )
            grounded_claims, claim_grounding = grounded_ledger(blocks, ledger)
            write_json_atomic(
                config.output_dir / "claim_grounding.json", claim_grounding
            )
            if not grounded_claims.objects and not grounded_claims.claims:
                errors.append(
                    "source claim ledger contained no grounded objects or claims"
                )
                grounded_claims = None
            else:
                selected_blocks = assembly_blocks(blocks, grounded_claims)
            if errors:
                outcome.record(
                    "partial" if grounded_claims is not None else "failed",
                    errors=errors,
                    fallback=None if grounded_claims is not None else "complete_source",
                )
    approximate_tokens = _plan_extraction(
        selected_blocks, grounded_claims or _empty_ledger(), config
    )
    journal.emit(
        "evidence.selected",
        source_blocks=len(blocks),
        selected_blocks=len(selected_blocks),
        selected_spans=len(
            assembly_spans(selected_blocks, grounded_claims or _empty_ledger())
        ),
        strategy="reader_notes" if grounded_claims is not None else "complete_source",
        estimated_assembly_input_tokens=approximate_tokens,
    )
    logger.info(
        "Selected {} of {} evidence blocks (~{} assembly request tokens)",
        len(selected_blocks),
        len(blocks),
        approximate_tokens,
    )
    return ReadingResult(selected_blocks, grounded_claims, errors, approximate_tokens)


def _build_study(
    config: ExtractionConfig,
    client: ModelClient,
    reading: ReadingResult,
    journal: EventJournal,
) -> BuiltStudy:
    selected_blocks = reading.selected_blocks
    grounded_claims = reading.grounded_claims
    approximate_tokens = reading.assembly_input_tokens
    limit_issues = config.stage_policy("assemble").limit_issues(approximate_tokens)
    assembly_blocked = bool(limit_issues)
    if assembly_blocked:
        message = f"assembly request estimate {approximate_tokens}: " + "; ".join(
            limit_issues
        )
        logger.error(message)
        with journal.stage("assemble") as outcome:
            outcome.record("blocked", errors=[message], fallback="empty_extraction")
        extraction = _empty_extraction(message)
        initial_extraction = None
        extraction_errors = [message]
    else:
        extraction, initial_extraction, extraction_errors = _run_model_calls(
            config,
            client,
            selected_blocks,
            grounded_claims or _empty_ledger(),
            journal=journal,
        )

    return BuiltStudy(
        extraction, initial_extraction, extraction_errors, assembly_blocked
    )


def _correct_study(
    config: ExtractionConfig,
    client: ModelClient,
    built: BuiltStudy,
    blocks: list[EvidenceBlock],
    grounded_claims: ClaimLedger | None,
    journal: EventJournal,
) -> CorrectedStudy:
    """Normalize citations, select a refinement, and check a targeted patch.

    Both the refinement and the final patched candidate are compared with the
    original draft. The saved audits explain every acceptance or fallback.
    """
    extraction = built.extraction
    initial_extraction = built.initial_extraction
    assembly_blocked = built.assembly_blocked
    extraction, quote_repairs = repair_noncontiguous_citation_quotes(extraction, blocks)
    extraction, pointer_repairs = repair_unique_citation_pointers(extraction, blocks)
    refinement_selection: dict[str, object] | None = None
    comparable_draft: StudyExtraction | None = None
    draft_quote_repairs: dict[str, Any] | None = None
    draft_pointer_repairs: dict[str, Any] | None = None
    if initial_extraction is not None:
        comparable_draft, draft_quote_repairs = repair_noncontiguous_citation_quotes(
            initial_extraction, blocks
        )
        comparable_draft, draft_pointer_repairs = repair_unique_citation_pointers(
            comparable_draft, blocks
        )
        if extraction != comparable_draft:
            write_json_atomic(
                config.output_dir / "refinement_extraction.json",
                extraction.model_dump(mode="json"),
            )
        extraction, refinement_selection = _select_refinement_candidate(
            comparable_draft, extraction, blocks, grounded_claims
        )
        journal.emit("candidate.selected", stage="refine", **refinement_selection)
        if refinement_selection["selected"] == "draft":
            quote_repairs = draft_quote_repairs
            pointer_repairs = draft_pointer_repairs
            logger.warning("Retaining the draft: {}", refinement_selection["reason"])
    citation_repairs = {
        "repair_count": quote_repairs["repair_count"] + pointer_repairs["repair_count"],
        "quote_repairs": quote_repairs,
        "pointer_repairs": pointer_repairs,
    }
    pre_repair_validation = validate_study(extraction, blocks)
    pre_repair_validation_public = dict(pre_repair_validation)
    pre_repair_validation_public.pop("verified_values")
    pre_repair_coverage = (
        audit_claim_coverage(grounded_claims, extraction)
        if grounded_claims is not None
        else None
    )
    repair_audit: RepairAudit | None = None
    if config.use_targeted_repair and not assembly_blocked:
        write_json_atomic(
            config.output_dir / "pre_repair_validation.json",
            pre_repair_validation_public,
        )
        if pre_repair_coverage is not None:
            write_json_atomic(
                config.output_dir / "pre_repair_claim_coverage_audit.json",
                pre_repair_coverage,
            )
        logger.info("Checking targeted text-only repair worklist")
        with journal.stage("repair") as outcome:
            extraction, repair_audit = run_targeted_repair(
                client=client,
                study=extraction,
                blocks=blocks,
                ledger=grounded_claims,
                coverage=pre_repair_coverage,
                validation=pre_repair_validation_public,
                model=config.effective_model("repair"),
                reasoning_effort=config.stage_policy("repair").reasoning_effort,
                max_output_tokens=config.stage_policy("repair").max_output_tokens,
            )
            if repair_audit.status == "failed":
                outcome.record(
                    "failed",
                    errors=[repair_audit.reason or "repair failed"],
                    fallback="retain_candidate",
                )
            elif repair_audit.status == "rejected":
                outcome.record("needs_review", fallback="retain_candidate")
        write_json_atomic(
            config.output_dir / "targeted_repair.json",
            repair_audit.model_dump(mode="json"),
        )
        journal.emit(
            "candidate.selected",
            stage="repair",
            selected=repair_audit.status,
            reason=repair_audit.reason,
            before_quality=repair_audit.before_quality,
            after_quality=repair_audit.after_quality,
        )
        if repair_audit.status == "accepted":
            extraction, repair_quote_repairs = repair_noncontiguous_citation_quotes(
                extraction, blocks
            )
            extraction, repair_pointer_repairs = repair_unique_citation_pointers(
                extraction, blocks
            )
            citation_repairs["repair_count"] += (
                repair_quote_repairs["repair_count"]
                + repair_pointer_repairs["repair_count"]
            )
            citation_repairs["targeted_quote_repairs"] = repair_quote_repairs
            citation_repairs["targeted_pointer_repairs"] = repair_pointer_repairs

    if comparable_draft is not None and refinement_selection is not None:
        final_candidate = extraction
        extraction, refinement_selection = _gate_final_candidate_against_draft(
            comparable_draft,
            final_candidate,
            blocks,
            grounded_claims,
            refinement_selection,
        )
        journal.emit("candidate.selected", stage="final_check", **refinement_selection)
        if extraction is comparable_draft and final_candidate != comparable_draft:
            assert draft_quote_repairs is not None
            assert draft_pointer_repairs is not None
            discarded_repairs = citation_repairs
            citation_repairs = {
                "repair_count": int(draft_quote_repairs["repair_count"])
                + int(draft_pointer_repairs["repair_count"]),
                "quote_repairs": draft_quote_repairs,
                "pointer_repairs": draft_pointer_repairs,
                "discarded_candidate_repairs": discarded_repairs,
            }
            logger.warning(
                "Retaining the draft because the final candidate regressed a "
                "grounded quality signal"
            )
        write_json_atomic(
            config.output_dir / "refinement_selection.json", refinement_selection
        )
    write_json_atomic(config.output_dir / "citation_repairs.json", citation_repairs)

    return CorrectedStudy(
        extraction, citation_repairs, repair_audit, refinement_selection
    )


def _finalize_study(
    config: ExtractionConfig,
    corrected: CorrectedStudy,
    built: BuiltStudy,
    blocks: list[EvidenceBlock],
    grounded_claims: ClaimLedger | None,
    journal: EventJournal,
) -> FinalStudy:
    """Save the source-grounded result and compare its integrity with the first draft."""
    extraction = corrected.extraction
    initial_extraction = built.initial_extraction
    pre_finalization = extraction
    with journal.stage("finalize"):
        extraction, conservative_finalization = remove_unsupported_optional_claims(
            extraction, blocks
        )
        journal.emit(
            "candidate.finalized",
            removal_count=conservative_finalization["removal_count"],
            audit="conservative_finalization.json",
        )
        if conservative_finalization["removal_count"]:
            write_json_atomic(
                config.output_dir / "pre_conservative_extraction.json",
                pre_finalization.model_dump(mode="json"),
            )
            logger.warning(
                "Removed {} unsupported optional atomic claim(s); full details remain "
                "in the finalization audit",
                conservative_finalization["removal_count"],
            )
        write_json_atomic(
            config.output_dir / "conservative_finalization.json",
            conservative_finalization,
        )

        write_json_atomic(
            config.output_dir / "extraction.json", extraction.model_dump(mode="json")
        )
    with journal.stage("validate", audit="validation.json") as outcome:
        validation = validate_study(extraction, blocks)
        grounded_values = validation.pop("verified_values")
        write_json_atomic(config.output_dir / "grounded_values.json", grounded_values)
        write_json_atomic(config.output_dir / "validation.json", validation)
        coverage_audit: dict[str, object] | None = None
        if grounded_claims is not None:
            coverage_audit = audit_claim_coverage(grounded_claims, extraction)
            write_json_atomic(
                config.output_dir / "claim_coverage_audit.json", coverage_audit
            )
        quality_comparison: dict[str, object] | None = None
        if initial_extraction is not None:
            comparable_draft, _ = repair_noncontiguous_citation_quotes(
                initial_extraction, blocks
            )
            comparable_draft, _ = repair_unique_citation_pointers(
                comparable_draft, blocks
            )
            draft_validation = validate_study(comparable_draft, blocks)
            draft_validation.pop("verified_values")
            write_json_atomic(
                config.output_dir / "draft_validation.json", draft_validation
            )
            draft_coverage = (
                audit_claim_coverage(grounded_claims, comparable_draft)
                if grounded_claims is not None
                else None
            )
            if draft_coverage is not None:
                write_json_atomic(
                    config.output_dir / "draft_claim_coverage_audit.json",
                    draft_coverage,
                )
            quality_comparison = {
                "draft_validation_issue_count": len(draft_validation["issues"]),
                "final_validation_issue_count": len(validation["issues"]),
                "draft_values": {
                    key: draft_validation["counts"][key]
                    for key in ("reported_values", "source_verified_values")
                },
                "final_values": {
                    key: validation["counts"][key]
                    for key in ("reported_values", "source_verified_values")
                },
                "draft_claim_coverage": (
                    draft_coverage["counts"] if draft_coverage else None
                ),
                "final_claim_coverage": (
                    coverage_audit["counts"] if coverage_audit else None
                ),
            }
            write_json_atomic(
                config.output_dir / "quality_comparison.json", quality_comparison
            )
        review_reasons = []
        if validation["status"] != "verified":
            review_reasons.append(
                f"source validation found {len(validation['issues'])} issue(s)"
            )
        if coverage_audit is not None and coverage_audit["status"] != "complete":
            review_reasons.append("reader-note coverage is incomplete")
        if review_reasons:
            outcome.record("needs_review", errors=review_reasons)
    return FinalStudy(
        extraction,
        validation,
        coverage_audit,
        quality_comparison,
        conservative_finalization,
    )


def _run_extraction(
    config: ExtractionConfig, journal: EventJournal, state: RunState
) -> dict[str, object]:
    """Execute operations in dependency order; each returns the next operation's input."""
    source = _prepare_source(config, journal, state)
    if config.dry_run:
        return _dry_run_report(config, source, journal)

    client = ModelClient(
        cache_dir=config.model_cache_dir,
        output_dir=config.output_dir,
        heartbeat_seconds=config.heartbeat_seconds,
        timeout_seconds=config.timeout_seconds,
        max_model_calls=config.max_model_calls,
        max_cost_usd=config.max_cost_usd,
        max_total_tokens=config.max_total_tokens,
        max_elapsed_seconds=config.max_elapsed_seconds,
        stage_policies=config.stage_policies(claim_mode=source.claim_mode),
        journal=journal,
    )
    state.client = client
    reading = _read_source(config, client, source.blocks, journal)
    built = _build_study(config, client, reading, journal)
    with journal.stage("correct") as outcome:
        corrected = _correct_study(
            config,
            client,
            built,
            source.blocks,
            reading.grounded_claims,
            journal,
        )
        if corrected.refinement_selection is not None:
            outcome.decisions.append(
                {"operation": "select_candidate", **corrected.refinement_selection}
            )
        if corrected.repair_audit is not None:
            audit = corrected.repair_audit
            outcome.decisions.append(
                {
                    "operation": "targeted_repair",
                    "selected": audit.status,
                    "reason": audit.reason,
                    "before_quality": audit.before_quality,
                    "after_quality": audit.after_quality,
                }
            )
    final = _finalize_study(
        config,
        corrected,
        built,
        source.blocks,
        reading.grounded_claims,
        journal,
    )
    exports = _interpret_and_export(
        config,
        client,
        final,
        source.blocks,
        built.assembly_blocked,
        journal,
    )
    return _completed_report(
        source, reading, built, corrected, final, exports, client, journal
    )
