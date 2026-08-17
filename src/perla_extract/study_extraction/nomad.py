"""Project rich study records into pinned NOMAD ingestion archives.

The rich extraction remains authoritative.  This module is deliberately a boundary:
it knows NOMAD's vocabulary and units, emits one archive per scientifically atomic
source record, and reports every value that cannot be projected without guessing.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from functools import lru_cache

from .models import (
    DeviceFamily,
    IndividualDevice,
    MaterialConstituent,
    ProcessingStep,
    ReportedValue,
    StabilityTest,
    StudyExtraction,
)
from .nomad_contract import (
    CompositionStatus,
    NOMADArchive,
    NOMADCell,
    NOMADComposition,
    NOMADCompositionProjection,
    NOMADConversionIssue,
    NOMADExport,
    NOMADExtractionMetadata,
    NOMADIon,
    NOMADLayer,
    NOMADProcessingStep,
    NOMADRecordMapping,
    NOMADStability,
    SourceKind,
)

_METRIC_NAMES = {
    "pce": {"pce", "powerconversionefficiency", "efficiency"},
    "jsc": {"jsc", "shortcircuitcurrentdensity"},
    "voc": {"voc", "opencircuitvoltage"},
    "ff": {"ff", "fillfactor"},
    "active_area": {"activearea", "devicearea", "aperturearea"},
}
_METRIC_UNITS = {
    "pce": "percent",
    "jsc": "milliampere / centimeter**2",
    "voc": "volt",
    "ff": "percent",
    "active_area": "centimeter**2",
}
_ROLE_NAMES = {
    "substrate": "Substrate",
    "transparent_electrode": "Contact",
    "hole_transport_layer": "Hole-transport",
    "electron_transport_layer": "Electron-transport",
    "absorber": "Absorber",
    "back_electrode": "Contact",
}
_SITE_ROLES = {
    "asite": "ions_a_site",
    "asitecation": "ions_a_site",
    "aion": "ions_a_site",
    "bsite": "ions_b_site",
    "bsitecation": "ions_b_site",
    "bion": "ions_b_site",
    "xsite": "ions_x_site",
    "xsiteanion": "ions_x_site",
    "xion": "ions_x_site",
}
_COEFFICIENT_NAMES = {"coefficient", "stoichiometriccoefficient", "fraction"}
_BAND_GAP_NAMES = {"bandgap", "opticalbandgap"}
_DIMENSIONALITY_NAMES = {"dimensionality", "perovskitedimensionality"}
_DIMENSIONALITIES = {"0D", "1D", "2D", "2D/3D", "3D", "Other"}
_ATMOSPHERES = {
    "Ambient air",
    "Dry air",
    "Air",
    "N2",
    "Ar",
    "He",
    "H2",
    "Vacuum",
    "Other",
    "Unknown",
}
_CONDITION_FIELDS = {
    "temperature": ("temperature", "degree_Celsius"),
    "duration": ("duration", "second"),
}
_STABILITY_CONDITIONS = {
    "temperature": ("temperature", "degree_Celsius"),
    "relativehumidity": ("humidity", "percent"),
    "humidity": ("humidity", "percent"),
    "lightintensity": ("light_intensity", "milliwatt / centimeter**2"),
    "illuminationintensity": ("light_intensity", "milliwatt / centimeter**2"),
}


def _key(value: str) -> str:
    """Canonicalize labels only for explicit source-to-target vocabulary matching."""

    return re.sub(
        r"[^a-z0-9]+", "", value.casefold().replace("μ", "u").replace("µ", "u")
    )


def _payload(value: ReportedValue) -> dict[str, object]:
    """Retain an atomic value and its evidence whenever NOMAD has no faithful field."""

    return {
        "name": value.name,
        "raw_value": value.raw_value,
        "value_number": value.value_number,
        "unit": value.unit,
        "evidence_blocks": [citation.block_id for citation in value.evidence],
    }


@lru_cache(maxsize=1)
def _unit_registry():
    """Create Pint lazily so importing schemas has no unit-registry side effects."""

    from pint import UnitRegistry

    registry = UnitRegistry()
    registry.define("sun = 1000 * watt / meter ** 2")
    return registry


def _pint_unit(unit: str) -> str:
    """Translate typography, not scientific meaning, before Pint parses a unit."""

    superscript = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺", "0123456789-+")
    value = (
        unit.strip()
        .replace("°C", "degree_Celsius")
        .replace("·", " * ")
        .replace("−", "-")
    )
    value = re.sub(
        r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺]+",
        lambda match: "**" + match.group(0).translate(superscript),
        value,
    )
    value = value.replace("^", "**")
    return re.sub(r"(?<=[A-Za-z])\s+([+-]?\d+)(?=\s|$|[/)*])", r"**\1", value)


def _convert(value: ReportedValue, target_unit: str) -> float | None:
    """Convert only an explicit numeric value with an explicit compatible unit."""

    from pint.errors import PintError

    if value.value_number is None or value.unit is None:
        return None
    unit = value.unit.strip()
    if target_unit == "percent":
        return (
            value.value_number
            if _key(unit) in {"percent", "percentage"} or unit == "%"
            else None
        )
    try:
        quantity = _unit_registry().Quantity(value.value_number, _pint_unit(unit))
        return float(quantity.to(target_unit).magnitude)
    except (PintError, TypeError, ValueError):
        return None


def _issue(
    issues: list[NOMADConversionIssue],
    code: str,
    kind: SourceKind,
    source_id: str,
    detail: str,
    path: str | None = None,
) -> None:
    """Append a conversion issue through one consistently shaped boundary."""

    issues.append(
        NOMADConversionIssue(
            code=code,
            source_kind=kind,
            source_id=source_id,
            path=path,
            detail=detail,
        )
    )


def _explicit_coefficient(constituent: MaterialConstituent) -> str | None:
    """Use a constituent amount as stoichiometry only when it says that it is one."""

    amount = constituent.amount
    if amount is None or amount.unit not in {None, ""}:
        return None
    return amount.raw_value if _key(amount.name) in _COEFFICIENT_NAMES else None


def _project_composition(family: DeviceFamily) -> NOMADCompositionProjection:
    """Map reported formula/site claims while refusing formula interpretation."""

    raw_formula = family.absorber_formula.raw_value if family.absorber_formula else None
    composition = NOMADComposition(long_form=raw_formula, formula=raw_formula)
    notes: list[str] = []
    band_gap = _unique_value(family.absorber_properties, _BAND_GAP_NAMES)
    band_gap_ev = _convert(band_gap, "electron_volt") if band_gap else None
    if band_gap_ev is not None:
        composition.band_gap = band_gap_ev
    elif band_gap is not None:
        notes.append("The reported band gap lacks an explicit eV-compatible value.")
    dimensionality = _unique_value(family.absorber_properties, _DIMENSIONALITY_NAMES)
    if dimensionality and dimensionality.raw_value in _DIMENSIONALITIES:
        composition.dimensionality = dimensionality.raw_value
    elif dimensionality is not None:
        notes.append(
            "The reported dimensionality does not match the pinned NOMAD vocabulary."
        )
    explicit_sites = 0
    incomplete_sites = 0
    for constituent in family.absorber_constituents:
        target = _SITE_ROLES.get(_key(constituent.role or ""))
        if target is None:
            continue
        explicit_sites += 1
        coefficient = _explicit_coefficient(constituent)
        if coefficient is None:
            incomplete_sites += 1
        getattr(composition, target).append(
            NOMADIon(abbreviation=constituent.name, coefficient=coefficient)
        )

    has_projected_property = (
        composition.band_gap is not None or composition.dimensionality is not None
    )
    if raw_formula is None and explicit_sites == 0 and not has_projected_property:
        status: CompositionStatus = "needs_review" if notes else "not_reported"
        result: NOMADComposition | None = None
    elif incomplete_sites:
        status = "needs_review"
        result = composition
        notes.append(
            "At least one explicitly labelled site ion lacks an explicitly labelled stoichiometric coefficient."
        )
    elif explicit_sites:
        status = "needs_review" if notes else "ready"
        result = composition
    else:
        status = "partial"
        result = composition
        if raw_formula is not None:
            notes.append(
                "The formula is preserved, but site ions were not explicit; the pinned NOMAD classic converter may require reviewed site assignments."
            )
    return NOMADCompositionProjection(
        family_id=family.family_id,
        status=status,
        raw_formula=raw_formula,
        nomad_composition=result,
        issues=notes,
    )


def _unique_value(
    values: Iterable[ReportedValue], names: set[str]
) -> ReportedValue | None:
    """Return a target value only when exactly one atomic source value matches."""

    matches = [value for value in values if _key(value.name) in names]
    return matches[0] if len(matches) == 1 else None


def _project_metrics(
    values: Iterable[ReportedValue],
    kind: SourceKind,
    source_id: str,
    issues: list[NOMADConversionIssue],
) -> tuple[dict[str, float], list[dict[str, object]]]:
    """Project unique compatible device metrics and retain every remainder."""

    grouped: dict[str, list[ReportedValue]] = {}
    remainder: list[dict[str, object]] = []
    for value in values:
        field = next(
            (
                name
                for name, aliases in _METRIC_NAMES.items()
                if _key(value.name) in aliases
            ),
            None,
        )
        if field is None:
            remainder.append(_payload(value))
        else:
            grouped.setdefault(field, []).append(value)
    projected: dict[str, float] = {}
    for field, matches in grouped.items():
        if len(matches) != 1:
            remainder.extend(_payload(value) for value in matches)
            _issue(
                issues,
                "ambiguous_metric",
                kind,
                source_id,
                f"{len(matches)} values match NOMAD field {field}; none was selected.",
                field,
            )
            continue
        converted = _convert(matches[0], _METRIC_UNITS[field])
        if converted is None:
            remainder.append(_payload(matches[0]))
            _issue(
                issues,
                "incompatible_metric",
                kind,
                source_id,
                f"{matches[0].name!r} has no explicit compatible number and unit.",
                field,
            )
        else:
            projected[field] = converted
    return projected, remainder


def _project_step(step: ProcessingStep) -> NOMADProcessingStep:
    """Populate generic NOMAD process fields and preserve all atomic source details."""

    projected: dict[str, object] = {
        "step_name": step.operation,
        "method": step.operation,
        "additional_parameters": {
            "source_step_id": step.step_id,
            "materials": step.materials,
            "conditions": [_payload(value) for value in step.conditions],
            "evidence_blocks": [citation.block_id for citation in step.evidence],
        },
    }
    for name, (field, unit) in _CONDITION_FIELDS.items():
        value = _unique_value(step.conditions, {name})
        converted = _convert(value, unit) if value else None
        if converted is not None:
            projected[field] = converted
    atmosphere = _unique_value(step.conditions, {"atmosphere"})
    if atmosphere and atmosphere.raw_value in _ATMOSPHERES:
        projected["atmosphere"] = atmosphere.raw_value
    antisolvent = _unique_value(step.conditions, {"antisolvent"})
    if antisolvent:
        projected["antisolvent"] = antisolvent.raw_value
    return NOMADProcessingStep.model_validate(projected)


def _project_family(
    family: DeviceFamily | None,
    composition: NOMADCompositionProjection | None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Project shared device construction once for every linked atomic record."""

    if family is None:
        return {}, {}
    steps_by_layer: dict[str, list[NOMADProcessingStep]] = {}
    for step in family.processing_steps:
        for layer_id in step.target_layer_ids:
            steps_by_layer.setdefault(layer_id, []).append(_project_step(step))
    layers: list[NOMADLayer] = []
    for layer in sorted(family.layers, key=lambda item: item.sequence or 10_000):
        thickness = _unique_value(layer.reported_properties, {"thickness"})
        thickness_nm = _convert(thickness, "nanometer") if thickness else None
        layers.append(
            NOMADLayer(
                name=layer.material,
                thickness=thickness_nm,
                functionality=_ROLE_NAMES.get(
                    layer.role, "Unknown" if layer.role == "not_reported" else "Other"
                ),
                deposition=steps_by_layer.get(layer.layer_id, []),
            )
        )
    architecture = {
        "p-i-n": "pin",
        "n-i-p": "nip",
        "tandem": "Other",
        "other": "Other",
        "not_reported": "Unknown",
    }[family.polarity]
    fields: dict[str, object] = {
        "device_architecture": architecture,
        "layers": layers,
        "layer_order": " | ".join(layer.name for layer in layers) or None,
    }
    if composition and composition.nomad_composition:
        fields["perovskite_composition"] = composition.nomad_composition
    context = {
        "family_id": family.family_id,
        "family_label": family.label,
        "variant": family.variant,
        "architecture_raw": family.architecture,
        "full_stack_raw": family.full_stack_raw,
        "unassigned_processing_steps": [
            _project_step(step).model_dump(mode="json", exclude_none=True)
            for step in family.processing_steps
            if not step.target_layer_ids
        ],
        "absorber_properties": [
            _payload(value) for value in family.absorber_properties
        ],
        "absorber_constituents": [
            item.model_dump(mode="json") for item in family.absorber_constituents
        ],
        "layer_reported_properties": [
            {
                "layer_id": layer.layer_id,
                "properties": [_payload(value) for value in layer.reported_properties],
            }
            for layer in family.layers
            if layer.reported_properties
        ],
    }
    return fields, context


