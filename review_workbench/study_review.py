"""Versioned human review of rich, evidence-backed study extractions.

The model output is an immutable seed. Human edits are guarded JSON-pointer operations
that atomically commit a validated ``StudyExtraction`` with its audit event. Immutable
revision snapshots make the benchmark reproducible while derived flat exports remain
pleasant to inspect and consume.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.artifacts import write_json_atomic
from perla_extract.study_extraction.evidence import source_contains_text
from perla_extract.study_extraction.models import (
    STUDY_SCHEMA_VERSION,
    StudyExtraction,
    study_schema_sha256,
)
from review_workbench.review_storage import (
    LocalReviewStateStorage,
    ReviewPaperSource,
    ReviewRevision,
    ReviewStateStorage,
    StaleRevisionError,
)
from review_workbench.spreadsheet_review import (
    create_review_workbook,
    read_review_workbook,
    read_review_workbook_comments,
)

PAPER_ID = re.compile(r"^[A-Za-z0-9.-]+--[A-Za-z0-9._-]+$")
Split = Literal["calibration", "dev", "test"]
MutationAction = Literal["add", "replace", "remove"]
ReviewStage = Literal["inventory", "fields", "completeness", "adjudication"]
RecordCollection = Literal[
    "device_families",
    "individual_devices",
    "performance_observations",
    "population_statistics",
    "stability_tests",
]
RecordDecision = Literal["verified", "uncertain", "needs_correction"]

RECORD_IDENTIFIERS: dict[str, str] = {
    "device_families": "family_id",
    "individual_devices": "device_id",
    "performance_observations": "observation_id",
    "population_statistics": "population_id",
    "stability_tests": "test_id",
}
RECORD_LABELS: dict[str, str] = {
    "device_families": "Device family",
    "individual_devices": "Individual device",
    "performance_observations": "Performance observation",
    "population_statistics": "Population statistic",
    "stability_tests": "Stability test",
}


class Citation(BaseModel):
    """Identify the supplied passage used to justify one human decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    block_id: str = Field(min_length=1, max_length=200)
    quote: str = Field(min_length=1, max_length=1600)


class MutationRequest(BaseModel):
    """Describe one guarded edit against a known ground-truth revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    action: MutationAction
    path: str = Field(pattern=r"^/(?:[^/]|~[01])+(?:/(?:[^/]|~[01])+)*$")
    value: Any = None
    evidence: list[Citation] = Field(default_factory=list, max_length=20)
    note: str = Field(default="", max_length=2000)
    base_revision: int = Field(ge=0)

    @model_validator(mode="after")
    def require_support(self) -> "MutationRequest":
        """Require positive evidence for additions and corrections."""

        if self.action in {"add", "replace"} and not self.evidence:
            raise ValueError("add and replace operations require evidence")
        if self.action == "remove" and not self.note.strip():
            raise ValueError("remove operations require a counterevidence note")
        return self


class UndoMutationRequest(BaseModel):
    """Identify one saved correction to reverse against the latest paper state."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str = Field(min_length=1, max_length=200)
    base_revision: int = Field(ge=0)


class MainTextFigureCensus(BaseModel):
    """Measure schema content lost when main-text figures are not extracted.

    Counts stay aggregate because reviewers should identify missing structured facts,
    not digitize plot traces or create a second annotation interface for figures.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    figures_reviewed: int = Field(default=0, ge=0)
    schema_relevant_figures: int = Field(default=0, ge=0)
    figure_only_records: int = Field(default=0, ge=0)
    figure_only_atomic_values: int = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_figure_counts(self) -> "MainTextFigureCensus":
        """Keep the denominator and claimed figure-only contribution coherent."""

        if self.schema_relevant_figures > self.figures_reviewed:
            raise ValueError("schema-relevant figures cannot exceed figures reviewed")
        if (
            self.figure_only_records or self.figure_only_atomic_values
        ) and not self.schema_relevant_figures:
            raise ValueError(
                "figure-only records or values require a schema-relevant figure"
            )
        return self


class InventoryAuditRequest(BaseModel):
    """Capture corrected record totals and the main-text figure extraction gap."""

    model_config = ConfigDict(extra="forbid", strict=True)

    base_revision: int = Field(ge=0)
    review_scope_sources: list[Literal["main", "supplement"]] = Field(
        min_length=1,
        validation_alias=AliasChoices("review_scope_sources", "searched_sources"),
    )
    expected_counts: dict[str, int]
    main_text_figure_census: MainTextFigureCensus = Field(
        default_factory=MainTextFigureCensus
    )
    missing_or_ambiguous: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def validate_counts(self) -> "InventoryAuditRequest":
        """Keep the census generic but reject nonsensical negative counts."""

        if any(
            not isinstance(value, int) or value < 0
            for value in self.expected_counts.values()
        ):
            raise ValueError("inventory counts must be non-negative integers")
        return self


class StageRequest(BaseModel):
    """Record that a reviewer completed one explicit quality gate."""

    model_config = ConfigDict(extra="forbid", strict=True)

    stage: ReviewStage
    base_revision: int = Field(ge=0)
    note: str = Field(default="", max_length=4000)


class RecordDecisionRequest(BaseModel):
    """Record a review outcome for one complete, stable-ID study record."""

    model_config = ConfigDict(extra="forbid", strict=True)

    collection: RecordCollection
    record_id: str = Field(min_length=1, max_length=200)
    decision: RecordDecision
    base_revision: int = Field(ge=0)
    note: str = Field(default="", max_length=2000)


class ReviewerResetRequest(BaseModel):
    """Clear one reviewer's current decisions and progress for a paper."""

    model_config = ConfigDict(extra="forbid", strict=True)

    base_revision: int = Field(ge=0)


