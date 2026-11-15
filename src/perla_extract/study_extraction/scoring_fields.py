"""Presentation categories for canonical facts; never candidate matching keys.

Groups describe scientific domains rather than source record containers. Subfields
partition the ledger, keeping qualitative statements and statistics out of plots
labeled quantitative performance. Unknown claims stay in explicit catchalls.
"""

import re
from typing import Literal, get_args

from .models import ReportedValue
from .scoring_claims import LiteralClaim, normalize_claim
from .scoring_coverage import FACT_GROUPS as FACT_GROUPS
from .scoring_coverage import FactGroup as FactGroup
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
    "relative_performance",
    "qualitative_observations",
    "unparsed_measurement_claims",
    "statistical_metadata",
    "stability_protocol",
    "stability_outcomes",
    "processing_operations",
    "processing_materials",
    "processing_conditions",
]
FACT_SUBFIELDS: tuple[FactSubfield, ...] = get_args(FactSubfield)


def fact_subfield(
    group: str, name: str, path: str, stage: str | None, value: object = None
) -> FactSubfield:
    """Classify canonical fields; unknown properties stay counted in explicit catchalls."""
    if group in {"performance", "population"}:
        if name in {"sample_size", "errorbarstatistic"}:
            return "statistical_metadata"
        if protocol_name(name):
            return "measurement_protocol"
        claim = normalize_claim(value) if isinstance(value, ReportedValue) else None
        if isinstance(claim, LiteralClaim):
            # Unparsed bounds/numbers must not be presented as qualitative trends.
            numeric_cue = re.search(
                r"[0-9<>≤≥±%]|\b(?:half|quarter|third|percent|twice)\b",
                claim.text,
                re.I,
            )
            return (
                "unparsed_measurement_claims"
                if numeric_cue
                else "qualitative_observations"
            )
        if name == "efficiencyretention":
            return "relative_performance"
        return (
            "photovoltaic_metrics"
            if name in {"pce", "voc", "jsc", "ff"}
            else "other_measurement_fields"
        )
    if group == "stability":
        return "stability_outcomes" if "/outcomes/" in path else "stability_protocol"
    if group == "processing":
        if name == "operation":
            return "processing_operations"
        return (
            "processing_materials"
            if name in {"material", "treated_material"}
            else "processing_conditions"
        )
    if group in {"composition", "stack"}:
        if stage == "recipe" or name.startswith("precursor"):
            return "recipe_composition"
        if name in {"formula", "constituent", "material"}:
            return "material_identity"
        if name == "constituent_role":
            return "material_roles"
        if name.startswith("constituent_amount"):
            return "composition_amounts"
        if name in {"role", "material_form", "polarity", "stack"}:
            return "device_structure"
        return (
            "composition_properties"
            if name == "chlorineinresultingperovskitefilm"
            else "material_properties"
        )
    raise ValueError(f"No fact-kind contract for group {group}")


def fact_group(concept: tuple[str, str]) -> FactGroup:
    namespace, _ = concept
    domains: dict[str, FactGroup] = {
        "measurement": "performance",
        "material": "material_device_identity",
        "recipe": "processing",
        "processing": "processing",
        "aging_protocol": "stability",
        "aging_outcome": "stability",
    }
    return domains[namespace]
