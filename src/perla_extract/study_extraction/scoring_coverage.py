"""Reference completeness declarations, independent of any model's predictions.

Matching always retains every supplied fact. These declarations determine whether
agreement can be interpreted as extraction precision/F1, rather than causing
unannotated predictions to disappear from a denominator.
"""

from typing import Literal, get_args

from pydantic import Field, model_validator

from .models import StrictModel

FactGroup = Literal[
    "performance", "material_device_identity", "processing", "stability"
]
FACT_GROUPS: tuple[FactGroup, ...] = get_args(FactGroup)


ReviewStatus = Literal["complete", "partial", "unreviewed", "not_applicable"]


class DomainCoverage(StrictModel):
    text_and_tables: ReviewStatus = "unreviewed"
    figures: ReviewStatus = "unreviewed"
    attribution_context: ReviewStatus = "unreviewed"
    evidence: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def evidence_for_decisions(self) -> "DomainCoverage":
        if (
            any(
                getattr(self, field) != "unreviewed"
                for field in ("text_and_tables", "figures", "attribution_context")
            )
            and not self.evidence.strip()
        ):
            raise ValueError("Coverage decisions require a source/review rationale")
        return self

    def complete(self) -> bool:
        return all(
            getattr(self, field) in {"complete", "not_applicable"}
            for field in ("text_and_tables", "figures", "attribution_context")
        )


class ReferenceCoverage(StrictModel):
    domains: dict[FactGroup, DomainCoverage] = Field(
        default_factory=lambda: {group: DomainCoverage() for group in FACT_GROUPS}
    )

    @model_validator(mode="after")
    def complete_domain_roster(self) -> "ReferenceCoverage":
        if set(self.domains) != set(FACT_GROUPS):
            raise ValueError("Coverage must declare every scientific domain")
        return self

    def complete(self) -> bool:
        return all(domain.complete() for domain in self.domains.values())
