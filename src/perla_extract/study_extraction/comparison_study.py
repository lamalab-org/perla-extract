"""Check a paired experiment against its declared cohort and interventions.

The study file is ordinary JSON written before running experiments. It does not
schedule requests or establish when the researcher made their decisions. Its hash
and full contents accompany comparisons so the intended study remains inspectable.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, JsonValue, field_validator, model_validator

from .models import StrictModel


def same_setting(left: object, right: object) -> bool:
    """Compare JSON settings without treating boolean true as numeric one."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            same_setting(left[name], right[name]) for name in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            same_setting(a, b) for a, b in zip(left, right)
        )
    return left == right


class FactorContrast(StrictModel):
    control: JsonValue
    treatment: JsonValue

    @model_validator(mode="after")
    def different_values(self) -> FactorContrast:
        json.dumps([self.control, self.treatment], allow_nan=False)
        if same_setting(self.control, self.treatment):
            raise ValueError(
                "a declared factor must differ between control and treatment"
            )
        return self


class ComparisonStudy(StrictModel):
    """One common cohort, repeat count and intervention for both arms."""

    paper_ids: list[str] = Field(min_length=1)
    repeats: int = Field(strict=True, ge=1)
    factors: dict[str, FactorContrast]

    @field_validator("paper_ids")
    @classmethod
    def distinct_papers(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or value != value.strip() for value in values):
            raise ValueError(
                "paper_ids must be nonempty and have no surrounding whitespace"
            )
        if len(set(values)) != len(values):
            raise ValueError("paper_ids must not contain duplicates")
        return values

    @field_validator("factors")
    @classmethod
    def named_factors(
        cls, values: dict[str, FactorContrast]
    ) -> dict[str, FactorContrast]:
        if any(not name.strip() or name != name.strip() for name in values):
            raise ValueError(
                "factor names must be nonempty and have no surrounding whitespace"
            )
        return values

    def expected_pairs(self) -> set[tuple[str, int]]:
        return {
            (paper, repeat)
            for paper in self.paper_ids
            for repeat in range(1, self.repeats + 1)
        }


def load_study(path: Path) -> ComparisonStudy:
    """Reject malformed or misspelled declarations before inspecting run results."""
    return ComparisonStudy.model_validate_json(path.read_bytes())


def cohort_issues(study: ComparisonStudy, pairs: set[tuple[str, int]]) -> list[str]:
    expected = study.expected_pairs()
    issues = []
    for label, members in (
        ("Missing", expected - pairs),
        ("Unexpected", pairs - expected),
    ):
        if members:
            detail = ", ".join(f"{paper}/{repeat}" for paper, repeat in sorted(members))
            issues.append(f"{label} study paper/repeat pairs: {detail}")
    return issues


def setting_issues(
    study: ComparisonStudy,
    configurations: dict[tuple[str, int, str], dict],
) -> list[str]:
    """Require declared arm values and a stable method across papers and repeats.

    Inputs are flattened scientific settings, excluding source paths and other
    per-run bookkeeping. Accounting-only attempts are intentionally not inputs:
    they retain expenditure without becoming additional quality observations.
    """
    issues = []
    first_by_arm: dict[str, tuple[tuple[str, int, str], dict]] = {}
    for key, settings in sorted(configurations.items()):
        paper, repeat, arm = key
        for name, contrast in study.factors.items():
            expected = getattr(contrast, arm)
            if name not in settings or not same_setting(settings[name], expected):
                actual = repr(settings[name]) if name in settings else "<missing>"
                issues.append(
                    f"{paper}/{repeat}/{arm}: study factor {name} expected "
                    f"{expected!r}, found {actual}"
                )
        if arm not in first_by_arm:
            first_by_arm[arm] = key, settings
            continue
        first_key, first_settings = first_by_arm[arm]
        changed = sorted(
            name
            for name in first_settings.keys() | settings.keys()
            if name not in first_settings
            or name not in settings
            or not same_setting(first_settings[name], settings[name])
        )
        if changed:
            issues.append(
                f"{paper}/{repeat}/{arm}: settings differ from "
                f"{first_key[0]}/{first_key[1]}/{arm}: {', '.join(changed)}"
            )
    return issues
