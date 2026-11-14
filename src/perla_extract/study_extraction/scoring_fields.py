"""Disjoint kinds of scored claims, shared by matching and result breakdowns.

Each projected fact belongs to one kind. These kinds add no aggregate weight:
the existing six parent groups still determine group-macro F1. Recipe claims and
measurement protocol remain distinguishable even in the value-only diagnostic.
"""

from typing import Literal, get_args

from .scoring_protocol import protocol_name

FactSubfield = Literal[
    "material_identity",
    "composition_amounts",
    "composition_properties",
    "material_roles",
    "material_properties",
    "recipe_composition",
    "device_structure",
    "photovoltaic_metrics",
    "measurement_protocol",
    "other_measurement_fields",
    "population_metrics",
    "population_metadata",
    "stability_protocol",
    "stability_outcomes",
    "processing_operations",
    "processing_materials",
    "processing_conditions",
    "specimen_properties",
]
FACT_SUBFIELDS: tuple[FactSubfield, ...] = get_args(FactSubfield)


def fact_subfield(group: str, name: str, path: str, stage: str | None) -> FactSubfield:
    """Classify canonical fields; unknown properties stay counted in explicit catchalls."""
    if group == "performance":
        if protocol_name(name):
            return "measurement_protocol"
        return (
            "photovoltaic_metrics"
            if name in {"pce", "voc", "jsc", "ff"}
            else "other_measurement_fields"
        )
    if group == "population":
        if protocol_name(name):
            return "measurement_protocol"
        return (
            "population_metadata"
            if name in {"sample_size", "errorbarstatistic"}
            else "population_metrics"
        )
    if group == "stability":
        return "stability_outcomes" if "/outcomes/" in path else "stability_protocol"
    if group == "processing":
        if path.startswith("/individual_devices/"):
            return "specimen_properties"
        if name == "operation":
            return "processing_operations"
        return (
            "processing_materials" if "/materials/" in path else "processing_conditions"
        )
    if group == "composition":
        if stage == "recipe" or name.startswith("precursor"):
            return "recipe_composition"
        if name in {"formula", "constituent"}:
            return "material_identity"
        if name == "constituent_role":
            return "material_roles"
        if name.startswith("constituent_amount"):
            return "composition_amounts"
        return (
            "composition_properties"
            if name == "chlorineinresultingperovskitefilm"
            else "material_properties"
        )
    if group == "stack":
        if name == "material":
            return "material_identity"
        return (
            "device_structure"
            if name in {"role", "material_form", "polarity", "stack"}
            else "material_properties"
        )
    raise ValueError(f"No fact-kind contract for group {group}")
