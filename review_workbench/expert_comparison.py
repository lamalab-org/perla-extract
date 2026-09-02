"""Blinded, auditable comparison of two literature-extraction workflows.

This module is deliberately separate from ground-truth review.  Comparison answers
measure an extractor; they must never silently become scientific labels.  Both
candidates cross the same reduced-schema boundary, receive neutral record names, and
are assigned between reviewers before anybody sees an output.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.pydantic_model_reduced import PerovskiteSolarCells
from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.compatibility import to_reduced_with_report
from perla_extract.study_extraction.models import StudyExtraction
from review_workbench.review_storage import StaleRevisionError

Verdict = Literal["correct", "incorrect", "unsupported", "cannot_determine"]
Origin = Literal["historical_database", "new_extractor"]
PreferenceChoice = Literal["A", "B", "tie", "both_inadequate", "cannot_judge"]
PreferenceDimension = Literal[
    "factual_correctness",
    "coverage_completeness",
    "chemical_detail",
    "record_relationships",
    "evidence_traceability",
    "nomad_readiness",
    "curation_effort",
    "overall_preference",
]
IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
NON_CLAIM_FIELDS = {
    "additional_notes",
    "additional_parameters",
    "evidence_blocks",
    "source_step_id",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _discarded_paths(original: Any, validated: Any, path: str = "") -> list[str]:
    """Find input keys omitted by a permissive historical Pydantic model."""

    if isinstance(original, dict) and isinstance(validated, dict):
        discarded = [f"{path}/{key}" for key in original.keys() - validated.keys()]
        for key in original.keys() & validated.keys():
            discarded.extend(
                _discarded_paths(original[key], validated[key], f"{path}/{key}")
            )
        return discarded
    if isinstance(original, list) and isinstance(validated, list):
        return [
            nested
            for index, (left, right) in enumerate(
                zip(original, validated, strict=False)
            )
            for nested in _discarded_paths(left, right, f"{path}/{index}")
        ]
    return []


class SourceReference(BaseModel):
    """Locate evidence for a negative judgment or an omitted fact."""

    model_config = ConfigDict(extra="forbid", strict=True)
    source: Literal["main", "supplement"]
    page: int = Field(ge=1)
    quote: str = Field(default="", max_length=1000)


class AtomicField(BaseModel):
    """Present one scalar claim without exposing candidate-specific identifiers."""

    model_config = ConfigDict(extra="forbid", strict=True)
    field_key: str
    record_key: str
    path: str
    label: str
    value: bool | int | float | str


class NeutralRecord(BaseModel):
    """Group scalar claims that belong to one reduced-schema solar-cell row."""

    model_config = ConfigDict(extra="forbid", strict=True)
    record_key: str
    summary: str
    fields: list[AtomicField]


class Candidate(BaseModel):
    """Keep origin private while preserving immutable common and native payloads."""

    model_config = ConfigDict(extra="forbid", strict=True)
    origin: Origin
    common_payload: dict[str, Any]
    native_payload: dict[str, Any]
    common_sha256: str
    native_sha256: str
    projection_issues: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_hashes(self) -> Candidate:
        """Detect edits to either candidate after the experiment was assembled."""

        if self.common_sha256 != _digest(self.common_payload):
            raise ValueError("common candidate hash does not match its payload")
        if self.native_sha256 != _digest(self.native_payload):
            raise ValueError("native candidate hash does not match its payload")
        return self


class Assignment(BaseModel):
    """Assign one neutral candidate to one reviewer for the primary evaluation."""

    model_config = ConfigDict(extra="forbid", strict=True)
    reviewer_id: str = Field(pattern=IDENTIFIER_PATTERN)
    blind_label: Literal["A", "B"]


class PairwiseRubric(BaseModel):
    """Freeze one comparison criterion and its decision standard with the study."""

    model_config = ConfigDict(extra="forbid", strict=True)
    key: PreferenceDimension
    label: str
    question: str
    minimum_acceptable: str
    preference_rule: str


def _pairwise_rubrics() -> list[PairwiseRubric]:
    """Return fresh rubric objects so one experiment cannot mutate another."""

    definitions = [
        (
            "factual_correctness",
            "Factual correctness",
            "Which output has fewer or less consequential incorrect and unsupported scientific claims?",
            "No material claim contradicts the source, and unsupported claims are uncommon and minor.",
            "Prefer one candidate when its errors are clearly fewer or scientifically less consequential; do not decide by record count alone.",
        ),
        (
            "coverage_completeness",
            "Coverage and completeness",
            "Which captures more schema-relevant information without rewarding repetition or verbosity?",
            "The principal device design and the reported performance, processing, and stability information in scope are represented.",
            "Prefer one candidate when it captures important missing facts or records without adding unsupported duplicates.",
        ),
        (
            "chemical_detail",
            "Chemical detail",
            "Which better preserves material identities, formulas, constituents, layer roles, quantities, and processing chemistry?",
            "The chemistry is specific enough to distinguish the reported absorber and functional layers without invented normalization.",
            "Prefer one candidate when its additional chemical detail is source-supported, structured, and scientifically discriminating.",
        ),
        (
            "record_relationships",
            "Record relationships",
            "Which more coherently links families, devices, measurements, population statistics, and stability tests?",
            "Champion values, individual measurements, aggregates, and stability specimens are not silently treated as one device.",
            "Prefer one candidate when its links preserve the experimental units of analysis and require fewer structural corrections.",
        ),
        (
            "evidence_traceability",
            "Evidence traceability",
            "Which makes its important claims easier to locate and verify in the main paper or supporting information?",
            "A reviewer can trace material scientific claims to sufficiently specific source context without reconstructing the extraction.",
            "Prefer one candidate when verification is consistently faster and less ambiguous, not merely because it contains shorter text.",
        ),
        (
            "nomad_readiness",
            "NOMAD readiness",
            "Which maps more cleanly into a scientifically useful downstream NOMAD record?",
            "Values, units, materials, record types, and relationships are structured well enough for deterministic downstream conversion.",
            "Prefer one candidate when less semantic reconstruction or loss-prone normalization is needed for NOMAD export.",
        ),
        (
            "curation_effort",
            "Required curation effort",
            "Which would require less expert work before it could be accepted into a curated database?",
            "The output needs bounded checking and correction rather than wholesale re-extraction or structural rebuilding.",
            "Prefer the candidate requiring less total expert effort, considering both error correction and recovery of missing information.",
        ),
        (
            "overall_preference",
            "Overall preference",
            "Which is the better starting point for expert curation after considering all trade-offs?",
            "The output is scientifically credible, sufficiently complete, understandable, and practically editable.",
            "Prefer one candidate only when its combined advantages are meaningful for real curation; otherwise use tie or both inadequate.",
        ),
    ]
    return [
        PairwiseRubric(
            key=key,
            label=label,
            question=question,
            minimum_acceptable=minimum,
            preference_rule=rule,
        )
        for key, label, question, minimum, rule in definitions
    ]


class ComparisonSource(BaseModel):
    """Freeze inputs, assignment, and provenance before expert review begins."""

    model_config = ConfigDict(extra="forbid", strict=True)
    format_version: Literal[1, 2] = 2
    comparison_id: str = Field(pattern=IDENTIFIER_PATTERN)
    paper_id: str = Field(pattern=IDENTIFIER_PATTERN)
    split: Literal["calibration", "dev", "test"] = "dev"
    title: str
    source_hashes: dict[str, str] = Field(default_factory=dict)
    common_schema: Literal["perla-reduced-v1"] = "perla-reduced-v1"
    randomization_seed_sha256: str
    created_at: datetime
    candidates: dict[Literal["A", "B"], Candidate]
    assignments: list[Assignment]
    pairwise_rubrics: list[PairwiseRubric] = Field(default_factory=_pairwise_rubrics)

    @model_validator(mode="after")
    def validate_design(self) -> ComparisonSource:
        """Reject experiments that are not blinded, balanced, or independently assigned."""

        if set(self.candidates) != {"A", "B"}:
            raise ValueError("a comparison must contain candidates A and B")
        origins = {candidate.origin for candidate in self.candidates.values()}
        if origins != {"historical_database", "new_extractor"}:
            raise ValueError(
                "a comparison must contain one historical and one new candidate"
            )
        reviewers = [assignment.reviewer_id for assignment in self.assignments]
        if len(reviewers) != len(set(reviewers)) or not reviewers:
            raise ValueError("reviewers must be non-empty and unique")
        counts = [
            sum(a.blind_label == label for a in self.assignments)
            for label in ("A", "B")
        ]
        if abs(counts[0] - counts[1]) > 1:
            raise ValueError("candidate assignments must be balanced")
        expected_rubrics = {item.key for item in _pairwise_rubrics()}
        actual_rubrics = [item.key for item in self.pairwise_rubrics]
        if len(actual_rubrics) != len(set(actual_rubrics)) or set(
            actual_rubrics
        ) != expected_rubrics:
            raise ValueError("pairwise rubrics must contain every criterion once")
        return self


class FieldJudgment(BaseModel):
    """Record an expert decision about exactly one presented atomic claim."""

    model_config = ConfigDict(extra="forbid", strict=True)
    field_key: str
    verdict: Verdict
    correction: str = Field(default="", max_length=2000)
    reference: SourceReference | None = None

    @model_validator(mode="after")
    def require_negative_evidence(self) -> FieldJudgment:
        """Make incorrect and unsupported decisions auditable against the paper."""

        if self.verdict in {"incorrect", "unsupported"} and self.reference is None:
            raise ValueError(
                "incorrect or unsupported judgments require a source reference"
            )
        return self


class MissingFact(BaseModel):
    """Capture schema-relevant information absent from the assigned candidate."""

    model_config = ConfigDict(extra="forbid", strict=True)
    description: str = Field(min_length=1, max_length=2000)
    value: str = Field(default="", max_length=1000)
    reference: SourceReference


class UtilityRatings(BaseModel):
    """Measure practical usefulness separately from field-level correctness."""

    model_config = ConfigDict(extra="forbid", strict=True)
    chemical_detail: int = Field(ge=1, le=5)
    relationships: int = Field(ge=1, le=5)
    verification_ease: int = Field(ge=1, le=5)
    nomad_usefulness: int = Field(ge=1, le=5)


class ComparisonReview(BaseModel):
    """Store one reviewer's append-only draft or final assessment."""

    model_config = ConfigDict(extra="forbid", strict=True)
    revision: int = Field(ge=1)
    comparison_id: str = Field(pattern=IDENTIFIER_PATTERN)
    reviewer_id: str = Field(pattern=IDENTIFIER_PATTERN)
    blind_label: Literal["A", "B"]
    started_at: datetime
    saved_at: datetime
    submitted_at: datetime | None = None
    active_seconds: int = Field(default=0, ge=0)
    judgments: list[FieldJudgment] = Field(default_factory=list)
    missing_facts: list[MissingFact] = Field(default_factory=list)
    extra_records: int = Field(default=0, ge=0)
    missing_records: int = Field(default=0, ge=0)
    wrong_links: int = Field(default=0, ge=0)
    confidence: int | None = Field(default=None, ge=1, le=5)
    notes: str = Field(default="", max_length=5000)

    @property
    def final(self) -> bool:
        return self.submitted_at is not None


