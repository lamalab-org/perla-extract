"""End-to-end, high-recall extraction of a paper and its supplementary information."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from .artifacts import write_json_atomic
from .claims import (
    ClaimLedger,
    assembly_blocks,
    assembly_evidence_payload,
    assembly_spans,
    audit_claim_coverage,
    combine_ledgers,
    grounded_ledger,
)
from .client import ModelCallError, ModelClient
from .compatibility import to_reduced_with_report
from .enrichment import (
    COMPOSITION_ENRICHMENT_PROMPT,
    ENRICHMENT_SYSTEM_PROMPT,
    PROCESSING_ENRICHMENT_PROMPT,
    EnrichmentAudit,
    run_enrichment,
)
from .evidence import (
    repair_noncontiguous_citation_quotes,
    repair_unique_citation_pointers,
)
from .guidance import DEVICE_FAMILY_POLICY, SHARED_QUANTITY_POLICY
from .logging import logger
from .models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    PaperMetadata,
    StudyExtraction,
    study_schema_sha256,
)
from .nomad import NOMADExport, to_nomad_with_report
from .partitioning import plan_evidence_windows
from .refinement import REFINEMENT_PROMPT, refine_draft
from .repair import (
    REPAIR_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    RepairAudit,
    candidate_quality,
    run_targeted_repair,
)
from .source import parse_documents
from .spans import build_evidence_spans, evidence_payload, evidence_spans_sha256
from .transport import (
    compact_to_span_citations,
    expand_span_citations,
    span_citation_schema,
)
from .validation import validate_study

DEFAULT_EXTRACTION_MODEL = "openrouter/openai/gpt-5.6-sol:exacto"
DEFAULT_CLAIM_MODEL = DEFAULT_EXTRACTION_MODEL

SYSTEM_PROMPT = """Extract the complete present photovoltaic study as device-centered data.
Use only supplied evidence. Preserve source wording and distinctions. Never invent,
interpolate, digitize graph traces, or import values from cited literature."""

EXTRACTION_PROMPT = f"""Read all supplied evidence before extracting.

Identify every distinct photovoltaic device family made in the present study. Then
identify every individually measured device, performance
observation, population statistic, and stability specimen. Connect records only when
the evidence supports the connection.

{DEVICE_FAMILY_POLICY}
{SHARED_QUANTITY_POLICY}
Rules:
- Preserve a device as one coherent object. Do not create one device per metric,
  paragraph, layer, scan direction, or stability checkpoint.
- Put a fabrication or material value that distinguishes one measured specimen in
  that IndividualDevice's reported_properties, not among family-wide conditions.
- Keep individual devices and population statistics in separate top-level arrays.
  Means, medians, distributions, sample sizes, ranges, and population maxima are not
  individual-device measurements.
- Mark a device champion only when the source explicitly identifies the device as a
  champion/best-performing device or its efficiency as record/highest. An extremum
  for one attribute (for example voltage, current, or stability) does not make the
  device a champion. Otherwise use not_reported.
- Keep reverse scans, forward scans, stabilized output, certified measurements, and
  EQE-integrated currents as distinct observations of the same device where supported.
- A performance observation requires at least one reported result. A statement that a
  measurement or spectrum exists, without any reported outcome, is context rather than
  a performance observation. Non-numeric outcomes are valid when the source actually
  reports them; do not use a method name or specimen description as a metric value.
- Extract the complete ordered layer stack and all reported processing steps. Create
  one scoped absorber record per absorber layer or subcell, keeping each absorber's
  formula, constituents, properties, additives, and dopants together. Never combine
  wide-bandgap and narrow-bandgap tandem chemistry into one composition.
  Use one MaterialConstituent per named chemical; do not combine a list of chemicals
  into one constituent.
- For every layer, keep its electrical role, chemical constituents, and physical form
  separate. Copy material_form_raw only when the source explicitly names the form,
  then select the closest allowed material_form. Use other for an explicit form that
  is outside the vocabulary and not_reported when the source states no form. A known
  material name alone is not evidence that it is a self-assembled monolayer.
  self_assembled_monolayer requires explicit SAM or self-assembled-monolayer wording;
  monolayer requires explicit monolayer wording without a self-assembly claim;
  compact_layer, mesoporous_layer, nanostructured_layer, and bulk_heterojunction
  likewise require corresponding source wording. Do not derive form from deposition
  method.
