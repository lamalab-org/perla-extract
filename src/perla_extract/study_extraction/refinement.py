"""Re-read a complete extraction draft without hiding the model's changes."""

from __future__ import annotations

import json
from pathlib import Path

from .artifacts import write_json_atomic
from .client import ModelCallError, ModelClient
from .models import EvidenceBlock, StudyExtraction
from .transport import compact_study, compact_study_schema, expand_compact_study

REFINEMENT_PROMPT = """Audit the supplied draft against all supplied evidence and
return a complete corrected StudyExtraction.

Treat the draft and independent inventory as fallible aids, never as source evidence.
For every grounded inventory candidate, either represent the source-supported record
at the correct reporting level or explain the unresolved conflict in unresolved_notes.
Recover supported records and atomic values the draft missed. Remove or correct
duplicates, unsupported claims, wrong links, and mixed individual/population records.
Keep specimen-specific values on IndividualDevice.reported_properties and conditions
that change during aging on the corresponding StabilityCheckpoint.conditions.
Preserve correct content. Every retained or added scientific claim must cite supplied
evidence under the ordinary extraction rules. Return the entire revised extraction,
not a patch or commentary.
"""


def _prompt(evidence_prompt: str, draft: StudyExtraction) -> str:
    """Put the fallible draft before the unchanged extraction evidence and rules."""

    return (
        REFINEMENT_PROMPT
        + "\n\nDRAFT EXTRACTION WITH SHARED EVIDENCE CATALOG:\n"
        + json.dumps(compact_study(draft), ensure_ascii=False)
        + "\n\nEVIDENCE AND INVENTORY:\n"
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
) -> tuple[StudyExtraction, str | None]:
    """Refine a valid draft, retaining it as the safe fallback and audit baseline."""

    write_json_atomic(draft_path, draft.model_dump(mode="json"))
    try:
        refined = client.complete(
            kind=kind,
            slug=slug,
            model=model,
            system=system_prompt,
            prompt=_prompt(evidence_prompt, draft),
            response_model=StudyExtraction,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            request_schema=compact_study_schema(block.block_id for block in blocks),
            decode=expand_compact_study,
        )
    except ModelCallError as exc:
        return draft, str(exc)
    write_json_atomic(audit_path, _audit(draft, refined))
    return refined, None
