"""Create a source-grounded final extraction without hiding discarded claims."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from .models import EvidenceBlock, StudyExtraction
from .validation import Location, validate_study_details

_REMOVABLE_REASONS = {
    "raw_value not found in cited evidence",
    "material_form_raw not found in cited evidence",
}


def _remove_claim(data: dict[str, Any], location: Location) -> object:
    """Remove one optional atomic claim while preserving its value for the audit."""

    if not location:
        raise ValueError("the extraction root is not an optional claim")
    parent: Any = data
    for part in location[:-1]:
        parent = parent[part]
    key = location[-1]
    removed = deepcopy(parent[key])

    if key == "material_form_raw" and isinstance(parent, dict):
        parent[key] = None
        parent["material_form"] = "not_reported"
    elif (
        isinstance(removed, dict)
        and {"name", "raw_value", "evidence"} <= removed.keys()
    ):
        if isinstance(parent, list):
            if not isinstance(key, int):
                raise ValueError("a list claim requires an indexed validation path")
            parent.pop(key)
        else:
            parent[key] = None
    else:
        raise ValueError("validation path does not identify a removable atomic claim")
    return removed


def remove_unsupported_optional_claims(
    extraction: StudyExtraction, blocks: list[EvidenceBlock]
) -> tuple[StudyExtraction, dict[str, Any]]:
    """Drop only optional claims whose removal strictly improves source validation.

    Model repair is useful for recoverable pointers and quotations, but an
    unsupported optional value should not prevent an otherwise grounded study from
    entering review. Each tentative removal must produce a schema-valid extraction,
    lower the total issue count, and introduce no new issue category. The exact
    removed content remains in this audit and the workflow also retains the complete
    pre-finalization extraction.
    """

    current = extraction
    removals: list[dict[str, object]] = []
    while True:
        before = validate_study_details(current, blocks)
        before_counts = Counter(issue.reason for issue in before.issues)
        accepted = False
        for issue in before.issues:
            if issue.reason not in _REMOVABLE_REASONS:
                continue
            candidate_data = deepcopy(current.model_dump(mode="json"))
            try:
                removed = _remove_claim(candidate_data, issue.location)
                candidate = StudyExtraction.model_validate(candidate_data)
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            after = validate_study_details(candidate, blocks)
            after_counts = Counter(item.reason for item in after.issues)
            if len(after.issues) >= len(before.issues):
                continue
            if any(
                after_counts[reason] > count for reason, count in before_counts.items()
            ):
                continue
            if any(reason not in before_counts for reason in after_counts):
                continue
            removals.append(
                {
                    "path": issue.path,
                    "reason": issue.reason,
                    "removed": removed,
                }
            )
            current = candidate
            accepted = True
            break
        if not accepted:
            final = validate_study_details(current, blocks)
            return current, {
                "removal_count": len(removals),
                "removals": removals,
                "remaining_issue_count": len(final.issues),
                "remaining_issues": [issue.as_dict() for issue in final.issues],
            }
