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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    "identity_links",
]
RecordDecision = Literal["verified", "uncertain", "needs_correction"]

RECORD_IDENTIFIERS: dict[str, str] = {
    "device_families": "family_id",
    "individual_devices": "device_id",
    "performance_observations": "observation_id",
    "population_statistics": "population_id",
    "stability_tests": "test_id",
    "identity_links": "link_id",
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


class InventoryAuditRequest(BaseModel):
    """Capture a blind device census before showing model candidates."""

    model_config = ConfigDict(extra="forbid", strict=True)

    base_revision: int = Field(ge=0)
    searched_sources: list[Literal["main", "supplement"]] = Field(min_length=1)
    expected_counts: dict[str, int]
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


class ReviewEvent(BaseModel):
    """Append-only audit record for a mutation, census, or stage decision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    event_id: str
    revision: int = Field(ge=1)
    timestamp: str
    reviewer_id: str
    kind: Literal[
        "mutation",
        "inventory_audit",
        "record_decision",
        "stage_complete",
        "seed_imported",
    ]
    action: MutationAction | None = None
    path: str | None = None
    before: Any = None
    after: Any = None
    evidence: list[Citation] = Field(default_factory=list)
    note: str = ""
    details: dict[str, Any] = Field(default_factory=dict)


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
        return {
            "paper_id": paper_id,
            "split": split,
            "revision": revision.revision,
            "ground_truth": revision.ground_truth,
            "seed_extraction": source.seed_extraction,
            "events": revision.events,
            "manifest": source.manifest,
            "summary": self.summary(revision.ground_truth, revision.events),
        }

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
        for event in events:
            if event["kind"] == "stage_complete":
                stages.setdefault(event["details"]["stage"], []).append(
                    event["reviewer_id"]
                )
            elif event["kind"] == "inventory_audit":
                audits[event["reviewer_id"]] = event["details"]
            elif event["kind"] == "record_decision":
                details = event["details"]
                record_key = str(details["record_key"])
                if catalog.get(record_key) == details.get("record_digest"):
                    decisions.setdefault(event["reviewer_id"], {})[record_key] = str(
                        details["decision"]
                    )
        return {
            "device_families": len(truth["device_families"]),
            "individual_devices": len(truth["individual_devices"]),
            "performance_observations": len(truth["performance_observations"]),
            "population_statistics": len(truth["population_statistics"]),
            "stability_tests": len(truth["stability_tests"]),
            "identity_links": len(truth.get("identity_links", [])),
            "completed_stages": stages,
            "inventory_audits": audits,
            "record_decisions": decisions,
            "record_count": len(catalog),
            "record_identifiers": RECORD_IDENTIFIERS,
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
            raise ValueError(
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
        """Persist the independent device census performed before candidates are shown."""

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

    def complete_stage(
        self, split: str, paper_id: str, request: StageRequest, reviewer_id: str
    ) -> dict[str, Any]:
        """Advance review only after the evidence-based prerequisites are satisfied.

        Inventory requires a blind audit, field review requires a current decision for
        every record, and later stages require the preceding stage. These constraints
        keep interface clicks from bypassing the ground-truth protocol.
        """

        current_revision = self._validate_revision(
            split, paper_id, request.base_revision
        )
        events = current_revision.events
        reviewer_events = [
            event for event in events if event["reviewer_id"] == reviewer_id
        ]
        if any(
            event["kind"] == "stage_complete"
            and event["details"].get("stage") == request.stage
            for event in reviewer_events
        ):
            raise ValueError(f"{request.stage} stage is already complete")
        if request.stage == "inventory" and not any(
            event["kind"] == "inventory_audit" for event in reviewer_events
        ):
            raise ValueError(
                "submit a blind inventory audit before completing inventory"
            )
        prerequisite = {
            "fields": "inventory",
            "completeness": "fields",
            "adjudication": "completeness",
        }.get(request.stage)
        if prerequisite and not any(
            event["kind"] == "stage_complete"
            and event["details"].get("stage") == prerequisite
            for event in reviewer_events
        ):
            raise ValueError(f"complete the {prerequisite} stage first")
        if request.stage == "fields":
            truth = current_revision.ground_truth
            decisions = self.summary(truth, events)["record_decisions"].get(
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
