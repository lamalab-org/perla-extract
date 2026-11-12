"""Build, check, and export one reported photovoltaic study from a paper and its SI."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

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
from .client import ModelCallError, ModelClient, summarize_calls
from .compatibility import to_reduced_with_report
from .configuration import (
    DEFAULT_CLAIM_MODEL as DEFAULT_CLAIM_MODEL,
)
from .configuration import (
    DEFAULT_EXTRACTION_MODEL as DEFAULT_EXTRACTION_MODEL,
)
from .configuration import (
    ExtractionConfig,
    implementation_sha256,
    runtime_versions,
)
from .deployment import (
    DeploymentSettings,
    estimate_request_input,
    request_limit_issues,
    strict_response_schema,
)
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
from .finalization import remove_unsupported_optional_claims
from .guidance import (
    COMPOSITION_BOUNDARY_POLICY,
    DEVICE_FAMILY_POLICY,
    EVIDENCE_INTERPRETATION_POLICY,
    RECORD_BOUNDARY_POLICY,
    SHARED_QUANTITY_POLICY,
)
from .logging import logger
from .models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    PaperMetadata,
    StudyExtraction,
    study_schema_prose_sha256,
    study_schema_sha256,
)
from .nomad import NOMADExport, to_nomad_with_report
from .observability import EventJournal
from .partitioning import fit_request_windows, plan_evidence_windows
from .refinement import REFINEMENT_PROMPT, refine_draft
from .repair import (
    REPAIR_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    RepairAudit,
    candidate_quality,
    is_monotonic_quality,
    run_targeted_repair,
)
from .run_artifacts import write_run_manifest
from .source import parse_documents
from .spans import build_evidence_spans, evidence_payload, evidence_spans_sha256
from .tracing import create_langfuse_adapter
from .transport import (
    compact_to_span_citations,
    expand_span_citations,
    span_citation_schema,
)
from .validation import validate_study

SYSTEM_PROMPT = """Extract the complete present photovoltaic study as device-centered data.
Use only supplied evidence. Preserve source wording and distinctions. Never invent,
interpolate, digitize graph traces, or import values from cited literature."""

EXTRACTION_PROMPT = f"""Read all supplied evidence before extracting.

Identify every distinct photovoltaic device family made in the present study. Then
identify every individually measured device, performance
observation, population statistic, and stability specimen. Connect records only when
the evidence supports the connection.

{DEVICE_FAMILY_POLICY}
{RECORD_BOUNDARY_POLICY}
{COMPOSITION_BOUNDARY_POLICY}
{EVIDENCE_INTERPRETATION_POLICY}
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
{RECORD_BOUNDARY_POLICY}
{COMPOSITION_BOUNDARY_POLICY}
{EVIDENCE_INTERPRETATION_POLICY}
{SHARED_QUANTITY_POLICY}
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

CLAIM_RECALL_PROMPTS = (
    """INDEPENDENT OBJECT-FIRST READING: Start from the primary evidence again. First
enumerate every distinct experimental object, specimen, comparison group, and reporting
scope; then attach every supported atomic claim. Do not assume another reading captured
anything.""",
    """INDEPENDENT CLAIM-FIRST READING: Start from the primary evidence again. Scan each
sentence, table row, and caption for atomic values, conditions, relations, and explicit
scope; then attach them to supported objects. Do not assume another reading captured
anything.""",
)


