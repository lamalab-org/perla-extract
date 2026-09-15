"""Project the scientific schema into contextual facts for deterministic scoring.

This is a scoring contract, not extraction logic. It selects scientific fields and
retains their owners; labels, evidence wording, generated IDs and empty placeholders
do not earn points. No chemical identities or missing relationships are inferred.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .models import MaterialConstituent, ReportedValue, StudyExtraction
from .units import canonical_reported_quantity

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


def scientific_text(value: str) -> str:
    """Ignore Unicode typography and spacing, never chemical case or punctuation."""

    return " ".join(unicodedata.normalize("NFKC", value).replace("−", "-").split())


def property_name(name: str) -> str:
    key = re.sub(r"[\s_-]+", "", scientific_text(name).casefold())
    return METRIC_ALIASES.get(key, key)


def is_plain_number(value: ReportedValue) -> bool:
    """Allow unit conversion only for a single unqualified, internally consistent number.

    Ranges, inequalities, uncertainties and chemical formulas fall back to literal
    comparison. Their central numeric value alone does not represent the claim.
    """

    raw = scientific_text(value.raw_value)
    match = re.fullmatch(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(.*)", raw)
    return bool(
        match
        and value.value_number is not None
        and math.isfinite(value.value_number)
        and math.isclose(
            float(match[1]), value.value_number, rel_tol=1e-9, abs_tol=1e-12
        )
        and scientific_text(match[2]) in {"", scientific_text(value.unit or "")}
    )


@dataclass(frozen=True)
class UnorderedContext:
    """Mark context where order is irrelevant but duplicate entries still matter."""

    items: tuple[object, ...]


def equal_value(left: object, right: object, relative: float, absolute: float) -> bool:
    """Compare claims conservatively; missing units are not assumed dimensionless."""

    if isinstance(left, ReportedValue) and isinstance(right, ReportedValue):
        if is_plain_number(left) and is_plain_number(right):
            assert left.value_number is not None and right.value_number is not None
            if (left.unit is None) != (right.unit is None):
                return False
            left_number, right_number = left.value_number, right.value_number
            if left.unit is not None:
                a, b = (
                    canonical_reported_quantity(left),
                    canonical_reported_quantity(right),
                )
                if a is None or b is None:
                    return (
                        scientific_text(left.raw_value)
                        == scientific_text(right.raw_value)
                        and left.unit == right.unit
                    )
                if a[1] != b[1]:
                    return False
                left_number, right_number = a[0], b[0]
            return math.isclose(
                left_number, right_number, rel_tol=relative, abs_tol=absolute
            )
        return (
            scientific_text(left.raw_value) == scientific_text(right.raw_value)
            and scientific_text(left.unit or "") == scientific_text(right.unit or "")
            and left.value_number == right.value_number
        )
    if isinstance(left, str) and isinstance(right, str):
        return scientific_text(left) == scientific_text(right)
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
    owner: str
    name: str
    value: object
    context: tuple[object, ...]


@dataclass(frozen=True)
class ScientificScope:
    """Identify a nested object without using the very outcomes being evaluated."""

    owner: str
    kind: str
    path: str
    identity: tuple[object, ...]


@dataclass
class FactProjection:
    facts: list[ScientificFact]
    scopes: list[ScientificScope]

    def ambiguous_scopes(
        self, relative: float, absolute: float
    ) -> list[tuple[str, str]]:
        """Find indistinguishable containers, e.g. repeated unsequenced annealing steps."""

        buckets: dict[tuple[str, str], list[ScientificScope]] = {}
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

    def reference(collection: str, identifier: str | None) -> str | None:
        if identifier is None:
            return None
        key = f"{collection}:{identifier}"
        return identities.get(key, f"{side}:unmatched:{key}")

    def device_context(identifier: str | None) -> tuple[object, ...]:
        device = devices.get(identifier) if identifier is not None else None
        return (
            reference("individual_devices", identifier),
            reference("device_families", device.family_id) if device else None,
            device.variant if device else None,
            device.champion_status if device else None,
            device.selection_basis if device else None,
        )

    def add(
        group: FactGroup,
        path: str,
        owner: str,
        name: str,
        value: object,
        context: tuple = (),
    ) -> None:
        if value is not None and value != "not_reported" and value != "":
            facts.append(ScientificFact(group, path, owner, name, value, context))

    def quantities(
        group: FactGroup,
        path: str,
        owner: str,
        values: list[ReportedValue],
        context: tuple = (),
    ) -> None:
        for i, value in enumerate(values):
            add(group, f"{path}/{i}", owner, property_name(value.name), value, context)

    def conditions(values: list[ReportedValue]) -> UnorderedContext:
        return UnorderedContext(tuple((property_name(v.name), v) for v in values))

    def chemicals(
        path: str, owner: str, constituents: list[MaterialConstituent], context: tuple
    ) -> None:
        for i, constituent in enumerate(constituents):
            base = f"{path}/{i}"
            scopes.append(
                ScientificScope(
                    owner,
                    "constituent",
                    base,
                    (
                        *context,
                        constituent.name,
                        property_name(constituent.amount.name)
                        if constituent.amount
                        else None,
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
            chemical_context = (*context, constituent.name)
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
            owner = identities.get(key, f"{side}:unmatched:{key}")
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
                    context: tuple[object, ...] = ("layer", layer.sequence)
                    add(
                        "stack",
                        f"{base}/material",
                        owner,
                        "material",
                        layer.material,
                        context,
                    )
                    context = (*context, layer.material)
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
                    context = ("absorber", layer_reference(absorber.layer_id))
                    # Several unlinked absorbers cannot safely share an anonymous scope.
                    if absorber.layer_id is None and len(record.absorbers) > 1:
                        context = (*context, absorber.label)
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
                    context = ("step", step.sequence, targets)
                    operation = operation_name(step.operation, operation_aliases)
                    scopes.append(
                        ScientificScope(
                            owner,
                            "step",
                            base,
                            (step.sequence,)
                            if step.sequence is not None
                            else (
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
                    context = (*context, operation)
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
                        (*context, UnorderedContext(tuple(step.materials))),
                    )
            elif collection == "individual_devices":
                context = (
                    reference("device_families", record.family_id),
                    record.variant,
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
                    (
                        *device_context(record.device_id),
                        record.measurement_type,
                        record.scan_direction,
                    ),
                )
            elif collection == "population_statistics":
                context = (
                    reference("device_families", record.family_id),
                    record.statistic_type,
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
                    (*context, record.sample_size),
                )
            elif collection == "stability_tests":
                context = (
                    reference("device_families", record.family_id),
                    device_context(record.device_id),
                    record.link_status,
                )
                quantities(
                    "stability", f"{path}/conditions", owner, record.conditions, context
                )
                context = (*context, conditions(record.conditions))
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
                        (*context, conditions(checkpoint.conditions)),
                    )
                    quantities(
                        "stability",
                        f"{base}/conditions",
                        owner,
                        checkpoint.conditions,
                        (*context, checkpoint.time),
                    )
                    quantities(
                        "stability",
                        f"{base}/outcomes",
                        owner,
                        checkpoint.outcomes,
                        (*context, checkpoint.time, conditions(checkpoint.conditions)),
                    )
    return FactProjection(facts, scopes)
