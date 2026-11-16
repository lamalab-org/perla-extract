"""Project the scientific schema into contextual facts for deterministic scoring.

This is a scoring contract, not extraction logic. It selects scientific fields and
retains their owners; labels, evidence wording, generated IDs and empty placeholders
do not earn points. No chemical identities or missing relationships are inferred.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from typing import Literal

from .models import (
    MaterialConstituent,
    PerformanceObservation,
    PopulationStatistic,
    ReportedValue,
    StabilityTest,
    StudyExtraction,
    SubjectContext,
)
from .scoring_claims import (
    compound_quantity as compound_quantity,
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
from .scoring_context import (
    CohortSize,
    ContextComponent,
    ContextDifference,
    FactContext,
    RecordReference,
    compare_context,
    device_identity,
    device_treatment,
    has_subject_identity,
    missing_requirement,
    subject_context,
)
from .scoring_context import (
    UnorderedContext as UnorderedContext,
)
from .scoring_context import (
    equal_value as equal_value,
)
from .scoring_fields import FactGroup, FactSubfield, fact_group, fact_subfield
from .scoring_identity import measurement_subject
from .scoring_process import condition_name, describe_process
from .scoring_protocol import (
    canonical_conditions,
    performance_name,
    protocol_claims,
    protocol_name,
)
from .scoring_requirements import requirements_for
from .scoring_vocabulary import (
    error_bar_statistic,
    known_material,
    material_name,
    material_role,
    material_stage,
    property_name,
    scientific_concept,
    scoped_property_name,
    treatment_condition_name,
    whole_material_role,
)

SourceGroup = Literal[
    "performance", "population", "stability", "composition", "stack", "processing"
]


DEFAULT_OPERATION_ALIASES = {
    "thermal annealing": "annealing",
    "spincoating": "spin coating",
    "spin_coating": "spin coating",
    "spin-coating": "spin coating",
}


def scientific_descriptor(record: object) -> str | None:
    """Retain identity that this schema stores only in a cohort/specimen label.

    A population's treatment arm and an unlinked stability specimen cannot be
    identified by outcome values. Normalize typography, not semantic meaning.
    Other display labels are deliberately not part of this rule. Unknown synonyms
    remain disagreements; the scorer must not guess equivalence or change truth.
    """

    if isinstance(record, PopulationStatistic):
        return (
            None
            if record.subjects and all(has_subject_identity(s) for s in record.subjects)
            else scientific_text(record.label)
        )
    if isinstance(record, StabilityTest) and record.device_id is None:
        return scientific_text(record.specimen_label)
    return None


@dataclass(frozen=True)
class ScientificFact:
    group: FactGroup
    path: str
    owner: RecordReference
    name: str
    value: object
    context: FactContext
    subfield: FactSubfield
    concept: tuple[str, str]
    source_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FactComparison:
    """The decision used by all score views and their optional explanation."""

    value_equal: bool
    context_equal: bool
    context_status: Literal["equivalent", "contradictory", "unresolved"]
    ambiguity_blocked: bool
    context_differences: tuple[ContextDifference, ...]
    owner_equal: bool
    missing_context: bool
    conflicting_context: bool

    def eligible(self, view: str) -> bool:
        if view == "recovery":
            return self.value_equal
        return not self.ambiguity_blocked and self.context_equal and self.value_equal


def compare_facts(
    left: ScientificFact,
    right: ScientificFact,
    relative: float,
    absolute: float,
    *,
    ambiguity_blocked: bool,
    explain: bool = False,
) -> FactComparison:
    # One decision path serves scoring and explanation. Explanation only controls
    # retention of the detailed differences, never eligibility.
    differences = compare_context(
        requirements_for(left.concept, left.context).context(left.context),
        requirements_for(right.concept, right.context).context(right.context),
        relative,
        absolute,
    )
    missing_context = any(d.reason == "missing_required" for d in differences)
    conflicting_context = any(d.reason == "conflict" for d in differences)
    owner_equal = left.owner == right.owner and left.owner.unmatched_side is None
    if not owner_equal:
        differences = (
            ContextDifference(
                "owner",
                None,
                None,
                left.owner,
                right.owner,
                "missing_required"
                if missing_requirement(left.owner) or missing_requirement(right.owner)
                else "conflict",
            ),
            *differences,
        )
    context_equal = not differences
    return FactComparison(
        value_equal=equal_value(left.value, right.value, relative, absolute),
        context_equal=context_equal,
        context_status=(
            "contradictory"
            if any(d.reason == "conflict" for d in differences)
            else "unresolved"
            if differences or ambiguity_blocked
            else "equivalent"
        ),
        ambiguity_blocked=ambiguity_blocked,
        context_differences=differences if explain else (),
        owner_equal=owner_equal,
        missing_context=missing_context,
        conflicting_context=conflicting_context,
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

    def merge_material_identities(self, aliases: list[tuple[str, str]]) -> None:
        """Attach explicitly redundant whole-material fields to their layer identity.

        Eligibility is determined from this input's own unambiguous layer links,
        equal names and constituent roles. This does not infer a mixture or use
        the opposite study to repair a record. Linked constituent aliases already
        merged by ``merge_linked_representations`` travel with their representative.
        """
        by_path = {path: f.path for f in self.facts for path in f.source_paths}
        facts = {f.path: f for f in self.facts}
        for target_path, source_path in aliases:
            target, source = by_path.get(target_path), by_path.get(source_path)
            if (
                target is None
                or source is None
                or target == source
                or source not in facts
            ):
                continue
            canonical, redundant = facts[target], facts.pop(source)
            facts[target] = replace(
                canonical, source_paths=canonical.source_paths + redundant.source_paths
            )
            for path in redundant.source_paths:
                by_path[path] = target
        self.facts = list(facts.values())

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
        """Count equal facts once across explicitly linked representations.

        Supported links are a layer and its sole absorber, or a device's treatment
        conditions and reported properties. No links are inferred between subjects.

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