class ReviewEvent(BaseModel):
    """Append-only audit record for a mutation, census, or stage decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str
    revision: int = Field(ge=1)
    timestamp: str
    reviewer_id: str
    kind: Literal[
        "mutation",
        "spreadsheet_review",
        "inventory_audit",
        "record_decision",
        "stage_complete",
        "review_reset",
        "ground_truth_refresh",
        "seed_imported",
    ]
    action: MutationAction | None = None
    path: str | None = None
    before: Any = None
    after: Any = None
    evidence: list[Citation] = Field(default_factory=list)
    note: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


class ReviewerPaperProgress(BaseModel):
    """Expose one reviewer's saved work without presenting it as final truth."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paper_id: str
    current_revision: int = Field(ge=1)
    last_saved_at: str
    event_counts: dict[str, int]
    completed_stages: list[ReviewStage]
    current_inventory_audit: dict[str, Any] | None = None
    current_record_decisions: dict[str, RecordDecision] = Field(default_factory=dict)
    current_event_ids: list[str] = Field(default_factory=list)
    resettable_review_count: int = Field(ge=0)
    undoable_event_ids: list[str] = Field(default_factory=list)
    undone_event_ids: list[str] = Field(default_factory=list)
    events: list[ReviewEvent]


class ReviewerProgress(BaseModel):
    """Bundle the authenticated reviewer's persisted annotations for one split."""

    model_config = ConfigDict(extra="forbid", strict=True)

    reviewer_id: str
    split: Split
    paper_count: int = Field(ge=0)
    annotation_count: int = Field(ge=0)
    resettable_review_count: int = Field(ge=0)
    papers: list[ReviewerPaperProgress]


def _decode_pointer(path: str) -> list[str]:
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _parent(document: Any, path: str) -> tuple[Any, str]:
    parts = _decode_pointer(path)
    node = document
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node, parts[-1]


def _apply(
    document: dict[str, Any], request: MutationRequest
) -> tuple[Any, dict[str, Any]]:
    """Apply one RFC-6901-addressed operation and return its previous value."""

    result = copy.deepcopy(document)
    parent, key = _parent(result, request.path)
    if isinstance(parent, list):
        if request.action == "add":
            index = len(parent) if key == "-" else int(key)
            parent.insert(index, request.value)
            return None, result
        index = int(key)
        before = copy.deepcopy(parent[index])
        if request.action == "replace":
            parent[index] = request.value
        else:
            parent.pop(index)
        return before, result
    if request.action == "add":
        if key in parent:
            raise ValueError(f"{request.path} already exists")
        parent[key] = request.value
        return None, result
    if key not in parent:
        raise ValueError(f"{request.path} does not exist")
    before = copy.deepcopy(parent[key])
    if request.action == "replace":
        parent[key] = request.value
    else:
        del parent[key]
    return before, result


def _reverse_mutation(
    document: dict[str, Any], event: ReviewEvent
) -> tuple[MutationAction, str, Any, Any, dict[str, Any]]:
    """Reverse one mutation only while its saved result is still untouched.

    Matching the current value to the event's ``after`` value prevents an undo from
    overwriting later corrections. List additions recorded with ``/-`` are located by
    their complete saved value because their numeric position was not known earlier.
    """

    if event.kind != "mutation" or not event.action or not event.path:
        raise ValueError("only saved corrections can be undone")
    if event.details.get("undoes_event_id"):
        raise ValueError("an undo event cannot itself be undone")

    result = copy.deepcopy(document)
    parent, key = _parent(result, event.path)
    if event.action == "add":
        if isinstance(parent, list):
            if key == "-":
                matches = [
                    index for index, value in enumerate(parent) if value == event.after
                ]
                if len(matches) != 1:
                    raise ValueError("the added value has since changed")
                index = matches[0]
                resolved_path = f"{event.path[:-1]}{index}"
            else:
                index = int(key)
                if index >= len(parent) or parent[index] != event.after:
                    raise ValueError("the added value has since changed")
                resolved_path = event.path
            current = copy.deepcopy(parent[index])
            parent.pop(index)
        else:
            if key not in parent or parent[key] != event.after:
                raise ValueError("the added value has since changed")
            current = copy.deepcopy(parent[key])
            del parent[key]
            resolved_path = event.path
        return "remove", resolved_path, current, None, result

    if event.action == "replace":
        if isinstance(parent, list):
            index = int(key)
            if index >= len(parent) or parent[index] != event.after:
                raise ValueError("the corrected value has since changed")
            current = copy.deepcopy(parent[index])
            parent[index] = copy.deepcopy(event.before)
        else:
            if key not in parent or parent[key] != event.after:
                raise ValueError("the corrected value has since changed")
            current = copy.deepcopy(parent[key])
            parent[key] = copy.deepcopy(event.before)
        return "replace", event.path, current, event.before, result

    if isinstance(parent, list):
        index = int(key)
        if index > len(parent) or event.before in parent:
            raise ValueError("the removed value cannot be restored safely")
        parent.insert(index, copy.deepcopy(event.before))
    else:
        if key in parent:
            raise ValueError("the removed value cannot be restored safely")
        parent[key] = copy.deepcopy(event.before)
    return "add", event.path, None, event.before, result


def _digest(value: object) -> str:
    """Create a stable content identity so edits invalidate old review decisions."""

    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def _record_catalog(truth: dict[str, Any]) -> dict[str, str]:
    """Map stable schema record keys to the digest of their current content."""

    catalog: dict[str, str] = {}
    for collection, identifier_field in RECORD_IDENTIFIERS.items():
        for record in truth.get(collection, []):
            record_id = str(record[identifier_field])
            catalog[f"{collection}:{record_id}"] = _digest(record)
    return catalog


def _record(
    truth: dict[str, Any], collection: str, record_id: str
) -> dict[str, Any]:
    """Resolve a stable top-level record identity without relying on list position."""

    identifier = RECORD_IDENTIFIERS[collection]
    match = next(
        (item for item in truth[collection] if str(item[identifier]) == record_id),
        None,
    )
    if match is None:
        raise ValueError(f"unknown {collection} record {record_id}")
    return match


