"""End-to-end, high-recall extraction of a paper and its supplementary information."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from .artifacts import write_json_atomic
from .client import ModelCallError, ModelClient
from .compatibility import to_reduced_with_report
from .logging import logger
from .merge import merge_candidates
from .models import Paper, StudyExtraction
from .partitioning import EvidenceBlock, WindowPlan, plan_windows
from .reconciliation import ReconciliationResult, attach_valid_equivalences
from .source import parse_documents
from .validation import validate_study

SCHEMA_VERSION = "2026-08-15.1"
PROMPT_VERSION = "2026-08-15.1"

SYSTEM_PROMPT = """Extract the complete present photovoltaic study as device-centered data.
Use only supplied evidence. Preserve source wording and distinctions. Never invent,
interpolate, digitize graph traces, or import facts from cited literature."""

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
- Extract the complete ordered layer stack, the explicitly reported absorber formula,
  absorber constituents, additives or dopants, and all reported processing steps.
  Use one MaterialConstituent per named chemical; do not combine a list of chemicals
  into one constituent.
- Put arbitrary reported material, geometry, processing, measurement, and stability
  values in generic Fact records. Do not omit a value because it lacks a dedicated
  schema field.
- Fact.raw_value and EvidenceRef.quote must be copied from a supplied block. Cite the
  smallest useful quote and only supplied block IDs. Typography-preserving renderings
  are source evidence and may be used for subscript or superscript notation.
- Stability tests remain separate experiments with ordered checkpoints, even when
  linked to a performance device.
- Exclude background examples, prior literature, reference entries, review material,
  and devices not made or measured in this study.
- Use null or not_reported for unknown identity or context. Do not guess.
- Empty arrays are correct only when all supplied evidence truly contains no record of
  that kind.
- Return equivalence_groups as an empty array. Cross-window identity is handled by a
  separate auditable reconciliation call.
"""

WINDOW_PROMPT = """PRIMARY EVIDENCE is the part assigned to this extraction window.
CONTEXT EVIDENCE is supplied only to understand identity and terminology. Emit a
candidate only when at least one of its EvidenceRef entries cites a PRIMARY EVIDENCE
block. Do not emit candidates supported exclusively by context. Partial candidates are
allowed: later windows are combined without deleting them."""

RECONCILIATION_PROMPT = """Identify only candidates that refer to the same real-world
entity across extraction windows. Return explicit equivalence groups; do not merge,
delete, rank, or rewrite candidates.

Rules:
- Group only IDs of the declared entity_kind and only IDs present below.
- Require positive identity evidence, not merely similar materials or metric values.
- Device variants, different individual devices, different scan directions, population
  statistics, and separate stability specimens are distinct unless the candidate
  evidence explicitly establishes identity.
- Each member may appear in at most one group of its entity kind.
- Cite the smallest supplied candidate evidence quotes that support the identity link.
- An empty group list is correct when identity is uncertain.
"""


@dataclass(frozen=True)
class ExtractionConfig:
    """Collect settings that change parsing, model behavior, or scientific output."""

    pdf: Path
    supplement: Path | None
    output_dir: Path
    model: str = "openrouter/openai/gpt-5.6-sol:exacto"
    reasoning_effort: str | None = "medium"
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
    dry_run: bool = False


def _evidence(blocks: list[EvidenceBlock]) -> list[dict[str, object]]:
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


def _direct_prompt(blocks: list[EvidenceBlock]) -> str:
    """Give one call global evidence context when the complete study fits."""

    return (
        EXTRACTION_PROMPT
        + "\n\nCOMPLETE STUDY EVIDENCE:\n"
        + json.dumps(_evidence(blocks), ensure_ascii=False)
    )


def _window_prompt(primary: list[EvidenceBlock], context: list[EvidenceBlock]) -> str:
    """Tell the model not to emit candidates supported only by repeated context."""

    return (
        EXTRACTION_PROMPT
        + "\n\n"
        + WINDOW_PROMPT
        + "\n\nCONTEXT EVIDENCE:\n"
        + json.dumps(_evidence(context), ensure_ascii=False)
        + "\n\nPRIMARY EVIDENCE:\n"
        + json.dumps(_evidence(primary), ensure_ascii=False)
    )


