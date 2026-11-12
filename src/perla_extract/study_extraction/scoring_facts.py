"""Project the scientific schema into contextual facts for deterministic scoring.

This is a scoring contract, not extraction logic. It selects scientific fields and
retains their owners; labels, evidence wording, generated IDs and empty placeholders
do not earn points. No chemical identities or missing relationships are inferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .models import (
    MaterialConstituent,
    PopulationStatistic,
    ReportedValue,
    StabilityTest,
    StudyExtraction,
)
from .scoring_claims import (
    compound_quantity as compound_quantity,
)
from .scoring_claims import (
    equal_claims,
    normalize_claim,
)
from .scoring_claims import (
    is_plain_number as is_plain_number,
)
from .scoring_claims import (
    scientific_text as scientific_text,
)
from .scoring_claims import (
    scientific_value_text as scientific_value_text,
)

FactGroup = Literal[
    "performance", "population", "stability", "composition", "stack", "processing"
]
FACT_GROUPS: tuple[FactGroup, ...] = (
    "performance",
    "population",
    "stability",
    "composition",
    "stack",
    "processing",
)

# Only standard metric spellings are aliases. Unfamiliar properties still score,
# but arbitrary semantic paraphrases and chemical synonyms require adjudication.
METRIC_ALIASES = {
    "powerconversionefficiency": "pce",
    "opencircuitvoltage": "voc",
    "shortcircuitcurrentdensity": "jsc",
    "fillfactor": "ff",
}
DEFAULT_OPERATION_ALIASES = {"thermal annealing": "annealing"}


def operation_name(value: str, aliases: dict[str, str]) -> str:
    """Use only frozen, explicit equivalences; never infer prose similarity."""

    normalized = scientific_text(value).casefold()
    return aliases.get(normalized, normalized)


def property_name(name: str) -> str:
    key = re.sub(r"[\s_-]+", "", scientific_text(name).casefold())
    return METRIC_ALIASES.get(key, key)


def scientific_descriptor(record: object) -> str | None:
    """Retain identity that this schema stores only in a cohort/specimen label.

    A population's treatment arm and an unlinked stability specimen cannot be
    identified by outcome values. Normalize typography, not semantic meaning.
    Other display labels are deliberately not part of this rule. Unknown synonyms
    remain disagreements; the scorer must not guess equivalence or change truth.
    """

    if isinstance(record, PopulationStatistic):
        return scientific_text(record.label)
    if isinstance(record, StabilityTest) and record.device_id is None:
        return scientific_text(record.specimen_label)
    return None


@dataclass(frozen=True)
class UnorderedContext:
    """Mark context where order is irrelevant but duplicate entries still matter."""

    items: tuple[object, ...]


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
    """Name the existing ordered requirements without adding equality constraints.

    Names and source paths explain the requirements. Only their ordered values
    determine equality, exactly as the former positional tuples did.
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
class ScientificFact:
    group: FactGroup
    path: str
    owner: RecordReference
    name: str
    value: object
    context: FactContext


@dataclass(frozen=True)
class ContextDifference:
    name: str
    truth_path: str | None
    predicted_path: str | None
    truth_value: object
    predicted_value: object


@dataclass(frozen=True, slots=True)
class FactComparison:
    """The decision used by all score views and their optional explanation."""

    value_equal: bool
    context_equal: bool
    ambiguity_blocked: bool
    context_differences: tuple[ContextDifference, ...]

    def eligible(self, view: str) -> bool:
        if view == "value":
            return self.value_equal
        return (
            not self.ambiguity_blocked
            and self.context_equal
            and (view == "attribution" or self.value_equal)
        )


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