- Put arbitrary reported material, geometry, processing, measurement, and stability
  values in generic ReportedValue records. Do not omit a value because it lacks a
  dedicated schema field.
- Each ReportedValue must contain exactly one semantic quantity. A reported
  uncertainty or range may stay with that quantity, but never pack different metrics,
  table columns, or a complete row into one name or raw_value. Emit one object per
  quantity even when several quantities share the same source row.
- ReportedValue.raw_value and material_form_raw must be copied from supplied evidence.
  Every evidence array contains supplied span_id values, never copied quotation text
  or block IDs. Reuse a span_id when several atomic values share one row or sentence.
  Typography-preserving renderings are source evidence and may be used for subscript
  or superscript notation.
- Stability tests remain separate experiments with ordered checkpoints, even when
  linked to a performance device. Put a condition that changes between aging stages
  in the corresponding checkpoint.conditions; test.conditions contains only values
  that apply to the complete test.
- Exclude background examples, prior literature, reference entries, review material,
  and devices not made or measured in this study.
- Use null or not_reported for unknown identity or context. Do not guess.
- Empty arrays are correct only when all supplied evidence truly contains no record of
  that kind.
- Reconcile duplicate mentions before assigning final record IDs.
"""

CLAIM_LEDGER_PROMPT = f"""Read the supplied paper evidence before the final schema is
assembled. Return a neutral ledger of experimental objects and source claims, not a
draft StudyExtraction.

{DEVICE_FAMILY_POLICY}
Rules:
- Describe what each source-mentioned object is used for: a reusable device design,
  individual device, processing arm, characterization specimen, population,
  performance measurement, stability experiment, or other object. Do not promote an
  object to a device family merely because it has a label.
- Mark an object or claim target only when its facts belong in the present photovoltaic
  study extraction. Mark background, method-only, and characterization-only facts as
  context. Use uncertain when the evidence does not resolve scope.
- Emit one atomic claim per assertion. Use kind=reported_quantity for every numerical
  or non-numerical value that should become a ReportedValue. Its raw_value must contain
  only that one exact source value or outcome, not the surrounding procedure.
- When one explicit source quantity applies grammatically to several named materials
  or metrics, emit one reported_quantity claim whose raw_value is only the shared
  quantity and whose shared_targets contains every named target. For example, source
  text assigning 1.4 M to PbI2, MAI, and DMSO has raw_value="1.4 M" and three targets.
  If one sentence reports different quantities, emit separate claims; never assign a
  solvent or material to a quantity that the grammar does not give it. Do not infer
  shared scope from chemistry or proximity.
- Connect a claim to every experimental object it concerns. Repeated mentions and
  treatment labels may refer to the same underlying design; leave that reconciliation
  to the later document-level assembly call.