def _base_cell(
    study: StudyExtraction,
    family: DeviceFamily | None,
    composition: NOMADCompositionProjection | None,
    model: str | None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Create publication and family fields shared by every emitted archive."""

    fields, context = _project_family(family, composition)
    doi = study.paper.doi
    if doi and not doi.startswith(("http://", "https://")):
        doi = "https://doi.org/" + re.sub(r"^doi:\s*", "", doi.strip(), flags=re.I)
    fields.update(
        {
            "DOI_number": doi,
            "publication_title": study.paper.title,
            "extraction_metadata": NOMADExtractionMetadata(
                model=model, model_version=model
            ),
        }
    )
    return fields, context


def _stability_summary(
    test: StabilityTest,
    issues: list[NOMADConversionIssue],
) -> NOMADStability:
    """Fill only unambiguous NOMAD stability summary fields from rich checkpoints."""

    fields: dict[str, object] = {}
    for value in test.conditions:
        target = _STABILITY_CONDITIONS.get(_key(value.name))
        if target is None:
            continue
        field, unit = target
        converted = _convert(value, unit)
        if converted is not None and field not in fields:
            fields[field] = converted
    final = test.checkpoints[-1]
    if final.time:
        duration = _convert(final.time, "hour")
        if duration is not None:
            fields["time"] = duration
    absolute_pce = {
        checkpoint.checkpoint_id: _unique_value(
            checkpoint.outcomes, _METRIC_NAMES["pce"]
        )
        for checkpoint in test.checkpoints
    }
    first = test.checkpoints[0]
    first_pce = absolute_pce[first.checkpoint_id]
    last_pce = absolute_pce[final.checkpoint_id]
    first_time = _convert(first.time, "hour") if first.time else None
    first_value = (
        _convert(first_pce, "percent")
        if first_pce is not None and first_time == 0
        else None
    )
    last_value = _convert(last_pce, "percent") if last_pce else None
    if first_value is not None:
        fields["PCE_at_start"] = first_value
    if last_value is not None:
        fields["PCE_at_end"] = last_value
    for checkpoint in test.checkpoints:
        if checkpoint.time and _convert(checkpoint.time, "hour") == 1000:
            value = absolute_pce[checkpoint.checkpoint_id]
            converted = _convert(value, "percent") if value else None
            if converted is not None:
                fields["PCE_after_1000_hours"] = converted
    _issue(
        issues,
        "stability_checkpoints_preserved_in_notes",
        "stability_test",
        test.test_id,
        "NOMAD stores a summary; the complete ordered checkpoints remain in additional_notes and extraction.json.",
        "stability",
    )
    return NOMADStability.model_validate(fields)


def _archive_name(index: int, kind: SourceKind, source_id: str) -> str:
    """Create a stable filesystem-safe name without changing scientific identifiers."""

    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", source_id).strip("._") or "record"
    return f"{index:04d}-{kind}-{slug}.archive.json"


def to_nomad_with_report(
    study: StudyExtraction, model: str | None = None
) -> NOMADExport:
    """Export every atomic record as a separate pinned NOMAD archive.

    Performance observations, population statistics, and stability tests are never
    merged.  Unmeasured devices and otherwise unrepresented families receive their
    own construction-only archives so absence of performance does not erase a device.
    """

    issues: list[NOMADConversionIssue] = []
    families: dict[str, DeviceFamily] = {}
    for family in study.device_families:
        if family.family_id in families:
            _issue(
                issues,
                "duplicate_id",
                "device_family",
                family.family_id,
                "The first family with this ID is used for record links.",
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
                "The first device with this ID is used for record links.",
            )
        else:
            devices[device.device_id] = device
        if device.family_id and device.family_id not in families:
            _issue(
                issues,
                "dangling_reference",
                "individual_device",
                device.device_id,
                f"Unknown family_id {device.family_id!r}; the device is still exported.",
                "family_id",
            )
    projections = [_project_composition(family) for family in study.device_families]
    compositions = {item.family_id: item for item in projections}
    for projection in projections:
        for detail in projection.issues:
            _issue(
                issues,
                "composition_needs_review",
                "device_family",
                projection.family_id,
                detail,
                "perovskite_composition",
            )
    archives: list[NOMADArchive] = []
    mappings: list[NOMADRecordMapping] = []
    represented_devices: set[str] = set()
    represented_families: set[str] = set()

    def add(
        kind: SourceKind,
        source_id: str,
        fields: dict[str, object],
        note: dict[str, object],
    ) -> None:
        index = len(archives)
        fields["additional_notes"] = json.dumps(
            note, sort_keys=True, ensure_ascii=False
        )
        archives.append(NOMADArchive(data=NOMADCell.model_validate(fields)))
        mappings.append(
            NOMADRecordMapping(
                source_kind=kind,
                source_id=source_id,
                archive_index=index,
                archive_file=_archive_name(index, kind, source_id),
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
                f"Unknown device_id {observation.device_id!r}; the observation is still exported.",
                "device_id",
            )
        family = families.get(device.family_id) if device and device.family_id else None
        fields, family_note = _base_cell(
            study, family, compositions.get(family.family_id) if family else None, model
        )
        metrics, remainder = _project_metrics(
            observation.metrics,
            "performance_observation",
            observation.observation_id,
            issues,
        )
        fields.update(metrics)
        add(
            "performance_observation",
            observation.observation_id,
            fields,
            {
                "source_kind": "performance_observation",
                "source_id": observation.observation_id,
                "device_id": observation.device_id,
                "measurement_type": observation.measurement_type,
                "scan_direction": observation.scan_direction,
                "champion_status": device.champion_status if device else None,
                "family": family_note,
                "unprojected_metrics": remainder,
            },
        )
        represented_devices.add(observation.device_id)
        if family:
            represented_families.add(family.family_id)

    for population in study.population_statistics:
        family = families.get(population.family_id) if population.family_id else None
        if population.family_id and family is None:
            _issue(
                issues,
                "dangling_reference",
                "population_statistic",
                population.population_id,
                f"Unknown family_id {population.family_id!r}; the statistic is still exported.",
                "family_id",
            )
        fields, family_note = _base_cell(
            study, family, compositions.get(family.family_id) if family else None, model
        )
        metrics, remainder = _project_metrics(
            population.metrics, "population_statistic", population.population_id, issues
        )
        fields.update(metrics)
        fields["number_devices"] = population.sample_size
        fields["averaged_quantities"] = population.statistic_type == "mean"
        add(
            "population_statistic",
            population.population_id,
            fields,
            {
                "source_kind": "population_statistic",
                "source_id": population.population_id,
                "statistic_type": population.statistic_type,
                "family": family_note,
                "unprojected_metrics": remainder,
            },
        )
        if family:
            represented_families.add(family.family_id)

    for stability in study.stability_tests:
        device = devices.get(stability.device_id) if stability.device_id else None
        family_id = stability.family_id or (device.family_id if device else None)
        family = families.get(family_id) if family_id else None
        if stability.device_id and device is None:
            _issue(
                issues,
                "dangling_reference",
                "stability_test",
                stability.test_id,
                f"Unknown device_id {stability.device_id!r}; the test is still exported.",
                "device_id",
            )
        if family_id and family is None:
            _issue(
                issues,
                "dangling_reference",
                "stability_test",
                stability.test_id,
                f"Unknown family_id {family_id!r}; the test is still exported.",
                "family_id",
            )
        fields, family_note = _base_cell(
            study, family, compositions.get(family.family_id) if family else None, model
        )
        fields["stability"] = _stability_summary(stability, issues)
        add(
            "stability_test",
            stability.test_id,
            fields,
            {
                "source_kind": "stability_test",
                "source_id": stability.test_id,
                "device_id": stability.device_id,
                "link_status": stability.link_status,
                "family": family_note,
                "conditions": [_payload(value) for value in stability.conditions],
                "checkpoints": [
                    checkpoint.model_dump(mode="json")
                    for checkpoint in stability.checkpoints
                ],
            },
        )
        if device:
            represented_devices.add(device.device_id)
        if family:
            represented_families.add(family.family_id)

    for device in study.individual_devices:
        if device.device_id in represented_devices:
            continue
        family = families.get(device.family_id) if device.family_id else None
        fields, family_note = _base_cell(
            study, family, compositions.get(family.family_id) if family else None, model
        )
        add(
            "individual_device",
            device.device_id,
            fields,
            {
                "source_kind": "individual_device",
                "source_id": device.device_id,
                "label": device.label,
                "variant": device.variant,
                "champion_status": device.champion_status,
                "selection_basis": device.selection_basis,
                "family": family_note,
            },
        )
        if family:
            represented_families.add(family.family_id)

    for family in study.device_families:
        if family.family_id in represented_families:
            continue
        fields, family_note = _base_cell(
            study, family, compositions[family.family_id], model
        )
        add(
            "device_family",
            family.family_id,
            fields,
            {
                "source_kind": "device_family",
                "source_id": family.family_id,
                "family": family_note,
            },
        )

    for link in study.identity_links:
        _issue(
            issues,
            "identity_link_not_collapsed",
            link.entity_kind,
            link.link_id,
            "Identity-linked candidates remain separate archives to avoid discarding conflicting evidence.",
        )
    return NOMADExport(
        archives=archives,
        mappings=mappings,
        composition_projections=projections,
        issues=issues,
    )
