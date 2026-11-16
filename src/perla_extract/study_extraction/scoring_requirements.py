"""Scientific dependencies of the fields supported by local context rules.

A rule removes only declared independent attributes after the subject is aligned.
Everything else, including unfamiliar context, remains a requirement. No rule
uses a measured value to establish its own owner. This table is also included in
diagnostics so a reader can trace a reported score to the comparison contract.
"""

from dataclasses import dataclass

from .scoring_context import FactContext, RecordReference
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
        "variant",
        "champion_status",
        "selection_basis",
        "subject_context",
    ),
    ("statistical_level", "measurement_type", "scan_direction", "protocol"),
)
ORDERED_PROCESS = FactRequirement(
    "ordered-process-condition",
    "family, target layer and ordered process step",
    ("step_id", "target_layer_ids", "operation"),
    (),
    ("materials",),
)
REQUIREMENTS = (STRICT_CONTEXT, INDIVIDUAL_PHOTOVOLTAIC, ORDERED_PROCESS)


def requirements_for(concept: tuple[str, str], context: FactContext) -> FactRequirement:
    """Select bounded rules by meaning and established links, never by value."""
    values = {c.name: c.value for c in context.components}
    namespace, name = concept
    if (
        namespace == "measurement"
        and name in {"pce", "voc", "jsc", "ff"}
        and values.get("statistical_level") == "individual"
    ):
        return INDIVIDUAL_PHOTOVOLTAIC
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
