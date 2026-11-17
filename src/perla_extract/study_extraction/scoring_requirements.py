"""Scientific dependencies of the fields supported by local context rules.

A rule removes only declared independent attributes after the subject is aligned.
Everything else, including unfamiliar context, remains a requirement. No rule
uses a measured value to establish its own owner. This table is also included in
diagnostics so a reader can trace a reported score to the comparison contract.
"""

from dataclasses import dataclass

from .scoring_context import (
    ContextComponent,
    ContextDifference,
    FactContext,
    RecordReference,
    UnorderedContext,
    compare_context,
    missing_requirement,
)
from .scoring_process import OPERATION_CONDITIONS


@dataclass(frozen=True)
class FactRequirement:
    rule_id: str
    subject: str
    identity_fields: tuple[str, ...]
    defining_fields: tuple[str, ...]
    independent_fields: tuple[str, ...] = ()

    def context(self, context: FactContext) -> FactContext:
        """Retain unknown requirements; omit only this rule's independent fields."""
        if not any(c.name in self.independent_fields for c in context.components):
            return context
        return FactContext(
            tuple(
                c for c in context.components if c.name not in self.independent_fields
            )
        )


STRICT_CONTEXT = FactRequirement(
    "strict-context",
    "record and nested scientific scope",
    (),
    ("all asserted context",),
)
INDIVIDUAL_PHOTOVOLTAIC = FactRequirement(
    "individual-photovoltaic",
    "individual specimen and measurement",
    (
        "device_id",
        "family_id",
        "champion_status",
        "subject_context",
    ),
    ("statistical_level", "measurement_type", "scan_direction", "protocol"),
    ("variant", "selection_basis"),
)
POPULATION_PHOTOVOLTAIC = FactRequirement(
    "population-photovoltaic",
    "cohort and statistic",
    ("family_id", "label", "subjects"),
    (
        "statistical_level",
        "statistic_type",
        "measurement_type",
        "scan_direction",
        "protocol",
        "sample_size",
    ),
)
ORDERED_PROCESS = FactRequirement(
    "ordered-process-condition",
    "family, target layer and ordered process step",
    ("step_id", "target_layer_ids", "operation"),
    (),
    ("materials",),
)
REQUIREMENTS = (
    STRICT_CONTEXT,
    INDIVIDUAL_PHOTOVOLTAIC,
    POPULATION_PHOTOVOLTAIC,
    ORDERED_PROCESS,
)


def requirements_for(concept: tuple[str, str], context: FactContext) -> FactRequirement:
    """Select bounded rules by meaning and established links, never by value."""
    values = {c.name: c.value for c in context.components}
    namespace, name = concept
    if namespace == "measurement" and name in {"pce", "voc", "jsc", "ff"}:
        if values.get("statistical_level") == "individual":
            return INDIVIDUAL_PHOTOVOLTAIC
        if values.get("statistical_level") == "population":
            return POPULATION_PHOTOVOLTAIC
    step = values.get("step_id")
    operation = values.get("operation")
    if (
        namespace == "processing"
        and isinstance(step, RecordReference)
        and step.unmatched_side is None
        and isinstance(operation, str)
        and name in OPERATION_CONDITIONS.get(operation, {}).values()
    ):
        return ORDERED_PROCESS
    return STRICT_CONTEXT


def compare_requirements(
    concept: tuple[str, str],
    truth: FactContext,
    prediction: FactContext,
    relative: float,
    absolute: float,
) -> tuple[ContextDifference, ...]:
    """Compare field requirements; extra prediction protocol is not a reference error.

    The reference defines which protocol conditions can be assessed. Every stated
    reference condition remains required, including unknown condition names.
    Additional predicted conditions are scored as their own fields. This does not
    relax specimen/cohort identity, measurement kind, scan direction or statistic.
    """
    rule = requirements_for(concept, truth)
    left, right = (
        rule.context(truth),
        requirements_for(concept, prediction).context(prediction),
    )
    if rule in (INDIVIDUAL_PHOTOVOLTAIC, POPULATION_PHOTOVOLTAIC):
        reference_protocol = next(
            (c.value for c in left.components if c.name == "protocol"), None
        )
        if isinstance(reference_protocol, UnorderedContext):
            names = {name for name, _ in reference_protocol.items}
            right = FactContext(
                tuple(
                    ContextComponent(
                        c.name,
                        UnorderedContext(
                            tuple(item for item in c.value.items if item[0] in names)
                        ),
                        c.path,
                    )
                    if c.name == "protocol" and isinstance(c.value, UnorderedContext)
                    else c
                    for c in right.components
                )
            )
        # Specimen preparation is already used for owner alignment. Unannotated
        # preparation cannot veto that owner, but explicitly conflicting inputs can.
        left_subject = next(
            (c.value for c in left.components if c.name == "subject_context"), None
        )
        if missing_requirement(left_subject):
            left = FactContext(
                tuple(c for c in left.components if c.name != "subject_context")
            )
            right = FactContext(
                tuple(c for c in right.components if c.name != "subject_context")
            )
        if any(
            c.name == "champion_status" and missing_requirement(c.value)
            for c in left.components
        ):
            left = FactContext(
                tuple(c for c in left.components if c.name != "champion_status")
            )
            right = FactContext(
                tuple(c for c in right.components if c.name != "champion_status")
            )
    return compare_context(left, right, relative, absolute)
