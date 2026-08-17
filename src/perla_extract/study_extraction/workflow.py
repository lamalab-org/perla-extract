"""End-to-end, high-recall extraction of a paper and its supplementary information."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from .artifacts import write_json_atomic
from .candidate_collection import combine_window_candidates
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
from .identity_linking import IdentityLinkProposal, attach_valid_identity_links
from .inventory import (
    EvidenceInventory,
    InventoryItem,
    audit_inventory_coverage,
    grounded_inventory_items,
    routed_blocks,
)
from .logging import logger
from .models import (
    STUDY_SCHEMA_VERSION,
    EvidenceBlock,
    PaperMetadata,
    StudyExtraction,
    study_schema_sha256,
)
from .nomad import NOMADExport, to_nomad_with_report
from .partitioning import EvidenceWindowPlan, plan_evidence_windows
from .refinement import REFINEMENT_PROMPT, refine_draft
from .repair import (
    REPAIR_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    RepairAudit,
    run_targeted_repair,
)
from .source import parse_documents
from .transport import (
    compact_study_schema,
    constrain_evidence_block_ids,
    expand_compact_study,
)
from .validation import validate_study

DEFAULT_EXTRACTION_MODEL = "openrouter/openai/gpt-5.6-sol:exacto"
DEFAULT_INVENTORY_MODEL = "openrouter/openai/gpt-5.6-terra:exacto"

SYSTEM_PROMPT = """Extract the complete present photovoltaic study as device-centered data.
Use only supplied evidence. Preserve source wording and distinctions. Never invent,
interpolate, digitize graph traces, or import values from cited literature."""

EXTRACTION_PROMPT = """Read all supplied evidence before extracting.

Identify every distinct device family or processing/composition variant made in the
present study. Then identify every individually measured device, performance
observation, population statistic, and stability specimen. Connect records only when
the evidence supports the connection.

Rules:
- Preserve a device as one coherent object. Do not create one device per metric,
  paragraph, layer, scan direction, or stability checkpoint.
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
- Put arbitrary reported material, geometry, processing, measurement, and stability
  values in generic ReportedValue records. Do not omit a value because it lacks a
  dedicated schema field.
- Each ReportedValue must contain exactly one semantic quantity. A reported
  uncertainty or range may stay with that quantity, but never pack different metrics,
  table columns, or a complete row into one name or raw_value. Emit one object per
  quantity even when several quantities share the same source row.
- ReportedValue.raw_value and EvidenceCitation.quote must be copied from a supplied
  block. Cite the smallest useful quote and only supplied block IDs.
  Typography-preserving renderings are source evidence and may be used for subscript
  or superscript notation.
- Define each source quotation once in evidence_catalog and refer to its citation_id
  from every evidence array. Reuse a citation_id when several atomic values share the
  same source row or sentence.
- Stability tests remain separate experiments with ordered checkpoints, even when
  linked to a performance device.
- Exclude background examples, prior literature, reference entries, review material,
  and devices not made or measured in this study.
- Use null or not_reported for unknown identity or context. Do not guess.
- Empty arrays are correct only when all supplied evidence truly contains no record of
  that kind.
- Return identity_links as an empty array. Cross-window identity is handled by a
  separate auditable identity-linking call.
"""

WINDOW_PROMPT = """PRIMARY EVIDENCE is the part assigned to this extraction window.
CONTEXT EVIDENCE is supplied only to understand identity and terminology. Emit a
candidate only when at least one of its EvidenceCitation entries cites a PRIMARY
EVIDENCE block. Do not emit candidates supported exclusively by context. Partial
candidates are allowed: later windows are combined without deleting them."""

IDENTITY_LINK_PROMPT = """Identify only candidates that refer to the same real-world
entity across extraction windows. Return explicit identity links; do not merge, delete,
rank, or rewrite candidates.

Rules:
- Group only IDs of the declared entity_kind and only IDs present below.
- Require positive identity evidence, not merely similar materials or metric values.
- Device variants, different individual devices, different scan directions, population
  statistics, and separate stability specimens are distinct unless the candidate
  evidence explicitly establishes identity.