def _reconciliation_prompt(candidates: StudyExtraction) -> str:
    """Ask only for identity links so reconciliation cannot rewrite candidates."""

    return (
        RECONCILIATION_PROMPT
        + "\n\nCANDIDATE UNION:\n"
        + candidates.model_dump_json(exclude={"equivalence_groups"})
    )


def _approximate_tokens(prompt: str, schema: dict) -> int:
    """Estimate request size conservatively without a model-specific tokenizer."""

    return (len(prompt) + len(json.dumps(schema, ensure_ascii=False))) // 4


def _empty(note: str) -> StudyExtraction:
    """Produce a valid inspectable result even when no model call succeeds."""

    return StudyExtraction(
        paper=Paper(title=None, doi=None),
        device_families=[],
        individual_devices=[],
        performance_observations=[],
        population_statistics=[],
        stability_tests=[],
        unresolved_notes=[note[:500]],
    )


def _usage(calls: list[dict[str, object]]) -> dict[str, float | int]:
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


def _configuration(
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
            "schema_version": SCHEMA_VERSION,
            "prompt_version": PROMPT_VERSION,
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


def _extract(
    config: ExtractionConfig,
    client: ModelClient,
    blocks: list[EvidenceBlock],
    mode: str,
    plan: WindowPlan | None,
) -> tuple[StudyExtraction, list[str]]:
    """Run the selected call plan while retaining every successful partial result.

    Window failures are accumulated rather than invalidating successful windows.
    Reconciliation adds audited equivalence groups to the lossless candidate union; it
    never chooses a winner or rewrites scientific fields.
    """

    errors: list[str] = []
    if mode == "single":
        logger.info("Extracting the complete study in one model call")
        try:
            extraction = client.complete(
                kind="complete_study",
                slug="complete_study",
                model=config.model,
                system=SYSTEM_PROMPT,
                prompt=_direct_prompt(blocks),
                response_model=StudyExtraction,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
            )
        except ModelCallError as exc:
            errors.append(str(exc))
            extraction = _empty(
                "Complete-study model call failed; inspect requests/ and report.json."
            )
        return extraction, errors

    parts: list[tuple[str, StudyExtraction]] = []
    windows = plan.windows if plan else []
    for index, window in enumerate(windows, start=1):
        logger.info(
            "Extracting evidence window {}/{} ({}, {} primary blocks)",
            index,
            len(windows),
            window.window_id,
            len(window.primary_blocks),
        )
        try:
            part = client.complete(
                kind="evidence_window",
                slug=window.window_id,
                model=config.model,
                system=SYSTEM_PROMPT,
                prompt=_window_prompt(window.primary_blocks, window.context_blocks),
                response_model=StudyExtraction,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
            )
        except ModelCallError as exc:
            errors.append(f"{window.window_id}: {exc}")
            continue
        parts.append((window.window_id, part))
        write_json_atomic(
            config.output_dir / "windows" / f"{window.window_id}.json",
            part.model_dump(mode="json"),
        )
    extraction = (
        merge_candidates(parts)
        if parts
        else _empty(
            "All evidence-window model calls failed; inspect requests/ and report.json."
        )
    )
    if parts:
        write_json_atomic(
            config.output_dir / "candidates.json", extraction.model_dump(mode="json")
        )
    if len(parts) > 1:
        logger.info("Reconciling candidate identity across {} windows", len(parts))
        try:
            proposal = client.complete(
                kind="candidate_reconciliation",
                slug="candidate_reconciliation",
                model=config.model,
                system=SYSTEM_PROMPT,
                prompt=_reconciliation_prompt(extraction),
                response_model=ReconciliationResult,
                max_output_tokens=config.max_output_tokens,
                reasoning_effort=config.reasoning_effort,
            )
        except ModelCallError as exc:
            errors.append(f"candidate_reconciliation: {exc}")
            write_json_atomic(
                config.output_dir / "reconciliation.json",
                {"status": "failed", "error": str(exc)},
            )
        else:
            extraction, audit = attach_valid_equivalences(extraction, proposal)
            write_json_atomic(
                config.output_dir / "reconciliation.json",
                {
                    "status": "accepted" if not audit.issues else "needs_review",
                    **audit.model_dump(mode="json"),
                },
            )
    return extraction, errors


def run_extraction(config: ExtractionConfig) -> dict[str, object]:
    """Write a complete, inspectable extraction run and return its report.

    Parsing and configuration artifacts are written before model calls. Model failure
    still yields a valid ``extraction.json`` and report; local grounding annotates
    rather than filters the rich result; reduced conversion is an independent final
    step whose losses are recorded separately.
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
    response_model = StudyExtraction
    schema = response_model.model_json_schema()
    prompt = _direct_prompt(blocks)
    approximate_tokens = _approximate_tokens(prompt, schema)
    mode = (
        config.mode
        if config.mode != "auto"
        else (
            "single"
            if approximate_tokens <= config.single_call_max_input_tokens
            else "windowed"
        )
    )
    configuration = _configuration(config, mode, source_events)
    write_json_atomic(config.output_dir / "run_configuration.json", configuration)
    write_json_atomic(config.output_dir / "extraction.schema.json", schema)
    logger.info(
        "Prepared {} evidence blocks (~{} request tokens); mode={}",
        len(blocks),
        approximate_tokens,
        mode,
    )

    plan: WindowPlan | None = None
    if mode == "windowed":
        schema_tokens = len(json.dumps(schema, ensure_ascii=False)) // 4
        evidence_budget = max(
            8_000, (config.window_input_tokens - schema_tokens - 4_000) * 4
        )
        plan = plan_windows(
            blocks,
            max_characters=evidence_budget,
            max_context_characters=min(24_000, evidence_budget // 3),
        )
        write_json_atomic(
            config.output_dir / "window_plan.json", plan.model_dump(mode="json")
        )

    if config.dry_run:
        report = {
            "status": "dry_run",
            "mode": mode,
            "evidence_blocks": len(blocks),
            "approximate_request_tokens": approximate_tokens,
            "planned_calls": (
                1
                if mode == "single"
                else (
                    len(plan.windows if plan else [])
                    + int(len(plan.windows if plan else []) > 1)
                )
            ),
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
    extraction, errors = _extract(config, client, blocks, mode, plan)

    write_json_atomic(
        config.output_dir / "extraction.json", extraction.model_dump(mode="json")
    )
    validation = validate_study(extraction, blocks)
    grounded_facts = validation.pop("verified_facts")
    write_json_atomic(config.output_dir / "grounded_facts.json", grounded_facts)
    write_json_atomic(config.output_dir / "validation.json", validation)
    conversion_error: str | None = None
    try:
        reduced = to_reduced_with_report(extraction)
    except (ValidationError, ValueError) as exc:
        conversion_error = str(exc)
        write_json_atomic(
            config.output_dir / "reduced_conversion_failure.json",
            {"error": conversion_error},
        )
    else:
        write_json_atomic(
            config.output_dir / "reduced.json", reduced.cells.model_dump(mode="json")
        )
        write_json_atomic(
            config.output_dir / "reduced_conversion.json",
            reduced.model_dump(mode="json"),
        )

    counts = validation["counts"]
    status = (
        "failed"
        if not client.calls or all(call.get("error") for call in client.calls)
        else (
            "partial"
            if errors or conversion_error
            else (
                "complete"
                if validation["status"] == "verified"
                else "complete_needs_review"
            )
        )
    )
    report = {
        "status": status,
        "mode": mode,
        "evidence_blocks": len(blocks),
        "approximate_request_tokens": approximate_tokens,
        **counts,
        "validation_issue_count": len(validation["issues"]),
        "conversion_error": conversion_error,
        "errors": errors,
        "calls": client.calls,
        "usage": _usage(client.calls),
        "source_parsing": source_events,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    write_json_atomic(config.output_dir / "report.json", report)
    return report
