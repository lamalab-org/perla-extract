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
    IndividualDevice,
    ProcessingStep,
    ReportedValue,
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
    """Pair reduced rows with traceable mappings and unavoidable information loss.

    Consumers should inspect ``issues`` rather than treating a schema-valid reduced
    row as proof that every rich field was represented faithfully.
    """

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


def _normalized_key(value: str) -> str:
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


def _record_conversion_issue(
    issues: list[ConversionIssue], code: str, kind: str, source_id: str, detail: str
) -> None:
    issues.append(
        ConversionIssue(code=code, source_kind=kind, source_id=source_id, detail=detail)
    )


def _reported_value_payload(reported_value: ReportedValue) -> dict:
    """Serialize an unprojected value without discarding its evidence citations."""

    return {
        "name": reported_value.name,
        "raw_value": reported_value.raw_value,
        "value_number": reported_value.value_number,
        "unit": reported_value.unit,
        "evidence_blocks": [ref.block_id for ref in reported_value.evidence],
    }


def _metric_field(reported_value: ReportedValue) -> str | None:
    """Apply legacy metric aliases only at export, never during extraction."""

    name = _normalized_key(reported_value.name)
    return next(
        (field for field, aliases in _METRIC_NAMES.items() if name in aliases), None
    )


def _project_metric(reported_value: ReportedValue, field: str) -> dict | None:
    """Project a metric only when its number and unit already fit the reduced schema."""

    if reported_value.value_number is None:
        return None
    unit = _normalized_key(reported_value.unit or "")
    if field == "pce" and (
        reported_value.unit == "%" or unit in {"percent", "percentage"}
    ):
        return {"value": reported_value.value_number, "unit": "%"}
    if field == "jsc":
        units = {
            "macm2": "mA cm^-2",
            "acm2": "A cm^-2",
            "am2": "A m^-2",
            "mam2": "mA m^-2",
            "uacm2": "uA cm^-2",
        }
        normalized = units.get(unit)
        return (
            {"value": reported_value.value_number, "unit": normalized}
            if normalized
            else None
        )
    if field == "voc" and unit in {"v", "mv"}:
        return {
            "value": reported_value.value_number,
            "unit": "V" if unit == "v" else "mV",
        }
    if field == "ff" and (
        reported_value.unit == "%" or unit in {"percent", "percentage"}
    ):
        return {"value": reported_value.value_number}
    return None


def _project_performance_metrics(
    reported_values: Iterable[ReportedValue],
    kind: str,
    source_id: str,
    issues: list[ConversionIssue],
) -> tuple[dict, list[dict]]:
    """Project unambiguous performance values and return every remainder verbatim."""

    values_by_metric: dict[str, list[ReportedValue]] = {}
    remainder: list[dict] = []
    for reported_value in reported_values:
        field = _metric_field(reported_value)
        if field is None:
            remainder.append(_reported_value_payload(reported_value))
        else:
            values_by_metric.setdefault(field, []).append(reported_value)
    projected: dict = {}
    for field, matching in values_by_metric.items():
        if len(matching) != 1:
            remainder.extend(
                _reported_value_payload(reported_value) for reported_value in matching
            )
            _record_conversion_issue(
                issues,
                "ambiguous_metric",
                kind,
                source_id,
                f"Multiple {field} reported values were not flattened.",
            )
            continue
        value = _project_metric(matching[0], field)
        if value is None:
            remainder.append(_reported_value_payload(matching[0]))
            _record_conversion_issue(
                issues,
                "incompatible_metric_unit",
                kind,
                source_id,
                f"{matching[0].name} was retained in notes.",
            )
        else:
            projected[field] = value
    return projected, remainder


def _reduced_processing_step(step: ProcessingStep) -> dict:
    """Preserve arbitrary processing values instead of forcing a lossy projection."""

    return {
        "step_name": step.operation,
        "method": step.operation,
        "atmosphere": None,
        "additional_parameters": {
            "source_step_id": step.step_id,
            "materials": step.materials,
            "conditions": [
                _reported_value_payload(reported_value)
                for reported_value in step.conditions
            ],
        },
    }