def prompt_sha256() -> str:
    """Fingerprint every prompt template that can change scientific model output."""

    encoded = json.dumps(
        [
            SYSTEM_PROMPT,
            EXTRACTION_PROMPT,
            CLAIM_LEDGER_PROMPT,
            CLAIM_LEDGER_GUIDANCE,
            *CLAIM_RECALL_PROMPTS,
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


def _ledger_payload(ledger: ClaimLedger, blocks: list[EvidenceBlock]) -> str:
    """Serialize the grounded claim ledger with compact evidence references."""

    return json.dumps(
        compact_to_span_citations(ledger, build_evidence_spans(blocks)),
        ensure_ascii=False,
    )


def _direct_prompt(blocks: list[EvidenceBlock], ledger: ClaimLedger) -> str:
    """Build a study from complete source or reader-selected statements and text."""

    if not ledger.objects and not ledger.claims:
        return (
            EXTRACTION_PROMPT
            + "\n\nCOMPLETE SOURCE EVIDENCE:\n"
            + json.dumps(assembly_evidence_payload(blocks, ledger), ensure_ascii=False)
        )
    return (
        EXTRACTION_PROMPT
        + "\n\nSOURCE-GROUNDED CLAIM LEDGER:\n"
        + CLAIM_LEDGER_GUIDANCE
        + "\n"
        + _ledger_payload(ledger, blocks)
        + "\n\nCLAIM-SUPPORTING SOURCE PASSAGES:\n"
        + json.dumps(assembly_evidence_payload(blocks, ledger), ensure_ascii=False)
    )


def _assembly_components(
    blocks: list[EvidenceBlock], ledger: ClaimLedger
) -> dict[str, object]:
    """Identify the actual request parts for per-stage size diagnostics."""

    has_notes = bool(ledger.objects or ledger.claims)
    return {
        "instructions": EXTRACTION_PROMPT
        + (CLAIM_LEDGER_GUIDANCE if has_notes else ""),
        "source_evidence": assembly_evidence_payload(blocks, ledger),
        "reader_notes": _ledger_payload(ledger, blocks) if has_notes else "",
    }


def _claim_ledger_prompt(
    primary: list[EvidenceBlock],
    context: list[EvidenceBlock] | None = None,
    recall_pass: int = 1,
) -> str:
    """Ask one reading to inventory source statements and their subjects.

    Repeated readings receive the same source and different attention instructions;
    they do not receive earlier model output. Their errors can still be correlated.
    Combining readings offers another chance to find omitted facts, not independent
    scientific confirmation of a statement.
    """

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
        + ("\n\n" + CLAIM_RECALL_PROMPTS[recall_pass - 2] if recall_pass > 1 else "")
        + "\n\nCONTEXT EVIDENCE:\n"
        + json.dumps(evidence_payload(context), ensure_ascii=False)
        + "\n\nPRIMARY EVIDENCE:\n"
        + json.dumps(evidence_payload(primary), ensure_ascii=False)
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


def _reading_input_limit(config: ExtractionConfig, configured_limit: int) -> int:
    """Reserve the configured output before fitting a reader deployment's context."""

    deployment = config.effective_deployment("read")
    limits = [configured_limit]
    if deployment:
        if deployment.max_input_tokens is not None:
            limits.append(deployment.max_input_tokens)
        if deployment.context_tokens is not None:
            output = deployment.max_output_tokens or config.claim_max_output_tokens
            limits.append(deployment.context_tokens - output)
    limit = min(limits)
    if limit <= 0:
        raise ValueError("reading output allowance leaves no space for source input")
    return limit


def _plan_claim_collection(
    config: ExtractionConfig, blocks: list[EvidenceBlock]
) -> tuple[
    str,
    int,
    list[tuple[str, list[EvidenceBlock], list[EvidenceBlock]]],
]:
    """Fit every reading's serialized instructions, evidence, and citation schema.

    The longest configured reading determines each window's size. Source blocks are
    never dropped to satisfy a limit; indivisible oversized blocks fail planning.
    """

    deployment = config.effective_deployment("read")
    tokenizer = deployment.tokenizer if deployment else None

    def estimate(primary: list[EvidenceBlock], context: list[EvidenceBlock]) -> int:
        schema = span_citation_schema(
            ClaimLedger, build_evidence_spans([*context, *primary])
        )
        return max(
            _approximate_tokens(
                _claim_ledger_prompt(primary, context, reading),
                schema,
                tokenizer=tokenizer,
            )
            for reading in range(1, config.claim_recall_passes + 1)
        )

    approximate_tokens = estimate(blocks, [])
    single_limit = _reading_input_limit(config, config.single_call_max_input_tokens)
    mode = (
        config.claim_mode
        if config.claim_mode != "auto"
        else ("single" if approximate_tokens <= single_limit else "windowed")
    )
    if mode == "single":
        if approximate_tokens > single_limit:
            raise ValueError(
                f"complete reading estimate {approximate_tokens} exceeds input limit "
                f"{single_limit}; use --claim-mode auto or windowed"
            )
        plan: list[tuple[str, list[EvidenceBlock], list[EvidenceBlock]]] = [
            ("complete_claim_ledger", blocks, [])
        ]
    else:
        input_limit = _reading_input_limit(config, config.claim_window_input_tokens)
        evidence_budget = max(1, input_limit * 3)
        windows = plan_evidence_windows(
            blocks,
            max_characters=evidence_budget,
            max_context_characters=min(24_000, evidence_budget // 3),
        ).windows
        windows = fit_request_windows(
            windows, max_input_tokens=input_limit, estimate=estimate
        )
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
    """Read every source block, then combine exact repeated statements with provenance.

    Window outputs describe source statements, not partial final studies. One later
    assembly call resolves scientific identity across all windows. Reading failures
    remain explicit; if no reading succeeds the caller uses the complete source.
    """

    _, _, plan = _plan_claim_collection(config, blocks)

    parts: list[tuple[str, ClaimLedger]] = []
    errors: list[str] = []
    for index, (slug, primary, context) in enumerate(plan, start=1):
        visible = [*context, *primary]
        spans = build_evidence_spans(visible)
        for pass_index in range(1, config.claim_recall_passes + 1):
            pass_slug = f"{slug}-pass-{pass_index}"
            logger.info(
                "Collecting source claims window {}/{} pass {}/{} ({})",
                index,
                len(plan),
                pass_index,
                config.claim_recall_passes,
                slug,
            )
            try:

                def decode_claim_payload(
                    payload: object, visible_spans=spans
                ) -> object:
                    return expand_span_citations(payload, visible_spans)

                ledger = client.complete(
                    kind="source_claim_ledger",
                    slug=pass_slug,
                    model=config.effective_model("read"),
                    system=SYSTEM_PROMPT,
                    prompt=_claim_ledger_prompt(primary, context, pass_index),
                    prompt_components={
                        "reading_instructions": CLAIM_LEDGER_PROMPT
                        + (
                            CLAIM_RECALL_PROMPTS[pass_index - 2]
                            if pass_index > 1
                            else ""
                        ),
                        "primary_evidence": evidence_payload(primary),
                        "context_evidence": evidence_payload(context),
                    },
                    response_model=ClaimLedger,
                    max_output_tokens=config.claim_max_output_tokens,
                    reasoning_effort=config.reasoning_effort,
                    request_schema=span_citation_schema(ClaimLedger, spans),
                    decode=decode_claim_payload,
                )
            except ModelCallError as exc:
                errors.append(f"{pass_slug}: {exc}")
                continue
            parts.append((pass_slug, ledger))
    planned_calls = len(plan) * config.claim_recall_passes
    if not parts:
        return _empty_ledger(), errors, planned_calls
    provenance: dict[str, object] = {}
    combined = combine_ledgers(parts, provenance=provenance)
    write_json_atomic(config.output_dir / "claim_readings.json", provenance)
    return combined, errors, planned_calls


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
            "schema_prose_sha256": study_schema_prose_sha256(),
            "pipeline_implementation_sha256": implementation_sha256(),
            "runtime_versions": runtime_versions(),
            "scientific_settings": config.scientific_settings(),
            "effective_models": config.scientific_settings()["effective_models"],
            "parser_dependencies": next(
                (
                    event.get("dependency_versions", {})
                    for event in source_events
                    if event.get("dependency_versions")
                ),
                {},
            ),
            "parser_implementation_sha256": next(
                (
                    event.get("parser_implementation_sha256")
                    for event in source_events
                    if event.get("parser_implementation_sha256")
                ),
                None,
            ),
            "source_parsing": source_events,
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
    identity = {
        name: value[name]
        for name in (
            "scientific_settings",
            "effective_mode",
            "schema_sha256",
            "schema_prose_sha256",
            "prompt_sha256",
            "evidence_spans_sha256",
            "source_sha256",
            "parser_dependencies",
            "parser_implementation_sha256",
            "pipeline_implementation_sha256",
            "runtime_versions",
        )
    }
    encoded = json.dumps(identity, sort_keys=True, default=str).encode("utf-8")
    value["configuration_sha256"] = hashlib.sha256(encoded).hexdigest()
    return value


def _runtime_deployments(
    config: ExtractionConfig, claim_mode: str
) -> dict[str, DeploymentSettings]:
    """Apply shared input caps at the provider boundary, including correction calls."""

    result = {}
    for stage in ("read", "assemble", "refine", "repair", "enrich"):
        declared = config.effective_deployment(stage)
        limit = config.assembly_max_input_tokens
        if stage == "read":
            limit = (
                config.claim_window_input_tokens
                if claim_mode == "windowed"
                else config.single_call_max_input_tokens
            )
        if declared.max_input_tokens is not None:
            limit = min(limit, declared.max_input_tokens)
        result[stage] = replace(declared, max_input_tokens=limit)
    return result


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
    try:
        with journal.stage("assemble") if journal else nullcontext():
            extraction = client.complete(
                kind="complete_study",
                slug="complete_study",
                model=config.effective_model("assemble"),
                system=SYSTEM_PROMPT,
                prompt=prompt,
                prompt_components=_assembly_components(blocks, ledger),
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
        with journal.stage("refine") if journal else nullcontext():
            extraction, error = refine_draft(
                client,
                draft=extraction,
                evidence_prompt=prompt,
                evidence_components=_assembly_components(blocks, ledger),
                blocks=blocks,
                model=config.effective_model("refine"),
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
    config: ExtractionConfig | None = None,
) -> int:
    """Estimate the one global assembly request after claim-based compaction."""

    request_schema = span_citation_schema(
        StudyExtraction, assembly_spans(blocks, ledger)
    )
    deployment = config.effective_deployment("assemble") if config else None
    return _approximate_tokens(
        _direct_prompt(blocks, ledger),
        request_schema,
        tokenizer=deployment.tokenizer if deployment else None,
    )


def _select_refinement_candidate(
    draft: StudyExtraction,
    refinement: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
) -> tuple[StudyExtraction, dict[str, object]]:
    """Accept a changed draft only when neither technical issue count increases.

    These source-link and claim-coverage counts are integrity checks, not a measure
    of scientific correctness. Tied counts permit a changed candidate; evaluate
    whether that policy helps accuracy against independent reviewed references.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    refinement_quality = candidate_quality(refinement, blocks, ledger)
    same_candidate = draft == refinement
    selected = (
        "refinement"
        if not same_candidate
        and is_monotonic_quality(draft_quality, refinement_quality)
        else "draft"
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
    """Compare the final corrected candidate with the original draft once more.

    Repair is checked against its immediate input. This final comparison prevents
    a chain of accepted edits from increasing either technical issue count relative
    to the first valid draft. Record counts are not rewards: removing a duplicate
    can be a legitimate correction.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    candidate_quality_summary = candidate_quality(candidate, blocks, ledger)
    accepted = is_monotonic_quality(draft_quality, candidate_quality_summary)
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
            journal.adapter = create_langfuse_adapter(enabled=True)
        except Exception as error:
            logger.warning(
                "Trace export unavailable; retaining local events ({})",
                type(error).__name__,
            )
            journal.emit(
                "telemetry.failed", error_type=type(error).__name__, status="disabled"
            )
    journal.emit("run.started", dry_run=config.dry_run)
    state: dict[str, Any] = {}
    try:
        report = _run_extraction(config, journal, state)
    except BaseException as error:
        client = state.get("client")
        calls = client.calls if client is not None else []
        report = {
            "run_id": journal.run_id,
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
            "source_parsing": state.get("source_parsing", []),
        }
        if not (config.output_dir / "run_configuration.json").exists():
            write_json_atomic(
                config.output_dir / "run_configuration.json",
                _run_configuration(config, "unplanned", [], []),
            )
        write_json_atomic(config.output_dir / "report.json", report)
        journal.emit(
            "run.finished", status=report["status"], error_type=type(error).__name__
        )
        write_run_manifest(config.output_dir, run_id=journal.run_id)
        raise
    report["run_id"] = journal.run_id
    write_json_atomic(config.output_dir / "report.json", report)
    journal.emit("run.finished", status=report["status"], usage=report.get("usage", {}))
    write_run_manifest(config.output_dir, run_id=journal.run_id)
    return report


def _run_extraction(
    config: ExtractionConfig, journal: EventJournal, state: dict[str, Any]
) -> dict[str, object]:
    """Run source preparation, scientific stages, validation, and exports in order."""

    started = time.monotonic()
    logger.info("Parsing main paper{}", " and supplement" if config.supplement else "")
    with journal.stage("parse"):
        blocks, source_events = parse_documents(
            config.pdf,
            config.supplement,
            parser=config.parser,
            cache_dir=config.document_cache_dir,
            refresh_cache=config.refresh_document_cache,
            heartbeat_seconds=config.heartbeat_seconds,
        )
    state["source_parsing"] = source_events
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
    write_json_atomic(
        config.output_dir / "run_configuration.json",
        _run_configuration(config, "unplanned", source_events, blocks),
    )
    schema = StudyExtraction.model_json_schema()
    if config.use_claim_ledger:
        claim_mode, claim_tokens, claim_plan = _plan_claim_collection(config, blocks)
    else:
        claim_mode, claim_tokens, claim_plan = "disabled", 0, []
    approximate_tokens = _plan_extraction(blocks, _empty_ledger(), config)
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
        "recall_passes": config.claim_recall_passes if config.use_claim_ledger else 0,
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
        assembly_deployment = _runtime_deployments(config, claim_mode)["assemble"]
        known_issues = (
            request_limit_issues(
                approximate_tokens,
                assembly_deployment.max_output_tokens or config.max_output_tokens,
                assembly_deployment,
            )
            if not config.use_claim_ledger
            else []
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
        claim_call_count = (
            len(claim_plan) * config.claim_recall_passes
            if config.use_claim_ledger
            else 0
        )
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
            "usage": _summarize_usage([]),
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
                "provider_requests": 0,
                "cost_tracking_complete": True,
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
        max_total_tokens=config.max_total_tokens,
        max_elapsed_seconds=config.max_elapsed_seconds,
        deployments=_runtime_deployments(config, claim_mode),
        journal=journal,
    )
    state["client"] = client
    errors: list[str] = []
    ledger: ClaimLedger | None = None
    grounded_claims: ClaimLedger | None = None
    selected_blocks = blocks
    if config.use_claim_ledger:
        logger.info("Collecting source claims before schema assembly")
        with journal.stage("read"):
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
    write_json_atomic(
        config.output_dir / "run_configuration.json",
        _run_configuration(config, claim_mode, source_events, blocks),
    )
    logger.info(
        "Selected {} of {} evidence blocks (~{} assembly request tokens)",
        len(selected_blocks),
        len(blocks),
        approximate_tokens,
    )
    assembly_blocked = approximate_tokens > config.assembly_max_input_tokens
    if assembly_blocked:
        message = (
            f"assembly request estimate {approximate_tokens} exceeds configured limit "
            f"{config.assembly_max_input_tokens}; increase --assembly-max-input-tokens "
            "only after confirming the selected model's context window"
        )
        logger.error(message)
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
    errors.extend(extraction_errors)

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
        with journal.stage("repair"):
            extraction, repair_audit = run_targeted_repair(
                client=client,
                study=extraction,
                blocks=blocks,
                ledger=grounded_claims,
                coverage=pre_repair_coverage,
                validation=pre_repair_validation_public,
                model=config.effective_model("repair"),
                reasoning_effort=config.reasoning_effort,
                max_output_tokens=config.repair_max_output_tokens,
            )
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
    if config.use_enrichment and not assembly_blocked:
        logger.info("Interpreting composition and processing from local evidence")
        with journal.stage("enrich"):
            enrichment = run_enrichment(
                client=client,
                study=extraction,
                blocks=blocks,
                model=config.effective_model("enrich"),
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
    nomad_error: str | None = None
    try:
        with journal.stage("export"):
            nomad = to_nomad_with_report(
                extraction,
                model=config.effective_model("assemble"),
                enrichment=enrichment,
                evidence_blocks=blocks,
            )
    except (ValidationError, ValueError) as exc:
        nomad_error = str(exc)
        write_json_atomic(
            config.output_dir / "nomad_conversion_failure.json",
            {"error": nomad_error},
        )
    else:
        _write_nomad_artifacts(config.output_dir, nomad)

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
        result_statuses = [
            result.status for result in enrichment.composition_results
        ] + [result.status for result in enrichment.processing_results]
        enrichment_status = (
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
        "conservative_removal_count": conservative_finalization["removal_count"],
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
