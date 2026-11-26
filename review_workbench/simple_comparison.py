"""Simplified A/B comparison: workbench ground truth against an extractor output.

A reviewer reads both candidates as cited prose under the paper and chooses the
better extraction with a written justification. This is a quick overall preference;
it complements, and never replaces, the claim-level expert comparison in
``expert_comparison``. Like that study, it is blind and auditable:

* both candidates are rendered by the same deterministic renderer, with publication
  metadata shared by the comparison, and are frozen with their hashes at creation;
* the A/B mapping is randomized from a seed whose hash alone is stored;
* reviewers see only the rendered prose, never the native payloads or origins; and
* a submitted choice is immutable, and origins are revealed only to administrators
  after every assigned reviewer has submitted.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.artifacts import write_json_exclusive
from perla_extract.study_extraction.models import EvidenceBlock, StudyExtraction
from review_workbench.study_prose import PaperInfo, ProseDocument, render_prose

IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
Label = Literal["A", "B"]
Origin = Literal["ground_truth", "extractor_seed", "extractor_run"]
Split = Literal["calibration", "dev", "test"]
MINIMUM_JUSTIFICATION = 10
# The first two path parts of every route this study answers.
ROUTES = [["api", "simple-comparisons"], ["api", "simple-comparison-export"]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


class SimpleCandidate(BaseModel):
    """One frozen candidate: its native payload for audit, its prose for review."""

    model_config = ConfigDict(extra="forbid")
    origin: Origin
    native_payload: dict[str, Any]
    native_sha256: str
    document_sha256: str
    prose: ProseDocument
    prose_sha256: str

    @model_validator(mode="after")
    def validate_hashes(self) -> SimpleCandidate:
        if self.native_sha256 != _digest(self.native_payload):
            raise ValueError("native candidate hash does not match its payload")
        if self.prose_sha256 != _digest(self.prose.model_dump(mode="json")):
            raise ValueError("prose hash does not match the rendered candidate")
        return self


class SimpleComparisonSource(BaseModel):
    """Freeze the paper, both candidates, and the reviewers before anyone reviews."""

    model_config = ConfigDict(extra="forbid")
    format_version: Literal[1] = 1
    comparison_id: str = Field(pattern=IDENTIFIER_PATTERN)
    paper_id: str = Field(pattern=IDENTIFIER_PATTERN)
    split: Split
    paper: PaperInfo
    source_hashes: dict[str, str] = Field(default_factory=dict)
    ground_truth_revision: int = Field(ge=1)
    randomization_seed_sha256: str
    created_at: datetime
    candidates: dict[Label, SimpleCandidate]
    reviewer_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_design(self) -> SimpleComparisonSource:
        if set(self.candidates) != {"A", "B"}:
            raise ValueError("a comparison must contain candidates A and B")
        origins = sorted(candidate.origin for candidate in self.candidates.values())
        if origins.count("ground_truth") != 1:
            raise ValueError("exactly one candidate must be the ground truth")
        if len(set(self.reviewer_ids)) != len(self.reviewer_ids):
            raise ValueError("reviewers must be unique")
        return self


class SimplePreference(BaseModel):
    """One reviewer's immutable choice, bound to the two texts they were shown."""

    model_config = ConfigDict(extra="forbid")
    comparison_id: str = Field(pattern=IDENTIFIER_PATTERN)
    reviewer_id: str = Field(min_length=1, max_length=200)
    candidate_hashes: dict[Label, str]
    choice: Label
    justification: str = Field(max_length=5000)
    active_seconds: int = Field(default=0, ge=0)
    submitted_at: datetime

    @model_validator(mode="after")
    def require_justification(self) -> SimplePreference:
        if len(self.justification.strip()) < MINIMUM_JUSTIFICATION:
            raise ValueError(
                "explain your choice in at least "
                f"{MINIMUM_JUSTIFICATION} characters before submitting"
            )
        if set(self.candidate_hashes) != {"A", "B"}:
            raise ValueError("a preference requires the hashes of A and B")
        return self


class SimpleComparisonStorage(Protocol):
    """Persist immutable comparison sources and one preference per reviewer."""

    def create(self, source: SimpleComparisonSource) -> None: ...
    def list_ids(self) -> list[str]: ...
    def load_source(self, comparison_id: str) -> SimpleComparisonSource: ...
    def load_preference(
        self, comparison_id: str, reviewer_id: str
    ) -> SimplePreference | None: ...
    def save_preference(self, preference: SimplePreference) -> None: ...


