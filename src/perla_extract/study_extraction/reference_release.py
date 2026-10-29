"""Check the fixed paper roster before presenting a dataset benchmark result."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from .evaluation import BenchmarkProvenance


class ReferencePaper(BaseModel):
    """Keep release metadata while checking the identity fields used for scoring."""

    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    paper_id: str = Field(min_length=1)
    ground_truth_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceRelease(BaseModel):
    """Pin the exact reference roster, not a subset that happened to finish running.

    Additional metadata is retained for the release digest. The release's original
    matcher version is historical: rescoring it with a newer scorer must not require
    rewriting the reference or pretending the old results used the new method.
    """

    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)
    release: str = Field(min_length=1)
    split: str = Field(min_length=1)
    paper_count: int = Field(ge=1)
    papers: Annotated[list[ReferencePaper], Field(min_length=1)]

    @model_validator(mode="after")
    def complete_unique_roster(self) -> ReferenceRelease:
        ids = [paper.paper_id for paper in self.papers]
        if len(ids) != self.paper_count or len(set(ids)) != len(ids):
            raise ValueError(
                "reference release count or unique paper roster is invalid"
            )
        return self

    def verify(self, provenance: Sequence[BenchmarkProvenance | None]) -> None:
        """Refuse missing papers, unexpected papers, duplicates and changed truth."""

        if any(item is None for item in provenance):
            raise ValueError(
                "a fixed release requires reference provenance for every report"
            )
        actual = {item.paper_id: item for item in provenance if item is not None}
        expected = {paper.paper_id: paper.ground_truth_sha256 for paper in self.papers}
        if len(actual) != len(provenance):
            raise ValueError("duplicate paper in benchmark reports")
        if actual.keys() != expected.keys():
            raise ValueError(
                f"reference roster mismatch: missing={sorted(expected.keys() - actual.keys())}; "
                f"unexpected={sorted(actual.keys() - expected.keys())}"
            )
        wrong = sorted(
            paper_id
            for paper_id, item in actual.items()
            if item.ground_truth_sha256 != expected[paper_id]
            or item.split != self.split
        )
        if wrong:
            raise ValueError(
                f"reports do not use the selected reference release: {wrong}"
            )

    def digest(self) -> str:
        """Bind the aggregate to all supplied release metadata, including its roster."""

        content = (
            json.dumps(
                self.model_dump(mode="json"),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
