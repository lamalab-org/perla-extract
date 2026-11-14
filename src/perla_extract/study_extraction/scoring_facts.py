"""Project the scientific schema into contextual facts for deterministic scoring.

This is a scoring contract, not extraction logic. It selects scientific fields and
retains their owners; labels, evidence wording, generated IDs and empty placeholders
do not earn points. No chemical identities or missing relationships are inferred.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, replace
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
from .scoring_process import describe_process
from .scoring_vocabulary import material_name, scoped_property_name

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
    "referenceefficiencyretained": "efficiencyretention",
    "pceretention": "efficiencyretention",
}
DEFAULT_OPERATION_ALIASES = {
    "thermal annealing": "annealing",
    "spincoating": "spin coating",
    "spin_coating": "spin coating",
    "spin-coating": "spin coating",
}


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
    source_paths: tuple[str, ...] = ()


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
    owner_equal = left.owner == right.owner and left.owner.unmatched_side is None
    if explain and not owner_equal:
        differences = (
            ContextDifference("owner", None, None, left.owner, right.owner),
            *differences,
        )
    context_equal = owner_equal and (
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
    linked_containers: list[tuple[str, str]] = field(default_factory=list)
    conflicts: list[tuple[str, str]] = field(default_factory=list)

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
            and not any(
                (left.path.startswith(a + "/") and right.path.startswith(b + "/"))
                or (left.path.startswith(b + "/") and right.path.startswith(a + "/"))
                for a, b in self.linked_containers
            )
        ]

    def merge_linked_representations(self) -> None:
        """Count equal facts once across a layer and its sole linked absorber.

        Only equal property, context and complete value can merge. Unit conversion
        may introduce floating-point roundoff, allowed up to 16 machine epsilons
        relatively, with no absolute allowance. This fixed bound is independent
        of the scientific matching tolerance and cannot be widened by a caller.
        Copies within
        one list remain separate predictions. Differing singleton values on the
        two representations are conflicts, not alternate answers to choose from.
        Every original path survives in the representative fact's source_paths.
        """

        removed: set[str] = set()
        replacements: dict[str, ScientificFact] = {}
        for layer, absorber in self.linked_containers:
            left = [f for f in self.facts if f.path.startswith(layer + "/")]
            right = [f for f in self.facts if f.path.startswith(absorber + "/")]
            used: set[str] = set()

            def same_property(a: ScientificFact, b: ScientificFact) -> bool:
                return (a.group, a.name, a.owner) == (
                    b.group,
                    b.name,
                    b.owner,
                ) and equal_value(a.context, b.context, 0, 0)

            for b in right:
                candidates = [a for a in left if same_property(a, b)]
                match = next(
                    (
                        a
                        for a in candidates
                        if a.path not in used
                        and equal_value(
                            a.value, b.value, 16 * sys.float_info.epsilon, 0
                        )
                    ),
                    None,
                )
                if match is not None:
                    used.add(match.path)
                    removed.add(b.path)
                    replacements[match.path] = replace(
                        match, source_paths=match.source_paths + b.source_paths
                    )
                elif (
                    len(candidates) == 1
                    and isinstance(b.value, ReportedValue)
                    and isinstance(candidates[0].value, ReportedValue)
                    and sum(same_property(candidates[0], f) for f in right) == 1
                ):
                    self.conflicts.append((candidates[0].path, b.path))
        self.facts = [
            replacements.get(f.path, f) for f in self.facts if f.path not in removed
        ]


def scientific_facts(
    study: StudyExtraction,
    identities: dict[str, str],
    side: str,
    operation_aliases: dict[str, str],
    process_references: dict[str, str | None] | None = None,
) -> FactProjection:
    """Select key fields while retaining device, layer, recipe and checkpoint context.

    ``identities`` contains only established top-level record pairings. Unmatched
    references stay side-specific so two broken links cannot accidentally agree.
    Paths refer to the original JSON, making every missed or extra fact inspectable.
    """

    facts: list[ScientificFact] = []
    scopes: list[ScientificScope] = []
    linked_containers: list[tuple[str, str]] = []
    process_references = process_references or {}
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
        source_paths: tuple[str, ...] = (),
    ) -> None:
        if value is not None and value != "not_reported" and value != "":
            facts.append(
                ScientificFact(
                    group, path, owner, name, value, context, source_paths or (path,)
                )
            )

    def quantities(
        group: FactGroup,
        path: str,
        owner: RecordReference,
        values: list[ReportedValue],
        context: FactContext = FactContext(),
        material: str | None = None,
    ) -> None:
        for i, value in enumerate(values):
            name = (
                scoped_property_name(value.name, material) if material else value.name
            )
            add(group, f"{path}/{i}", owner, property_name(name), value, context)

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
                            name=material_name(constituent.name),
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
                material_name(constituent.name),
                context,
            )
            chemical_context = context.extend(
                FactContext.of(base, name=material_name(constituent.name))
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
                    layer.layer_id: (layer.sequence, material_name(layer.material))
                    for layer in record.layers
                }
                linked_absorbers = {
                    layer.layer_id: (i, absorber)
                    for layer in record.layers
                    for i, absorber in enumerate(record.absorbers)
                    if absorber.layer_id == layer.layer_id
                    and sum(a.layer_id == layer.layer_id for a in record.absorbers) == 1
                }
                layer_contexts: dict[str, FactContext] = {}

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
                            else (material_name(layer.material), layer.role),
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
                        material_name(layer.material),
                        context,
                    )
                    context = context.extend(
                        FactContext.of(base, material=material_name(layer.material))
                    )
                    layer_contexts[layer.layer_id] = context
                    if layer.layer_id in linked_absorbers:
                        absorber_index, _ = linked_absorbers[layer.layer_id]
                        linked_containers.append(
                            (base, f"{path}/absorbers/{absorber_index}")
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
                        "composition"
                        if layer.layer_id in linked_absorbers
                        or layer.role == "absorber"
                        else "stack",
                        f"{base}/reported_properties",
                        owner,
                        layer.reported_properties,
                        context,
                        material=layer.material,
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
                        layer_contexts[absorber.layer_id]
                        if absorber.layer_id in linked_absorbers
                        else context,
                        material=next(
                            (
                                layer.material
                                for layer in record.layers
                                if layer.layer_id == absorber.layer_id
                            ),
                            None,
                        ),
                    )
                    chemicals(
                        f"{base}/constituents",
                        owner,
                        absorber.constituents,
                        layer_contexts[absorber.layer_id]
                        if absorber.layer_id in linked_absorbers
                        else context,
                    )
                for i, step in enumerate(record.processing_steps):
                    base = f"{path}/processing_steps/{i}"
                    targets = UnorderedContext(
                        tuple(layer_reference(t) for t in step.target_layer_ids)
                    )
                    if base in process_references:
                        paired = process_references[base]
                        position = FactContext.of(
                            base,
                            step_id=RecordReference(
                                paired or base, None if paired else side
                            ),
                        )
                    else:
                        position = FactContext.of(base, sequence=step.sequence)
                    context = (
                        FactContext.of(kind="step")
                        .extend(position)
                        .extend(FactContext.of(base, target_layer_ids=targets))
                    )
                    description = describe_process(
                        step,
                        [
                            layer.material
                            for layer in record.layers
                            if layer.layer_id in step.target_layer_ids
                        ],
                        operation_aliases,
                    )
                    operation = description.operation
                    scopes.append(
                        ScientificScope(
                            owner,
                            "step",
                            base,
                            (
                                step.sequence,
                                operation,
                                targets,
                                UnorderedContext(
                                    tuple(material_name(m) for m in step.materials)
                                ),
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
                        (
                            f"{base}/operation",
                            f"{base}/conditions/{description.method_index}",
                        )
                        if description.method_index is not None
                        else (),
                    )
                    context = context.extend(FactContext.of(base, operation=operation))
                    for j, material in enumerate(step.materials):
                        add(
                            "processing",
                            f"{base}/materials/{j}",
                            owner,
                            "material",
                            material_name(material),
                            context,
                        )
                    condition_context = context.extend(
                        FactContext.of(
                            base,
                            materials=UnorderedContext(
                                tuple(material_name(m) for m in step.materials)
                            ),
                        )
                    )
                    for j, condition in enumerate(step.conditions):
                        if j != description.method_index:
                            add(
                                "processing",
                                f"{base}/conditions/{j}",
                                owner,
                                property_name(condition.name),
                                condition,
                                condition_context,
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
    projection = FactProjection(facts, scopes, linked_containers)
    projection.merge_linked_representations()
    return projection