class LocalSimpleComparisonStorage:
    """Write each source and preference once, as JSON files."""

    def __init__(self, root: Path):
        self.root = root.resolve() / "simple_comparison_state"

    def _source(self, comparison_id: str) -> Path:
        return self.root / "sources" / f"{comparison_id}.json"

    def _preference(self, comparison_id: str, reviewer_id: str) -> Path:
        return (
            self.root / "preferences" / comparison_id / f"{_digest(reviewer_id)}.json"
        )

    def create(self, source: SimpleComparisonSource) -> None:
        try:
            write_json_exclusive(
                self._source(source.comparison_id), source.model_dump(mode="json")
            )
        except FileExistsError as error:
            raise ValueError("comparison already exists") from error

    def list_ids(self) -> list[str]:
        return sorted(path.stem for path in (self.root / "sources").glob("*.json"))

    def load_source(self, comparison_id: str) -> SimpleComparisonSource:
        path = self._source(comparison_id)
        if not path.exists():
            raise FileNotFoundError(f"simple comparison {comparison_id} not found")
        return SimpleComparisonSource.model_validate_json(
            path.read_text(encoding="utf-8")
        )

    def load_preference(
        self, comparison_id: str, reviewer_id: str
    ) -> SimplePreference | None:
        path = self._preference(comparison_id, reviewer_id)
        return (
            SimplePreference.model_validate_json(path.read_text(encoding="utf-8"))
            if path.exists()
            else None
        )

    def save_preference(self, preference: SimplePreference) -> None:
        try:
            write_json_exclusive(
                self._preference(preference.comparison_id, preference.reviewer_id),
                preference.model_dump(mode="json"),
            )
        except FileExistsError as error:
            raise ValueError(
                "your choice for this comparison is already submitted"
            ) from error


def candidate(
    origin: Origin,
    extraction: dict[str, Any],
    blocks: list[EvidenceBlock],
    paper: PaperInfo,
) -> SimpleCandidate:
    """Validate one extraction against the current schema and render its prose."""

    try:
        StudyExtraction.model_validate(extraction)
    except ValueError as error:
        raise ValueError(
            f"the {origin.replace('_', ' ')} does not match the current study schema; "
            "use an extraction made with the current schema"
        ) from error
    prose = render_prose(extraction, blocks, paper)
    return SimpleCandidate(
        origin=origin,
        native_payload=extraction,
        native_sha256=_digest(extraction),
        document_sha256=_digest([block.model_dump(mode="json") for block in blocks]),
        prose=prose,
        prose_sha256=_digest(prose.model_dump(mode="json")),
    )


def build_simple_comparison(
    *,
    comparison_id: str,
    paper_id: str,
    split: Split,
    paper: PaperInfo,
    ground_truth: SimpleCandidate,
    extractor: SimpleCandidate,
    ground_truth_revision: int,
    reviewer_ids: list[str],
    randomization_seed: str,
    source_hashes: dict[str, str] | None = None,
) -> SimpleComparisonSource:
    """Randomize which candidate is A without storing the seed itself."""

    if not randomization_seed.strip():
        raise ValueError("a randomization seed is required")
    labels: list[Label] = ["A", "B"]
    random.Random(f"{randomization_seed}:{comparison_id}").shuffle(labels)
    return SimpleComparisonSource(
        comparison_id=comparison_id,
        paper_id=paper_id,
        split=split,
        paper=paper,
        source_hashes=source_hashes or {},
        ground_truth_revision=ground_truth_revision,
        randomization_seed_sha256=hashlib.sha256(
            randomization_seed.encode()
        ).hexdigest(),
        created_at=_now(),
        candidates={labels[0]: ground_truth, labels[1]: extractor},
        reviewer_ids=sorted(
            {reviewer.strip() for reviewer in reviewer_ids if reviewer.strip()}
        ),
    )