Every object and claim needs a supplied evidence span ID. Never invent span IDs.
"""

CLAIM_LEDGER_GUIDANCE = """The supplied claim ledger is a fallible map of the source,
not source evidence and not a list of records to create. Reconcile duplicate mentions
globally. A target device-design object may support a device family; a treatment arm
or characterization specimen does not. For every supported target claim, represent it
at the correct reporting level or explain why it remains unresolved. Context claims
must not create output records. Preserve explicit shared_targets as separate atomic
values in the final schema."""


def prompt_sha256() -> str:
    """Fingerprint every prompt template that can change scientific model output."""

    encoded = json.dumps(
        [
            SYSTEM_PROMPT,
            EXTRACTION_PROMPT,
            CLAIM_LEDGER_PROMPT,
            CLAIM_LEDGER_GUIDANCE,
            ENRICHMENT_SYSTEM_PROMPT,
            COMPOSITION_ENRICHMENT_PROMPT,
            PROCESSING_ENRICHMENT_PROMPT,
            REFINEMENT_PROMPT,
            REPAIR_SYSTEM_PROMPT,
            REPAIR_PROMPT,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExtractionConfig:
    """Collect settings that change parsing, model behavior, or scientific output."""

    pdf: Path
    supplement: Path | None
    output_dir: Path
    model: str = DEFAULT_EXTRACTION_MODEL
    reasoning_effort: str | None = None
    use_claim_ledger: bool = True
    claim_model: str | None = DEFAULT_CLAIM_MODEL
    claim_max_output_tokens: int = 30_000
    use_enrichment: bool = True
    enrichment_model: str | None = None
    enrichment_max_output_tokens: int = 20_000
    use_refinement: bool = True
    refinement_model: str | None = None
    use_targeted_repair: bool = True
    repair_model: str | None = None
    repair_max_output_tokens: int = 30_000
    parser: str = "docling"
    claim_mode: str = "auto"
    single_call_max_input_tokens: int = 90_000
    claim_window_input_tokens: int = 60_000
    max_output_tokens: int = 80_000
    max_model_calls: int | None = None
    max_cost_usd: float | None = None
    temperature: float | None = None
    heartbeat_seconds: float = 20
    timeout_seconds: float = 600
    document_cache_dir: Path = Path(".perla-cache/documents")
    model_cache_dir: Path = Path(".perla-cache/models")
    refresh_document_cache: bool = False
    reduced_export: bool = False
    dry_run: bool = False


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
    for stale in archive_dir.glob("*.archive.json"):
        stale.unlink()
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


def _ledger_payload(ledger: ClaimLedger, blocks: list[EvidenceBlock]) -> str:
    """Serialize the grounded claim ledger with compact evidence references."""

    return json.dumps(
        compact_to_span_citations(ledger, build_evidence_spans(blocks)),
        ensure_ascii=False,
    )


def _direct_prompt(blocks: list[EvidenceBlock], ledger: ClaimLedger) -> str:
    """Give schema assembly one global view of grounded claims and their source."""

    return (
        EXTRACTION_PROMPT
        + "\n\nSOURCE-GROUNDED CLAIM LEDGER:\n"
        + CLAIM_LEDGER_GUIDANCE
        + "\n"
        + _ledger_payload(ledger, blocks)
        + "\n\nCLAIM-SUPPORTING SOURCE PASSAGES:\n"
        + json.dumps(assembly_evidence_payload(blocks, ledger), ensure_ascii=False)
    )


def _claim_ledger_prompt(
    primary: list[EvidenceBlock], context: list[EvidenceBlock] | None = None
) -> str:
    """Request neutral claims, requiring windowed claims to cite primary evidence."""

    context = context or []
    return (
        CLAIM_LEDGER_PROMPT
        + (
            "\n\nThis is one part of a long document. Emit an object, claim, or "
            "exclusion only when it cites PRIMARY EVIDENCE; CONTEXT is supplied "
            "only to resolve terminology."
            if context
            else ""
        )
        + "\n\nCONTEXT EVIDENCE:\n"
        + json.dumps(evidence_payload(context), ensure_ascii=False)
        + "\n\nPRIMARY EVIDENCE:\n"
        + json.dumps(evidence_payload(primary), ensure_ascii=False)
    )


def _empty_ledger() -> ClaimLedger:
    """Represent absence of usable claims without inventing extraction guidance."""

    return ClaimLedger(objects=[], claims=[])


def _approximate_tokens(prompt: str, schema: dict) -> int:
    """Estimate request size conservatively without a model-specific tokenizer."""

    return (len(prompt) + len(json.dumps(schema, ensure_ascii=False))) // 4


def _plan_claim_collection(
    config: ExtractionConfig, blocks: list[EvidenceBlock]
) -> tuple[
    str,
    int,
    list[tuple[str, list[EvidenceBlock], list[EvidenceBlock]]],
]:
    """Choose one claim call or complete section-aware coverage windows."""

    schema = span_citation_schema(ClaimLedger, build_evidence_spans(blocks))
    direct_prompt = _claim_ledger_prompt(blocks)
    approximate_tokens = _approximate_tokens(direct_prompt, schema)
    mode = (
        config.claim_mode
        if config.claim_mode != "auto"
        else (
            "single"
            if approximate_tokens <= config.single_call_max_input_tokens
            else "windowed"
        )
    )
    if mode == "single":
        plan: list[tuple[str, list[EvidenceBlock], list[EvidenceBlock]]] = [
            ("complete_claim_ledger", blocks, [])
        ]
    else:
        schema_tokens = len(json.dumps(schema, ensure_ascii=False)) // 4
        evidence_budget = max(
            8_000,
            (config.claim_window_input_tokens - schema_tokens - 4_000) * 4,
        )
        windows = plan_evidence_windows(
            blocks,
            max_characters=evidence_budget,
            max_context_characters=min(24_000, evidence_budget // 3),
        ).windows
        plan = [
            (window.window_id, window.primary_blocks, window.context_blocks)
            for window in windows
        ]
    return mode, approximate_tokens, plan


def _collect_claim_ledger(
    config: ExtractionConfig,
    client: ModelClient,
    blocks: list[EvidenceBlock],
) -> tuple[ClaimLedger, list[str], int]:
    """Read every source block into a neutral ledger before schema assembly.

    A normal paper uses one document-level call. Long inputs are partitioned only at
    this claim-collection boundary; the resulting ledger is then reconciled globally.
    This avoids constructing and merging partial device schemas from disconnected
    windows.
    """

    _, _, plan = _plan_claim_collection(config, blocks)

    parts: list[tuple[str, ClaimLedger]] = []
    errors: list[str] = []
    for index, (slug, primary, context) in enumerate(plan, start=1):
        logger.info("Collecting source claims {}/{} ({})", index, len(plan), slug)
        visible = [*context, *primary]
        spans = build_evidence_spans(visible)
        try:
            ledger = client.complete(
                kind="source_claim_ledger",
                slug=slug,
                model=config.claim_model or config.model,
                system=SYSTEM_PROMPT,
                prompt=_claim_ledger_prompt(primary, context),
                response_model=ClaimLedger,
                max_output_tokens=config.claim_max_output_tokens,
                reasoning_effort=config.reasoning_effort,
                request_schema=span_citation_schema(ClaimLedger, spans),
                decode=lambda payload, visible_spans=spans: expand_span_citations(
                    payload, visible_spans
                ),
            )
        except ModelCallError as exc:
            errors.append(f"{slug}: {exc}")
            continue
        parts.append((slug, ledger))
    if not parts:
        return _empty_ledger(), errors, len(plan)
    if len(plan) == 1:
        return parts[0][1], errors, len(plan)
    return combine_ledgers(parts), errors, len(plan)


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


def _summarize_usage(calls: list[dict[str, object]]) -> dict[str, float | int]:
    """Aggregate charges from live calls; cache hits cost no tokens or money."""

    usage_records = [call.get("usage", {}) for call in calls]
    return {
        "live_calls": sum(not bool(call.get("cache_hit")) for call in calls),
        "cache_hits": sum(bool(call.get("cache_hit")) for call in calls),
        "prompt_tokens": sum(
            int(item.get("prompt_tokens", 0) or 0) for item in usage_records
        ),
        "completion_tokens": sum(
            int(item.get("completion_tokens", 0) or 0) for item in usage_records
        ),
        "total_tokens": sum(
            int(item.get("total_tokens", 0) or 0) for item in usage_records
        ),
        "cost": round(
            sum(float(item.get("cost", 0) or 0) for item in usage_records), 8
        ),
    }


def _run_configuration(
    config: ExtractionConfig,
    mode: str,
    source_events: list[dict],
    evidence_blocks: list[EvidenceBlock],
) -> dict:
    """Persist a secret-free fingerprint of every scientifically relevant setting."""

    value = asdict(config)
    value.update(
        {
            "pdf": str(config.pdf),
            "supplement": str(config.supplement) if config.supplement else None,
            "output_dir": str(config.output_dir),
            "document_cache_dir": str(config.document_cache_dir),
            "model_cache_dir": str(config.model_cache_dir),
            "effective_mode": mode,
            "schema_version": STUDY_SCHEMA_VERSION,
            "schema_sha256": study_schema_sha256(),
            "prompt_sha256": prompt_sha256(),
            "evidence_spans_sha256": evidence_spans_sha256(
                build_evidence_spans(evidence_blocks)
            ),
            "source_sha256": [
                event.get("source_sha256")
                for event in source_events
                if event.get("source_sha256")
            ],
        }
    )
    encoded = json.dumps(value, sort_keys=True, default=str).encode("utf-8")
    value["configuration_sha256"] = hashlib.sha256(encoded).hexdigest()
    return value


def _run_model_calls(
    config: ExtractionConfig,
    client: ModelClient,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger,
) -> tuple[StudyExtraction, StudyExtraction | None, list[str]]:
    """Assemble one global study draft, then optionally reconcile it once more.

    Long inputs are partitioned only while collecting claims. This function always
    sees the combined ledger and its exact supporting passages, so duplicate mentions,
    treatment arms, and characterization specimens are resolved before final IDs are
    assigned rather than merged as partial schemas afterward.
    """

    errors: list[str] = []
    initial_extraction: StudyExtraction | None = None
    prompt = _direct_prompt(blocks, ledger)
    spans = assembly_spans(blocks, ledger)
    logger.info("Assembling the complete study from the global claim ledger")
    try:
        extraction = client.complete(
            kind="complete_study",
            slug="complete_study",
            model=config.model,
            system=SYSTEM_PROMPT,
            prompt=prompt,
            response_model=StudyExtraction,
            max_output_tokens=config.max_output_tokens,
            reasoning_effort=config.reasoning_effort,
            request_schema=span_citation_schema(StudyExtraction, spans),
            decode=lambda payload: expand_span_citations(payload, spans),
        )
    except ModelCallError as exc:
        errors.append(str(exc))
        extraction = _empty_extraction(
            "Complete-study model call failed; inspect requests/ and report.json."
        )
    if config.use_refinement and not errors:
        initial_extraction = extraction
        logger.info("Reconciling the complete draft against claims and source evidence")
        extraction, error = refine_draft(
            client,
            draft=extraction,
            evidence_prompt=prompt,
            blocks=blocks,
            model=config.refinement_model or config.model,
            reasoning_effort=config.reasoning_effort,
            max_output_tokens=config.max_output_tokens,
            system_prompt=SYSTEM_PROMPT,
            kind="study_refinement",
            slug="study_refinement",
            draft_path=config.output_dir / "draft_extraction.json",
            audit_path=config.output_dir / "refinement_audit.json",
            spans=spans,
        )
        if error:
            errors.append(f"study_refinement: {error}")
    return extraction, initial_extraction, errors


def _plan_extraction(
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger,
) -> int:
    """Estimate the one global assembly request after claim-based compaction."""

    request_schema = span_citation_schema(
        StudyExtraction, assembly_spans(blocks, ledger)
    )
    return _approximate_tokens(_direct_prompt(blocks, ledger), request_schema)


def _select_refinement_candidate(
    draft: StudyExtraction,
    refinement: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
) -> tuple[StudyExtraction, dict[str, object]]:
    """Prefer a changed refinement unless it introduces deterministic errors.

    Citation counts measure textual grounding, not entity precision: eight
    source-quoted treatment arms can still be one device family. The refinement call
    is responsible for semantic reconciliation, while deterministic validation keeps
    malformed citations and values from replacing a safer draft. All quality counts
    remain in the audit for evaluation rather than acting as a recall-only gate.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    refinement_quality = candidate_quality(refinement, blocks, ledger)
    same_candidate = draft == refinement
    draft_rank = (
        draft_quality["validation_issues"],
        draft_quality["semantic_issues"],
    )
    refinement_rank = (
        refinement_quality["validation_issues"],
        refinement_quality["semantic_issues"],
    )
    selected = (
        "draft" if same_candidate or draft_rank < refinement_rank else "refinement"
    )
    reason = (
        "refinement changed the draft without worsening validation or claim coverage"
    )
    if same_candidate:
        reason = "refinement produced no candidate change"
    elif selected == "draft":
        reason = "refinement worsened validation or claim coverage"
    return (
        draft if selected == "draft" else refinement,
        {
            "selected": selected,
            "reason": reason,
            "draft_quality": draft_quality,
            "refinement_quality": refinement_quality,
        },
    )