class NativeUtilityReview(BaseModel):
    """Rate an assigned workflow's native output only after accuracy is locked."""

    model_config = ConfigDict(extra="forbid", strict=True)
    comparison_id: str = Field(pattern=IDENTIFIER_PATTERN)
    reviewer_id: str = Field(pattern=IDENTIFIER_PATTERN)
    blind_label: Literal["A", "B"]
    candidate_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    submitted_at: datetime
    active_seconds: int = Field(default=0, ge=0)
    ratings: UtilityRatings
    suitable_as_curation_start: Literal["yes", "no", "unsure"]
    notes: str = Field(default="", max_length=5000)


class PreferenceRatings(BaseModel):
    """Require a decision on each pre-registered pairwise comparison rubric."""

    model_config = ConfigDict(extra="forbid", strict=True)
    factual_correctness: PreferenceChoice
    coverage_completeness: PreferenceChoice
    chemical_detail: PreferenceChoice
    record_relationships: PreferenceChoice
    evidence_traceability: PreferenceChoice
    nomad_readiness: PreferenceChoice
    curation_effort: PreferenceChoice
    overall_preference: PreferenceChoice


class PairwisePreferenceReview(BaseModel):
    """Store one immutable, dimension-specific preference after independent scoring."""

    model_config = ConfigDict(extra="forbid", strict=True)
    comparison_id: str = Field(pattern=IDENTIFIER_PATTERN)
    reviewer_id: str = Field(pattern=IDENTIFIER_PATTERN)
    candidate_hashes: dict[Literal["A", "B"], str]
    submitted_at: datetime
    active_seconds: int = Field(default=0, ge=0)
    preferences: PreferenceRatings
    confidence: int = Field(ge=1, le=5)
    rationale: str = Field(default="", max_length=5000)

    @model_validator(mode="after")
    def validate_candidate_hashes(self) -> PairwisePreferenceReview:
        """Bind the preference to exactly the two native outputs shown to the expert."""

        if set(self.candidate_hashes) != {"A", "B"}:
            raise ValueError("pairwise preferences require candidate hashes for A and B")
        if any(
            not isinstance(value, str)
            or not re.fullmatch(r"[a-f0-9]{64}", value)
            for value in self.candidate_hashes.values()
        ):
            raise ValueError("pairwise candidate hashes must be SHA-256 values")
        return self