def _reverse_spreadsheet_review(
    truth: dict[str, Any], event: ReviewEvent
) -> dict[str, Any]:
    """Reverse a workbook import only when every corrected record is untouched."""

    if event.kind != "spreadsheet_review":
        raise ValueError("only a spreadsheet review can be reversed here")
    if event.details.get("undoes_event_id"):
        raise ValueError("an undo event cannot itself be undone")
    result = copy.deepcopy(truth)
    for replacement in event.details.get("record_replacements", []):
        collection = str(replacement["collection"])
        record_id = str(replacement["record_id"])
        current = _record(result, collection, record_id)
        if current != replacement["after"]:
            raise ValueError(
                f"{collection}:{record_id} has changed since the workbook import"
            )
        current.clear()
        current.update(copy.deepcopy(replacement["before"]))
    return StudyExtraction.model_validate(result).model_dump(mode="json")


def _contains_record_reference(
    value: object,
    reference_field: str,
    target_id: str,
    *,
    root_identifier: str | None = None,
    at_root: bool = True,
) -> bool:
    """Find explicit identifier references without interpreting scientific fields.

    Reference fields use the same names as the target collection identifiers. The
    root record's own identifier is excluded, while identity-link candidate lists are
    also recognized. This keeps deletion protection derived from the schema's IDs
    instead of a hand-maintained graph of record types.
    """

    if isinstance(value, dict):
        for field, child in value.items():
            if at_root and field == root_identifier:
                continue
            if field == reference_field and child is not None and str(child) == target_id:
                return True
            if field == "candidate_ids" and isinstance(child, list):
                if any(str(candidate) == target_id for candidate in child):
                    return True
            if _contains_record_reference(
                child,
                reference_field,
                target_id,
                root_identifier=root_identifier,
                at_root=False,
            ):
                return True
    elif isinstance(value, list):
        return any(
            _contains_record_reference(
                child,
                reference_field,
                target_id,
                root_identifier=root_identifier,
                at_root=False,
            )
            for child in value
        )
    return False


def _record_references(
    truth: dict[str, Any], collection: str, index: int
) -> list[str]:
    """List records that must be corrected before a referenced record is removed."""

    target = truth[collection][index]
    reference_field = RECORD_IDENTIFIERS[collection]
    target_id = str(target[reference_field])
    references: list[str] = []
    for candidate_collection, candidate_identifier in RECORD_IDENTIFIERS.items():
        for candidate_index, candidate in enumerate(truth[candidate_collection]):
            if candidate_collection == collection and candidate_index == index:
                continue
            if _contains_record_reference(
                candidate,
                reference_field,
                target_id,
                root_identifier=candidate_identifier,
            ):
                references.append(
                    f"{candidate_collection}:{candidate[candidate_identifier]}"
                )
    return references


def _record_reference_catalog(truth: dict[str, Any]) -> dict[str, list[str]]:
    """Expose deletion dependencies from the same check enforced on mutation."""

    references: dict[str, list[str]] = {}
    for collection, identifier in RECORD_IDENTIFIERS.items():
        for index, record in enumerate(truth[collection]):
            linked = _record_references(truth, collection, index)
            if linked:
                references[f"{collection}:{record[identifier]}"] = linked
    return references


def _removed_record_location(path: str) -> tuple[str, int] | None:
    """Recognize deletion of a complete top-level record collection item."""

    parts = _decode_pointer(path)
    if (
        len(parts) == 2
        and parts[0] in RECORD_IDENTIFIERS
        and parts[1].isdigit()
    ):
        return parts[0], int(parts[1])
    return None


