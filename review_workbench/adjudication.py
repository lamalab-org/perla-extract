"""Resolve a short admin queue, then adopt current reviews into a frozen reference.

All proposals and decisions live in the existing immutable paper revision history.
No separate database or browser-only approval state is needed. Reviewer approval is
adopted explicitly, never inferred from a census count or an old record identifier.
"""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from perla_extract.study_extraction.models import StudyExtraction
from perla_extract.study_extraction.validation import validate_study
from review_workbench.ground_truth_export import _evidence_blocks
from review_workbench.study_review import (
    RECORD_IDENTIFIERS,
    Citation,
    RecordCollection,
    ReviewEvent,
    StudyReviewStore,
    _changed_collections,
    _digest,
    _record_catalog,
)


class RecordChange(BaseModel):
    """Bind a suggested replacement to its exact previous content, not a list index."""

    model_config = ConfigDict(extra="forbid", strict=True)
    collection: RecordCollection
    record_id: str = Field(min_length=1, max_length=200)
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class Proposal(BaseModel):
    """Keep the rationale and evidence next to every proposed scientific edit."""

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=12000)
    changes: list[RecordChange] = Field(default_factory=list, max_length=100)
    evidence: list[Citation] = Field(default_factory=list, max_length=30)


class PlanRequest(BaseModel):
    """Import suggestions only when the saved study is still the one inspected."""

    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    study_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposals: list[Proposal] = Field(min_length=1, max_length=100)


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    case_id: str
    action: Literal["accept", "keep"]
    note: str = Field(min_length=1, max_length=4000)


class FinalizeRequest(BaseModel):
    """Require explicit responsibility for correctness and missing-record checking."""

    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    adopt_current_reviews: Literal[True]
    completeness_checked: Literal[True]
    note: str = Field(default="", max_length=4000)


class ConfirmRecordsRequest(BaseModel):
    """Let an admin check a workbook's current records together without fake inheritance."""

    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    record_keys: list[str] = Field(min_length=1, max_length=2000)
    current_records_checked: Literal[True]
    note: str = Field(min_length=1, max_length=4000)


def _records(truth: dict) -> dict[str, dict]:
    return {
        f"{collection}:{record[identifier]}": record
        for collection, identifier in RECORD_IDENTIFIERS.items()
        for record in truth[collection]
    }


def _binding(truth: dict, keys: list[str]) -> str:
    """Invalidate a resolution when its affected records change; notes bind the paper."""

    records = _records(truth)
    return _digest({key: records.get(key) for key in keys} if keys else truth)