def _project_family(family: DeviceFamily | None) -> tuple[dict, dict]:
    """Project stack structure while keeping multi-absorber data out of a flat slot.

    The historical reduced schema can represent only one composition.  Choosing one
    side of a tandem would silently change meaning, so only an unambiguous single
    absorber is projected and every scoped absorber remains available in notes.
    """

    if family is None:
        return {}, {}
    architecture = {
        "p-i-n": "pin",
        "n-i-p": "nip",
        "tandem": "Other",
        "other": "Other",
        "not_reported": None,
    }[family.polarity]
    absorber = family.absorbers[0] if len(family.absorbers) == 1 else None
    composition: dict = (
        {"formula": absorber.formula.raw_value}
        if absorber is not None and absorber.formula is not None
        else {}
    )
    steps_by_layer: dict[str, list[dict]] = {}
    for step in family.processing_steps:
        for layer_id in step.target_layer_ids:
            steps_by_layer.setdefault(layer_id, []).append(
                _reduced_processing_step(step)
            )
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
        "absorbers": [
            {
                "absorber_id": item.absorber_id,
                "layer_id": item.layer_id,
                "label": item.label,
                "formula": (
                    _reported_value_payload(item.formula) if item.formula else None
                ),
                "constituents": [
                    {
                        "name": constituent.name,
                        "role": constituent.role,
                        "amount": (
                            _reported_value_payload(constituent.amount)
                            if constituent.amount
                            else None
                        ),
                        "evidence_blocks": [
                            ref.block_id for ref in constituent.evidence
                        ],
                    }
                    for constituent in item.constituents
                ],
                "properties": [
                    _reported_value_payload(reported_value)
                    for reported_value in item.properties
                ],
                "evidence_blocks": [ref.block_id for ref in item.evidence],
            }
            for item in family.absorbers
        ],
        "layer_reported_properties": [
            {
                "layer_id": layer.layer_id,
                "material": layer.material,
                "reported_properties": [
                    _reported_value_payload(reported_value)
                    for reported_value in layer.reported_properties
                ],
            }
            for layer in family.layers
            if layer.reported_properties
        ],
        "layer_details": [
            {
                "layer_id": layer.layer_id,
                "role": layer.role,
                "material": layer.material,
                "constituents": [
                    {
                        "name": constituent.name,
                        "role": constituent.role,
                        "amount": (
                            _reported_value_payload(constituent.amount)
                            if constituent.amount
                            else None
                        ),
                    }
                    for constituent in layer.constituents
                ],
                "material_form_raw": layer.material_form_raw,
                "material_form": layer.material_form,
            }
            for layer in family.layers
        ],
        "reduced_composition_omitted_reason": (
            "multiple scoped absorbers cannot be represented by the reduced schema"
            if len(family.absorbers) > 1
            else None
        ),
        "unassigned_processing_steps": [
            _reduced_processing_step(step)
            for step in family.processing_steps
            if not step.target_layer_ids
        ],
    }
    return {
        "perovskite_composition": composition or None,
        "device_architecture": architecture,
        "layers": layers or None,
    }, rich_only


def _reduced_performance_aggregation(
    device: IndividualDevice | None, measurement_type: str
) -> str:
    """Choose aggregation from explicit source semantics, never metric magnitude."""

    if measurement_type == "stabilized_power_output":
        return "stabilized"
    if device and (
        device.champion_status == "yes" or device.selection_basis == "champion"
    ):
        return "champion"
    return "single_device"


def _build_reduced_cell(fields: dict, note: dict) -> PerovskiteSolarCell:
    """Validate one reduced row and store its rich provenance as stable JSON."""

    supported = PerovskiteSolarCell.model_fields
    return PerovskiteSolarCell.model_validate(
        {
            **{key: value for key, value in fields.items() if key in supported},
            "additional_notes": json.dumps(note, sort_keys=True, ensure_ascii=False),
        }
    )


