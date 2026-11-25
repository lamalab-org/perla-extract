"""Keep a correction only when source integrity does not regress.

These checks do not establish scientific accuracy. They guard citation validity
and reader-note coverage; reviewed reference data measures accuracy separately.
"""

from __future__ import annotations

from .claims import ClaimLedger
from .correction_checks import candidate_quality, is_monotonic_quality
from .models import EvidenceBlock, StudyExtraction


def _select_refinement_candidate(
    draft: StudyExtraction,
    refinement: StudyExtraction,
    blocks: list[EvidenceBlock],
    ledger: ClaimLedger | None,
) -> tuple[StudyExtraction, dict[str, object]]:
    """Keep source-supported values while checking source links and note coverage.

    These source-link and claim-coverage counts are integrity checks, not a measure
    of scientific correctness. Tied issue counts permit a changed candidate only
    when source values survive; measure accuracy against reviewed references.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    refinement_quality = candidate_quality(refinement, blocks, ledger, baseline=draft)
    same_candidate = draft == refinement
    selected = (
        "refinement"
        if not same_candidate
        and is_monotonic_quality(draft_quality, refinement_quality)
        else "draft"
    )
    reason = (
        "refinement preserved source-supported values without worsening "
        "validation or reader-note coverage"
    )
    if same_candidate:
        reason = "refinement produced no candidate change"
    elif selected == "draft":
        reason = (
            "refinement lost source-supported values or worsened validation "
            "or reader-note coverage"
        )
    return (
        draft if selected == "draft" else refinement,
        {
            "selected": selected,
            "reason": reason,
            "proposal_decision": {"selected": selected, "reason": reason},
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
    a chain of accepted edits from losing source values or worsening integrity
    relative to the first valid draft. Record counts are not rewards: removing an
    exact duplicate can be a legitimate correction.
    """

    draft_quality = candidate_quality(draft, blocks, ledger)
    candidate_quality_summary = candidate_quality(
        candidate, blocks, ledger, baseline=draft
    )
    accepted = is_monotonic_quality(draft_quality, candidate_quality_summary)
    result = dict(selection)
    result.setdefault(
        "proposal_decision",
        {"selected": selection["selected"], "reason": selection.get("reason")},
    )
    result["pre_repair_selected"] = selection["selected"]
    result["draft_quality"] = draft_quality
    result["final_candidate_quality"] = candidate_quality_summary
    integrity_reason = (
        "final candidate preserves source-supported claims without worsening integrity"
        if accepted
        else "final candidate loses source-supported claims or worsens integrity"
    )
    result["final_integrity_check"] = {"accepted": accepted, "reason": integrity_reason}
    if accepted:
        result["selected"] = str(selection["selected"])
        # A retained draft passing this check does not make the rejected proposal
        # acceptable. Preserve that selection explanation at the top level too.
        result["reason"] = selection.get("reason", integrity_reason)
        return candidate, result
    result["selected"] = "draft"
    result["reason"] = (
        "final candidate lost source-supported values or worsened validation "
        "or reader-note coverage relative to the draft"
    )
    return draft, result