class SimpleComparisonService:
    """Expose blinded prose to assigned reviewers and origins only after all submit."""

    def __init__(self, storage: SimpleComparisonStorage):
        self.storage = storage

    @staticmethod
    def _require_assigned(source: SimpleComparisonSource, reviewer_id: str) -> None:
        if reviewer_id not in source.reviewer_ids:
            raise PermissionError("this comparison is not assigned to you")

    def _submitted(self, source: SimpleComparisonSource) -> int:
        return sum(
            self.storage.load_preference(source.comparison_id, reviewer) is not None
            for reviewer in source.reviewer_ids
        )

    def list_for(
        self, reviewer_id: str, *, include_unassigned: bool = False
    ) -> list[dict[str, Any]]:
        """List assigned comparisons; administrators also see every comparison's progress."""

        result = []
        for comparison_id in self.storage.list_ids():
            source = self.storage.load_source(comparison_id)
            assigned = reviewer_id in source.reviewer_ids
            if not (assigned or include_unassigned):
                continue
            item: dict[str, Any] = {
                "comparison_id": comparison_id,
                "paper_id": source.paper_id,
                "split": source.split,
                "title": source.paper.title or source.paper_id,
                "assigned": assigned,
                "submitted": assigned
                and self.storage.load_preference(comparison_id, reviewer_id)
                is not None,
            }
            if include_unassigned:
                item["progress"] = {
                    "submitted": self._submitted(source),
                    "assigned": len(source.reviewer_ids),
                }
            result.append(item)
        return result

    def open(self, comparison_id: str, reviewer_id: str) -> dict[str, Any]:
        """Return the blinded view: paper, prose of A and B, and any own submission."""

        source = self.storage.load_source(comparison_id)
        self._require_assigned(source, reviewer_id)
        preference = self.storage.load_preference(comparison_id, reviewer_id)
        return {
            "comparison_id": comparison_id,
            "paper_id": source.paper_id,
            "split": source.split,
            "paper": source.paper.model_dump(mode="json"),
            "sources": sorted(source.source_hashes),
            "candidates": {
                label: {
                    "prose": item.prose.model_dump(mode="json"),
                    "prose_sha256": item.prose_sha256,
                }
                for label, item in sorted(source.candidates.items())
            },
            "preference": preference.model_dump(mode="json") if preference else None,
        }

    def save(
        self, comparison_id: str, reviewer_id: str, payload: object
    ) -> dict[str, Any]:
        """Store one choice with its justification; it cannot be changed afterwards."""

        if not isinstance(payload, dict):
            raise ValueError("the choice must be a JSON object")
        source = self.storage.load_source(comparison_id)
        self._require_assigned(source, reviewer_id)
        if self.storage.load_preference(comparison_id, reviewer_id) is not None:
            raise ValueError("your choice for this comparison is already submitted")
        shown = payload.get("candidate_hashes")
        expected = {
            label: item.prose_sha256 for label, item in source.candidates.items()
        }
        if shown is not None and shown != expected:
            raise ValueError("the comparison changed since you opened it; reload it")
        preference = SimplePreference.model_validate(
            {
                "choice": payload.get("choice"),
                "justification": str(payload.get("justification") or ""),
                "active_seconds": int(payload.get("active_seconds") or 0),
                "comparison_id": comparison_id,
                "reviewer_id": reviewer_id,
                "candidate_hashes": expected,
                "submitted_at": _now(),
            }
        )
        self.storage.save_preference(preference)
        return preference.model_dump(mode="json")

    def export(self, comparison_id: str) -> dict[str, Any]:
        """Reveal origins and every choice once all assigned reviewers submitted."""

        source = self.storage.load_source(comparison_id)
        preferences = [
            self.storage.load_preference(comparison_id, reviewer)
            for reviewer in source.reviewer_ids
        ]
        submitted = [item for item in preferences if item is not None]
        if len(submitted) != len(source.reviewer_ids):
            raise ValueError(
                f"{len(source.reviewer_ids) - len(submitted)} assigned reviewer(s) "
                "have not submitted yet; origins stay hidden until all have"
            )
        origins = {label: item.origin for label, item in source.candidates.items()}
        preferred = {origin: 0 for origin in origins.values()}
        for item in submitted:
            preferred[origins[item.choice]] += 1
        return {
            "comparison_id": comparison_id,
            "paper_id": source.paper_id,
            "split": source.split,
            "paper": source.paper.model_dump(mode="json"),
            "ground_truth_revision": source.ground_truth_revision,
            "source_hashes": source.source_hashes,
            "randomization_seed_sha256": source.randomization_seed_sha256,
            "origins": origins,
            "candidate_hashes": {
                label: {
                    "native_sha256": item.native_sha256,
                    "document_sha256": item.document_sha256,
                    "prose_sha256": item.prose_sha256,
                }
                for label, item in source.candidates.items()
            },
            "preferred_by_origin": preferred,
            "preferences": [
                {**item.model_dump(mode="json"), "chosen_origin": origins[item.choice]}
                for item in submitted
            ],
        }


def _blocks(document: object, what: str) -> list[EvidenceBlock]:
    raw = document.get("blocks") if isinstance(document, dict) else document
    if not isinstance(raw, list):
        raise ValueError(f"the {what} needs its document.json with evidence blocks")
    return [EvidenceBlock.model_validate(block) for block in raw]


