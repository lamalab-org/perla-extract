"""Deterministically export the rich study schema to PERLA's reduced schema.

The two schemas are not isomorphic.  This adapter therefore never claims a
round-trip: it emits separate reduced rows for separate source record types and
returns an explicit report of values that could not be represented faithfully.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from perla_extract.pydantic_model_reduced import (
    PerovskiteSolarCell,
    PerovskiteSolarCells,
)

from .models import (
    DeviceFamily,
    Fact,
    IndividualDevice,
    ProcessingStep,
    StudyExtraction,
)


class ConversionIssue(BaseModel):
    """Describe one explicit limitation or rejected projection."""

    model_config = ConfigDict(extra="forbid")
    code: str
    source_kind: str
    source_id: str
    detail: str


class RecordMapping(BaseModel):
    """Link one rich source record to its reduced cell index."""

    model_config = ConfigDict(extra="forbid")
    source_kind: str
    source_id: str
    target_cell_index: int = Field(ge=0)


class ReducedExport(BaseModel):
    """Bundle reduced cells with provenance and unavoidable conversion losses."""

    model_config = ConfigDict(extra="forbid")
    cells: PerovskiteSolarCells
    mappings: list[RecordMapping]
    issues: list[ConversionIssue]


_METRIC_NAMES = {
    "pce": {"pce", "powerconversionefficiency", "efficiency"},
    "jsc": {"jsc", "shortcircuitcurrentdensity"},
    "voc": {"voc", "opencircuitvoltage"},
    "ff": {"ff", "fillfactor"},
}
_ROLE_NAMES = {
    "substrate": "Substrate",
    "transparent_electrode": "Contact",
    "hole_transport_layer": "Hole-transport",
    "electron_transport_layer": "Electron-transport",
    "absorber": "Absorber",
    "back_electrode": "Contact",
}


def _key(value: str) -> str:
    """Normalize labels only for declared schema-to-schema field matching."""

    normalized = (
        value.casefold()
        .replace("µ", "u")
        .replace("μ", "u")
        .replace("²", "2")
        .replace("⁻", "-")
        .replace("−", "-")
    )
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _issue(
    issues: list[ConversionIssue], code: str, kind: str, source_id: str, detail: str
) -> None:
    """Append a consistently shaped conversion issue."""

    issues.append(
        ConversionIssue(code=code, source_kind=kind, source_id=source_id, detail=detail)
    )


def _fact_payload(fact: Fact) -> dict:
    """Serialize an unprojected fact without discarding its evidence references."""

    return {
        "name": fact.name,
        "raw_value": fact.raw_value,
        "value_number": fact.value_number,
        "unit": fact.unit,
        "evidence_blocks": [ref.block_id for ref in fact.evidence],
    }


def _metric_field(fact: Fact) -> str | None:
    """Return the reduced metric field for an explicitly supported fact name."""

    name = _key(fact.name)
    return next(
        (field for field, aliases in _METRIC_NAMES.items() if name in aliases), None
    )


def _project_metric(fact: Fact, field: str) -> dict | None:
    """Project a metric only when its number and unit already fit the reduced schema."""

    if fact.value_number is None:
        return None
    unit = _key(fact.unit or "")
    if field == "pce" and (fact.unit == "%" or unit in {"percent", "percentage"}):
        return {"value": fact.value_number, "unit": "%"}
    if field == "jsc":
        units = {
            "macm2": "mA cm^-2",
            "acm2": "A cm^-2",
            "am2": "A m^-2",
            "mam2": "mA m^-2",
            "uacm2": "uA cm^-2",
        }
        normalized = units.get(unit)
        return {"value": fact.value_number, "unit": normalized} if normalized else None
    if field == "voc" and unit in {"v", "mv"}:
        return {"value": fact.value_number, "unit": "V" if unit == "v" else "mV"}
    if field == "ff" and (fact.unit == "%" or unit in {"percent", "percentage"}):
        return {"value": fact.value_number}
    return None


def _metrics(
    facts: Iterable[Fact], kind: str, source_id: str, issues: list[ConversionIssue]
) -> tuple[dict, list[dict]]:
    """Project unambiguous performance facts and return every remainder verbatim."""

    candidates: dict[str, list[Fact]] = {}
    remainder: list[dict] = []
    for fact in facts:
        field = _metric_field(fact)
        if field is None:
            remainder.append(_fact_payload(fact))
        else:
            candidates.setdefault(field, []).append(fact)
    projected: dict = {}
    for field, matching in candidates.items():
        if len(matching) != 1:
            remainder.extend(_fact_payload(fact) for fact in matching)
            _issue(
                issues,
                "ambiguous_metric",
                kind,
                source_id,
                f"Multiple {field} facts were not flattened.",
            )
            continue
        value = _project_metric(matching[0], field)
        if value is None:
            remainder.append(_fact_payload(matching[0]))
            _issue(
                issues,
                "incompatible_metric_unit",
                kind,
                source_id,
                f"{matching[0].name} was retained in notes.",
            )
        else:
            projected[field] = value
    return projected, remainder


def _processing(step: ProcessingStep) -> dict:
    """Keep generic processing conditions in the reduced extension dictionary."""

    return {
        "step_name": step.operation,
        "method": step.operation,
        "atmosphere": None,
        "additional_parameters": {
            "source_step_id": step.step_id,
            "materials": step.materials,
            "conditions": [_fact_payload(fact) for fact in step.conditions],
        },
    }


def _family_fields(family: DeviceFamily | None) -> tuple[dict, dict]:
    """Project stack structure while returning rich-only family data for notes."""

    if family is None:
        return {}, {}
    architecture = {
        "p-i-n": "pin",
        "n-i-p": "nip",
        "tandem": "Other",
        "other": "Other",
        "not_reported": None,
    }[family.polarity]
    composition: dict = (
        {"formula": family.absorber_formula.raw_value}
        if family.absorber_formula
        else {}
    )
    steps_by_layer: dict[str, list[dict]] = {}
    for step in family.processing_steps:
        for layer_id in step.target_layer_ids:
            steps_by_layer.setdefault(layer_id, []).append(_processing(step))
    layers = []
    for layer in sorted(family.layers, key=lambda item: item.sequence or 10_000):
        layer_data = {
            "name": layer.material,
            "functionality": (
                None
                if layer.role == "not_reported"
                else _ROLE_NAMES.get(layer.role, "Other")
            ),
            "deposition": steps_by_layer.get(layer.layer_id) or None,
        }
        layers.append(layer_data)

    rich_only = {
        "family_id": family.family_id,
        "family_label": family.label,
        "variant": family.variant,
        "architecture_raw": family.architecture,
        "full_stack_raw": family.full_stack_raw,
        "absorber_constituents": [
            {
                "name": item.name,
                "role": item.role,
                "amount": _fact_payload(item.amount) if item.amount else None,
                "evidence_blocks": [ref.block_id for ref in item.evidence],
            }
            for item in family.absorber_constituents
        ],
        "absorber_formula": (
            _fact_payload(family.absorber_formula) if family.absorber_formula else None
        ),
        "layer_details": [
            {
                "layer_id": layer.layer_id,
                "material": layer.material,
                "details": [_fact_payload(fact) for fact in layer.details],
            }
            for layer in family.layers
            if layer.details
        ],
        "unprojected_absorber_properties": [
            _fact_payload(fact) for fact in family.absorber_properties
        ],
        "unassigned_processing_steps": [
            _processing(step)
            for step in family.processing_steps
            if not step.target_layer_ids
        ],
    }
    return {
        "perovskite_composition": composition or None,
        "device_architecture": architecture,
        "layers": layers or None,
    }, rich_only


def _device_aggregation(device: IndividualDevice | None, measurement_type: str) -> str:
    """Choose aggregation from explicit source semantics, never metric magnitude."""

    if measurement_type == "stabilized_power_output":
        return "stabilized"
    if device and (
        device.champion_status == "yes" or device.selection_basis == "champion"
    ):
        return "champion"
    return "single_device"


def _cell(fields: dict, note: dict) -> PerovskiteSolarCell:
    """Validate one reduced row and store its rich provenance as stable JSON."""

    supported = PerovskiteSolarCell.model_fields
    return PerovskiteSolarCell.model_validate(
        {
            **{key: value for key, value in fields.items() if key in supported},
            "additional_notes": json.dumps(note, sort_keys=True, ensure_ascii=False),
        }
    )


def to_reduced_with_report(study: StudyExtraction) -> ReducedExport:
    """Export every rich record type to a separate reduced cell with a loss report."""

    issues: list[ConversionIssue] = []
    for group in study.equivalence_groups:
        _issue(
            issues,
            "equivalence_not_collapsed",
            group.entity_kind,
            group.equivalence_id,
            "Equivalent rich candidates remain separate reduced rows so conversion does not discard or heuristically merge conflicting details.",
        )
    families: dict[str, DeviceFamily] = {}
    for family in study.device_families:
        if family.family_id in families:
            _issue(
                issues,
                "duplicate_id",
                "device_family",
                family.family_id,
                "The first family with this ID is used for references.",
            )
        else:
            families[family.family_id] = family
    devices: dict[str, IndividualDevice] = {}
    for device in study.individual_devices:
        if device.device_id in devices:
            _issue(
                issues,
                "duplicate_id",
                "individual_device",
                device.device_id,
                "The first device with this ID is used for references.",
            )
        else:
            devices[device.device_id] = device
        if device.family_id and device.family_id not in families:
            _issue(
                issues,
                "dangling_reference",
                "individual_device",
                device.device_id,
                f"Unknown family_id {device.family_id!r}; device data are still exported.",
            )
    mappings: list[RecordMapping] = []
    cells: list[PerovskiteSolarCell] = []
    represented_devices: set[str] = set()
    represented_families: set[str] = set()

    def add(kind: str, source_id: str, cell: PerovskiteSolarCell) -> None:
        index = len(cells)
        cells.append(cell)
        mappings.append(
            RecordMapping(
                source_kind=kind, source_id=source_id, target_cell_index=index
            )
        )

    for observation in study.performance_observations:
        device = devices.get(observation.device_id)
        if device is None:
            _issue(
                issues,
                "dangling_reference",
                "performance_observation",
                observation.observation_id,
                f"Unknown device_id {observation.device_id!r}; metrics are still exported.",
            )
        family = families.get(device.family_id) if device and device.family_id else None
        family_fields, family_note = _family_fields(family)
        metric_fields, remainder = _metrics(
            observation.metrics,
            "performance_observation",
            observation.observation_id,
            issues,
        )
        note = {
            "record_kind": "performance_observation",
            "observation_id": observation.observation_id,
            "device_id": observation.device_id,
            "family": family_note,
            "measurement_type": observation.measurement_type,
            "scan_direction": observation.scan_direction,
            "aggregation": _device_aggregation(device, observation.measurement_type),
            "champion_status": (device.champion_status if device else "not_reported"),
            "selection_basis": (device.selection_basis if device else "not_reported"),
            "unprojected_metrics": remainder,
        }
        add(
            "performance_observation",
            observation.observation_id,
            _cell(
                {
                    **family_fields,
                    **metric_fields,
                    "performance_aggregation": _device_aggregation(
                        device, observation.measurement_type
                    ),
                },
                note,
            ),
        )
        represented_devices.add(observation.device_id)
        if family:
            represented_families.add(family.family_id)

    for device in study.individual_devices:
        if device.device_id in represented_devices:
            continue
        family = families.get(device.family_id) if device.family_id else None
        family_fields, family_note = _family_fields(family)
        add(
            "individual_device",
            device.device_id,
            _cell(
                {
                    **family_fields,
                    "performance_aggregation": _device_aggregation(
                        device, "not_reported"
                    ),
                },
                {
                    "record_kind": "individual_device",
                    "device_id": device.device_id,
                    "label": device.label,
                    "variant": device.variant,
                    "aggregation": _device_aggregation(device, "not_reported"),
                    "champion_status": device.champion_status,
                    "selection_basis": device.selection_basis,
                    "family": family_note,
                },
            ),
        )
        if family:
            represented_families.add(family.family_id)

    population_aggregation = {
        "mean": "mean",
        "median": "median",
        "distribution": "distribution",
    }
    for population in study.population_statistics:
        family = families.get(population.family_id) if population.family_id else None
        if population.family_id and family is None:
            _issue(
                issues,
                "dangling_reference",
                "population_statistic",
                population.population_id,
                f"Unknown family_id {population.family_id!r}; metrics are still exported.",
            )
        family_fields, family_note = _family_fields(family)
        metric_fields, remainder = _metrics(
            population.metrics, "population_statistic", population.population_id, issues
        )
        aggregation = population_aggregation.get(
            population.statistic_type, "distribution"
        )
        add(
            "population_statistic",
            population.population_id,
            _cell(
                {
                    **family_fields,
                    **metric_fields,
                    "number_devices": population.sample_size,
                    "averaged_quantities": (
                        True if population.statistic_type == "mean" else None
                    ),
                    "performance_aggregation": aggregation,
                },
                {
                    "record_kind": "population_statistic",
                    "population_id": population.population_id,
                    "label": population.label,
                    "statistic_type": population.statistic_type,
                    "aggregation": aggregation,
                    "family": family_note,
                    "unprojected_metrics": remainder,
                },
            ),
        )
        if family:
            represented_families.add(family.family_id)

    for test in study.stability_tests:
        device = devices.get(test.device_id) if test.device_id else None
        if test.device_id and device is None:
            _issue(
                issues,
                "dangling_reference",
                "stability_test",
                test.test_id,
                f"Unknown device_id {test.device_id!r}; the test is still exported.",
            )
        family_id = test.family_id or (device.family_id if device else None)
        family = families.get(family_id) if family_id else None
        if family_id and family is None:
            _issue(
                issues,
                "dangling_reference",
                "stability_test",
                test.test_id,
                f"Unknown family_id {family_id!r}; the test is still exported.",
            )
        family_fields, family_note = _family_fields(family)
        raw_stability = {
            "test_id": test.test_id,
            "conditions": [_fact_payload(fact) for fact in test.conditions],
            "checkpoints": [
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "time": _fact_payload(checkpoint.time) if checkpoint.time else None,
                    "outcomes": [_fact_payload(fact) for fact in checkpoint.outcomes],
                }
                for checkpoint in test.checkpoints
            ],
        }
        add(
            "stability_test",
            test.test_id,
            _cell(
                family_fields,
                {
                    "record_kind": "stability_test",
                    "test_id": test.test_id,
                    "device_id": test.device_id,
                    "family_id": family_id,
                    "specimen_label": test.specimen_label,
                    "link_status": test.link_status,
                    "family": family_note,
                    "rich_stability": raw_stability,
                },
            ),
        )
        _issue(
            issues,
            "rich_stability_in_notes",
            "stability_test",
            test.test_id,
            "Ordered checkpoints are preserved in additional_notes because the reduced Stability model cannot represent them losslessly.",
        )
        if family:
            represented_families.add(family.family_id)

    for family in study.device_families:
        if family.family_id in represented_families:
            continue
        family_fields, family_note = _family_fields(family)
        add(
            "device_family",
            family.family_id,
            _cell(
                family_fields, {"record_kind": "device_family", "family": family_note}
            ),
        )

    return ReducedExport(
        cells=PerovskiteSolarCells(cells=cells), mappings=mappings, issues=issues
    )


def to_reduced(study: StudyExtraction) -> PerovskiteSolarCells:
    """Return the deterministic reduced export when a report is not required."""

    return to_reduced_with_report(study).cells
