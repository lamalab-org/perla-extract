"""Compare scientific requirements shared by record pairing and fact credit.

Context declares which specimen, protocol or recipe a claim belongs to. Numeric
requirements use the same unit conversion and tolerance as the claim itself.
Missing units stay unknown; unordered collections retain their multiplicity.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ReportedValue, SubjectContext
from .scoring_claims import equal_claims, normalize_claim, scientific_value_text


@dataclass(frozen=True)
class UnorderedContext:
    """Mark context where order is irrelevant but duplicate entries still matter."""

    items: tuple[object, ...]


@dataclass(frozen=True)
class CohortSize:
    """A missing count is metadata loss; two reported different counts conflict.

    This applies only after a cohort has been paired by family, label and statistic.
    The sample-size fact itself still requires exact integer equality.
    """

    count: int | None


@dataclass(frozen=True)
class RecordReference:
    """A paired canonical record key, or an explicitly side-local unresolved key."""

    key: str
    unmatched_side: str | None = None


@dataclass(frozen=True)
class ContextComponent:
    name: str
    value: object
    path: str | None = None


@dataclass(frozen=True)
class FactContext:
    """Compare named scientific requirements independently of serialization order."""

    components: tuple[ContextComponent, ...] = ()

    def __post_init__(self) -> None:
        names = [component.name for component in self.components]
        if len(names) != len(set(names)):
            raise ValueError("Context component names must be unique within a scope")

    @classmethod
    def of(cls, path: str | None = None, **values: object) -> FactContext:
        return cls(
            tuple(
                ContextComponent(
                    name, value, f"{path}/{name}" if path is not None else None
                )
                for name, value in values.items()
            )
        )

    def extend(self, other: FactContext) -> FactContext:
        return FactContext(self.components + other.components)

    @property
    def values(self) -> tuple[object, ...]:
        return tuple(component.value for component in self.components)


def equal_value(left: object, right: object, relative: float, absolute: float) -> bool:
    """Compare claims conservatively; missing units are not assumed dimensionless."""

    if isinstance(left, CohortSize) and isinstance(right, CohortSize):
        return left.count is None or right.count is None or left.count == right.count
    if isinstance(left, ReportedValue) and isinstance(right, ReportedValue):
        return equal_claims(
            normalize_claim(left), normalize_claim(right), relative, absolute
        )
    if isinstance(left, str) and isinstance(right, str):
        return scientific_value_text(left) == scientific_value_text(right)
    if isinstance(left, FactContext) and isinstance(right, FactContext):
        return not compare_context(left, right, relative, absolute)
    if isinstance(left, UnorderedContext) and isinstance(right, UnorderedContext):
        if len(left.items) != len(right.items):
            return False
        # Augmenting paths avoid greedy errors when tolerance neighborhoods overlap.
        edges = [
            [
                j
                for j, b in enumerate(right.items)
                if equal_value(a, b, relative, absolute)
            ]
            for a in left.items
        ]
        assigned: dict[int, int] = {}

        def assign(i: int, visited: set[int]) -> bool:
            for j in edges[i]:
                if j in visited:
                    continue
                visited.add(j)
                if j not in assigned or assign(assigned[j], visited):
                    assigned[j] = i
                    return True
            return False

        return all(assign(i, set()) for i in range(len(left.items)))
    if isinstance(left, tuple) and isinstance(right, tuple):
        return len(left) == len(right) and all(
            equal_value(a, b, relative, absolute) for a, b in zip(left, right)
        )
    return type(left) is type(right) and left == right


@dataclass(frozen=True)
class ContextDifference:
    name: str
    truth_path: str | None
    predicted_path: str | None
    truth_value: object
    predicted_value: object
    reason: str = "conflict"


def compare_context(
    left: FactContext, right: FactContext, relative: float, absolute: float
) -> tuple[ContextDifference, ...]:
    """Compare requirements by name; a missing requirement cannot match by position."""

    differences: list[ContextDifference] = []
    left_fields = {c.name: c for c in left.components}
    right_fields = {c.name: c for c in right.components}
    for name in sorted(left_fields.keys() | right_fields.keys()):
        a, b = left_fields.get(name), right_fields.get(name)
        if a is not None and b is not None:
            if isinstance(a.value, FactContext) and isinstance(b.value, FactContext):
                differences.extend(
                    compare_context(a.value, b.value, relative, absolute)
                )
                continue
            if equal_value(a.value, b.value, relative, absolute):
                continue
        component = a if a is not None else b
        assert component is not None
        differences.append(
            ContextDifference(
                component.name,
                a.path if a is not None else None,
                b.path if b is not None else None,
                a.value if a is not None else None,
                b.value if b is not None else None,
                "missing_required"
                if a is None or b is None or a.value is None or b.value is None
                else "conflict",
            )
        )
    return tuple(differences)


def has_subject_identity(subject: SubjectContext) -> bool:
    from .scoring_vocabulary import treatment_condition_name

    return any(
        treatment_condition_name(value.name) is not None for value in subject.conditions
    )


def subject_context(
    subject: SubjectContext, path: str | None = None, *, identity_only: bool = False
) -> FactContext:
    """Preserve treatment meaning and units; labels and outcomes are not identity."""
    from .scoring_vocabulary import (
        material_name,
        property_name,
        treatment_condition_name,
    )

    return FactContext.of(
        path,
        treatment_stage=subject.treatment_stage,
        treated_material=material_name(subject.treated_material)
        if subject.treated_material
        else None,
        conditions=UnorderedContext(
            tuple(
                (
                    treatment_condition_name(value.name) or property_name(value.name),
                    value,
                )
                for value in subject.conditions
                if not identity_only or treatment_condition_name(value.name) is not None
            )
        ),
    )