def compare_facts(
    left: ScientificFact,
    right: ScientificFact,
    relative: float,
    absolute: float,
    *,
    ambiguity_blocked: bool,
    explain: bool = False,
) -> FactComparison:
    # Ordinary scoring needs only booleans and retains the ambiguity short circuit.
    # Detailed fields are collected solely for requested explanation rows/columns.
    differences = (
        compare_context(left.context, right.context, relative, absolute)
        if explain
        else ()
    )
    context_equal = (
        not differences
        if explain
        else not ambiguity_blocked
        and equal_value(left.context, right.context, relative, absolute)
    )
    return FactComparison(
        value_equal=equal_value(left.value, right.value, relative, absolute),
        context_equal=context_equal,
        ambiguity_blocked=ambiguity_blocked,
        context_differences=differences,
    )


@dataclass(frozen=True)
class ScientificScope:
    """Identify a nested object without using the very outcomes being evaluated."""

    owner: RecordReference
    kind: str
    path: str
    identity: tuple[object, ...] | FactContext


@dataclass
class FactProjection:
    facts: list[ScientificFact]
    scopes: list[ScientificScope]

    def ambiguous_scopes(
        self, relative: float, absolute: float
    ) -> list[tuple[str, str]]:
        """Find indistinguishable containers, e.g. repeated unsequenced annealing steps."""

        buckets: dict[tuple[RecordReference, str], list[ScientificScope]] = {}
        for scope in self.scopes:
            buckets.setdefault((scope.owner, scope.kind), []).append(scope)
        return [
            (left.path, right.path)
            for scopes in buckets.values()
            for i, left in enumerate(scopes)
            for right in scopes[i + 1 :]
            if equal_value(left.identity, right.identity, relative, absolute)
        ]


