"""Re-read a complete extraction draft without hiding the model's changes."""

from __future__ import annotations

import json
from pathlib import Path

from .artifacts import write_json_atomic
from .client import ModelCallError, ModelClient
from .guidance import DEVICE_FAMILY_POLICY, SHARED_QUANTITY_POLICY
from .models import EvidenceBlock, StudyExtraction
from .spans import EvidenceSpan, build_evidence_spans
from .transport import (
    compact_to_span_citations,
    expand_span_citations,
    span_citation_schema,
)

REFINEMENT_PROMPT = f"""Audit the supplied draft against all supplied evidence and
return a complete corrected StudyExtraction.

{DEVICE_FAMILY_POLICY}
{SHARED_QUANTITY_POLICY}
Treat the draft and claim ledger as fallible aids, never as source evidence. Reconcile
experimental objects globally before deciding final record identity. For every
grounded target claim, either represent the supported fact at the correct reporting
level or explain the unresolved conflict in unresolved_notes. Context objects and
claims must not create output records.
Recover supported records and atomic values the draft missed. Remove or correct
duplicates, unsupported claims, wrong links, and mixed individual/population records.
In particular, consolidate draft families that are only processing arms of the same
device design, and remove characterization-only partial structures from
device_families. Do not discard their supported facts: move specimen-specific values
to the appropriate individual device, or state an unresolved group-only distinction
in unresolved_notes when the present schema cannot represent it faithfully.
Keep specimen-specific values on IndividualDevice.reported_properties and conditions
that change during aging on the corresponding StabilityCheckpoint.conditions.
Preserve correct content. Every retained or added scientific claim must cite supplied
evidence under the ordinary extraction rules. Return the entire revised extraction,
not a patch or commentary.
"""


def _prompt(
    evidence_prompt: str,
    draft: StudyExtraction,
    spans: list[EvidenceSpan],
) -> str:
    """Put the fallible draft before the unchanged extraction evidence and rules."""

    return (
        REFINEMENT_PROMPT
        + "\n\nDRAFT EXTRACTION WITH EVIDENCE SPAN REFERENCES:\n"
        + json.dumps(
            compact_to_span_citations(draft, spans),
            ensure_ascii=False,
        )
        + "\n\nCLAIMS AND SOURCE EVIDENCE:\n"
        + evidence_prompt
    )


def _audit(draft: StudyExtraction, refined: StudyExtraction) -> dict[str, object]:
    """Index record-level changes without attempting to explain model behavior."""

    identifiers = {
        "device_families": "family_id",
        "individual_devices": "device_id",
        "performance_observations": "observation_id",
        "population_statistics": "population_id",
        "stability_tests": "test_id",
        "identity_links": "link_id",
    }
    changes: dict[str, object] = {}
    for collection, identifier in identifiers.items():
        before = {
            str(getattr(record, identifier)): record.model_dump(mode="json")
            for record in getattr(draft, collection)
        }
        after = {
            str(getattr(record, identifier)): record.model_dump(mode="json")
            for record in getattr(refined, collection)
        }
        changes[collection] = {
            "before_count": len(before),
            "after_count": len(after),
            "added_ids": sorted(after.keys() - before.keys()),
            "removed_ids": sorted(before.keys() - after.keys()),
            "changed_ids": sorted(
                record_id
                for record_id in before.keys() & after.keys()
                if before[record_id] != after[record_id]
            ),
        }
    return {"collections": changes}


def refine_draft(
    client: ModelClient,
    *,
    draft: StudyExtraction,
    evidence_prompt: str,
    blocks: list[EvidenceBlock],
    model: str,
    reasoning_effort: str | None,
    max_output_tokens: int,
    system_prompt: str,
    kind: str,
    slug: str,
    draft_path: Path,
    audit_path: Path,
    spans: list[EvidenceSpan] | None = None,
) -> tuple[StudyExtraction, str | None]:
    """Refine a valid draft, retaining it as the safe fallback and audit baseline."""

    write_json_atomic(draft_path, draft.model_dump(mode="json"))
    citation_spans = spans or build_evidence_spans(blocks)
    try:
        refined = client.complete(
            kind=kind,
            slug=slug,
            model=model,
            system=system_prompt,
            prompt=_prompt(evidence_prompt, draft, citation_spans),
            response_model=StudyExtraction,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            request_schema=span_citation_schema(StudyExtraction, citation_spans),
            decode=lambda payload: expand_span_citations(payload, citation_spans),
        )
    except ModelCallError as exc:
        return draft, str(exc)
    write_json_atomic(audit_path, _audit(draft, refined))
    return refined, None