def create_from_workbench(
    store: Any, payload: object, *, source_hashes: dict[str, str]
) -> SimpleComparisonSource:
    """Freeze finalized ground truth against the seed run or an uploaded run.

    ``payload`` holds ``comparison_id``, ``paper_id``, ``split``, ``reviewer_ids``,
    ``randomization_seed``, optional ``paper`` metadata, and ``extractor``: either
    ``{"kind": "seed"}`` for the run the paper was imported from, or
    ``{"kind": "run", "extraction": {...}, "document": {...}}`` for another run.
    """

    from review_workbench.ground_truth_export import build_ground_truth_export

    if not isinstance(payload, dict):
        raise ValueError("the comparison must be a JSON object")
    required = ("comparison_id", "paper_id", "reviewer_ids", "randomization_seed")
    missing = [key for key in required if not payload.get(key)]
    if missing:
        raise ValueError(f"missing comparison fields: {', '.join(missing)}")
    paper_id = str(payload["paper_id"])
    split = str(payload.get("split") or "dev")
    store.validate_identity(split, paper_id)
    if "main" not in source_hashes:
        raise ValueError("the comparison paper must have a main PDF in the review app")
    reviewers = payload["reviewer_ids"]
    if not isinstance(reviewers, list):
        raise ValueError("reviewer_ids must be a list")

    try:
        export = build_ground_truth_export(store, split, paper_id)
    except ValueError as error:
        raise ValueError(
            f"only finalized ground truth can be compared: {error}"
        ) from error
    revision = store.storage.load_revision(split, paper_id)
    truth = export.ground_truth.model_dump(mode="json")
    paper = PaperInfo.model_validate(
        payload.get("paper")
        or {"title": truth["paper"].get("title"), "doi": truth["paper"].get("doi")}
    )
    truth_blocks = _blocks(
        store.storage.load_evidence(split, paper_id, revision.evidence_version),
        "ground truth",
    )

    extractor = payload.get("extractor") or {"kind": "seed"}
    if not isinstance(extractor, dict):
        raise ValueError("extractor must be a JSON object")
    if extractor.get("kind") == "seed":
        source = store.storage.load_source(split, paper_id)
        origin: Origin = "extractor_seed"
        extraction = source.seed_extraction
        extractor_blocks = _blocks(source.document, "imported seed run")
    elif extractor.get("kind") == "run":
        origin = "extractor_run"
        extraction = extractor.get("extraction")
        if not isinstance(extraction, dict):
            raise ValueError("the uploaded run needs its extraction.json")
        extractor_blocks = _blocks(extractor.get("document"), "uploaded run")
    else:
        raise ValueError('extractor kind must be "seed" or "run"')

    return build_simple_comparison(
        comparison_id=str(payload["comparison_id"]),
        paper_id=paper_id,
        split=split,  # type: ignore[arg-type]
        paper=paper,
        ground_truth=candidate("ground_truth", truth, truth_blocks, paper),
        extractor=candidate(origin, extraction, extractor_blocks, paper),
        ground_truth_revision=revision.revision,
        reviewer_ids=[str(reviewer) for reviewer in reviewers],
        randomization_seed=str(payload["randomization_seed"]),
        source_hashes=source_hashes,
    )


def _require_admin(user: dict[str, Any]) -> None:
    if user.get("role") != "admin":
        raise PermissionError("administrator access is required")


def get_route(
    service: SimpleComparisonService, parts: list[str], user: dict[str, Any]
) -> dict[str, Any] | None:
    """Answer the GET routes of this study; None when the path is not one of them.

    * ``/api/simple-comparisons``: comparisons assigned to the user (all for admins)
    * ``/api/simple-comparisons/<id>``: the blinded view
    * ``/api/simple-comparison-export/<id>``: origins and choices (admins only)
    """

    if parts == ["api", "simple-comparisons"]:
        return {
            "comparisons": service.list_for(
                user["id"], include_unassigned=user.get("role") == "admin"
            )
        }
    if len(parts) == 3 and parts[:2] == ["api", "simple-comparisons"]:
        return service.open(parts[2], user["id"])
    if len(parts) == 3 and parts[:2] == ["api", "simple-comparison-export"]:
        _require_admin(user)
        return service.export(parts[2])
    return None


def post_route(
    application: Any,
    parts: list[str],
    user: dict[str, Any],
    read_json: Any,
) -> dict[str, Any] | None:
    """Answer the POST routes of this study; None when the path is not one of them.

    * ``/api/simple-comparisons``: create a comparison (admins only)
    * ``/api/simple-comparisons/<id>/preference``: submit the user's choice
    """

    if parts == ["api", "simple-comparisons"]:
        _require_admin(user)
        return application.create_simple_comparison(read_json())
    if (
        len(parts) == 4
        and parts[:2] == ["api", "simple-comparisons"]
        and parts[3] == "preference"
    ):
        return application.simple_comparisons.save(parts[2], user["id"], read_json())
    return None