def scientific_facts(
    study: StudyExtraction,
    identities: dict[str, str],
    side: str,
    operation_aliases: dict[str, str],
) -> FactProjection:
    """Select key fields while retaining device, layer, recipe and checkpoint context.

    ``identities`` contains only established top-level record pairings. Unmatched
    references stay side-specific so two broken links cannot accidentally agree.
    Paths refer to the original JSON, making every missed or extra fact inspectable.
    """

    facts: list[ScientificFact] = []
    scopes: list[ScientificScope] = []
    devices = {item.device_id: item for item in study.individual_devices}
    device_paths = {
        item.device_id: f"/individual_devices/{i}"
        for i, item in enumerate(study.individual_devices)
    }

    def reference(collection: str, identifier: str | None) -> RecordReference | None:
        if identifier is None:
            return None
        key = f"{collection}:{identifier}"
        return (
            RecordReference(identities[key])
            if key in identities
            else RecordReference(key, side)
        )

    def device_context(identifier: str | None, source_path: str) -> FactContext:
        device = devices.get(identifier) if identifier is not None else None
        return FactContext.of(
            source_path, device_id=reference("individual_devices", identifier)
        ).extend(
            FactContext.of(
                device_paths.get(identifier) if identifier is not None else None,
                family_id=reference("device_families", device.family_id)
                if device
                else None,
                variant=device.variant if device else None,
                champion_status=device.champion_status if device else None,
                selection_basis=device.selection_basis if device else None,
            )
        )

    def add(
        group: FactGroup,
        path: str,
        owner: RecordReference,
        name: str,
        value: object,
        context: FactContext = FactContext(),
    ) -> None:
        if value is not None and value != "not_reported" and value != "":
            facts.append(ScientificFact(group, path, owner, name, value, context))

    def quantities(
        group: FactGroup,
        path: str,
        owner: RecordReference,
        values: list[ReportedValue],
        context: FactContext = FactContext(),
    ) -> None:
        for i, value in enumerate(values):
            add(group, f"{path}/{i}", owner, property_name(value.name), value, context)

    def conditions(values: list[ReportedValue]) -> UnorderedContext:
        return UnorderedContext(tuple((property_name(v.name), v) for v in values))

    def chemicals(
        path: str,
        owner: RecordReference,
        constituents: list[MaterialConstituent],
        context: FactContext,
    ) -> None:
        for i, constituent in enumerate(constituents):
            base = f"{path}/{i}"
            scopes.append(
                ScientificScope(
                    owner,
                    "constituent",
                    base,
                    context.extend(
                        FactContext.of(
                            base,
                            name=constituent.name,
                            amount_name=property_name(constituent.amount.name)
                            if constituent.amount
                            else None,
                        )
                    ),
                )
            )
            add(
                "composition",
                f"{base}/name",
                owner,
                "constituent",
                constituent.name,
                context,
            )
            chemical_context = context.extend(
                FactContext.of(base, name=constituent.name)
            )
            add(
                "composition",
                f"{base}/role",
                owner,
                "constituent_role",
                constituent.role,
                chemical_context,
            )
            add(
                "composition",
                f"{base}/amount",
                owner,
                "constituent_amount:" + property_name(constituent.amount.name)
                if constituent.amount
                else "constituent_amount",
                constituent.amount,
                chemical_context,
            )

    identifiers = {
        "device_families": "family_id",
        "individual_devices": "device_id",
        "performance_observations": "observation_id",
        "population_statistics": "population_id",
        "stability_tests": "test_id",
    }
    for collection, id_field in identifiers.items():
        for index, record in enumerate(getattr(study, collection)):
            key = f"{collection}:{getattr(record, id_field)}"
            owner = (
                RecordReference(identities[key])
                if key in identities
                else RecordReference(key, side)
            )
            path = f"/{collection}/{index}"
            if collection == "device_families":
                add("stack", f"{path}/polarity", owner, "polarity", record.polarity)
                # Raw stack prose is a fallback only; do not reward duplicate representations.
                if not record.layers:
                    add(
                        "stack",
                        f"{path}/full_stack_raw",
                        owner,
                        "stack",
                        record.full_stack_raw or record.architecture,
                    )
                layers = {
                    layer.layer_id: (layer.sequence, layer.material)
                    for layer in record.layers
                }

                def layer_reference(identifier: str | None) -> object:
                    return (
                        layers.get(identifier, f"{side}:unmatched_layer:{identifier}")
                        if identifier
                        else None
                    )

                for i, layer in enumerate(record.layers):
                    base = f"{path}/layers/{i}"
                    scopes.append(
                        ScientificScope(
                            owner,
                            "layer",
                            base,
                            (layer.sequence,)
                            if layer.sequence is not None
                            else (layer.material, layer.role),
                        )
                    )
                    context = FactContext.of(kind="layer").extend(
                        FactContext.of(base, sequence=layer.sequence)
                    )
                    add(
                        "stack",
                        f"{base}/material",
                        owner,
                        "material",
                        layer.material,
                        context,
                    )
                    context = context.extend(
                        FactContext.of(base, material=layer.material)
                    )
                    add("stack", f"{base}/role", owner, "role", layer.role, context)
                    add(
                        "stack",
                        f"{base}/material_form",
                        owner,
                        "material_form",
                        layer.material_form,
                        context,
                    )
                    quantities(
                        "stack",
                        f"{base}/reported_properties",
                        owner,
                        layer.reported_properties,
                        context,
                    )
                    chemicals(
                        f"{base}/constituents", owner, layer.constituents, context
                    )
                for i, absorber in enumerate(record.absorbers):
                    base = f"{path}/absorbers/{i}"
                    context = FactContext.of(kind="absorber").extend(
                        FactContext.of(
                            base, layer_id=layer_reference(absorber.layer_id)
                        )
                    )
                    # Several unlinked absorbers cannot safely share an anonymous scope.
                    if absorber.layer_id is None and len(record.absorbers) > 1:
                        context = context.extend(
                            FactContext.of(base, label=absorber.label)
                        )
                    scopes.append(ScientificScope(owner, "absorber", base, context))
                    add(
                        "composition",
                        f"{base}/formula",
                        owner,
                        "formula",
                        absorber.formula,
                        context,
                    )
                    quantities(
                        "composition",
                        f"{base}/properties",
                        owner,
                        absorber.properties,
                        context,
                    )
                    chemicals(
                        f"{base}/constituents", owner, absorber.constituents, context
                    )
                for i, step in enumerate(record.processing_steps):
                    base = f"{path}/processing_steps/{i}"
                    targets = UnorderedContext(
                        tuple(layer_reference(t) for t in step.target_layer_ids)
                    )
                    context = FactContext.of(kind="step").extend(
                        FactContext.of(
                            base, sequence=step.sequence, target_layer_ids=targets
                        )
                    )
                    operation = operation_name(step.operation, operation_aliases)
                    scopes.append(
                        ScientificScope(
                            owner,
                            "step",
                            base,
                            (
                                step.sequence,
                                operation,
                                targets,
                                UnorderedContext(tuple(step.materials)),
                            ),
                        )
                    )
                    add(
                        "processing",
                        f"{base}/operation",
                        owner,
                        "operation",
                        operation,
                        context,
                    )
                    context = context.extend(FactContext.of(base, operation=operation))
                    for j, material in enumerate(step.materials):
                        add(
                            "processing",
                            f"{base}/materials/{j}",
                            owner,
                            "material",
                            material,
                            context,
                        )
                    quantities(
                        "processing",
                        f"{base}/conditions",
                        owner,
                        step.conditions,
                        context.extend(
                            FactContext.of(
                                base, materials=UnorderedContext(tuple(step.materials))
                            )
                        ),
                    )
            elif collection == "individual_devices":
                context = FactContext.of(
                    path,
                    family_id=reference("device_families", record.family_id),
                    variant=record.variant,
                )
                quantities(
                    "processing",
                    f"{path}/reported_properties",
                    owner,
                    record.reported_properties,
                    context,
                )
            elif collection == "performance_observations":
                quantities(
                    "performance",
                    f"{path}/metrics",
                    owner,
                    record.metrics,
                    device_context(record.device_id, path).extend(
                        FactContext.of(
                            path,
                            measurement_type=record.measurement_type,
                            scan_direction=record.scan_direction,
                        )
                    ),
                )
            elif collection == "population_statistics":
                context = FactContext.of(
                    path,
                    family_id=reference("device_families", record.family_id),
                    statistic_type=record.statistic_type,
                    label=scientific_descriptor(record),
                )
                add(
                    "population",
                    f"{path}/sample_size",
                    owner,
                    "sample_size",
                    record.sample_size,
                    context,
                )
                quantities(
                    "population",
                    f"{path}/metrics",
                    owner,
                    record.metrics,
                    context.extend(
                        FactContext.of(path, sample_size=record.sample_size)
                    ),
                )
            elif collection == "stability_tests":
                context = FactContext.of(
                    path,
                    family_id=reference("device_families", record.family_id),
                    device_id=device_context(record.device_id, path),
                    link_status=record.link_status,
                    specimen_label=scientific_descriptor(record),
                )
                quantities(
                    "stability", f"{path}/conditions", owner, record.conditions, context
                )
                context = context.extend(
                    FactContext.of(path, conditions=conditions(record.conditions))
                )
                for i, checkpoint in enumerate(record.checkpoints):
                    base = f"{path}/checkpoints/{i}"
                    scopes.append(
                        ScientificScope(
                            owner,
                            "checkpoint",
                            base,
                            (checkpoint.time, conditions(checkpoint.conditions)),
                        )
                    )
                    add(
                        "stability",
                        f"{base}/time",
                        owner,
                        "time",
                        checkpoint.time,
                        context.extend(
                            FactContext.of(
                                base, conditions=conditions(checkpoint.conditions)
                            )
                        ),
                    )
                    quantities(
                        "stability",
                        f"{base}/conditions",
                        owner,
                        checkpoint.conditions,
                        context.extend(FactContext.of(base, time=checkpoint.time)),
                    )
                    quantities(
                        "stability",
                        f"{base}/outcomes",
                        owner,
                        checkpoint.outcomes,
                        context.extend(
                            FactContext.of(
                                base,
                                time=checkpoint.time,
                                conditions=conditions(checkpoint.conditions),
                            )
                        ),
                    )
    return FactProjection(facts, scopes)
