"""Adopt an externally completed review without inventing per-field approvals.

The coordinator confirms that an expert finished the paper review. We preserve
that statement separately from the expert's clicks and exclude later changes.
This module never changes scientific values or decides what an ambiguous note means.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from review_workbench.study_review import RECORD_IDENTIFIERS, _record_catalog


class AdoptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    expert_id: str = Field(min_length=1)
    reviewed_through_revision: int = Field(ge=1)
    completed_paper_review_confirmed: Literal[True]
    note: str = Field(min_length=1, max_length=4000)


def review_candidate(current, expert_id: str) -> dict | None:
    """Find the end of the expert's saved review, not the current model revision."""
    undone = {e["details"].get("undoes_event_id") for e in current.events}
    reset = max(
        (
            e["revision"]
            for e in current.events
            if e["reviewer_id"] == expert_id
            and e["kind"] == "review_reset"
            and e["event_id"] not in undone
        ),
        default=0,
    )
    events = [
        e
        for e in current.events
        if e["reviewer_id"] == expert_id
        and e["event_id"] not in undone
        and not e["details"].get("undoes_event_id")
        and e["revision"] > reset
        and e["kind"]
        in {
            "record_decision",
            "mutation",
            "structural_mutation",
            "inventory_audit",
            "spreadsheet_review",
        }
    ]
    if not events:
        return None
    last = events[-1]
    return {
        "expert_id": expert_id,
        "reviewed_through_revision": last["revision"],
        "review_event_id": last["event_id"],
        "reviewed_at": last["timestamp"],
    }


def unchanged_since_review(current, cutoff: int) -> dict[str, str]:
    """Exclude records edited after the expert finished, even if later reverted.

    Full-record and structural edits have stable identifiers. Historical scalar
    edits have only array positions; for those, conservatively reopen that collection
    rather than attach an old position to a different device.
    """
    catalog = _record_catalog(current.ground_truth)
    changed = set()
    for event in current.events:
        if event["revision"] <= cutoff:
            continue
        details = event["details"]
        if event["kind"] == "ground_truth_refresh":
            return {}
        if event["kind"] == "mutation":
            parts = (event.get("path") or "").strip("/").split("/")
            if parts[0] not in RECORD_IDENTIFIERS:
                # Paper/source metadata changes may alter what was reviewed.
                return {}
            collection, identifier = parts[0], RECORD_IDENTIFIERS[parts[0]]
            values = [event.get("before"), event.get("after")]
            if len(parts) == 2 and any(
                isinstance(v, dict) and identifier in v for v in values
            ):
                changed.update(
                    f"{collection}:{v[identifier]}"
                    for v in values
                    if isinstance(v, dict) and identifier in v
                )
            else:
                changed.update(k for k in catalog if k.startswith(collection + ":"))
        for item in details.get("record_replacements", []):
            changed.add(f"{item['collection']}:{item['record_id']}")
        for item in details.get("collection_replacements", []):
            collection = item["collection"]
            identifier = RECORD_IDENTIFIERS[collection]
            before = {r[identifier]: r for r in item["before"]}
            after = {r[identifier]: r for r in item["after"]}
            changed.update(
                f"{collection}:{key}"
                for key in before.keys() | after.keys()
                if before.get(key) != after.get(key)
            )
    return {key: digest for key, digest in catalog.items() if key not in changed}


def scope_changes_since_review(current, cutoff: int) -> list[dict]:
    """Keep later removals and metadata changes visible even without a record to approve.

    Explicit finalization decisions already document the finalizer's judgment.
    Ordinary editor actions do not, and a deleted record cannot appear in the
    current-record approval queue.
    """
    changes = []
    for event in current.events:
        if event["revision"] <= cutoff or event["kind"] == "adjudication_decision":
            continue
        details = event["details"]
        removed = []
        metadata = False
        if event["kind"] == "mutation":
            parts = (event.get("path") or "").strip("/").split("/")
            collection = parts[0]
            metadata = collection not in RECORD_IDENTIFIERS
            before, after = event.get("before"), event.get("after")
            if (
                not metadata
                and len(parts) == 2
                and isinstance(before, dict)
                and after is None
            ):
                removed.append(f"{collection}:{before[RECORD_IDENTIFIERS[collection]]}")
        for item in details.get("record_replacements", []):
            if item.get("before") is not None and item.get("after") is None:
                removed.append(f"{item['collection']}:{item['record_id']}")
        for item in details.get("collection_replacements", []):
            collection = item["collection"]
            identifier = RECORD_IDENTIFIERS[collection]
            missing = {r[identifier] for r in item["before"]} - {
                r[identifier] for r in item["after"]
            }
            removed.extend(f"{collection}:{key}" for key in sorted(missing))
        if removed or metadata:
            changes.append(
                {
                    "event_id": event["event_id"],
                    "revision": event["revision"],
                    "removed_record_keys": sorted(set(removed)),
                    "metadata_changed": metadata,
                }
            )
    return changes