def _protocol_context(
    record: PerformanceObservation | PopulationStatistic, path: str
) -> FactContext:
    """Require every asserted condition, independently of its storage container."""
    return FactContext(
        (ContextComponent("protocol", UnorderedContext(protocol_claims(record)), path),)
    )


def scientific_facts(
    study: StudyExtraction,
    identities: dict[str, str],
    side: str,
    operation_aliases: dict[str, str],
    process_references: dict[str, str | None] | None = None,
    layer_references: dict[str, str | None] | None = None,
) -> FactProjection:
    """Select key fields while retaining device, layer, recipe and checkpoint context.

    ``identities`` contains only established top-level record pairings. Unmatched
    references stay side-specific so two broken links cannot accidentally agree.
    Paths refer to the original JSON, making every missed or extra fact inspectable.
    """

    facts: list[ScientificFact] = []
    scopes: list[ScientificScope] = []
    linked_containers: list[tuple[str, str]] = []
    material_aliases: list[tuple[str, str]] = []
    material_conflicts: list[tuple[str, str]] = []
    process_references = process_references or {}
    layer_references = layer_references or {}
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
        return (
            FactContext.of(
                source_path, device_id=reference("individual_devices", identifier)
            )
            .extend(
                FactContext.of(
                    device_paths.get(identifier) if identifier is not None else None,
                    family_id=reference("device_families", device.family_id)
                    if device
                    else None,
                )
            )
            .extend(
                device_identity(device, device_paths[device.device_id])
                if device
                else FactContext()
            )
        )

    def add(
        group: SourceGroup,
        path: str,
        owner: RecordReference,
        name: str,
        value: object,
        context: FactContext = FactContext(),
        source_paths: tuple[str, ...] = (),
    ) -> None:
        if (
            isinstance(value, ReportedValue)
            and value.value_number is None
            and value.unit is None
            and value.raw_value.strip().casefold() in {"not_reported", "not reported"}
        ):
            return
        if value is not None and value != "not_reported" and value != "":
            if name == "errorbarstatistic":
                if isinstance(value, str):
                    value = error_bar_statistic(value)
                elif (
                    isinstance(value, ReportedValue)
                    and value.value_number is None
                    and value.unit is None
                ):
                    value = error_bar_statistic(value.raw_value)
            stage = next(
                (
                    c.value
                    for c in context.components
                    if c.name == "material_stage" and isinstance(c.value, str)
                ),
                None,
            )
            concept = scientific_concept(
                group, name, stage, outcome="/outcomes/" in path
            )
            if "/measurement_conditions/" in path and protocol_name(name) is None:
                concept = ("measurement_condition", name)
            facts.append(
                ScientificFact(
                    group=fact_group(concept),
                    path=path,
                    owner=owner,
                    name=name,
                    value=value,
                    context=requirements_for(concept, context).context(context),
                    source_paths=source_paths or (path,),
                    subfield=fact_subfield(group, name, path, stage, value),
                    concept=concept,
                )
            )

    def quantities(
        group: SourceGroup,
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

    def measurement_conditions(
        group: SourceGroup,
        record: PerformanceObservation | PopulationStatistic,
        path: str,
        owner: RecordReference,
        context: FactContext,
    ) -> None:
        entries = canonical_conditions(record, path)
        for item in entries:
            add(
                group,
                item.source_paths[0],
                owner,
                item.name,
                item.value,
                context,
                item.source_paths,
            )
        # Contradictory copies across the old and new containers cannot establish
        # a measurement protocol. Keep their facts and flag the affected record.
        for i, left in enumerate(entries):
            for right in entries[i + 1 :]:
                if (
                    left.name == right.name
                    and any("/metrics/" in p for p in left.source_paths)
                    and any("/measurement_conditions/" in p for p in right.source_paths)
                    and not equal_value(
                        left.value, right.value, 16 * sys.float_info.epsilon, 0
                    )
                ):
                    material_conflicts.append((path, right.source_paths[0]))

    def treatment_facts(
        subject: SubjectContext,
        path: str,
        owner: RecordReference,
        context: FactContext | None = None,
    ) -> None:
        context = context if context is not None else subject_context(subject, path)
        scopes.append(
            ScientificScope(
                owner,
                "treatment_subject",
                path,
                subject_context(subject, path, identity_only=True),
            )
        )
        add(
            "processing",
            f"{path}/treatment_stage",
            owner,
            "treatment_stage",
            subject.treatment_stage,
            context,
        )
        add(
            "processing",
            f"{path}/treated_material",
            owner,
            "treated_material",
            material_name(subject.treated_material)
            if subject.treated_material
            else None,
            context,
        )
        for i, condition in enumerate(subject.conditions):
            add(
                "processing",
                f"{path}/conditions/{i}",
                owner,
                "treatment:"
                + (
                    treatment_condition_name(condition.name)
                    or property_name(condition.name)
                ),
                condition,
                context,
            )

    def chemicals(
        path: str,
        owner: RecordReference,
        constituents: list[MaterialConstituent],
        context: FactContext,
    ) -> None:
        for i, constituent in enumerate(constituents):
            base = f"{path}/{i}"
            constituent_context = context.extend(
                FactContext(
                    (
                        ContextComponent(
                            "material_stage",
                            material_stage(constituent.role),
                            f"{base}/role",
                        ),
                    )
                )
            )
            scopes.append(
                ScientificScope(
                    owner,
                    "constituent",
                    base,
                    constituent_context.extend(
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
                constituent_context,
            )
            chemical_context = constituent_context.extend(
                FactContext.of(base, name=material_name(constituent.name))
            )
            add(
                "composition",
                f"{base}/role",
                owner,
                "constituent_role",
                material_role(constituent.role),
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
            if (subject := measurement_subject(record)) is not None:
                # Compare facts about the specimen, not generated observation IDs.
                # Unmatched/dangling links remain side-local through reference().
                subject_owner = reference(*subject)
                assert subject_owner is not None
                owner = subject_owner
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

                layer_paths = {
                    layer.layer_id: f"{path}/layers/{i}"
                    for i, layer in enumerate(record.layers)
                }

                def whole_constituent(
                    layer_material: str,
                    layer_path: str,
                    constituents: list[MaterialConstituent],
                    base: str,
                ) -> None:
                    """A sole whole-material claim can repeat or contradict its layer."""
                    if len(constituents) != 1 or not whole_material_role(
                        constituents[0].role
                    ):
                        return
                    member = constituents[0]
                    paths = (f"{layer_path}/material", f"{base}/constituents/0/name")
                    if material_name(member.name) == material_name(layer_material):
                        material_aliases.append(paths)
                    elif known_material(member.name) and known_material(layer_material):
                        material_conflicts.append(paths)

                def layer_reference(identifier: str | None) -> object:
                    source = layer_paths.get(identifier)
                    if source in layer_references:
                        paired = layer_references[source]
                        return (
                            RecordReference(paired)
                            if paired is not None
                            else RecordReference(source, side)
                        )
                    return (
                        layers.get(identifier, f"{side}:unmatched_layer:{identifier}")
                        if identifier
                        else None
                    )

                for i, layer in enumerate(record.layers):
                    base = f"{path}/layers/{i}"
                    whole_constituent(layer.material, base, layer.constituents, base)
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
                    position = (
                        FactContext(
                            (
                                ContextComponent(
                                    "layer", layer_reference(layer.layer_id), base
                                ),
                            )
                        )
                        if base in layer_references
                        else FactContext.of(base, sequence=layer.sequence)
                    )
                    context = FactContext.of(kind="layer").extend(position)
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
                    if absorber.layer_id in linked_absorbers:
                        layer_index, layer = next(
                            (j, item)
                            for j, item in enumerate(record.layers)
                            if item.layer_id == absorber.layer_id
                        )
                        whole_constituent(
                            layer.material,
                            layer_paths[layer.layer_id],
                            absorber.constituents,
                            base,
                        )
                        formula = absorber.formula
                        if (
                            formula
                            and formula.unit is None
                            and formula.value_number is None
                        ):
                            material_path = f"{path}/layers/{layer_index}/material"
                            if material_name(formula.raw_value) == material_name(
                                layer.material
                            ):
                                material_aliases.append(
                                    (material_path, f"{base}/formula")
                                )
                            elif known_material(formula.raw_value) and known_material(
                                layer.material
                            ):
                                material_conflicts.append(
                                    (material_path, f"{base}/formula")
                                )
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
                                condition_name(condition.name, operation),
                                condition,
                                condition_context,
                            )
            elif collection == "individual_devices":
                treatment = device_treatment(record, path)
                parent = FactContext.of(
                    path, family_id=reference("device_families", record.family_id)
                )
                treatment_context = (
                    parent.extend(treatment) if treatment is not None else None
                )
                if record.subject_context is not None:
                    treatment_facts(
                        record.subject_context,
                        f"{path}/subject_context",
                        owner,
                        treatment_context,
                    )
                    linked_containers.append(
                        (
                            f"{path}/subject_context/conditions",
                            f"{path}/reported_properties",
                        )
                    )
                context = FactContext.of(
                    path,
                    family_id=reference("device_families", record.family_id),
                    variant=record.variant,
                )
                for j, value in enumerate(record.reported_properties):
                    name = treatment_condition_name(value.name)
                    value_context = context
                    if name is not None:
                        assert treatment_context is not None
                        value_context = treatment_context
                    add(
                        "processing",
                        f"{path}/reported_properties/{j}",
                        owner,
                        "treatment:" + name
                        if name is not None
                        else property_name(value.name),
                        value,
                        value_context,
                    )
            elif collection == "performance_observations":
                context = device_context(record.device_id, path).extend(
                    FactContext.of(
                        path,
                        statistical_level="individual",
                        measurement_type=record.measurement_type,
                        scan_direction=record.scan_direction,
                    )
                )
                protocol = _protocol_context(record, path)
                for j, metric in enumerate(record.metrics):
                    if protocol_name(metric.name):
                        continue
                    add(
                        "performance",
                        f"{path}/metrics/{j}",
                        owner,
                        performance_name(metric.name, record.measurement_type),
                        metric,
                        context.extend(protocol),
                    )
                measurement_conditions("performance", record, path, owner, context)
            elif collection == "population_statistics":
                for i, subject in enumerate(record.subjects):
                    treatment_facts(subject, f"{path}/subjects/{i}", owner)
                context = FactContext.of(
                    path,
                    statistical_level="population",
                    family_id=reference("device_families", record.family_id),
                    label=scientific_descriptor(record),
                    subjects=UnorderedContext(
                        tuple(
                            subject_context(s, f"{path}/subjects/{i}")
                            for i, s in enumerate(record.subjects)
                        )
                    ),
                )
                add(
                    "population",
                    f"{path}/sample_size",
                    owner,
                    "sample_size",
                    record.sample_size,
                    context,
                )
                add(
                    "population",
                    f"{path}/error_bar_type",
                    owner,
                    "errorbarstatistic",
                    record.error_bar_type,
                    context,
                )
                outcome_context = context.extend(
                    FactContext.of(
                        path,
                        sample_size=CohortSize(record.sample_size),
                        statistic_type=record.statistic_type,
                        measurement_type=record.measurement_type,
                        scan_direction=record.scan_direction,
                    )
                ).extend(_protocol_context(record, path))
                for j, metric in enumerate(record.metrics):
                    if protocol_name(metric.name):
                        continue
                    add(
                        "population",
                        f"{path}/metrics/{j}",
                        owner,
                        performance_name(metric.name, record.measurement_type),
                        metric,
                        context
                        if property_name(metric.name)
                        in {"errorbarstatistic", "sample_size"}
                        else outcome_context,
                    )
                measurement_conditions("population", record, path, owner, context)

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
                    FactContext.of(path, test_conditions=conditions(record.conditions))
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
                                base,
                                checkpoint_conditions=conditions(checkpoint.conditions),
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
                                checkpoint_conditions=conditions(checkpoint.conditions),
                            )
                        ),
                    )
    projection = FactProjection(facts, scopes, linked_containers)
    projection.merge_linked_representations()
    projection.merge_material_identities(material_aliases)
    projection.conflicts.extend(material_conflicts)
    return projection
