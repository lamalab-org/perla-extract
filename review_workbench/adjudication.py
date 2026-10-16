"""Resolve a short admin queue, then adopt current reviews into a frozen reference.

All proposals and decisions live in the existing immutable paper revision history.
No separate database or browser-only approval state is needed. Reviewer approval is
adopted explicitly, never inferred from a census count or an old record identifier.
"""

from __future__ import annotations

import copy
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.models import StudyExtraction
from perla_extract.study_extraction.validation import validate_study
from review_workbench.completed_review import (
    AdoptReviewRequest,
    review_candidate,
    scope_changes_since_review,
    unchanged_since_review,
)
from review_workbench.ground_truth_export import _evidence_blocks
from review_workbench.review_storage import ReviewRevision
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
    review_lane: Literal["scientific", "reconciliation"] = "scientific"
    changes: list[RecordChange] = Field(default_factory=list, max_length=100)
    evidence: list[Citation] = Field(default_factory=list, max_length=30)
    feedback: list["WorkbookFeedback"] = Field(default_factory=list, max_length=500)


class WorkbookFeedback(BaseModel):
    """Preserve a workbook comment and the fields its reviewer actually saw.

    A link to a replacement record is an admin-prepared correspondence, not an
    inherited approval. The old values remain inspectable even after that record
    is edited or removed. Workbook hashes identify the preserved original file.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=200)
    workbook_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    filename: str = Field(min_length=1, max_length=500)
    sheet: str = Field(min_length=1, max_length=100)
    cell: str = Field(min_length=1, max_length=30)
    text: str = Field(min_length=1, max_length=12000)
    old_record_key: str = Field(min_length=1, max_length=400)
    review_outcome: str = Field(max_length=200)
    reviewed_fields: dict[str, Any]
    current_record_keys: list[str] = Field(default_factory=list, max_length=100)


class PlanRequest(BaseModel):
    """Import suggestions only when the saved study is still the one inspected."""

    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=1)
    study_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    proposals: list[Proposal] = Field(min_length=1, max_length=100)
    supersedes: list[str] = Field(default_factory=list, max_length=100)
    feedback_counts: dict[str, int] = Field(default_factory=dict)


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
    completeness_checked: Literal[True] | None = None
    adopt_completed_review: bool = False
    note: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def require_completeness_basis(self):
        if not self.completeness_checked and not self.adopt_completed_review:
            raise ValueError("Check completeness or adopt a completed expert review")
        return self


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
    """Bind a resolution to specific records, or the whole paper when no keys exist."""

    records = _records(truth)
    return _digest({key: records.get(key) for key in keys} if keys else truth)


def _case_binding(truth: dict, case: dict) -> str:
    """Reopen a comment when its subject changes, not for unrelated new records.

    Binding scope is deliberately separate from approval scope (``keys``): resolving
    a comment must not approve every field in the records that comment mentions.
    Unmapped feedback still binds the whole paper because it may describe omissions.
    """
    return _binding(truth, case.get("binding_keys", case["keys"]))


def _trusted_corrections(events: list[dict], records: dict, trusted: set[str]) -> dict:
    """Accept explicit record replacements, never infer approval from a scalar edit.

    The full-record editor and validated workbook imports retain the exact reviewed
    replacement. Trust follows the authenticated author and that content, not a
    filename, display name, old row position, or model agreement. Comments remain
    separate questions and later explicit decisions take precedence.
    """

    accepted = {}
    for event in events:
        actor = event["reviewer_id"]
        if actor not in trusted or event["details"].get("undoes_event_id"):
            continue
        replacements = []
        replacement_keys = set()
        if event["kind"] == "mutation" and event.get("action") == "replace":
            parts = (event.get("path") or "").strip("/").split("/")
            if len(parts) == 2 and parts[0] in RECORD_IDENTIFIERS:
                after = event.get("after")
                if isinstance(after, dict):
                    replacements = [{"collection": parts[0], "after": after}]
        elif (
            event["kind"] == "spreadsheet_review"
            and event["details"].get("workbook_import_mode")
            == "validated_current_workbook"
        ):
            replacements = event["details"].get("record_replacements", [])
        for replacement in replacements:
            collection, after = replacement["collection"], replacement["after"]
            if not isinstance(after, dict):
                continue
            key = f"{collection}:{after.get(RECORD_IDENTIFIERS[collection])}"
            if any(
                not comment.get("record_id")
                or (
                    comment.get("record_collection") == collection
                    and comment["record_id"]
                    == after.get(RECORD_IDENTIFIERS[collection])
                )
                for comment in event["details"].get("cell_comments", [])
            ):
                continue
            if after == records.get(key):
                replacement_keys.add(key)
                accepted.setdefault(key, {})[actor] = {
                    "event_id": event["event_id"],
                    "revision": event["revision"],
                }
        decisions = (
            [event["details"]]
            if event["kind"] == "record_decision"
            else event["details"].get("decisions", [])
        )
        for decision in decisions:
            # 'Correct fields' accompanies a replacement in Excel, but on its own
            # is only a request for correction, not a replacement or an approval.
            if (
                decision.get("decision") in {"verified", "uncertain"}
                or decision["record_key"] not in replacement_keys
            ):
                accepted.get(decision["record_key"], {}).pop(actor, None)
    return {key: authors for key, authors in accepted.items() if authors}


class AdjudicationQueue:
    def __init__(self, store: StudyReviewStore):
        self.store = store

    def adopt_review(
        self, split: str, paper: str, request: AdoptReviewRequest, reviewer: str
    ) -> dict:
        """Record the coordinator's confirmation, accepting unchanged expert-reviewed data.

        This is a declared review scope, not a fabricated action by the expert.
        Conflicting or unresolved decisions stay pending. Final export still requires
        source validation and resolution of the remaining concrete exceptions.
        """
        current = self.store._validate_revision(split, paper, request.base_revision)
        trusted = {
            v.strip()
            for v in os.environ.get("REVIEW_TRUSTED_REVIEWER_IDS", "").split(",")
            if v.strip()
        }
        if request.expert_id not in trusted:
            raise ValueError("Choose a configured expert reviewer")
        candidate = review_candidate(current, request.expert_id)
        if (
            not candidate
            or candidate["reviewed_through_revision"]
            != request.reviewed_through_revision
        ):
            raise ValueError("The expert's review changed; reload before adopting it")
        if any(
            e["kind"] == "ground_truth_refresh"
            and e["revision"] > request.reviewed_through_revision
            for e in current.events
        ):
            raise ValueError(
                "The extraction or evidence was replaced after this expert's review; reconcile that version first"
            )
        view = self.load(split, paper, reviewer, current=current)
        if any(
            c.get("expert_id") == request.expert_id
            and c.get("reviewed_through_revision") == request.reviewed_through_revision
            for c in view["adopted_paper_reviews"]
        ):
            raise ValueError("This completed review has already been adopted")
        catalog = unchanged_since_review(current, request.reviewed_through_revision)
        # Trust does not silently settle an explicit objection from any reviewer.
        objections = {
            c["record_key"]
            for c in view["cases"]
            if c.get("record_key")
            and any(
                value != "verified"
                for value in c.get("reviewer_decisions", {}).values()
            )
        }
        decisions = [
            {"record_key": key, "record_digest": digest, "decision": "verified"}
            for key, digest in catalog.items()
            if key not in objections
        ]
        document = self.store.storage.load_evidence(
            split, paper, current.evidence_version
        )
        scope = sorted({b.source for b in _evidence_blocks(document)})
        return self._commit(
            split,
            paper,
            current,
            reviewer,
            "adjudication_decision",
            {
                "action": "adopt_completed_expert_review",
                "completed_paper_review": {
                    **candidate,
                    "evidence_version": current.evidence_version,
                    "review_scope_sources": scope,
                    "basis": "coordinator_confirmed_external_completion",
                    "confirmed_by": reviewer,
                    "study_sha256_at_adoption": _digest(current.ground_truth),
                },
                "decisions": decisions,
                "collection_replacements": [],
            },
            note=request.note,
        )

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
        feedback = [entry for p in request.proposals for entry in p.feedback]
        if len({entry.id for entry in feedback}) != len(feedback):
            raise ValueError("Each workbook comment must occur exactly once in a plan")
        counts: dict[str, int] = {}
        records = _records(current.ground_truth)
        for entry in feedback:
            counts[entry.workbook_sha256] = counts.get(entry.workbook_sha256, 0) + 1
            if len(set(entry.current_record_keys)) != len(
                entry.current_record_keys
            ) or not set(entry.current_record_keys).issubset(records):
                raise ValueError(
                    "Workbook feedback must link to distinct current records"
                )
        if counts != request.feedback_counts:
            raise ValueError(
                "Workbook comment counts do not match the supplied inventory"
            )
        superseded = set(request.supersedes) | {
            identifier
            for event in current.events
            if event["kind"] == "adjudication_plan"
            for identifier in event["details"].get("supersedes", [])
        }
        active_feedback = {
            entry["id"]
            for event in current.events
            if event["kind"] == "adjudication_plan"
            for proposal in event["details"]["proposals"]
            if proposal["id"] not in superseded
            for entry in proposal.get("feedback", [])
        }
        if active_feedback.intersection(entry.id for entry in feedback):
            raise ValueError(
                "Supersede the previous feedback group before replacing its correspondence"
            )
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
        if len(set(request.supersedes)) != len(request.supersedes) or not set(
            request.supersedes
        ).issubset(existing):
            raise ValueError("Superseded proposals must be distinct saved proposal IDs")
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
                "supersedes": request.supersedes,
                "feedback_counts": request.feedback_counts,
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

    def load(
        self,
        split: str,
        paper: str,
        reviewer: str,
        *,
        current: ReviewRevision | None = None,
    ) -> dict:
        self.store.validate_identity(split, paper)
        current = current or self.store.storage.load_revision(split, paper)
        truth, events = current.ground_truth, current.events
        summary = self.store.summary(truth, events)
        catalog = _record_catalog(truth)
        records = _records(truth)
        trusted = {
            value.strip()
            for value in os.environ.get("REVIEW_TRUSTED_REVIEWER_IDS", "").split(",")
            if value.strip()
        }
        own = summary["record_decisions"].get(reviewer, {})
        undone = {e["details"].get("undoes_event_id") for e in events}
        resets = {
            e["reviewer_id"]: e["revision"]
            for e in events
            if e["kind"] == "review_reset" and e["event_id"] not in undone
        }
        active_events = [
            e
            for e in events
            if e["event_id"] not in undone
            and e["revision"] > resets.get(e["reviewer_id"], 0)
        ]
        refreshed_at = max(
            (e["revision"] for e in events if e["kind"] == "ground_truth_refresh"),
            default=0,
        )
        # A later expert reset withdraws the adopted review as well. Keep its
        # immutable event in history but do not resurrect its implicit approvals.
        active_events = [
            e
            for e in active_events
            if not e["details"].get("completed_paper_review")
            or (
                resets.get(e["details"]["completed_paper_review"]["expert_id"], 0)
                < e["details"]["completed_paper_review"]["reviewed_through_revision"]
                and e["details"]["completed_paper_review"]["evidence_version"]
                == current.evidence_version
                and refreshed_at
                < e["details"]["completed_paper_review"]["reviewed_through_revision"]
                and e["details"]["completed_paper_review"]["review_event_id"]
                not in undone
            )
        ]
        adopted_paper_reviews = [
            {**e["details"]["completed_paper_review"], "event_id": e["event_id"]}
            for e in active_events
            if e["details"].get("completed_paper_review")
        ]
        corrections = _trusted_corrections(active_events, records, trusted)
        finalization_events = [
            e for e in active_events if e["kind"] == "adjudication_decision"
        ]
        # A later objection must reopen review even if another person approved
        # this exact content earlier. Resets withdraw approvals, not corrections.
        objections = {}
        for event in active_events:
            details = event["details"]
            decisions = (
                [details]
                if event["kind"] == "record_decision"
                else details.get("decisions", [])
            )
            for decision in decisions:
                key = decision["record_key"]
                if decision.get("decision") in {
                    "uncertain",
                    "needs_correction",
                } and decision.get("record_digest") == catalog.get(key):
                    objections[key] = event["revision"]
        # Finalizers share decisions, but an approval covers only the record that
        # was actually checked. Changed records and undone decisions reopen review.
        adjudicated = {}
        for event in finalization_events:
            for decision in event["details"].get("decisions", []):
                key = decision["record_key"]
                if (
                    decision.get("decision") == "verified"
                    and key in catalog
                    and decision.get("record_digest") == catalog[key]
                    and event["revision"] > objections.get(key, 0)
                ):
                    adjudicated[key] = {event["reviewer_id"]: "verified"}
        inherited, record_cases, adopted_corrections = {}, [], {}
        for key, record in records.items():
            decisions = {
                actor: values[key]
                for actor, values in summary["record_decisions"].items()
                if key in values
            }
            for actor, provenance in corrections.get(key, {}).items():
                # Other reviewers' objections still require an explicit resolution.
                if decisions.get(actor) in {None, "needs_correction"}:
                    decisions[actor] = "verified"
                    adopted_corrections.setdefault(key, {})[actor] = provenance
            if key in adjudicated:
                if reviewer not in adjudicated[key]:
                    inherited[key] = adjudicated[key]
                continue
            if decisions and set(decisions.values()) == {"verified"}:
                if own.get(key) != "verified":
                    inherited[key] = decisions
                continue
            record_cases.append(
                {
                    "id": f"record:{key}",
                    "title": record.get("label")
                    or record.get("specimen_label")
                    or (
                        f"{record.get('measurement_type', key.split(':')[0]).replace('_', ' ')}"
                        f" · {key.split(':', 1)[1]}"
                    ),
                    "reason": "Reviewers disagree or left this record unresolved."
                    if decisions
                    else "No approval matches the current record. Historical Excel feedback may still apply.",
                    "record_key": key,
                    "record": record,
                    "reviewer_decisions": decisions,
                    "review_lane": "scientific" if decisions else "unreviewed",
                    "changes": [],
                    "evidence": record.get("evidence", []),
                    "keys": [key],
                }
            )
        # A more specific plan may retire an earlier checklist, but never erase
        # its event or approve its records. The replacement still needs a decision.
        superseded = {
            identifier
            for event in events
            if event["kind"] == "adjudication_plan"
            for identifier in event["details"].get("supersedes", [])
        }
        cases = []
        # Removed records cannot be flagged in the current-record queue. Preserve
        # those later changes as explicit decisions rather than silently accepting them.
        scope_changes = {
            change["event_id"]: change
            for adopted in adopted_paper_reviews
            for change in scope_changes_since_review(
                current, adopted["reviewed_through_revision"]
            )
        }
        for change in scope_changes.values():
            cases.append(
                {
                    "id": f"post-review-change:{change['event_id']}",
                    "review_lane": "scientific",
                    "title": "Check a change made after the expert review",
                    "reason": "Paper metadata or records were removed after the reviewed version. "
                    "Confirm that this change is intended: "
                    + (
                        ", ".join(change["removed_record_keys"])
                        or "paper metadata changed"
                    )
                    + f" (revision {change['revision']}).",
                    "changes": [],
                    "evidence": [],
                    "keys": [],
                }
            )
        feedback_by_record: dict[str, list[str]] = {}
        feedback_inventory = []
        for event in events:
            if event["kind"] == "adjudication_plan":
                for proposal in event["details"]["proposals"]:
                    if proposal["id"] in superseded:
                        continue
                    entries = proposal.get("feedback", [])
                    for entry in entries:
                        feedback_inventory.append(
                            {**entry, "case_id": f"proposal:{proposal['id']}"}
                        )
                        for key in entry["current_record_keys"]:
                            feedback_by_record.setdefault(key, []).append(entry["id"])
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
                            "review_lane": proposal.get("review_lane", "scientific"),
                            "feedback": [entry["id"] for entry in entries],
                            "current_changes": current_changes,
                            "stale": any(
                                c["before"] != c["current"] for c in current_changes
                            ),
                            "id": f"proposal:{proposal['id']}",
                            "keys": [
                                f"{c['collection']}:{c['record_id']}"
                                for c in proposal["changes"]
                            ],
                            "binding_keys": sorted(
                                {
                                    f"{c['collection']}:{c['record_id']}"
                                    for c in proposal["changes"]
                                }
                                | {
                                    key
                                    for entry in entries
                                    for key in entry["current_record_keys"]
                                }
                            )
                            if all(entry["current_record_keys"] for entry in entries)
                            else [],
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
                        "review_lane": "scientific" if note else "reconciliation",
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
        resolutions = {e["details"].get("case_id"): e for e in finalization_events}
        accepted_keys = set(records) - {c["record_key"] for c in record_cases}
        adopted_proposals = []
        pending = []
        for case in cases:
            resolution = resolutions.get(case["id"])
            if resolution:
                saved = resolution["details"]
                old_case = saved.get("case", {})
                matches = saved.get("binding") == _case_binding(truth, case)
                # Existing decisions used the whole paper for comment-only cases.
                # Preserve those only when that original, broader check still passes.
                legacy_matches = "binding_keys" not in old_case and saved.get(
                    "binding"
                ) == _binding(truth, case["keys"])
                if matches or legacy_matches:
                    continue
            # A prepared replacement sometimes describes an edit already saved by
            # an expert. Exact content + explicit approval is sufficient; no LLM
            # similarity judgment and no second scientific review are needed.
            changes = case.get("current_changes", [])
            if (
                changes
                and not case.get("feedback")
                and all(
                    c["after"] is not None
                    and c["after"] == c["current"]
                    and f"{c['collection']}:{c['record_id']}" in accepted_keys
                    for c in changes
                )
            ):
                adopted_proposals.append(
                    {
                        "case_id": case["id"],
                        "binding": _binding(truth, case["keys"]),
                        "keys": case["keys"],
                    }
                )
                continue
            case["binding"] = _case_binding(truth, case)
            pending.append(case)
        completed_reviews = (
            self._completed_reviews(
                split, paper, current, active_events, summary, trusted
            )
            + adopted_paper_reviews
        )
        for case in record_cases:
            case["workbook_feedback"] = feedback_by_record.get(case["record_key"], [])
            if not case["reviewer_decisions"] and case["workbook_feedback"]:
                case["review_lane"] = "reconciliation"
        # Routing changes who needs to do the work, not what counts as approved.
        # Every unresolved item still blocks finalization, including hidden lanes.
        all_cases = (
            [c for c in pending if c["changes"]]
            + record_cases
            + [c for c in pending if not c["changes"]]
        )
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
            "own_approved_count": len(catalog) - len(inherited) - len(record_cases),
            "inherited": inherited,
            "adopted_corrections": adopted_corrections,
            "adopted_proposals": adopted_proposals,
            "completed_reviews": completed_reviews,
            "adopted_paper_reviews": adopted_paper_reviews,
            "review_candidates": [
                candidate
                for actor in sorted(trusted)
                if (candidate := review_candidate(current, actor))
            ],
            "workbook_feedback": feedback_inventory,
            "workbook_current_records": {
                key: records[key] for key in feedback_by_record if key in records
            },
            "cases": all_cases,
            "case_counts": {
                lane: sum(c["review_lane"] == lane for c in all_cases)
                for lane in ("scientific", "reconciliation", "unreviewed")
            },
            "finalized": last["kind"] == "stage_complete"
            and last["details"].get("stage") == "adjudication",
            "last_decision": next(
                (
                    e
                    for e in reversed(finalization_events)
                    if e["reviewer_id"] == reviewer
                    and e["kind"] == "adjudication_decision"
                    and e["event_id"] not in undone
                    and not e["details"].get("undoes_event_id")
                ),
                None,
            ),
        }

    def _completed_reviews(
        self, split, paper, current, active_events, summary, trusted
    ):
        """Offer source-wide expert checks only for the exact study and source scope.

        For older events without hashes, the append-only history must show no
        subsequent scientific edit or source refresh. Counts alone never count as
        a completeness review, and a main-paper check cannot cover an available SI.
        """

        candidates = [
            event
            for event in active_events
            if event["reviewer_id"] in trusted
            and event["kind"] == "stage_complete"
            and event["details"].get("stage") == "completeness"
        ]
        if not candidates:
            return []
        document = self.store.storage.load_evidence(
            split, paper, current.evidence_version
        )
        sources = {block.source for block in _evidence_blocks(document)}
        result = []
        for event in candidates:
            check = event["details"].get("reviewed_snapshot")
            audit = summary["inventory_audits"].get(event["reviewer_id"])
            if (
                not audit
                or audit.get("missing_or_ambiguous", "").strip()
                or not sources
                or not sources.issubset(audit.get("review_scope_sources", []))
            ):
                continue
            if check is None:
                # Examine even undone mutations: an edit followed by an undo does
                # not prove that the old source-wide check was repeated.
                later = [e for e in current.events if e["revision"] > event["revision"]]
                if any(
                    e["kind"]
                    in {"mutation", "structural_mutation", "ground_truth_refresh"}
                    or e["details"].get("record_replacements")
                    or e["details"].get("collection_replacements")
                    or (
                        e["kind"] == "inventory_audit"
                        and e["reviewer_id"] == event["reviewer_id"]
                    )
                    for e in later
                ):
                    continue
                check = {
                    "study_sha256": _digest(current.ground_truth),
                    "evidence_version": current.evidence_version,
                    "inventory_sha256": _digest(audit),
                    "review_scope_sources": audit["review_scope_sources"],
                    "binding_method": "unchanged_since_completion",
                }
            if (
                check.get("study_sha256") != _digest(current.ground_truth)
                or check.get("evidence_version") != current.evidence_version
                or check.get("inventory_sha256") != _digest(audit)
            ):
                continue
            if any(
                len(current.ground_truth[k]) != count
                for k, count in audit.get("expected_counts", {}).items()
                if k in RECORD_IDENTIFIERS
            ):
                continue
            result.append(
                {
                    "reviewer_id": event["reviewer_id"],
                    "event_id": event["event_id"],
                    "revision": event["revision"],
                    **check,
                }
            )
        return result

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
                "binding": _case_binding(truth, case),
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
            raise ValueError("Choose one of your saved finalization decisions")
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
            note="Undid a finalization decision; original history preserved.",
        )

    def finalize(
        self, split: str, paper: str, request: FinalizeRequest, reviewer: str
    ) -> dict:
        current = self.store._validate_revision(split, paper, request.base_revision)
        queue = self.load(split, paper, reviewer, current=current)
        if queue["cases"]:
            raise ValueError(
                f"Resolve {len(queue['cases'])} remaining items before finalizing"
            )
        if request.adopt_completed_review and not queue["completed_reviews"]:
            raise ValueError(
                "No completed expert review matches this study and source scope"
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
                "adopted_corrections": queue["adopted_corrections"],
                "adopted_proposals": queue["adopted_proposals"],
                "adopted_completeness": queue["completed_reviews"]
                if request.adopt_completed_review
                else [],
                "completeness_checked": bool(request.completeness_checked),
            },
            note=request.note,
        )
