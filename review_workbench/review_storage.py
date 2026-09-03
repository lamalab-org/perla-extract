"""Atomic persistence contracts for collaborative ground-truth review."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from perla_extract.study_extraction.artifacts import write_json_exclusive


class StaleRevisionError(ValueError):
    """Signal that another reviewer committed the requested next revision first."""


class ReviewRevision(BaseModel):
    """Keep compiled truth and its complete audit history in one atomic snapshot."""

    model_config = ConfigDict(extra="forbid", strict=True)

    revision: int = Field(ge=1)
    ground_truth: dict[str, Any]
    events: list[dict[str, Any]]

    @model_validator(mode="after")
    def validate_history(self) -> ReviewRevision:
        """Reject snapshots whose advertised revision and audit history diverge."""

        event_revisions = [event.get("revision") for event in self.events]
        if event_revisions != list(range(1, self.revision + 1)):
            raise ValueError("events must contain every revision exactly once in order")
        return self


class ReviewPaperSource(BaseModel):
    """Store immutable inputs once instead of copying them into every revision."""

    model_config = ConfigDict(extra="forbid", strict=True)

    seed_extraction: dict[str, Any]
    manifest: dict[str, Any]
    document: Any = None
    initial_revision: ReviewRevision

    @model_validator(mode="after")
    def validate_initial_revision(self) -> ReviewPaperSource:
        """Keep the immutable seed identical to the truth at revision one."""

        if self.initial_revision.revision != 1:
            raise ValueError("a paper source must start at revision 1")
        if self.initial_revision.ground_truth != self.seed_extraction:
            raise ValueError("the initial truth must equal the immutable seed")
        return self


class ReviewStateStorage(Protocol):
    """Expose the small persistence boundary needed by the review state machine."""

    def create(self, split: str, paper_id: str, source: ReviewPaperSource) -> None: ...

    def load_source(self, split: str, paper_id: str) -> ReviewPaperSource: ...

    def load_revision(self, split: str, paper_id: str) -> ReviewRevision: ...

    def compare_and_swap(
        self,
        split: str,
        paper_id: str,
        expected_revision: int,
        revision: ReviewRevision,
    ) -> None: ...

    def list_paper_ids(self, split: str) -> list[str]: ...

    def list_paper_heads(self, split: str) -> list[tuple[str, int]]: ...


class LocalReviewStateStorage:
    """Implement atomic revisions with immutable files in the review directory."""

    def __init__(self, root: Path):
        self.root = root.resolve() / "state"

    def _source_path(self, split: str, paper_id: str) -> Path:
        return self.root / "sources" / split / f"{paper_id}.json"

    def _revision_dir(self, split: str, paper_id: str) -> Path:
        return self.root / "revisions" / split / paper_id

    def _revision_path(self, split: str, paper_id: str, revision: int) -> Path:
        return self._revision_dir(split, paper_id) / f"{revision:08d}.json"

    @staticmethod
    def _read(path: Path, model: type[BaseModel]) -> Any:
        if not path.exists():
            raise FileNotFoundError(path)
        return model.model_validate_json(path.read_text(encoding="utf-8"))

    def create(self, split: str, paper_id: str, source: ReviewPaperSource) -> None:
        try:
            write_json_exclusive(
                self._source_path(split, paper_id), source.model_dump(mode="json")
            )
        except FileExistsError as error:
            raise ValueError("paper already exists") from error

    def load_source(self, split: str, paper_id: str) -> ReviewPaperSource:
        return self._read(self._source_path(split, paper_id), ReviewPaperSource)

    def load_revision(self, split: str, paper_id: str) -> ReviewRevision:
        source = self.load_source(split, paper_id)
        paths = sorted(self._revision_dir(split, paper_id).glob("*.json"))
        if not paths:
            return source.initial_revision
        return self._read(paths[-1], ReviewRevision)

    def list_paper_heads(self, split: str) -> list[tuple[str, int]]:
        """Return lightweight revision pointers for paper-list views."""

        return [
            (paper_id, self.load_revision(split, paper_id).revision)
            for paper_id in self.list_paper_ids(split)
        ]

    def compare_and_swap(
        self,
        split: str,
        paper_id: str,
        expected_revision: int,
        revision: ReviewRevision,
    ) -> None:
        current = self.load_revision(split, paper_id)
        if current.revision != expected_revision:
            raise StaleRevisionError(
                f"stale revision {expected_revision}; current revision is {current.revision}"
            )
        if revision.revision != expected_revision + 1:
            raise ValueError(
                "new revision must immediately follow the expected revision"
            )
        try:
            write_json_exclusive(
                self._revision_path(split, paper_id, revision.revision),
                revision.model_dump(mode="json"),
            )
        except FileExistsError as error:
            current = self.load_revision(split, paper_id)
            raise StaleRevisionError(
                f"stale revision {expected_revision}; current revision is {current.revision}"
            ) from error

    def list_paper_ids(self, split: str) -> list[str]:
        return sorted(
            path.stem for path in (self.root / "sources" / split).glob("*.json")
        )