class StudyReviewStore:
    """Keep model output, current truth, and human decisions independently auditable.

    The seed is immutable, mutations compile into a Pydantic-valid truth document, and
    every accepted action appends an event. Revision checks prevent stale browser tabs
    from overwriting newer work; record digests invalidate decisions after edits.
    """

    def __init__(self, root: Path, storage: ReviewStateStorage | None = None) -> None:
        self.root = root.resolve()
        self.storage = storage or LocalReviewStateStorage(self.root)
        self._citation_indexes: dict[tuple[str, str], dict[str, str]] = {}

    @staticmethod
    def validate_identity(split: str, paper_id: str) -> tuple[Split, str]:
        if split not in {"calibration", "dev", "test"}:
            raise ValueError("split must be calibration, dev, or test")
        if not PAPER_ID.fullmatch(paper_id):
            raise ValueError("invalid paper identifier")
        return split, paper_id  # type: ignore[return-value]

    def truth_path(self, split: str, paper_id: str) -> Path:
        self.validate_identity(split, paper_id)
        return self.root / split / f"{paper_id}.json"

    def seed_path(self, split: str, paper_id: str) -> Path:
        return self.root / "seeds" / split / f"{paper_id}.json"

    def events_path(self, split: str, paper_id: str) -> Path:
        return self.root / "events" / split / f"{paper_id}.json"

    def document_path(self, split: str, paper_id: str) -> Path:
        return self.root / "documents" / split / f"{paper_id}.json"

    def manifest_path(self, split: str, paper_id: str) -> Path:
        return self.root / "manifests" / split / f"{paper_id}.json"

    def events(self, split: str, paper_id: str) -> list[dict[str, Any]]:
        self.validate_identity(split, paper_id)
        return self.storage.load_revision(split, paper_id).events

    def revision(self, split: str, paper_id: str) -> int:
        self.validate_identity(split, paper_id)
        return self.storage.load_revision(split, paper_id).revision

    def load_truth(self, split: str, paper_id: str) -> dict[str, Any]:
        self.validate_identity(split, paper_id)
        return self.storage.load_revision(split, paper_id).ground_truth

    def load_document(self, split: str, paper_id: str) -> Any:
        """Return the immutable evidence document captured with the model seed."""

        self.validate_identity(split, paper_id)
        return self.storage.load_source(split, paper_id).document

    def _materialize(
        self,
        split: str,
        paper_id: str,
        source: ReviewPaperSource,
        revision: ReviewRevision,
    ) -> None:
        """Refresh familiar flat exports after the atomic state is committed.

        These files remain convenient for inspection and benchmark consumers, but
        immutable snapshots under ``state/`` are the authoritative review history.
        """

        write_json_atomic(self.seed_path(split, paper_id), source.seed_extraction)
        write_json_atomic(self.truth_path(split, paper_id), revision.ground_truth)
        write_json_atomic(self.events_path(split, paper_id), revision.events)
        if source.document is not None:
            write_json_atomic(self.document_path(split, paper_id), source.document)
        write_json_atomic(self.manifest_path(split, paper_id), source.manifest)

    def _commit(
        self,
        split: str,
        paper_id: str,
        current: ReviewRevision,
        truth: dict[str, Any],
        event: dict[str, Any],
    ) -> dict[str, Any]:
        """Atomically commit truth and audit event as the next paper revision."""

        revision = ReviewRevision(
            revision=current.revision + 1,
            ground_truth=truth,
            events=[*current.events, event],
        )
        self.storage.compare_and_swap(split, paper_id, current.revision, revision)
        self._materialize(
            split, paper_id, self.storage.load_source(split, paper_id), revision
        )
        return self.load_bundle(split, paper_id)

    def load_bundle(self, split: str, paper_id: str) -> dict[str, Any]:
        self.validate_identity(split, paper_id)
        source = self.storage.load_source(split, paper_id)
        revision = self.storage.load_revision(split, paper_id)
        self._materialize(split, paper_id, source, revision)
        current_schema_hash = study_schema_sha256()
        seed_schema_version = source.manifest.get("schema_version")
        seed_schema_hash = source.manifest.get("schema_sha256")
        try:
            StudyExtraction.model_validate(revision.ground_truth)
            readable_by_current_schema = True
        except ValueError:
            readable_by_current_schema = False
        return {
            "paper_id": paper_id,
            "split": split,
            "revision": revision.revision,
            "ground_truth": revision.ground_truth,
            "seed_extraction": source.seed_extraction,
            "events": revision.events,
            "manifest": source.manifest,
            "schema_compatibility": {
                "seed_schema_version": seed_schema_version,
                "current_schema_version": STUDY_SCHEMA_VERSION,
                "seed_schema_sha256": seed_schema_hash,
                "current_schema_sha256": current_schema_hash,
                "exact_match": (
                    seed_schema_version == STUDY_SCHEMA_VERSION
                    and seed_schema_hash == current_schema_hash
                ),
                "readable_by_current_schema": readable_by_current_schema,
            },
            "summary": self.summary(revision.ground_truth, revision.events),
        }

    @staticmethod
    def _prepare_mutation_undo(
        truth: dict[str, Any], event: ReviewEvent
    ) -> tuple[MutationAction, str, Any, Any, dict[str, Any]]:
        """Apply every undo safety rule used by both discovery and execution."""

        action, path, before, after, proposed = _reverse_mutation(truth, event)
        if action == "remove":
            location = _removed_record_location(path)
            if location is not None and _record_references(truth, *location):
                raise ValueError(
                    "this edit cannot be undone while newer records refer to its value"
                )
        validated = StudyExtraction.model_validate(proposed).model_dump(mode="json")
        return action, path, before, after, validated

    def reviewer_progress(self, split: str, reviewer_id: str) -> dict[str, Any]:
        """Return only the authenticated reviewer's persisted annotation events.

        The immutable event log is the authoritative account of what a person saved.
        Current decisions and stage state are included separately because later edits
        may legitimately invalidate an earlier record decision without erasing its
        history. Seed-import events are administrative setup, not annotations.
        """

        if split not in {"calibration", "dev", "test"}:
            raise ValueError("split must be calibration, dev, or test")
        if not reviewer_id:
            raise ValueError("reviewer_id is required")
        paper_ids = self.storage.list_paper_ids(split)
        with ThreadPoolExecutor(max_workers=min(8, len(paper_ids) or 1)) as executor:
            revisions = executor.map(
                lambda paper_id: self.storage.load_revision(split, paper_id),
                paper_ids,
            )
            papers = []
            for paper_id, revision in zip(paper_ids, revisions, strict=True):
                paper = self._reviewer_paper_progress(
                    paper_id, revision, reviewer_id
                )
                if paper is not None:
                    papers.append(paper)
        result = ReviewerProgress(
            reviewer_id=reviewer_id,
            split=split,
            paper_count=len(papers),
            annotation_count=sum(len(paper.events) for paper in papers),
            resettable_review_count=sum(
                paper.resettable_review_count for paper in papers
            ),
            papers=papers,
        )
        return result.model_dump(mode="json")

    def _reviewer_paper_progress(
        self, paper_id: str, revision: ReviewRevision, reviewer_id: str
    ) -> ReviewerPaperProgress | None:
        """Derive current markers and immutable history for one paper."""

        events = [
            ReviewEvent.model_validate(event)
            for event in revision.events
            if event.get("reviewer_id") == reviewer_id
            and event.get("kind") != "seed_imported"
        ]
        if not events:
            return None
        undone_event_ids = {
            str(event.get("details", {}).get("undoes_event_id"))
            for event in revision.events
            if event.get("details", {}).get("undoes_event_id")
        }
        undoable_event_ids: list[str] = []
        for event in events:
            if event.kind not in {
                "mutation",
                "spreadsheet_review",
            } or event.event_id in undone_event_ids:
                continue
            try:
                if event.kind == "mutation":
                    self._prepare_mutation_undo(revision.ground_truth, event)
                else:
                    _reverse_spreadsheet_review(revision.ground_truth, event)
            except (IndexError, KeyError, TypeError, ValueError):
                continue
            undoable_event_ids.append(event.event_id)
        summary = self.summary(revision.ground_truth, revision.events)
        completed_stages = [
            stage
            for stage in ("inventory", "fields", "completeness", "adjudication")
            if reviewer_id in summary["completed_stages"].get(stage, [])
        ]
        current_inventory_audit = summary["inventory_audits"].get(reviewer_id)
        current_record_decisions = summary["record_decisions"].get(reviewer_id, {})
        return ReviewerPaperProgress(
            paper_id=paper_id,
            current_revision=revision.revision,
            last_saved_at=events[-1].timestamp,
            event_counts=dict(Counter(event.kind for event in events)),
            completed_stages=completed_stages,
            current_inventory_audit=current_inventory_audit,
            current_record_decisions=current_record_decisions,
            current_event_ids=self._current_reviewer_event_ids(
                revision, reviewer_id
            ),
            resettable_review_count=(
                len(completed_stages)
                + len(current_record_decisions)
                + int(current_inventory_audit is not None)
            ),
            undoable_event_ids=undoable_event_ids,
            undone_event_ids=sorted(undone_event_ids),
            events=events,
        )

    @staticmethod
    def _current_reviewer_event_ids(
        revision: ReviewRevision, reviewer_id: str
    ) -> list[str]:
        """Identify the exact events behind current badges in the activity view."""

        catalog = _record_catalog(revision.ground_truth)
        current: dict[str, str] = {}
        parsed_events = [
            ReviewEvent.model_validate(event) for event in revision.events
        ]
        undone_event_ids = {
            str(event.details.get("undoes_event_id"))
            for event in parsed_events
            if event.details.get("undoes_event_id")
        }
        for event in parsed_events:
            if event.reviewer_id != reviewer_id or event.event_id in undone_event_ids:
                continue
            if event.kind == "review_reset":
                current.clear()
            elif event.kind == "record_decision":
                key = str(event.details.get("record_key", ""))
                if catalog.get(key) == event.details.get("record_digest"):
                    current[f"decision:{key}"] = event.event_id
            elif event.kind == "spreadsheet_review":
                for decision in event.details.get("decisions", []):
                    key = str(decision.get("record_key", ""))
                    if catalog.get(key) == decision.get("record_digest"):
                        current[f"decision:{key}"] = event.event_id
            elif event.kind == "inventory_audit":
                current["inventory"] = event.event_id
            elif event.kind == "stage_complete":
                stage = str(event.details.get("stage", ""))
                current[f"stage:{stage}"] = event.event_id
        current_ids = set(current.values())
        return [event.event_id for event in parsed_events if event.event_id in current_ids]

    @staticmethod
    def summary(truth: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
        """Derive current review state instead of trusting mutable status flags.

        Record decisions count only while their stored digest matches current content,
        which makes a scientific edit automatically reopen that record for review.
        """

        stages: dict[str, list[str]] = {}
        audits: dict[str, dict[str, Any]] = {}
        decisions: dict[str, dict[str, str]] = {}
        catalog = _record_catalog(truth)
        undone_event_ids = {
            str(event.get("details", {}).get("undoes_event_id"))
            for event in events
            if event.get("details", {}).get("undoes_event_id")
        }
        for event in events:
            if event.get("event_id") in undone_event_ids:
                continue
            if event["kind"] == "stage_complete":
                stages.setdefault(event["details"]["stage"], []).append(
                    event["reviewer_id"]
                )
            elif event["kind"] == "inventory_audit":
                details = copy.deepcopy(event["details"])
                if "review_scope_sources" not in details and "searched_sources" in details:
                    details["review_scope_sources"] = details.pop("searched_sources")
                audits[event["reviewer_id"]] = details
            elif event["kind"] == "record_decision":
                details = event["details"]
                record_key = str(details["record_key"])
                if catalog.get(record_key) == details.get("record_digest"):
                    decisions.setdefault(event["reviewer_id"], {})[record_key] = str(
                        details["decision"]
                    )
            elif event["kind"] == "spreadsheet_review":
                for details in event["details"].get("decisions", []):
                    record_key = str(details["record_key"])
                    if catalog.get(record_key) == details.get("record_digest"):
                        decisions.setdefault(event["reviewer_id"], {})[record_key] = str(
                            details["decision"]
                        )
            elif event["kind"] == "review_reset":
                reviewer_id = event["reviewer_id"]
                audits.pop(reviewer_id, None)
                decisions.pop(reviewer_id, None)
                for reviewers in stages.values():
                    reviewers[:] = [
                        candidate for candidate in reviewers if candidate != reviewer_id
                    ]
        return {
            "device_families": len(truth["device_families"]),
            "individual_devices": len(truth["individual_devices"]),
            "performance_observations": len(truth["performance_observations"]),
            "population_statistics": len(truth["population_statistics"]),
            "stability_tests": len(truth["stability_tests"]),
            "completed_stages": stages,
            "inventory_audits": audits,
            "record_decisions": decisions,
            "record_count": len(catalog),
            "record_identifiers": RECORD_IDENTIFIERS,
            "record_references": _record_reference_catalog(truth),
        }

    def import_seed(
        self,
        split: str,
        paper_id: str,
        extraction: object,
        *,
        document: object | None,
        manifest: dict[str, Any] | None,
        reviewer_id: str,
    ) -> dict[str, Any]:
        """Create one immutable source bundle and its first review revision.

        Import validates the complete rich schema before anything is persisted and
        refuses to replace an existing paper, keeping benchmark initialization an
        explicit one-time event.
        """

        self.validate_identity(split, paper_id)
        if isinstance(extraction, dict) and isinstance(
            extraction.get("extraction"), dict
        ):
            extraction = extraction["extraction"]
        truth = StudyExtraction.model_validate(extraction).model_dump(mode="json")
        now = datetime.now(timezone.utc).isoformat()
        seed_digest = hashlib.sha256(
            json.dumps(truth, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=1,
            timestamp=now,
            reviewer_id=reviewer_id,
            kind="seed_imported",
            details={"seed_sha256": seed_digest},
        ).model_dump(mode="json")
        revision = ReviewRevision(revision=1, ground_truth=truth, events=[event])
        source = ReviewPaperSource(
            seed_extraction=truth,
            document=document,
            manifest={
                **(manifest or {}),
                "schema": "StudyExtraction",
                "schema_version": STUDY_SCHEMA_VERSION,
                "schema_sha256": study_schema_sha256(),
                "seed_sha256": seed_digest,
                "evidence_document_sha256": (
                    _digest(document) if document is not None else None
                ),
                "imported_at": now,
            },
            initial_revision=revision,
        )
        self.storage.create(split, paper_id, source)
        self._materialize(split, paper_id, source, revision)
        return self.load_bundle(split, paper_id)

    def _validate_revision(
        self, split: str, paper_id: str, base_revision: int
    ) -> ReviewRevision:
        """Read the revision a transition is based on before its atomic commit."""

        self.validate_identity(split, paper_id)
        revision = self.storage.load_revision(split, paper_id)
        if base_revision != revision.revision:
            raise StaleRevisionError(
                f"stale revision {base_revision}; current revision is {revision.revision}"
            )
        return revision

    def _validate_citations(
        self, split: str, paper_id: str, citations: list[Citation]
    ) -> None:
        """Require human evidence quotes to occur in the imported source blocks."""

        if not citations:
            return
        document = self.load_document(split, paper_id)
        if document is None:
            raise ValueError("evidence citations require an imported document.json")
        key = (split, paper_id)
        index = self._citation_indexes.get(key)
        if index is None:
            blocks = (
                document.get("blocks", document)
                if isinstance(document, dict)
                else document
            )
            index = {
                str(block.get("block_id")): str(block.get("text", ""))
                for block in blocks
                if isinstance(block, dict)
            }
            self._citation_indexes[key] = index
        for citation in citations:
            source = index.get(citation.block_id)
            if source is None:
                raise ValueError(f"unknown evidence block {citation.block_id}")
            if not source_contains_text(source, citation.quote):
                raise ValueError(
                    f"quote is not present in evidence block {citation.block_id}"
                )

    def review_workbook(
        self,
        split: str,
        paper_id: str,
        reviewer_id: str,
        *,
        device_id: str | None = None,
    ) -> bytes:
        """Create a reviewer-specific XLSX bound to the latest paper revision."""

        self.validate_identity(split, paper_id)
        revision = self.storage.load_revision(split, paper_id)
        decisions = self.summary(revision.ground_truth, revision.events)[
            "record_decisions"
        ].get(reviewer_id, {})
        return create_review_workbook(
            truth=revision.ground_truth,
            identifiers=RECORD_IDENTIFIERS,
            labels=RECORD_LABELS,
            paper_id=paper_id,
            split=split,
            revision=revision.revision,
            schema_sha256=study_schema_sha256(),
            current_decisions=decisions,
            device_id=device_id,
        )

    def import_review_workbook(
        self,
        split: str,
        paper_id: str,
        data: bytes,
        reviewer_id: str,
        *,
        filename: str = "review.xlsx",
    ) -> dict[str, Any]:
        """Commit all validated workbook corrections and decisions as one revision.

        Parsing first reconstructs the expected workbook from the current truth, so
        edited identifiers, missing rows, and stale downloads never become mutations.
        The resulting study is Pydantic-validated only after every scalar correction
        has been applied, preventing partially imported workbooks.
        """

        self.validate_identity(split, paper_id)
        current = self.storage.load_revision(split, paper_id)
        safe_filename = Path(filename).name[:240] or "review.xlsx"
        try:
            review = read_review_workbook(
                data,
                truth=current.ground_truth,
                identifiers=RECORD_IDENTIFIERS,
                labels=RECORD_LABELS,
                paper_id=paper_id,
                split=split,
                revision=current.revision,
                schema_sha256=study_schema_sha256(),
            )
        except ValueError as error:
            if not any(
                phrase in str(error)
                for phrase in ("older paper revision", "older layout")
            ):
                raise
            base_revision, comments, workbook_sha256 = (
                read_review_workbook_comments(data, paper_id=paper_id, split=split)
            )
            if not comments:
                raise error
            current = self.storage.load_revision(split, paper_id)
            event = ReviewEvent(
                event_id=str(uuid.uuid4()),
                revision=current.revision + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reviewer_id=reviewer_id,
                kind="spreadsheet_review",
                note=f"Imported comments from older workbook {safe_filename}.",
                details={
                    "workbook_sha256": workbook_sha256,
                    "filename": safe_filename,
                    "workbook_import_mode": "comments_only_from_older_workbook",
                    "workbook_base_revision": base_revision,
                    "cell_comments": [
                        {
                            "sheet": item.sheet,
                            "cell": item.cell,
                            "kind": item.kind,
                            "text": item.text,
                            "author": item.author,
                            "record_collection": item.record_collection,
                            "record_id": item.record_id,
                            "schema_path": item.schema_path,
                        }
                        for item in comments
                    ],
                },
            ).model_dump(mode="json")
            return self._commit(
                split, paper_id, current, current.ground_truth, event
            )
        # Read again through the normal transition guard immediately before commit.
        current = self._validate_revision(split, paper_id, review.base_revision)
        proposed = copy.deepcopy(current.ground_truth)
        changed_keys: set[tuple[str, str]] = set()
        evidence: list[Citation] = []
        changed_paths: list[dict[str, str]] = []
        for change in review.changes:
            citations = [Citation.model_validate(item) for item in change.evidence]
            self._validate_citations(split, paper_id, citations)
            request = MutationRequest(
                action="replace",
                path=change.path,
                value=change.value,
                evidence=citations,
                note=change.note,
                base_revision=current.revision,
            )
            before, proposed = _apply(proposed, request)
            if before == change.value:
                continue
            changed_keys.add((change.collection, change.record_id))
            evidence.extend(citations)
            changed_paths.append(
                {"path": change.path, "note": change.note}
            )
        validated = StudyExtraction.model_validate(proposed).model_dump(mode="json")
        replacements = [
            {
                "collection": collection,
                "record_id": record_id,
                "before": copy.deepcopy(
                    _record(current.ground_truth, collection, record_id)
                ),
                "after": copy.deepcopy(_record(validated, collection, record_id)),
            }
            for collection, record_id in sorted(changed_keys)
        ]

        existing_decisions = self.summary(
            current.ground_truth, current.events
        )["record_decisions"].get(reviewer_id, {})
        decision_details = []
        for decision in review.decisions:
            record_key = f"{decision.collection}:{decision.record_id}"
            record = _record(validated, decision.collection, decision.record_id)
            digest = _digest(record)
            if (
                existing_decisions.get(record_key) == decision.decision
                and (decision.collection, decision.record_id) not in changed_keys
            ):
                continue
            decision_details.append(
                {
                    "record_key": record_key,
                    "record_digest": digest,
                    "decision": decision.decision,
                    "note": decision.note,
                }
            )
        if not replacements and not decision_details and not review.comments:
            raise ValueError(
                "review workbook contains no new decisions, corrections, or comments"
            )
        unique_evidence = list(
            {
                (item.block_id, item.quote): item
                for item in evidence
            }.values()
        )
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer_id,
            kind="spreadsheet_review",
            evidence=unique_evidence,
            note=f"Imported reviewed workbook {safe_filename}.",
            details={
                "workbook_sha256": review.sha256,
                "filename": safe_filename,
                "scope": {"device": review.scope_device_id},
                "workbook_import_mode": "validated_current_workbook",
                "record_replacements": replacements,
                "changed_fields": changed_paths,
                "decisions": decision_details,
                "cell_comments": [
                    {
                        "sheet": item.sheet,
                        "cell": item.cell,
                        "kind": item.kind,
                        "text": item.text,
                        "author": item.author,
                        "record_collection": item.record_collection,
                        "record_id": item.record_id,
                        "schema_path": item.schema_path,
                    }
                    for item in review.comments
                ],
            },
        ).model_dump(mode="json")
        return self._commit(split, paper_id, current, validated, event)

    def mutate(
        self, split: str, paper_id: str, request: MutationRequest, reviewer_id: str
    ) -> dict[str, Any]:
        """Apply one evidence-guarded edit and revalidate the entire rich result.

        The event stores before and after values, while the compiled truth remains easy
        for downstream evaluation code to consume. Invalid intermediate states never
        reach disk.
        """

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        self._validate_citations(split, paper_id, request.evidence)
        if request.action == "remove":
            location = _removed_record_location(request.path)
            if location is not None:
                references = _record_references(
                    current_revision.ground_truth, *location
                )
                if references:
                    joined = ", ".join(references)
                    raise ValueError(
                        "cannot remove a record while other records refer to it; "
                        f"correct or remove these linked records first: {joined}"
                    )
        before, proposed = _apply(current_revision.ground_truth, request)
        if request.action == "replace" and before == request.value:
            raise ValueError("replacement does not change the record")
        validated = StudyExtraction.model_validate(proposed).model_dump(mode="json")
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current_revision.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer_id,
            kind="mutation",
            action=request.action,
            path=request.path,
            before=before,
            after=request.value if request.action != "remove" else None,
            evidence=request.evidence,
            note=request.note,
        ).model_dump(mode="json")
        return self._commit(split, paper_id, current_revision, validated, event)

    def undo_mutation(
        self,
        split: str,
        paper_id: str,
        request: UndoMutationRequest,
        reviewer_id: str,
    ) -> dict[str, Any]:
        """Reverse one still-current correction without erasing its audit history.

        The original and inverse events remain attributable. Undo is rejected when a
        later edit changed the same value, when the event belongs to another reviewer,
        or when reversing it would violate references or the current study schema.
        """

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        target_data = next(
            (
                event
                for event in current_revision.events
                if event.get("event_id") == request.event_id
            ),
            None,
        )
        if target_data is None:
            raise ValueError("saved correction was not found")
        target = ReviewEvent.model_validate(target_data)
        if target.reviewer_id != reviewer_id:
            raise PermissionError("you can undo only your own saved corrections")
        if any(
            event.get("details", {}).get("undoes_event_id") == target.event_id
            for event in current_revision.events
        ):
            raise ValueError("this saved correction has already been undone")

        if target.kind == "spreadsheet_review":
            validated = _reverse_spreadsheet_review(
                current_revision.ground_truth, target
            )
            inverse_replacements = [
                {
                    **replacement,
                    "before": copy.deepcopy(replacement["after"]),
                    "after": copy.deepcopy(replacement["before"]),
                }
                for replacement in target.details.get("record_replacements", [])
            ]
            event = ReviewEvent(
                event_id=str(uuid.uuid4()),
                revision=current_revision.revision + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reviewer_id=reviewer_id,
                kind="spreadsheet_review",
                evidence=target.evidence,
                note="Undid a previously imported review workbook.",
                details={
                    "undoes_event_id": target.event_id,
                    "record_replacements": inverse_replacements,
                    "changed_fields": target.details.get("changed_fields", []),
                    "decisions": [],
                },
            ).model_dump(mode="json")
        else:
            (
                inverse_action,
                inverse_path,
                before,
                after,
                validated,
            ) = self._prepare_mutation_undo(
                current_revision.ground_truth,
                target,
            )
            event = ReviewEvent(
                event_id=str(uuid.uuid4()),
                revision=current_revision.revision + 1,
                timestamp=datetime.now(timezone.utc).isoformat(),
                reviewer_id=reviewer_id,
                kind="mutation",
                action=inverse_action,
                path=inverse_path,
                before=before,
                after=after,
                evidence=target.evidence,
                note="Undid a previously saved correction.",
                details={"undoes_event_id": target.event_id},
            ).model_dump(mode="json")
        return self._commit(split, paper_id, current_revision, validated, event)

    def decide_record(
        self,
        split: str,
        paper_id: str,
        request: RecordDecisionRequest,
        reviewer_id: str,
    ) -> dict[str, Any]:
        """Bind a decision to a record's current digest rather than only its stable ID.

        Stable IDs locate records, but the digest ensures a later correction makes the
        previous verification decision disappear from the derived summary.
        """

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        truth = current_revision.ground_truth
        identifier_field = RECORD_IDENTIFIERS[request.collection]
        record = next(
            (
                item
                for item in truth[request.collection]
                if str(item[identifier_field]) == request.record_id
            ),
            None,
        )
        if record is None:
            raise ValueError(f"unknown {request.collection} record {request.record_id}")
        record_key = f"{request.collection}:{request.record_id}"
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current_revision.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer_id,
            kind="record_decision",
            note=request.note,
            details={
                "record_key": record_key,
                "record_digest": _digest(record),
                "decision": request.decision,
            },
        ).model_dump(mode="json")
        return self._commit(split, paper_id, current_revision, truth, event)

    def inventory_audit(
        self,
        split: str,
        paper_id: str,
        request: InventoryAuditRequest,
        reviewer_id: str,
    ) -> dict[str, Any]:
        """Persist a reviewer census without changing the current scientific records."""

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current_revision.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer_id,
            kind="inventory_audit",
            details=request.model_dump(mode="json", exclude={"base_revision"}),
        ).model_dump(mode="json")
        return self._commit(
            split,
            paper_id,
            current_revision,
            current_revision.ground_truth,
            event,
        )

    def reset_reviewer_state(
        self,
        split: str,
        paper_id: str,
        request: ReviewerResetRequest,
        reviewer_id: str,
    ) -> dict[str, Any]:
        """Clear a reviewer's current markers without rewriting scientific data.

        Decisions, census results, and completed stages are reviewer-specific derived
        state, so a single compensating event can clear them while preserving the
        audit trail. Scientific corrections affect the shared ground truth and remain
        governed by the stricter mutation-undo checks instead of being bulk-reverted.
        """

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        summary = self.summary(
            current_revision.ground_truth, current_revision.events
        )
        decisions = summary["record_decisions"].get(reviewer_id, {})
        inventory = summary["inventory_audits"].get(reviewer_id)
        stages = [
            stage
            for stage, reviewers in summary["completed_stages"].items()
            if reviewer_id in reviewers
        ]
        if not decisions and inventory is None and not stages:
            raise ValueError("there is no current review state to reset for this paper")
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current_revision.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer_id,
            kind="review_reset",
            note="Reset current reviewer decisions and progress.",
            details={
                "cleared_record_decisions": len(decisions),
                "cleared_inventory_audit": inventory is not None,
                "cleared_stages": stages,
            },
        ).model_dump(mode="json")
        return self._commit(
            split,
            paper_id,
            current_revision,
            current_revision.ground_truth,
            event,
        )

    def complete_stage(
        self, split: str, paper_id: str, request: StageRequest, reviewer_id: str
    ) -> dict[str, Any]:
        """Advance review only after the evidence-based prerequisites are satisfied.

        Inventory requires a saved census, field review requires a current decision for
        every record, and later stages require the preceding stage. These constraints
        keep interface clicks from bypassing the ground-truth protocol.
        """

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        events = current_revision.events
        current_summary = self.summary(current_revision.ground_truth, events)
        completed = current_summary["completed_stages"]
        if reviewer_id in completed.get(request.stage, []):
            raise ValueError(f"{request.stage} stage is already complete")
        if (
            request.stage == "inventory"
            and reviewer_id not in current_summary["inventory_audits"]
        ):
            raise ValueError(
                "save the paper census before completing inventory"
            )
        prerequisite = {
            "fields": "inventory",
            "completeness": "fields",
            "adjudication": "completeness",
        }.get(request.stage)
        if prerequisite and reviewer_id not in completed.get(prerequisite, []):
            raise ValueError(f"complete the {prerequisite} stage first")
        if request.stage == "fields":
            truth = current_revision.ground_truth
            decisions = current_summary["record_decisions"].get(
                reviewer_id, {}
            )
            unresolved = [
                record_key
                for record_key in _record_catalog(truth)
                if decisions.get(record_key) not in {"verified", "uncertain"}
            ]
            if unresolved:
                raise ValueError(
                    f"review every current record before completing fields ({len(unresolved)} remaining)"
                )
        event = ReviewEvent(
            event_id=str(uuid.uuid4()),
            revision=current_revision.revision + 1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reviewer_id=reviewer_id,
            kind="stage_complete",
            note=request.note,
            details={"stage": request.stage},
        ).model_dump(mode="json")
        return self._commit(
            split,
            paper_id,
            current_revision,
            current_revision.ground_truth,
            event,
        )

    def list_papers(self, split: str) -> list[dict[str, Any]]:
        """Return summaries derived from truth and events rather than cached counters."""

        self.validate_identity(split, "10.0000--placeholder")
        papers = []
        for paper_id in self.storage.list_paper_ids(split):
            revision = self.storage.load_revision(split, paper_id)
            papers.append(
                {
                    "id": paper_id,
                    "revision": revision.revision,
                    **self.summary(revision.ground_truth, revision.events),
                }
            )
        return papers