def _gate_final_candidate_against_draft(
    draft: StudyExtraction,
    candidate: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
    selection: dict[str, object],
) -> tuple[StudyExtraction, dict[str, object]]:
    """Prevent refinement and repair from jointly introducing invalid content.

    The targeted repair gate compares a patch with the candidate it received, but a
    repaired refinement can still be less valid than the original draft. This final
    gate therefore compares deterministic validation issues with the immutable
    baseline. It deliberately does not require non-decreasing record, value, or
    record or value counts: doing so would make precision corrections such as
    consolidating treatment-arm families impossible.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    candidate_quality_summary = candidate_quality(candidate, blocks, ledger)
    accepted = (
        candidate_quality_summary["validation_issues"]
        <= draft_quality["validation_issues"]
        and candidate_quality_summary["semantic_issues"]
        <= draft_quality["semantic_issues"]
    )
    result = dict(selection)
    result["pre_repair_selected"] = selection["selected"]
    result["draft_quality"] = draft_quality
    result["final_candidate_quality"] = candidate_quality_summary
    if accepted:
        result["selected"] = str(selection["selected"])
        result["reason"] = (
            "final candidate does not worsen validation or claim coverage relative to the draft"
        )
        return candidate, result
    result["selected"] = "draft"
    result["reason"] = (
        "final candidate worsened validation or claim coverage relative to the draft"
    )
    return draft, result


def run_extraction(config: ExtractionConfig) -> dict[str, object]:
    """Write a complete, inspectable extraction run and return its report.

    Parsing and configuration artifacts are written before model calls. Model failure
    still yields a valid ``extraction.json`` and report; local grounding annotates
    rather than filters the rich result. NOMAD archives are the primary downstream
    artifacts; historical reduced conversion is available only when requested.
    """

    started = time.monotonic()
    if config.claim_mode not in {"auto", "single", "windowed"}:
        raise ValueError("claim_mode must be auto, single, or windowed")
    for path in (config.pdf, config.supplement):
        if path is not None and not path.exists():
            raise FileNotFoundError(path)
    if (
        min(
            config.single_call_max_input_tokens,
            config.claim_window_input_tokens,
            config.max_output_tokens,
            config.claim_max_output_tokens,
            config.enrichment_max_output_tokens,
            config.repair_max_output_tokens,
        )
        <= 0
    ):
        raise ValueError("token limits must be positive")
    if config.max_model_calls is not None and config.max_model_calls <= 0:
        raise ValueError("max_model_calls must be positive")
    if config.max_cost_usd is not None and config.max_cost_usd <= 0:
        raise ValueError("max_cost_usd must be positive")

    config.output_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "claim_ledger.json",
        "claim_grounding.json",
        "claim_coverage_audit.json",
        "draft_claim_coverage_audit.json",
        "pre_repair_claim_coverage_audit.json",
        "evidence_routing.json",
        # Remove artifacts written by the superseded record-first inventory.
        "evidence_inventory.json",
        "inventory_grounding.json",
        "coverage_audit.json",
        "draft_coverage_audit.json",
        "pre_repair_coverage_audit.json",
    ):
        (config.output_dir / name).unlink(missing_ok=True)
    logger.info("Parsing main paper{}", " and supplement" if config.supplement else "")
    blocks, source_events = parse_documents(
        config.pdf,
        config.supplement,
        parser=config.parser,
        cache_dir=config.document_cache_dir,
        refresh_cache=config.refresh_document_cache,
        heartbeat_seconds=config.heartbeat_seconds,
    )
    write_json_atomic(
        config.output_dir / "document.json",
        {"blocks": [block.model_dump(mode="json") for block in blocks]},
    )
    write_json_atomic(
        config.output_dir / "evidence_spans.json",
        {
            "spans": [
                span.model_dump(mode="json") for span in build_evidence_spans(blocks)
            ]
        },
    )
    schema = StudyExtraction.model_json_schema()
    if config.use_claim_ledger:
        claim_mode, claim_tokens, claim_plan = _plan_claim_collection(config, blocks)
    else:
        claim_mode, claim_tokens, claim_plan = "disabled", 0, []
    approximate_tokens = _plan_extraction(blocks, _empty_ledger())
    run_configuration = _run_configuration(config, claim_mode, source_events, blocks)
    write_json_atomic(config.output_dir / "run_configuration.json", run_configuration)
    write_json_atomic(config.output_dir / "extraction.schema.json", schema)
    write_json_atomic(
        config.output_dir / "claim_ledger.schema.json", ClaimLedger.model_json_schema()
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

    if config.dry_run:
        extraction_call_count = 1
        claim_call_count = len(claim_plan) if config.use_claim_ledger else 0
        planned_calls = (
            extraction_call_count
            + extraction_call_count * int(config.use_refinement)
            + claim_call_count
            + (3 if config.use_enrichment else 0)
            + int(config.use_targeted_repair)
        )
        report = {
            "status": "dry_run",
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
                "planned_calls_fit": (
                    config.max_model_calls is None
                    or planned_calls <= config.max_model_calls
                ),
            },
            "source_parsing": source_events,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_json_atomic(config.output_dir / "report.json", report)
        return report

    client = ModelClient(
        cache_dir=config.model_cache_dir,
        output_dir=config.output_dir,
        heartbeat_seconds=config.heartbeat_seconds,
        timeout_seconds=config.timeout_seconds,
        temperature=config.temperature,
        max_model_calls=config.max_model_calls,
        max_cost_usd=config.max_cost_usd,
    )
    errors: list[str] = []
    ledger: ClaimLedger | None = None
    grounded_claims: ClaimLedger | None = None
    selected_blocks = blocks
    if config.use_claim_ledger:
        logger.info("Collecting source claims before schema assembly")
        ledger, claim_errors, _ = _collect_claim_ledger(config, client, blocks)
        errors.extend(claim_errors)
        write_json_atomic(
            config.output_dir / "claim_ledger.json", ledger.model_dump(mode="json")
        )
        grounded_claims, claim_grounding = grounded_ledger(blocks, ledger)
        write_json_atomic(config.output_dir / "claim_grounding.json", claim_grounding)
        if not grounded_claims.objects and not grounded_claims.claims:
            errors.append("source claim ledger contained no grounded objects or claims")
            grounded_claims = None
        else:
            selected_blocks = assembly_blocks(blocks, grounded_claims)

    approximate_tokens = _plan_extraction(
        selected_blocks, grounded_claims or _empty_ledger()
    )
    write_json_atomic(
        config.output_dir / "run_configuration.json",
        _run_configuration(config, claim_mode, source_events, selected_blocks),
    )
    (config.output_dir / "window_plan.json").unlink(missing_ok=True)
    logger.info(
        "Selected {} of {} evidence blocks (~{} assembly request tokens)",
        len(selected_blocks),
        len(blocks),
        approximate_tokens,
    )
    for name in (
        "draft_extraction.json",
        "draft_candidates.json",
        "draft_validation.json",
        "draft_claim_coverage_audit.json",
        "refinement_audit.json",
        "refinement_extraction.json",
        "refinement_selection.json",
        "quality_comparison.json",
        "pre_repair_validation.json",
        "pre_repair_claim_coverage_audit.json",
        "targeted_repair.json",
    ):
        (config.output_dir / name).unlink(missing_ok=True)
    for directory in ("draft_windows", "refinement_audits"):
        for stale in (config.output_dir / directory).glob("*.json"):
            stale.unlink()
    extraction, initial_extraction, extraction_errors = _run_model_calls(
        config,
        client,
        selected_blocks,
        grounded_claims or _empty_ledger(),
    )
    errors.extend(extraction_errors)

    extraction, quote_repairs = repair_noncontiguous_citation_quotes(extraction, blocks)
    extraction, pointer_repairs = repair_unique_citation_pointers(extraction, blocks)
    refinement_selection: dict[str, object] | None = None
    comparable_draft: StudyExtraction | None = None
    draft_quote_repairs: dict[str, object] | None = None
    draft_pointer_repairs: dict[str, object] | None = None
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
    if config.use_targeted_repair:
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
        extraction, repair_audit = run_targeted_repair(
            client=client,
            study=extraction,
            blocks=blocks,
            ledger=grounded_claims,
            coverage=pre_repair_coverage,
            validation=pre_repair_validation_public,
            model=config.repair_model or config.refinement_model or config.model,
            reasoning_effort=config.reasoning_effort,
            max_output_tokens=config.repair_max_output_tokens,
        )
        write_json_atomic(
            config.output_dir / "targeted_repair.json",
            repair_audit.model_dump(mode="json"),
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

    write_json_atomic(
        config.output_dir / "extraction.json", extraction.model_dump(mode="json")
    )
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
        comparable_draft, _ = repair_unique_citation_pointers(comparable_draft, blocks)
        draft_validation = validate_study(comparable_draft, blocks)
        draft_validation.pop("verified_values")
        write_json_atomic(config.output_dir / "draft_validation.json", draft_validation)
        draft_coverage = (
            audit_claim_coverage(grounded_claims, comparable_draft)
            if grounded_claims is not None
            else None
        )
        if draft_coverage is not None:
            write_json_atomic(
                config.output_dir / "draft_claim_coverage_audit.json", draft_coverage
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
    enrichment: EnrichmentAudit | None = None
    for name in (
        "enrichment.json",
        "composition_proposals.json",
        "processing_proposals.json",
    ):
        (config.output_dir / name).unlink(missing_ok=True)
    if config.use_enrichment:
        logger.info("Interpreting composition and processing from local evidence")
        enrichment = run_enrichment(
            client=client,
            study=extraction,
            blocks=blocks,
            model=config.enrichment_model or config.model,
            reasoning_effort=config.reasoning_effort,
            max_output_tokens=config.enrichment_max_output_tokens,
        )
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
    for name in (
        "nomad_export.json",
        "composition_projection.json",
        "nomad_conversion_failure.json",
    ):
        (config.output_dir / name).unlink(missing_ok=True)
    nomad_dir = config.output_dir / "nomad"
    for stale in nomad_dir.glob("*.archive.json"):
        stale.unlink()
    (nomad_dir / "manifest.json").unlink(missing_ok=True)
    nomad_error: str | None = None
    try:
        nomad = to_nomad_with_report(
            extraction, model=config.model, enrichment=enrichment
        )
    except (ValidationError, ValueError) as exc:
        nomad_error = str(exc)
        write_json_atomic(
            config.output_dir / "nomad_conversion_failure.json",
            {"error": nomad_error},
        )
    else:
        _write_nomad_artifacts(config.output_dir, nomad)

    for name in (
        "reduced.json",
        "reduced_conversion.json",
        "reduced_conversion_failure.json",
    ):
        (config.output_dir / name).unlink(missing_ok=True)
    reduced_error: str | None = None
    if config.reduced_export:
        try:
            reduced = to_reduced_with_report(extraction)
        except (ValidationError, ValueError) as exc:
            reduced_error = str(exc)
            write_json_atomic(
                config.output_dir / "reduced_conversion_failure.json",
                {"error": reduced_error},
            )
        else:
            write_json_atomic(
                config.output_dir / "reduced.json",
                reduced.cells.model_dump(mode="json"),
            )
            write_json_atomic(
                config.output_dir / "reduced_conversion.json",
                reduced.model_dump(mode="json"),
            )

    counts = validation["counts"]
    extraction_calls = [
        call
        for call in client.calls
        if call.get("kind")
        not in {
            "source_claim_ledger",
            "composition_enrichment",
            "processing_enrichment",
            "composition_enrichment_retry",
            "targeted_study_repair",
        }
    ]
    enrichment_status = None
    if enrichment is not None:
        results = [
            *enrichment.composition_results,
            *enrichment.processing_results,
        ]
        enrichment_status = (
            "failed"
            if enrichment.errors and not results
            else (
                "needs_review"
                if enrichment.errors
                or any(result.status != "accepted" for result in results)
                or enrichment.unresolved_absorber_ids
                or enrichment.unresolved_processing_step_ids
                else "complete"
            )
        )
    status = (
        "failed"
        if not extraction_calls or all(call.get("error") for call in extraction_calls)
        else (
            "partial"
            if errors or nomad_error or reduced_error
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
        "refinement_calls": sum(
            call.get("kind") in {"study_refinement", "evidence_window_refinement"}
            for call in client.calls
        ),
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
        "nomad_archive_count": len(nomad.archives) if nomad_error is None else 0,
        "nomad_issue_count": len(nomad.issues) if nomad_error is None else 0,
        "nomad_conversion_error": nomad_error,
        "reduced_conversion_error": reduced_error,
        "errors": errors,
        "calls": client.calls,
        "usage": _summarize_usage(client.calls),
        "budget": client.budget_status(),
        "source_parsing": source_events,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    write_json_atomic(config.output_dir / "report.json", report)
    return report
