"""Compare scientific requirements shared by record pairing and fact credit.

Context declares which specimen, protocol or recipe a claim belongs to. Numeric
requirements use the same unit conversion and tolerance as the claim itself.
Missing units stay unknown; unordered collections retain their multiplicity.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import ReportedValue
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
    """Store ordered scientific requirements with their diagnostic source paths.

    Component values determine equality. Names and paths identify the requirement
    responsible for a mismatch without becoming additional matching criteria.
    """

    components: tuple[ContextComponent, ...] = ()

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
    if isinstance(left, FactContext):
        left = left.values
    if isinstance(right, FactContext):
        right = right.values
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


def compare_context(
    left: FactContext, right: FactContext, relative: float, absolute: float
) -> tuple[ContextDifference, ...]:
    """Compare the original ordered requirements, retaining their differing fields."""

    differences: list[ContextDifference] = []
    for i in range(max(len(left.components), len(right.components))):
        a = left.components[i] if i < len(left.components) else None
        b = right.components[i] if i < len(right.components) else None
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
            )
        )
    return tuple(differences)
