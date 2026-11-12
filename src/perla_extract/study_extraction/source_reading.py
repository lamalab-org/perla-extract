"""Plan bounded readings, preserve their provenance, and combine source notes.

Every window retains all primary evidence. Context only resolves terminology;
repeat readings use independent instructions, not previous model answers.
"""

from __future__ import annotations

from .artifacts import write_json_atomic
from .claims import ClaimLedger, combine_ledgers
from .client import ModelCallError, ModelClient
from .configuration import ExtractionConfig
from .deployment import strict_response_schema
from .extraction_prompts import (
    CLAIM_LEDGER_PROMPT,
    CLAIM_RECALL_PROMPTS,
    SYSTEM_PROMPT,
    _claim_ledger_prompt,
)
from .logging import logger
from .models import EvidenceBlock
from .partitioning import fit_request_windows, plan_evidence_windows
from .spans import build_evidence_spans, evidence_payload
from .transport import expand_span_citations, span_citation_schema


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

    policy = config.stage_policy("read", claim_mode="single")

    def estimate(primary: list[EvidenceBlock], context: list[EvidenceBlock]) -> int:
        schema = span_citation_schema(
            ClaimLedger, build_evidence_spans([*context, *primary])
        )
        return max(
            policy.estimate(
                SYSTEM_PROMPT,
                _claim_ledger_prompt(primary, context, reading),
                strict_response_schema(ClaimLedger, schema),
            )["estimated_input_tokens"]
            for reading in range(1, config.claim_recall_passes + 1)
        )

    approximate_tokens = estimate(blocks, [])
    single_limit = policy.input_limit()
    assert single_limit is not None
    if single_limit <= 0:
        raise ValueError("reading output allowance leaves no space for source input")
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
        input_limit = config.stage_policy("read", claim_mode="windowed").input_limit()
        assert input_limit is not None
        if input_limit <= 0:
            raise ValueError(
                "reading output allowance leaves no space for source input"
            )
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
                    stage="read",
                    kind="source_claim_ledger",
                    slug=pass_slug,
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
                    request_schema=span_citation_schema(ClaimLedger, spans),
                    decode=decode_claim_payload,
                )
            except ModelCallError as exc:
                errors.append(f"{pass_slug}: {exc}")
                continue
            parts.append((pass_slug, ledger))
    planned_calls = len(plan) * config.claim_recall_passes
    if not parts:
        return ClaimLedger(objects=[], claims=[]), errors, planned_calls
    provenance: dict[str, object] = {}
    combined = combine_ledgers(parts, provenance=provenance)
    write_json_atomic(config.output_dir / "claim_readings.json", provenance)
    return combined, errors, planned_calls