class AdjudicationQueue:
    def __init__(self, store: StudyReviewStore):
        self.store = store

    def confirm_records(
        self, split: str, paper: str, request: ConfirmRecordsRequest, reviewer: str
    ) -> dict:
        current = self.store._validate_revision(split, paper, request.base_revision)
        pending = {
            case["record_key"]
            for case in self.load(split, paper, reviewer)["cases"]
            if "record_key" in case
        }
        keys = request.record_keys
        if len(set(keys)) != len(keys) or not set(keys).issubset(pending):
            raise ValueError("Select distinct records from the current pending queue")
        catalog = _record_catalog(current.ground_truth)
        return self._commit(
            split,
            paper,
            current,
            reviewer,
            "adjudication_decision",
            {
                "case_id": f"batch:{uuid.uuid4()}",
                "action": "confirm_records",
                "current_records_checked": True,
                "decisions": [
                    {
                        "record_key": key,
                        "record_digest": catalog[key],
                        "decision": "verified",
                    }
                    for key in keys
                ],
                "collection_replacements": [],
            },
            note=request.note,
        )

    def _commit(
        self,
        split,
        paper,
        current,
        reviewer,
        kind,
        details,
        *,
        truth=None,
        note="",
        evidence=None,
    ):
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer,
            kind=kind,
            details=details,
            note=note,
            evidence=evidence or [],
        )
        self.store._commit(
            split,
            paper,
            current,
            truth if truth is not None else current.ground_truth,
            event.model_dump(mode="json"),
        )
        return self.load(split, paper, reviewer)

    def import_plan(
        self, split: str, paper: str, request: PlanRequest, reviewer: str
    ) -> dict:
        current = self.store._validate_revision(split, paper, request.base_revision)
        if _digest(current.ground_truth) != request.study_sha256:
            raise ValueError(
                "This proposal was prepared for different records. Refresh it before importing."
            )
        ids = [proposal.id for proposal in request.proposals]
        if len(set(ids)) != len(ids):
            raise ValueError("Proposal identifiers must be distinct")
        existing = {
            p["id"]
            for event in current.events
            if event["kind"] == "adjudication_plan"
            for p in event["details"]["proposals"]
        }
        if existing.intersection(ids):
            raise ValueError(
                "This proposal is already saved; existing proposals are never replaced"
            )
        for proposal in request.proposals:
            self.store._validate_citations(split, paper, proposal.evidence)
            if proposal.changes:
                if not proposal.evidence:
                    raise ValueError("Scientific changes require source evidence")
                self._apply(current.ground_truth, proposal.changes)
        return self._commit(
            split,
            paper,
            current,
            reviewer,
            "adjudication_plan",
            {
                "proposals": [p.model_dump(mode="json") for p in request.proposals],
                "study_sha256": request.study_sha256,
            },
        )

    @staticmethod
    def _apply(truth: dict, changes: list[RecordChange]) -> dict:
        result = copy.deepcopy(truth)
        seen = set()
        for change in changes:
            key = (change.collection, change.record_id)
            if key in seen:
                raise ValueError("A proposal cannot change the same record twice")
            seen.add(key)
            identifier = RECORD_IDENTIFIERS[change.collection]
            records = result[change.collection]
            index = next(
                (i for i, r in enumerate(records) if r[identifier] == change.record_id),
                None,
            )
            before = records[index] if index is not None else None
            if before != change.before:
                raise ValueError(
                    "A proposed record has changed. Inspect the current version before applying it."
                )
            if (
                change.after is not None
                and change.after.get(identifier) != change.record_id
            ):
                raise ValueError("Replacement must retain its record identifier")
            if change.before == change.after:
                raise ValueError("Proposal must change a record")
            if index is not None:
                if change.after is None:
                    records.pop(index)
                else:
                    records[index] = change.after
            elif change.after is not None:
                records.append(change.after)
        return StudyExtraction.model_validate(result).model_dump(mode="json")

    def load(self, split: str, paper: str, reviewer: str) -> dict:
        self.store.validate_identity(split, paper)
        current = self.store.storage.load_revision(split, paper)
        truth, events = current.ground_truth, current.events
        summary = self.store.summary(truth, events)
        catalog = _record_catalog(truth)
        records = _records(truth)
        own = summary["record_decisions"].get(reviewer, {})
        inherited, record_cases = {}, []
        for key, record in records.items():
            decisions = {
                actor: values[key]
                for actor, values in summary["record_decisions"].items()
                if key in values
            }
            if own.get(key) == "verified":
                continue
            if decisions and set(decisions.values()) == {"verified"}:
                inherited[key] = decisions
                continue
            record_cases.append(
                {
                    "id": f"record:{key}",
                    "title": record.get("label")
                    or record.get("measurement_type")
                    or key.split(":")[0].replace("_", " "),
                    "reason": "Reviewers disagree or left this record unresolved."
                    if decisions
                    else "No approval matches the current record. Historical Excel feedback may still apply.",
                    "record_key": key,
                    "record": record,
                    "reviewer_decisions": decisions,
                    "changes": [],
                    "evidence": record.get("evidence", []),
                    "keys": [key],
                }
            )
        cases = []
        for event in events:
            if event["kind"] == "adjudication_plan":
                for proposal in event["details"]["proposals"]:
                    current_changes = [
                        {
                            **change,
                            "current": records.get(
                                f"{change['collection']}:{change['record_id']}"
                            ),
                        }
                        for change in proposal["changes"]
                    ]
                    cases.append(
                        {
                            **proposal,
                            "current_changes": current_changes,
                            "stale": any(
                                c["before"] != c["current"] for c in current_changes
                            ),
                            "id": f"proposal:{proposal['id']}",
                            "keys": [
                                f"{c['collection']}:{c['record_id']}"
                                for c in proposal["changes"]
                            ],
                        }
                    )
        # Keep the latest saved completeness note per reviewer; never convert counts into edits.
        for actor, audit in summary["inventory_audits"].items():
            note = audit.get("missing_or_ambiguous", "").strip()
            mismatches = {
                kind: {"reviewer_count": count, "current_records": len(truth[kind])}
                for kind, count in audit.get("expected_counts", {}).items()
                if kind in RECORD_IDENTIFIERS and len(truth[kind]) != count
            }
            if note or mismatches:
                content = {"reviewer": actor, "note": note, "counts": mismatches}
                cases.append(
                    {
                        "id": f"completeness:{_digest(content)}",
                        "title": "Resolve the reviewer's completeness check",
                        "reason": note
                        or "The reviewer's count differs from the saved records. Check the reason; do not force the counts to match.",
                        "counts": mismatches,
                        "reviewer": actor,
                        "changes": [],
                        "evidence": [],
                        "keys": [],
                    }
                )
        undone = {e["details"].get("undoes_event_id") for e in events}
        resolutions = {
            e["details"].get("case_id"): e
            for e in events
            if e["kind"] == "adjudication_decision"
            and e["reviewer_id"] == reviewer
            and e["event_id"] not in undone
        }
        pending = []
        for case in cases:
            resolution = resolutions.get(case["id"])
            if resolution and resolution["details"].get("binding") == _binding(
                truth, case["keys"]
            ):
                continue
            case["binding"] = _binding(truth, case["keys"])
            pending.append(case)
        last = events[-1]
        return {
            "paper_id": paper,
            "split": split,
            "revision": current.revision,
            "study_sha256": _digest(truth),
            "title": truth["paper"].get("title") or paper,
            "source_notes": truth.get("unresolved_notes", []),
            "record_count": len(catalog),
            "inherited_count": len(inherited),
            "own_approved_count": sum(own.get(k) == "verified" for k in catalog),
            "inherited": inherited,
            "cases": [c for c in pending if c["changes"]]
            + record_cases
            + [c for c in pending if not c["changes"]],
            "finalized": last["kind"] == "stage_complete"
            and last["details"].get("stage") == "adjudication",
            "last_decision": next(
                (
                    e
                    for e in reversed(events)
                    if e["reviewer_id"] == reviewer
                    and e["kind"] == "adjudication_decision"
                    and e["event_id"] not in undone
                    and not e["details"].get("undoes_event_id")
                ),
                None,
            ),
        }

    def decide(
        self, split: str, paper: str, request: DecisionRequest, reviewer: str
    ) -> dict:
        current = self.store._validate_revision(split, paper, request.base_revision)
        case = next(
            (
                c
                for c in self.load(split, paper, reviewer)["cases"]
                if c["id"] == request.case_id
            ),
            None,
        )
        if case is None:
            raise ValueError("This item is no longer pending; refresh the queue")
        before = current.ground_truth
        truth = before
        if request.action == "accept" and case["changes"]:
            if case.get("stale"):
                raise ValueError(
                    "These records changed after the suggestion was prepared. Inspect the current version instead."
                )
            truth = self._apply(
                before, [RecordChange.model_validate(c) for c in case["changes"]]
            )
            self.store._validate_citations(
                split, paper, [Citation.model_validate(e) for e in case["evidence"]]
            )
            self.store._reject_new_grounding_issues(split, paper, before, truth)
        catalog = _record_catalog(truth)
        decisions = [
            {"record_key": key, "record_digest": catalog[key], "decision": "verified"}
            for key in case["keys"]
            if key in catalog
        ]
        return self._commit(
            split,
            paper,
            current,
            reviewer,
            "adjudication_decision",
            {
                "case_id": case["id"],
                "action": request.action,
                "case": case,
                "binding": _binding(truth, case["keys"]),
                "decisions": decisions,
                "collection_replacements": _changed_collections(before, truth),
            },
            truth=truth,
            note=request.note,
            evidence=[Citation.model_validate(e) for e in case["evidence"]],
        )

    def undo(
        self, split: str, paper: str, base_revision: int, event_id: str, reviewer: str
    ) -> dict:
        current = self.store._validate_revision(split, paper, base_revision)
        event = next((e for e in current.events if e["event_id"] == event_id), None)
        if (
            not event
            or event["kind"] != "adjudication_decision"
            or event["reviewer_id"] != reviewer
        ):
            raise ValueError("Choose one of your saved admin decisions")
        if any(e["details"].get("undoes_event_id") == event_id for e in current.events):
            raise ValueError("This decision has already been undone")
        truth = copy.deepcopy(current.ground_truth)
        for replacement in event["details"]["collection_replacements"]:
            if truth[replacement["collection"]] != replacement["after"]:
                raise ValueError(
                    "Later edits changed these records; inspect them before undoing"
                )
            truth[replacement["collection"]] = replacement["before"]
        StudyExtraction.model_validate(truth)
        return self._commit(
            split,
            paper,
            current,
            reviewer,
            "adjudication_decision",
            {
                "undoes_event_id": event_id,
                "decisions": [],
                "collection_replacements": [],
            },
            truth=truth,
            note="Undid an admin decision; original history preserved.",
        )

    def finalize(
        self, split: str, paper: str, request: FinalizeRequest, reviewer: str
    ) -> dict:
        current = self.store._validate_revision(split, paper, request.base_revision)
        queue = self.load(split, paper, reviewer)
        if queue["cases"]:
            raise ValueError(
                f"Resolve {len(queue['cases'])} remaining items before finalizing"
            )
        document = self.store.storage.load_evidence(
            split, paper, current.evidence_version
        )
        validation = validate_study(
            StudyExtraction.model_validate(current.ground_truth),
            _evidence_blocks(document),
        )
        if validation["status"] != "verified":
            raise ValueError(
                f"Resolve source validation issues before freezing: {validation.get('issues', [])[:5]}"
            )
        decisions = [
            {"record_key": key, "record_digest": digest, "decision": "verified"}
            for key, digest in _record_catalog(current.ground_truth).items()
        ]
        return self._commit(
            split,
            paper,
            current,
            reviewer,
            "stage_complete",
            {
                "stage": "adjudication",
                "method": "admin_queue",
                "decisions": decisions,
                "adopted_reviews": queue["inherited"],
                "completeness_checked": request.completeness_checked,
            },
            note=request.note,
        )