class ComparisonStorage(Protocol):
    """Persist immutable experiment sources and reviewer-scoped revision logs."""

    def create(self, source: ComparisonSource) -> None: ...
    def list_ids(self) -> list[str]: ...
    def load_source(self, comparison_id: str) -> ComparisonSource: ...
    def load_review(
        self, comparison_id: str, reviewer_id: str
    ) -> ComparisonReview | None: ...
    def compare_and_swap(
        self, expected_revision: int, review: ComparisonReview
    ) -> None: ...

    def load_utility(
        self, comparison_id: str, reviewer_id: str
    ) -> NativeUtilityReview | None: ...

    def save_utility(self, review: NativeUtilityReview) -> None: ...

    def load_preference(
        self, comparison_id: str, reviewer_id: str
    ) -> PairwisePreferenceReview | None: ...

    def save_preference(self, review: PairwisePreferenceReview) -> None: ...


class LocalComparisonStorage:
    """Use immutable JSON files so drafts and submissions remain recoverable."""

    def __init__(self, root: Path):
        self.root = root.resolve() / "comparison_state"

    def _source(self, comparison_id: str) -> Path:
        return self.root / "sources" / f"{comparison_id}.json"

    def _reviews(self, comparison_id: str, reviewer_id: str) -> Path:
        return self.root / "reviews" / comparison_id / reviewer_id

    def _utility(self, comparison_id: str, reviewer_id: str) -> Path:
        return self.root / "utility" / comparison_id / f"{reviewer_id}.json"

    def _preference(self, comparison_id: str, reviewer_id: str) -> Path:
        return self.root / "preferences" / comparison_id / f"{reviewer_id}.json"

    def create(self, source: ComparisonSource) -> None:
        try:
            write_json_exclusive(
                self._source(source.comparison_id), source.model_dump(mode="json")
            )
        except FileExistsError as error:
            raise ValueError("comparison already exists") from error

    def list_ids(self) -> list[str]:
        return sorted(path.stem for path in (self.root / "sources").glob("*.json"))

    def load_source(self, comparison_id: str) -> ComparisonSource:
        path = self._source(comparison_id)
        if not path.exists():
            raise FileNotFoundError(path)
        return ComparisonSource.model_validate_json(path.read_text(encoding="utf-8"))

    def load_review(
        self, comparison_id: str, reviewer_id: str
    ) -> ComparisonReview | None:
        paths = sorted(self._reviews(comparison_id, reviewer_id).glob("*.json"))
        return (
            ComparisonReview.model_validate_json(paths[-1].read_text(encoding="utf-8"))
            if paths
            else None
        )

    def compare_and_swap(
        self, expected_revision: int, review: ComparisonReview
    ) -> None:
        current = self.load_review(review.comparison_id, review.reviewer_id)
        current_revision = current.revision if current else 0
        if (
            current_revision != expected_revision
            or review.revision != expected_revision + 1
        ):
            raise StaleRevisionError("comparison review changed in another session")
        path = (
            self._reviews(review.comparison_id, review.reviewer_id)
            / f"{review.revision:08d}.json"
        )
        try:
            write_json_exclusive(path, review.model_dump(mode="json"))
        except FileExistsError as error:
            raise StaleRevisionError(
                "comparison review changed in another session"
            ) from error

    def load_utility(
        self, comparison_id: str, reviewer_id: str
    ) -> NativeUtilityReview | None:
        path = self._utility(comparison_id, reviewer_id)
        return (
            NativeUtilityReview.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def save_utility(self, review: NativeUtilityReview) -> None:
        try:
            write_json_exclusive(
                self._utility(review.comparison_id, review.reviewer_id),
                review.model_dump(mode="json"),
            )
        except FileExistsError as error:
            raise ValueError("native utility review is already submitted") from error

    def load_preference(
        self, comparison_id: str, reviewer_id: str
    ) -> PairwisePreferenceReview | None:
        path = self._preference(comparison_id, reviewer_id)
        return (
            PairwisePreferenceReview.model_validate_json(
                path.read_text(encoding="utf-8")
            )
            if path.exists()
            else None
        )

    def save_preference(self, review: PairwisePreferenceReview) -> None:
        try:
            write_json_exclusive(
                self._preference(review.comparison_id, review.reviewer_id),
                review.model_dump(mode="json"),
            )
        except FileExistsError as error:
            raise ValueError("pairwise preference is already submitted") from error


def _label(path: str) -> str:
    name = path.rsplit(".", 1)[-1].replace("_", " ")
    return name[:1].upper() + name[1:]


def neutral_records(payload: dict[str, Any]) -> list[NeutralRecord]:
    """Turn reduced rows into stable scalar claims while omitting empty placeholders.

    Values stay atomic: a number and its unit are separate claims. Provenance IDs and
    free-text compatibility notes are audit material rather than scientific claims,
    so they remain in the frozen native payload but not in the scored field list.
    Identical path/value claims repeated by flat compatibility rows are scored once;
    record-count and linkage questions capture structural duplication separately.
    Lists retain their semantic paths, while record order is canonicalized from content
    rather than copied from either extractor.
    """

    cells = payload.get("cells") or []
    ordered = sorted(cells, key=_digest)
    records: list[NeutralRecord] = []
    seen_claims: set[str] = set()
    for index, cell in enumerate(ordered, 1):
        record_key = f"cell-{index:03d}"
        fields: list[AtomicField] = []

        def visit(value: Any, path: str) -> None:
            if value is None:
                return
            if isinstance(value, dict):
                for key in sorted(value):
                    if key in NON_CLAIM_FIELDS or key.endswith("_id"):
                        continue
                    visit(value[key], f"{path}.{key}" if path else key)
                return
            if isinstance(value, list):
                for item_index, item in enumerate(value, 1):
                    visit(item, f"{path}[{item_index}]")
                return
            if isinstance(value, (str, int, float, bool)):
                signature = _digest({"path": path, "value": value})
                if signature in seen_claims:
                    return
                seen_claims.add(signature)
                field_key = f"claim-{signature[:20]}"
                fields.append(
                    AtomicField(
                        field_key=field_key,
                        record_key=record_key,
                        path=f"/{path.replace('.', '/').replace('[', '/').replace(']', '')}",
                        label=_label(path),
                        value=value,
                    )
                )

        visit(cell, "")
        architecture = cell.get("device_architecture") or "architecture not reported"
        formula = (cell.get("perovskite_composition") or {}).get(
            "formula"
        ) or "composition not reported"
        records.append(
            NeutralRecord(
                record_key=record_key,
                summary=f"{formula} · {architecture}",
                fields=fields,
            )
        )
    return records


def candidate_from_payload(origin: Origin, payload: dict[str, Any]) -> Candidate:
    """Validate historical data or project a rich extraction to the common schema."""

    issues: list[dict[str, Any]] = []
    if origin == "historical_database":
        common = PerovskiteSolarCells.model_validate(payload).model_dump(mode="json")
        issues = [
            {
                "code": "historical_field_not_in_common_schema",
                "source_kind": "historical_database",
                "source_id": path,
                "detail": "The native historical field is not represented in the common comparison schema.",
            }
            for path in _discarded_paths(payload, common)
        ]
    else:
        export = to_reduced_with_report(StudyExtraction.model_validate(payload))
        common = export.cells.model_dump(mode="json")
        issues = [issue.model_dump(mode="json") for issue in export.issues]
    return Candidate(
        origin=origin,
        common_payload=common,
        native_payload=payload,
        common_sha256=_digest(common),
        native_sha256=_digest(payload),
        projection_issues=issues,
    )


def build_comparison_source(
    *,
    comparison_id: str,
    paper_id: str,
    title: str,
    split: Literal["calibration", "dev", "test"],
    historical: dict[str, Any],
    extracted: dict[str, Any],
    reviewer_ids: list[str],
    randomization_seed: str,
    source_hashes: dict[str, str] | None = None,
) -> ComparisonSource:
    """Freeze a balanced experiment without storing its randomization secret."""

    reviewer_ids = sorted(set(reviewer_ids))
    if not reviewer_ids:
        raise ValueError("at least one reviewer is required")
    labels: list[Literal["A", "B"]] = ["A", "B"]
    rng = random.Random(f"{randomization_seed}:{comparison_id}")
    rng.shuffle(labels)
    candidates = {
        labels[0]: candidate_from_payload("historical_database", historical),
        labels[1]: candidate_from_payload("new_extractor", extracted),
    }
    shuffled_reviewers = reviewer_ids[:]
    rng.shuffle(shuffled_reviewers)
    assignments = [
        Assignment(reviewer_id=reviewer, blind_label=("A" if index % 2 == 0 else "B"))
        for index, reviewer in enumerate(shuffled_reviewers)
    ]
    return ComparisonSource(
        comparison_id=comparison_id,
        paper_id=paper_id,
        split=split,
        title=title,
        source_hashes=source_hashes or {},
        randomization_seed_sha256=hashlib.sha256(
            randomization_seed.encode()
        ).hexdigest(),
        created_at=_now(),
        candidates=candidates,
        assignments=assignments,
    )


class ComparisonService:
    """Expose blinded reviewer views and enforce complete, immutable submissions."""

    def __init__(self, storage: ComparisonStorage):
        self.storage = storage

    @staticmethod
    def _assignment(source: ComparisonSource, reviewer_id: str) -> Assignment:
        assignment = next(
            (item for item in source.assignments if item.reviewer_id == reviewer_id),
            None,
        )
        if assignment is None:
            raise PermissionError("this comparison is not assigned to you")
        return assignment

    def list_for(
        self, reviewer_id: str, *, include_unassigned: bool = False
    ) -> list[dict[str, Any]]:
        """List assigned work and, for administrators, origin-free batch progress."""

        result = []
        for comparison_id in self.storage.list_ids():
            source = self.storage.load_source(comparison_id)
            completed = sum(
                bool(self.storage.load_preference(comparison_id, item.reviewer_id))
                for item in source.assignments
            )
            batch_ready = completed == len(source.assignments)
            try:
                assignment = self._assignment(source, reviewer_id)
            except PermissionError:
                if not include_unassigned:
                    continue
                result.append(
                    {
                        "comparison_id": comparison_id,
                        "paper_id": source.paper_id,
                        "split": source.split,
                        "title": source.title,
                        "blind_label": None,
                        "assigned": False,
                        "batch_ready": batch_ready,
                        "status": (
                            "batch_ready"
                            if batch_ready
                            else f"{completed}_of_{len(source.assignments)}_reviews_complete"
                        ),
                    }
                )
                continue
            review = self.storage.load_review(comparison_id, reviewer_id)
            result.append(
                {
                    "comparison_id": comparison_id,
                    "paper_id": source.paper_id,
                    "split": source.split,
                    "title": source.title,
                    "blind_label": assignment.blind_label,
                    "assigned": True,
                    "batch_ready": batch_ready,
                    "status": self._status(comparison_id, reviewer_id, review),
                }
            )
        return result

    def _status(
        self,
        comparison_id: str,
        reviewer_id: str,
        review: ComparisonReview | None,
    ) -> str:
        if review is None:
            return "not_started"
        if not review.final:
            return "accuracy_in_progress"
        if not self.storage.load_utility(comparison_id, reviewer_id):
            return "native_utility_pending"
        return (
            "complete"
            if self.storage.load_preference(comparison_id, reviewer_id)
            else "pairwise_preference_pending"
        )

    def open(self, comparison_id: str, reviewer_id: str) -> dict[str, Any]:
        source = self.storage.load_source(comparison_id)
        assignment = self._assignment(source, reviewer_id)
        review = self.storage.load_review(comparison_id, reviewer_id)
        if review is None:
            review = ComparisonReview(
                revision=1,
                comparison_id=comparison_id,
                reviewer_id=reviewer_id,
                blind_label=assignment.blind_label,
                started_at=_now(),
                saved_at=_now(),
            )
            try:
                self.storage.compare_and_swap(0, review)
            except StaleRevisionError:
                review = self.storage.load_review(comparison_id, reviewer_id)
                if review is None:
                    raise
        candidate = source.candidates[assignment.blind_label]
        return {
            "comparison_id": comparison_id,
            "paper_id": source.paper_id,
            "split": source.split,
            "title": source.title,
            "blind_label": assignment.blind_label,
            "common_schema": source.common_schema,
            "records": [
                item.model_dump(mode="json")
                for item in neutral_records(candidate.common_payload)
            ],
            "review": review.model_dump(mode="json"),
        }

    def save(
        self, comparison_id: str, reviewer_id: str, payload: object
    ) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError("comparison review must be a JSON object")
        source = self.storage.load_source(comparison_id)
        assignment = self._assignment(source, reviewer_id)
        current = self.storage.load_review(comparison_id, reviewer_id)
        if current is None:
            raise ValueError("open the comparison before saving it")
        if current.final:
            raise ValueError("a submitted comparison cannot be changed")
        expected = int(payload.get("revision", 0))
        if expected != current.revision:
            raise StaleRevisionError("comparison review changed in another session")
        submitted = bool(payload.get("submit", False))
        review = ComparisonReview.model_validate(
            {
                **current.model_dump(mode="python"),
                **{
                    key: payload[key]
                    for key in (
                        "active_seconds",
                        "judgments",
                        "missing_facts",
                        "extra_records",
                        "missing_records",
                        "wrong_links",
                        "confidence",
                        "notes",
                    )
                    if key in payload
                },
                "revision": current.revision + 1,
                "blind_label": assignment.blind_label,
                "saved_at": _now(),
                "submitted_at": _now() if submitted else None,
            }
        )
        if submitted:
            field_keys = {
                field.field_key
                for record in neutral_records(
                    source.candidates[assignment.blind_label].common_payload
                )
                for field in record.fields
            }
            judged = [item.field_key for item in review.judgments]
            if len(judged) != len(set(judged)) or set(judged) != field_keys:
                raise ValueError(
                    "final submission requires one judgment for every atomic field"
                )
            if review.confidence is None:
                raise ValueError("final submission requires a confidence rating")
        self.storage.compare_and_swap(expected, review)
        return review.model_dump(mode="json")

    def open_native(self, comparison_id: str, reviewer_id: str) -> dict[str, Any]:
        """Expose the assigned native output only after primary accuracy is immutable."""

        source = self.storage.load_source(comparison_id)
        assignment = self._assignment(source, reviewer_id)
        accuracy = self.storage.load_review(comparison_id, reviewer_id)
        if accuracy is None or not accuracy.final:
            raise ValueError("submit the common-schema accuracy review first")
        candidate = source.candidates[assignment.blind_label]
        utility = self.storage.load_utility(comparison_id, reviewer_id)
        return {
            "comparison_id": comparison_id,
            "blind_label": assignment.blind_label,
            "native_payload": candidate.native_payload,
            "candidate_sha256": candidate.native_sha256,
            "projection_issues": candidate.projection_issues,
            "review": utility.model_dump(mode="json") if utility else None,
        }

    def save_native(
        self, comparison_id: str, reviewer_id: str, payload: object
    ) -> dict[str, Any]:
        """Commit one immutable native-output utility assessment."""

        if not isinstance(payload, dict):
            raise ValueError("native utility review must be a JSON object")
        view = self.open_native(comparison_id, reviewer_id)
        if view["review"] is not None:
            raise ValueError("native utility review is already submitted")
        review = NativeUtilityReview.model_validate(
            {
                **payload,
                "comparison_id": comparison_id,
                "reviewer_id": reviewer_id,
                "blind_label": view["blind_label"],
                "candidate_sha256": view["candidate_sha256"],
                "submitted_at": _now(),
            }
        )
        self.storage.save_utility(review)
        return review.model_dump(mode="json")

    def open_pairwise(self, comparison_id: str, reviewer_id: str) -> dict[str, Any]:
        """Show anonymous A and B only after the independent assessments are locked."""

        source = self.storage.load_source(comparison_id)
        self._assignment(source, reviewer_id)
        accuracy = self.storage.load_review(comparison_id, reviewer_id)
        utility = self.storage.load_utility(comparison_id, reviewer_id)
        if accuracy is None or not accuracy.final or utility is None:
            raise ValueError(
                "submit the independent accuracy and native-output reviews first"
            )
        preference = self.storage.load_preference(comparison_id, reviewer_id)
        return {
            "comparison_id": comparison_id,
            "rubrics": [
                rubric.model_dump(mode="json") for rubric in source.pairwise_rubrics
            ],
            "candidates": {
                label: {
                    "native_payload": candidate.native_payload,
                    "candidate_sha256": candidate.native_sha256,
                }
                for label, candidate in source.candidates.items()
            },
            "review": preference.model_dump(mode="json") if preference else None,
        }

    def save_pairwise(
        self, comparison_id: str, reviewer_id: str, payload: object
    ) -> dict[str, Any]:
        """Commit the complete rubric-level A/B preference as one immutable response."""

        if not isinstance(payload, dict):
            raise ValueError("pairwise preference must be a JSON object")
        view = self.open_pairwise(comparison_id, reviewer_id)
        if view["review"] is not None:
            raise ValueError("pairwise preference is already submitted")
        review = PairwisePreferenceReview.model_validate(
            {
                **payload,
                "comparison_id": comparison_id,
                "reviewer_id": reviewer_id,
                "candidate_hashes": {
                    label: candidate["candidate_sha256"]
                    for label, candidate in view["candidates"].items()
                },
                "submitted_at": _now(),
            }
        )
        self.storage.save_preference(review)
        return review.model_dump(mode="json")

    def reveal(self, comparison_id: str, *, force: bool = False) -> dict[str, Any]:
        """Reveal origins only after every assigned review stage is immutable."""

        source = self.storage.load_source(comparison_id)
        incomplete = [
            item.reviewer_id
            for item in source.assignments
            if not (
                (review := self.storage.load_review(comparison_id, item.reviewer_id))
                and review.final
                and self.storage.load_utility(comparison_id, item.reviewer_id)
                and self.storage.load_preference(comparison_id, item.reviewer_id)
            )
        ]
        if incomplete and not force:
            raise ValueError(
                "cannot reveal before every assigned review stage is submitted"
            )
        return {
            "comparison_id": comparison_id,
            "mapping": {
                label: candidate.origin
                for label, candidate in source.candidates.items()
            },
            "candidate_hashes": {
                label: {
                    "common": candidate.common_sha256,
                    "native": candidate.native_sha256,
                }
                for label, candidate in source.candidates.items()
            },
            "incomplete_reviewers": incomplete,
        }

    def export(self, comparison_id: str) -> dict[str, Any]:
        """Return a frozen, identified analysis artifact only after batch completion."""

        source = self.storage.load_source(comparison_id)
        reveal = self.reveal(comparison_id)
        reviews = [
            self.storage.load_review(comparison_id, assignment.reviewer_id)
            for assignment in source.assignments
        ]
        utility_reviews = [
            self.storage.load_utility(comparison_id, assignment.reviewer_id)
            for assignment in source.assignments
        ]
        preference_reviews = [
            self.storage.load_preference(comparison_id, assignment.reviewer_id)
            for assignment in source.assignments
        ]
        by_origin: dict[str, dict[str, Any]] = {}
        for review in reviews:
            assert review is not None  # reveal already proved completeness
            origin = source.candidates[review.blind_label].origin
            summary = by_origin.setdefault(
                origin,
                {
                    "review_count": 0,
                    "verdicts": {
                        "correct": 0,
                        "incorrect": 0,
                        "unsupported": 0,
                        "cannot_determine": 0,
                    },
                    "missing_facts": 0,
                    "extra_records": 0,
                    "missing_records": 0,
                    "wrong_links": 0,
                    "active_seconds": 0,
                },
            )
            summary["review_count"] += 1
            summary["missing_facts"] += len(review.missing_facts)
            summary["extra_records"] += review.extra_records
            summary["missing_records"] += review.missing_records
            summary["wrong_links"] += review.wrong_links
            summary["active_seconds"] += review.active_seconds
            for judgment in review.judgments:
                summary["verdicts"][judgment.verdict] += 1
        for summary in by_origin.values():
            decided = sum(
                summary["verdicts"][key]
                for key in ("correct", "incorrect", "unsupported")
            )
            summary["supported_atomic_precision"] = (
                summary["verdicts"]["correct"] / decided if decided else None
            )
            summary["native_utility_review_count"] = 0
            summary["native_rating_means"] = {}
            summary["curation_start_suitability_counts"] = {
                "yes": 0,
                "no": 0,
                "unsure": 0,
            }
        rating_values: dict[str, dict[str, list[int]]] = {}
        for utility in utility_reviews:
            if utility is None:
                continue
            origin = source.candidates[utility.blind_label].origin
            summary = by_origin[origin]
            summary["native_utility_review_count"] += 1
            summary["curation_start_suitability_counts"][
                utility.suitable_as_curation_start
            ] += 1
            values = rating_values.setdefault(origin, {})
            for name, value in utility.ratings.model_dump().items():
                values.setdefault(name, []).append(value)
        for origin, dimensions in rating_values.items():
            by_origin[origin]["native_rating_means"] = {
                name: sum(values) / len(values) for name, values in dimensions.items()
            }
        preference_counts: dict[str, dict[str, int]] = {}
        for preference in preference_reviews:
            assert preference is not None  # reveal already proved completeness
            for dimension, choice in preference.preferences.model_dump().items():
                resolved_choice: str = (
                    source.candidates[choice].origin if choice in {"A", "B"} else choice
                )
                counts = preference_counts.setdefault(
                    dimension,
                    {
                        "historical_database": 0,
                        "new_extractor": 0,
                        "tie": 0,
                        "both_inadequate": 0,
                        "cannot_judge": 0,
                    },
                )
                counts[resolved_choice] += 1
        return {
            "format_version": 1,
            "comparison_id": comparison_id,
            "paper_id": source.paper_id,
            "common_schema": source.common_schema,
            "source_hashes": source.source_hashes,
            "pairwise_rubrics": [
                rubric.model_dump(mode="json") for rubric in source.pairwise_rubrics
            ],
            "candidate_mapping": reveal["mapping"],
            "candidate_hashes": reveal["candidate_hashes"],
            "projection_issues": {
                label: candidate.projection_issues
                for label, candidate in source.candidates.items()
            },
            "reviews": [review.model_dump(mode="json") for review in reviews if review],
            "native_utility_reviews": [
                review.model_dump(mode="json") for review in utility_reviews if review
            ],
            "pairwise_preference_reviews": [
                review.model_dump(mode="json")
                for review in preference_reviews
                if review
            ],
            "summary_by_origin": by_origin,
            "pairwise_preference_counts": preference_counts,
        }
