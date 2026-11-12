"""Outcomes of pipeline operations, including failures handled by a fallback.

A stage can return normally while retaining a failed outcome. For example, a failed
refinement keeps its input draft; that fallback does not make the refinement succeed.
The event journal and saved report serialize the same outcome.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

StageStatus = Literal[
    "complete", "needs_review", "partial", "failed", "blocked", "skipped", "interrupted"
]


@dataclass
class StageOutcome:
    stage: str
    stage_id: str
    status: StageStatus = "complete"
    errors: list[str] = field(default_factory=list)
    fallback: str | None = None
    decisions: list[dict[str, object]] = field(default_factory=list)
    error_type: str | None = None
    elapsed_seconds: float = 0.0

    def record(
        self,
        status: StageStatus,
        *,
        errors: list[str] | None = None,
        fallback: str | None = None,
    ) -> None:
        """Record a handled result before leaving the journal's stage context."""
        self.status = status
        self.errors = list(errors or [])
        self.fallback = fallback

    def to_dict(self) -> dict[str, object]:
        return asdict(self)