- Each candidate may appear in at most one link of its entity kind.
- Cite the smallest supplied candidate evidence quotes that support the identity link.
- An empty identity_links list is correct when identity is uncertain.
"""

INVENTORY_PROMPT = """Independently inventory the photovoltaic records made or
measured in the present study before detailed extraction. Identify photovoltaic device
families or processing/composition variants, individually measured photovoltaic
devices, their protocol-specific performance observations, population statistics, and
stability tests. Do not extract their numerical values. Keep distinct reporting levels
and variants distinct. Non-photovoltaic components, material-only specimens, and
integrated systems are context rather than target records unless the evidence
specifically reports a photovoltaic device or measurement represented by the schema.

Also list blocks that are clearly irrelevant to extracting present-study device
composition, processing, performance, measurement conditions, or stability. Exclude
only obvious background literature, administrative material, or unrelated content.
If a block might provide identity, composition, fabrication, measurement, or result
context, retain it by omitting it from exclusions. Every inventory item needs a small
verbatim quotation and a supplied block ID. Every exclusion also needs a verbatim
quotation from the excluded block so the routing decision is auditable. Never invent
block IDs.
"""

INVENTORY_GUIDANCE = """The supplied independent inventory contains recall hints,
not paper facts. Check every item against its quoted evidence. Represent each supported
item at the correct reporting level, and ignore an item when the evidence does not
actually establish a schema record. Never copy a claim merely because it appears in
the inventory."""

def prompt_sha256() -> str:
    """Fingerprint every prompt template that can change scientific model output."""

    encoded = json.dumps(
        [
            SYSTEM_PROMPT,
            EXTRACTION_PROMPT,
            WINDOW_PROMPT,
            IDENTITY_LINK_PROMPT,
            INVENTORY_PROMPT,
            INVENTORY_GUIDANCE,
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
    use_inventory: bool = True
    inventory_model: str | None = DEFAULT_INVENTORY_MODEL
    inventory_max_output_tokens: int = 20_000
    use_enrichment: bool = True
    enrichment_model: str | None = None
    enrichment_max_output_tokens: int = 20_000
    use_refinement: bool = True
    refinement_model: str | None = None
    use_targeted_repair: bool = True
    repair_model: str | None = None
    repair_max_output_tokens: int = 30_000
    parser: str = "docling"
    mode: str = "auto"
    single_call_max_input_tokens: int = 90_000
    window_input_tokens: int = 60_000
    max_output_tokens: int = 80_000
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


def _evidence_payload(blocks: list[EvidenceBlock]) -> list[dict[str, object]]:
    """Send the model source content and locations without parser implementation data."""

    return [
        {
            "block_id": block.block_id,
            "source": block.source,
            "page": block.page,
            "section": block.section_path[-1] if block.section_path else None,
            "kind": block.kind,
            "text": block.text,
        }
        for block in blocks
    ]


def _inventory_payload(items: list[InventoryItem]) -> str:
    """Serialize only source-grounded inventory candidates as extraction guidance."""

    return json.dumps(
        [item.model_dump(mode="json") for item in items], ensure_ascii=False
    )


def _direct_prompt(
    blocks: list[EvidenceBlock], inventory_items: list[InventoryItem]
) -> str:
    """Give one call global evidence context when the complete study fits."""

    return (
        EXTRACTION_PROMPT
        + "\n\nSOURCE-GROUNDED INDEPENDENT INVENTORY:\n"
        + INVENTORY_GUIDANCE
        + "\n"
        + _inventory_payload(inventory_items)
        + "\n\nCOMPLETE STUDY EVIDENCE:\n"
        + json.dumps(_evidence_payload(blocks), ensure_ascii=False)
    )


def _inventory_prompt(blocks: list[EvidenceBlock]) -> str:
    """Request a shallow record inventory without exposing the final extraction."""

    return (
        INVENTORY_PROMPT
        + "\n\nCOMPLETE STUDY EVIDENCE:\n"
        + json.dumps(_evidence_payload(blocks), ensure_ascii=False)
    )


def _window_prompt(
    primary: list[EvidenceBlock],
    context: list[EvidenceBlock],
    inventory_items: list[InventoryItem],
) -> str:
    """Tell the model not to emit candidates supported only by repeated context."""

    return (
        EXTRACTION_PROMPT
        + "\n\n"
        + WINDOW_PROMPT
        + "\n\nSOURCE-GROUNDED INVENTORY ITEMS IN THIS WINDOW:\n"
        + INVENTORY_GUIDANCE
        + "\n"
        + _inventory_payload(inventory_items)
        + "\n\nCONTEXT EVIDENCE:\n"
        + json.dumps(_evidence_payload(context), ensure_ascii=False)
        + "\n\nPRIMARY EVIDENCE:\n"
        + json.dumps(_evidence_payload(primary), ensure_ascii=False)
    )


def _identity_link_prompt(candidates: StudyExtraction) -> str:
    """Ask only for identity links so the model cannot rewrite candidates."""

    return (
        IDENTITY_LINK_PROMPT
        + "\n\nCANDIDATE UNION:\n"
        + candidates.model_dump_json(exclude={"identity_links"})
    )


def _approximate_tokens(prompt: str, schema: dict) -> int:
    """Estimate request size conservatively without a model-specific tokenizer."""

    return (len(prompt) + len(json.dumps(schema, ensure_ascii=False))) // 4


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
    config: ExtractionConfig, mode: str, source_events: list[dict]
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
    mode: str,
    window_plan: EvidenceWindowPlan | None,
    inventory_items: list[InventoryItem],
) -> tuple[StudyExtraction, StudyExtraction | None, list[str]]:
    """Run the selected call plan while retaining every successful partial result.

    Window failures are accumulated rather than invalidating successful windows.
    Identity linking adds audited links to the lossless candidate union; it never
    chooses a winner or rewrites scientific fields.
    """

    errors: list[str] = []
    if mode == "single":
        initial_extraction: StudyExtraction | None = None
        prompt = _direct_prompt(blocks, inventory_items)
        logger.info("Extracting the complete study in one model call")
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
                request_schema=compact_study_schema(block.block_id for block in blocks),
                decode=expand_compact_study,
            )
        except ModelCallError as exc:
            errors.append(str(exc))
            extraction = _empty_extraction(
                "Complete-study model call failed; inspect requests/ and report.json."
            )
        if config.use_refinement and not errors:
            initial_extraction = extraction
            logger.info("Refining the complete draft against source evidence")
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
            )
            if error:
                errors.append(f"study_refinement: {error}")
        return extraction, initial_extraction, errors

    window_extractions: list[tuple[str, StudyExtraction]] = []
    draft_window_extractions: list[tuple[str, StudyExtraction]] = []
    windows = window_plan.windows if window_plan else []
    for index, window in enumerate(windows, start=1):
        visible_ids = {
            block.block_id
            for block in [*window.primary_blocks, *window.context_blocks]
        }
        local_inventory = [
            item
            for item in inventory_items
            if any(citation.block_id in visible_ids for citation in item.evidence)
        ]
        prompt = _window_prompt(
            window.primary_blocks, window.context_blocks, local_inventory
        )
        logger.info(
            "Extracting evidence window {}/{} ({}, {} primary blocks)",
            index,
            len(windows),
            window.window_id,
            len(window.primary_blocks),
        )
        try:
            window_extraction = client.complete(
                kind="evidence_window",
                slug=window.window_id,
                model=config.model,
                system=SYSTEM_PROMPT,
                prompt=prompt,
                response_model=StudyExtraction,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
                request_schema=compact_study_schema(
                    block.block_id
                    for block in [*window.primary_blocks, *window.context_blocks]
                ),
                decode=expand_compact_study,
            )
        except ModelCallError as exc:
            errors.append(f"{window.window_id}: {exc}")
            continue
        if config.use_refinement:
            draft_window_extractions.append((window.window_id, window_extraction))
            logger.info("Refining evidence window {}/{}", index, len(windows))
            window_extraction, error = refine_draft(
                client,
                draft=window_extraction,
                evidence_prompt=prompt,
                blocks=[*window.primary_blocks, *window.context_blocks],
                model=config.refinement_model or config.model,
                reasoning_effort=config.reasoning_effort,
                max_output_tokens=config.max_output_tokens,
                system_prompt=SYSTEM_PROMPT,
                kind="evidence_window_refinement",
                slug=f"{window.window_id}_refinement",
                draft_path=(
                    config.output_dir / "draft_windows" / f"{window.window_id}.json"
                ),
                audit_path=(
                    config.output_dir
                    / "refinement_audits"
                    / f"{window.window_id}.json"
                ),
            )
            if error:
                errors.append(f"{window.window_id}_refinement: {error}")
        window_extractions.append((window.window_id, window_extraction))
        write_json_atomic(
            config.output_dir / "windows" / f"{window.window_id}.json",
            window_extraction.model_dump(mode="json"),
        )
    extraction = (
        combine_window_candidates(window_extractions)
        if window_extractions
        else _empty_extraction(
            "All evidence-window model calls failed; inspect requests/ and report.json."
        )
    )
    initial_extraction = (
        combine_window_candidates(draft_window_extractions)
        if draft_window_extractions
        else None
    )
    if initial_extraction is not None:
        write_json_atomic(
            config.output_dir / "draft_candidates.json",
            initial_extraction.model_dump(mode="json"),
        )
    if window_extractions:
        write_json_atomic(
            config.output_dir / "candidates.json", extraction.model_dump(mode="json")
        )
    if len(window_extractions) > 1:
        logger.info(
            "Linking candidate identity across {} windows", len(window_extractions)
        )
        try:
            identity_link_proposal = client.complete(
                kind="cross_window_identity_links",
                slug="cross_window_identity_links",
                model=config.model,
                system=SYSTEM_PROMPT,
                prompt=_identity_link_prompt(extraction),
                response_model=IdentityLinkProposal,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
                request_schema=constrain_evidence_block_ids(
                    IdentityLinkProposal.model_json_schema(),
                    (block.block_id for block in blocks),
                ),
            )
        except ModelCallError as exc:
            errors.append(f"cross_window_identity_links: {exc}")
            write_json_atomic(
                config.output_dir / "identity_links.json",
                {"status": "failed", "error": str(exc)},
            )
        else:
            extraction, identity_link_audit = attach_valid_identity_links(
                extraction, identity_link_proposal
            )
            write_json_atomic(
                config.output_dir / "identity_links.json",
                {
                    "status": (
                        "accepted" if not identity_link_audit.issues else "needs_review"
                    ),
                    **identity_link_audit.model_dump(mode="json"),
                },
            )
    return extraction, initial_extraction, errors


def _plan_extraction(
    config: ExtractionConfig,
    blocks: list[EvidenceBlock],
) -> tuple[str, int, EvidenceWindowPlan | None]:
    """Choose one global call when routed evidence fits, otherwise plan windows."""

    request_schema = compact_study_schema(block.block_id for block in blocks)
    approximate_tokens = _approximate_tokens(_direct_prompt(blocks, []), request_schema)
    mode = (
        config.mode
        if config.mode != "auto"
        else (
            "single"
            if approximate_tokens <= config.single_call_max_input_tokens
            else "windowed"
        )
    )
    if mode != "windowed":
        return mode, approximate_tokens, None
    schema_tokens = len(json.dumps(request_schema, ensure_ascii=False)) // 4
    evidence_budget = max(
        8_000, (config.window_input_tokens - schema_tokens - 4_000) * 4
    )
    return (
        mode,
        approximate_tokens,
        plan_evidence_windows(
            blocks,
            max_characters=evidence_budget,
            max_context_characters=min(24_000, evidence_budget // 3),
        ),
    )


def run_extraction(config: ExtractionConfig) -> dict[str, object]:
    """Write a complete, inspectable extraction run and return its report.

    Parsing and configuration artifacts are written before model calls. Model failure
    still yields a valid ``extraction.json`` and report; local grounding annotates
    rather than filters the rich result. NOMAD archives are the primary downstream
    artifacts; historical reduced conversion is available only when requested.
    """

    started = time.monotonic()
    if config.mode not in {"auto", "single", "windowed"}:
        raise ValueError("mode must be auto, single, or windowed")
    for path in (config.pdf, config.supplement):
        if path is not None and not path.exists():
            raise FileNotFoundError(path)
    if (
        min(
            config.single_call_max_input_tokens,
            config.window_input_tokens,
            config.max_output_tokens,
            config.inventory_max_output_tokens,
            config.enrichment_max_output_tokens,
            config.repair_max_output_tokens,
        )
        <= 0
    ):
        raise ValueError("token limits must be positive")

    config.output_dir.mkdir(parents=True, exist_ok=True)
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
    schema = StudyExtraction.model_json_schema()
    mode, approximate_tokens, window_plan = _plan_extraction(config, blocks)
    run_configuration = _run_configuration(config, mode, source_events)
    write_json_atomic(config.output_dir / "run_configuration.json", run_configuration)
    write_json_atomic(config.output_dir / "extraction.schema.json", schema)
    write_json_atomic(
        config.output_dir / "enrichment.schema.json",
        EnrichmentAudit.model_json_schema(),
    )
    logger.info(
        "Prepared {} evidence blocks (~{} request tokens); mode={}",
        len(blocks),
        approximate_tokens,
        mode,
    )

    if config.dry_run:
        if window_plan is not None:
            write_json_atomic(
                config.output_dir / "window_plan.json",
                window_plan.model_dump(mode="json"),
            )
        extraction_call_count = (
            1
            if mode == "single"
            else len(window_plan.windows if window_plan else [])
        )
        report = {
            "status": "dry_run",
            "mode": mode,
            "evidence_blocks": len(blocks),
            "selected_evidence_blocks": len(blocks),
            "approximate_request_tokens": approximate_tokens,
            "planned_calls": extraction_call_count
            + extraction_call_count * int(config.use_refinement)
            + int(config.use_inventory)
            + int(mode == "windowed" and extraction_call_count > 1)
            + (3 if config.use_enrichment else 0)
            + int(config.use_targeted_repair),
            "planned_refinement_calls": (
                extraction_call_count if config.use_refinement else 0
            ),
            "planned_enrichment_calls_max": 3 if config.use_enrichment else 0,
            "planned_targeted_repair_calls_max": int(config.use_targeted_repair),
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
    )
    errors: list[str] = []
    inventory: EvidenceInventory | None = None
    grounded_inventory: EvidenceInventory | None = None
    inventory_items: list[InventoryItem] = []
    selected_blocks = blocks
    if config.use_inventory:
        logger.info("Inventorying study records and routing evidence")
        try:
            inventory = client.complete(
                kind="evidence_inventory",
                slug="evidence_inventory",
                model=config.inventory_model or config.model,
                system=SYSTEM_PROMPT,
                prompt=_inventory_prompt(blocks),
                response_model=EvidenceInventory,
                max_output_tokens=config.inventory_max_output_tokens,
                reasoning_effort=config.reasoning_effort,
                request_schema=constrain_evidence_block_ids(
                    EvidenceInventory.model_json_schema(),
                    (block.block_id for block in blocks),
                ),
            )
        except ModelCallError as exc:
            errors.append(f"evidence_inventory: {exc}")
            write_json_atomic(
                config.output_dir / "evidence_routing.json",
                {
                    "status": "failed_open",
                    "error": str(exc),
                    "input_block_count": len(blocks),
                    "selected_block_count": len(blocks),
                },
            )
        else:
            write_json_atomic(
                config.output_dir / "evidence_inventory.json",
                inventory.model_dump(mode="json"),
            )
            inventory_items, inventory_grounding = grounded_inventory_items(
                blocks, inventory
            )
            grounded_inventory = EvidenceInventory(
                items=inventory_items, exclusions=inventory.exclusions
            )
            write_json_atomic(
                config.output_dir / "inventory_grounding.json", inventory_grounding
            )
            selected_blocks, routing = routed_blocks(blocks, inventory)
            write_json_atomic(
                config.output_dir / "evidence_routing.json",
                {"status": "complete", **routing},
            )

    mode, approximate_tokens, window_plan = _plan_extraction(config, selected_blocks)
    write_json_atomic(
        config.output_dir / "run_configuration.json",
        _run_configuration(config, mode, source_events),
    )
    if window_plan is not None:
        write_json_atomic(
            config.output_dir / "window_plan.json",
            window_plan.model_dump(mode="json"),
        )
    else:
        (config.output_dir / "window_plan.json").unlink(missing_ok=True)
    logger.info(
        "Selected {} of {} evidence blocks (~{} request tokens); mode={}",
        len(selected_blocks),
        len(blocks),
        approximate_tokens,
        mode,
    )
    for name in (
        "draft_extraction.json",
        "draft_candidates.json",
        "draft_validation.json",
        "draft_coverage_audit.json",
        "refinement_audit.json",
        "quality_comparison.json",
        "pre_repair_validation.json",
        "pre_repair_coverage_audit.json",
        "targeted_repair.json",
    ):
        (config.output_dir / name).unlink(missing_ok=True)
    for directory in ("draft_windows", "refinement_audits"):
        for stale in (config.output_dir / directory).glob("*.json"):
            stale.unlink()
    extraction, initial_extraction, extraction_errors = _run_model_calls(
        config, client, selected_blocks, mode, window_plan, inventory_items
    )
    errors.extend(extraction_errors)

    extraction, quote_repairs = repair_noncontiguous_citation_quotes(
        extraction, blocks
    )
    extraction, pointer_repairs = repair_unique_citation_pointers(extraction, blocks)
    citation_repairs = {
        "repair_count": quote_repairs["repair_count"]
        + pointer_repairs["repair_count"],
        "quote_repairs": quote_repairs,
        "pointer_repairs": pointer_repairs,
    }
    pre_repair_validation = validate_study(extraction, blocks)
    pre_repair_validation_public = dict(pre_repair_validation)
    pre_repair_validation_public.pop("verified_values")
    pre_repair_coverage = (
        audit_inventory_coverage(grounded_inventory, extraction)
        if grounded_inventory is not None
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
                config.output_dir / "pre_repair_coverage_audit.json",
                pre_repair_coverage,
            )
        logger.info("Checking targeted text-only repair worklist")
        extraction, repair_audit = run_targeted_repair(
            client=client,
            study=extraction,
            blocks=blocks,
            inventory=grounded_inventory,
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
    write_json_atomic(config.output_dir / "citation_repairs.json", citation_repairs)

    write_json_atomic(
        config.output_dir / "extraction.json", extraction.model_dump(mode="json")
    )
    validation = validate_study(extraction, blocks)
    grounded_values = validation.pop("verified_values")
    write_json_atomic(config.output_dir / "grounded_values.json", grounded_values)
    write_json_atomic(config.output_dir / "validation.json", validation)
    coverage_audit: dict[str, object] | None = None
    if grounded_inventory is not None:
        coverage_audit = audit_inventory_coverage(grounded_inventory, extraction)
        write_json_atomic(config.output_dir / "coverage_audit.json", coverage_audit)
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
            audit_inventory_coverage(grounded_inventory, comparable_draft)
            if grounded_inventory is not None
            else None
        )
        if draft_coverage is not None:
            write_json_atomic(
                config.output_dir / "draft_coverage_audit.json", draft_coverage
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
            "draft_coverage": draft_coverage["counts"] if draft_coverage else None,
            "final_coverage": coverage_audit["counts"] if coverage_audit else None,
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
            "evidence_inventory",
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
        "mode": mode,
        "evidence_blocks": len(blocks),
        "selected_evidence_blocks": len(selected_blocks),
        "approximate_request_tokens": approximate_tokens,
        **counts,
        "validation_issue_count": len(validation["issues"]),
        "citation_repair_count": citation_repairs["repair_count"],
        "refinement_calls": sum(
            call.get("kind")
            in {"study_refinement", "evidence_window_refinement"}
            for call in client.calls
        ),
        "targeted_repair_status": repair_audit.status if repair_audit else "disabled",
        "quality_comparison": quality_comparison,
        "coverage": coverage_audit["counts"] if coverage_audit else None,
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
        "source_parsing": source_events,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    write_json_atomic(config.output_dir / "report.json", report)
    return report