def to_reduced_with_report(study: StudyExtraction) -> ReducedExport:
    """Project rich records without collapsing scientifically distinct results.

    Every observation, population statistic, and stability test becomes a separate
    reduced row. Values that cannot be represented faithfully stay in structured
    ``additional_notes`` and produce an issue.
    """

    issues: list[ConversionIssue] = []
    families: dict[str, DeviceFamily] = {}
    for family in study.device_families:
        if family.family_id in families:
            _record_conversion_issue(
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
            _record_conversion_issue(
                issues,
                "duplicate_id",
                "individual_device",
                device.device_id,
                "The first device with this ID is used for references.",
            )
        else:
            devices[device.device_id] = device
        if device.family_id and device.family_id not in families:
            _record_conversion_issue(
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

    def add_reduced_cell(kind: str, source_id: str, cell: PerovskiteSolarCell) -> None:
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
            _record_conversion_issue(
                issues,
                "dangling_reference",
                "performance_observation",
                observation.observation_id,
                f"Unknown device_id {observation.device_id!r}; metrics are still exported.",
            )
        family = families.get(device.family_id) if device and device.family_id else None
        family_fields, family_note = _project_family(family)
        metric_fields, remainder = _project_performance_metrics(
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
            "aggregation": _reduced_performance_aggregation(
                device, observation.measurement_type
            ),
            "champion_status": (device.champion_status if device else "not_reported"),
            "selection_basis": (device.selection_basis if device else "not_reported"),
            "device_reported_properties": [
                _reported_value_payload(value) for value in device.reported_properties
            ]
            if device
            else [],
            "unprojected_metrics": remainder,
        }
        add_reduced_cell(
            "performance_observation",
            observation.observation_id,
            _build_reduced_cell(
                {
                    **family_fields,
                    **metric_fields,
                    "performance_aggregation": _reduced_performance_aggregation(
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
        family_fields, family_note = _project_family(family)
        add_reduced_cell(
            "individual_device",
            device.device_id,
            _build_reduced_cell(
                {
                    **family_fields,
                    "performance_aggregation": _reduced_performance_aggregation(
                        device, "not_reported"
                    ),
                },
                {
                    "record_kind": "individual_device",
                    "device_id": device.device_id,
                    "label": device.label,
                    "variant": device.variant,
                    "aggregation": _reduced_performance_aggregation(
                        device, "not_reported"
                    ),
                    "champion_status": device.champion_status,
                    "selection_basis": device.selection_basis,
                    "reported_properties": [
                        _reported_value_payload(value)
                        for value in device.reported_properties
                    ],
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
            _record_conversion_issue(
                issues,
                "dangling_reference",
                "population_statistic",
                population.population_id,
                f"Unknown family_id {population.family_id!r}; metrics are still exported.",
            )
        family_fields, family_note = _project_family(family)
        metric_fields, remainder = _project_performance_metrics(
            population.metrics, "population_statistic", population.population_id, issues
        )
        aggregation = population_aggregation.get(
            population.statistic_type, "distribution"
        )
        add_reduced_cell(
            "population_statistic",
            population.population_id,
            _build_reduced_cell(
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
            _record_conversion_issue(
                issues,
                "dangling_reference",
                "stability_test",
                test.test_id,
                f"Unknown device_id {test.device_id!r}; the test is still exported.",
            )
        family_id = test.family_id or (device.family_id if device else None)
        family = families.get(family_id) if family_id else None
        if family_id and family is None:
            _record_conversion_issue(
                issues,
                "dangling_reference",
                "stability_test",
                test.test_id,
                f"Unknown family_id {family_id!r}; the test is still exported.",
            )
        family_fields, family_note = _project_family(family)
        raw_stability = {
            "test_id": test.test_id,
            "conditions": [
                _reported_value_payload(reported_value)
                for reported_value in test.conditions
            ],
            "checkpoints": [
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "time": _reported_value_payload(checkpoint.time)
                    if checkpoint.time
                    else None,
                    "conditions": [
                        _reported_value_payload(reported_value)
                        for reported_value in checkpoint.conditions
                    ],
                    "outcomes": [
                        _reported_value_payload(reported_value)
                        for reported_value in checkpoint.outcomes
                    ],
                }
                for checkpoint in test.checkpoints
            ],
        }
        add_reduced_cell(
            "stability_test",
            test.test_id,
            _build_reduced_cell(
                family_fields,
                {
                    "record_kind": "stability_test",
                    "test_id": test.test_id,
                    "device_id": test.device_id,
                    "family_id": family_id,
                    "specimen_label": test.specimen_label,
                    "link_status": test.link_status,
                    "device_reported_properties": [
                        _reported_value_payload(value)
                        for value in device.reported_properties
                    ]
                    if device
                    else [],
                    "family": family_note,
                    "rich_stability": raw_stability,
                },
            ),
        )
        _record_conversion_issue(
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
        family_fields, family_note = _project_family(family)
        add_reduced_cell(
            "device_family",
            family.family_id,
            _build_reduced_cell(
                family_fields, {"record_kind": "device_family", "family": family_note}
            ),
        )

    return ReducedExport(
        cells=PerovskiteSolarCells(cells=cells), mappings=mappings, issues=issues
    )


def to_reduced(study: StudyExtraction) -> PerovskiteSolarCells:
    """Return reduced rows while deliberately discarding the conversion report.

    Prefer :func:`to_reduced_with_report` for scientific pipelines, where explicit
    mappings and losses are normally part of the provenance record.
    """

    return to_reduced_with_report(study).cells
