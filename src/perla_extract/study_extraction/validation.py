"""Annotate grounding and link problems without deleting extracted records."""

from __future__ import annotations

from collections import Counter

from .evidence import assembled_from_quotes, source_contains_text
from .identifiers import entity_id_lists
from .models import EvidenceBlock, StudyExtraction


def validate_study(
    extraction: StudyExtraction, blocks: list[EvidenceBlock]
) -> dict[str, object]:
    """Annotate textual grounding and relationship failures without deleting output.

    These deterministic checks prove that quotes and raw reported values occur in supplied
    blocks and that identifiers resolve. They do not prove semantic correctness or
    recall, so the complete extraction remains available for human review.
    """

    block_by_id = {block.block_id: block for block in blocks}
    issues: list[dict[str, str]] = []
    total_reported_values = 0
    source_verified_values = 0
    source_assembled_values = 0
    verified_values: list[dict[str, object]] = []

    def issue(path: str, reason: str) -> None:
        issues.append({"path": path, "reason": reason})

    def evidence_supported(items: list[dict], path: str) -> bool:
        supported = bool(items)
        for index, reference in enumerate(items):
            block = block_by_id.get(reference.get("block_id"))
            reference_path = f"{path}.evidence[{index}]"
            if block is None:
                issue(reference_path, "unknown block_id")
                supported = False
            elif not source_contains_text(block.text, reference.get("quote")):
                issue(reference_path, "quote not found in cited block")
                supported = False
        return supported

    def walk(value: object, path: str = "$") -> None:
        nonlocal total_reported_values, source_verified_values, source_assembled_values
        if isinstance(value, dict):
            if {"name", "raw_value", "evidence"} <= value.keys():
                total_reported_values += 1
                evidence_ok = evidence_supported(value["evidence"], path)
                raw_direct = any(
                    (block := block_by_id.get(reference.get("block_id"))) is not None
                    and source_contains_text(block.text, value["raw_value"])
                    for reference in value["evidence"]
                )
                raw_assembled = evidence_ok and assembled_from_quotes(
                    value["raw_value"], value["evidence"]
                )
                raw_ok = raw_direct or raw_assembled
                if not raw_ok:
                    issue(path, "raw_value not found in cited evidence")
                if evidence_ok and raw_ok:
                    source_verified_values += 1
                    source_assembled_values += int(raw_assembled and not raw_direct)
                    verified_values.append({"path": path, **value})
            elif "material_form_raw" in value and isinstance(
                value.get("evidence"), list
            ):
                evidence_supported(value["evidence"], path)
                raw_form = value.get("material_form_raw")
                if raw_form is not None and not any(
                    (block := block_by_id.get(reference.get("block_id"))) is not None
                    and source_contains_text(block.text, raw_form)
                    for reference in value["evidence"]
                ):
                    issue(
                        f"{path}.material_form_raw",
                        "material_form_raw not found in cited evidence",
                    )
            elif isinstance(value.get("evidence"), list):
                evidence_supported(value["evidence"], path)
            for key, item in value.items():
                if key != "evidence":
                    walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    data = extraction.model_dump(mode="json")
    walk(data)
    identifiers = entity_id_lists(extraction)
    family_ids = identifiers["device_family"]
    device_ids = identifiers["individual_device"]
    families, devices = set(family_ids), set(device_ids)
    collections = {
        "device_family": ("$.device_families", "family_id"),
        "individual_device": ("$.individual_devices", "device_id"),
        "performance_observation": (
            "$.performance_observations",
            "observation_id",
        ),
        "population_statistic": ("$.population_statistics", "population_id"),
        "stability_test": ("$.stability_tests", "test_id"),
    }
    for kind, ids in identifiers.items():
        if len(ids) != len(set(ids)):
            path, field = collections[kind]
            issue(path, f"duplicate {field}")
    absorber_ids = [
        absorber.absorber_id
        for family in extraction.device_families
        for absorber in family.absorbers
    ]
    if len(absorber_ids) != len(set(absorber_ids)):
        issue("$.device_families", "duplicate absorber_id")
    for family_index, family in enumerate(extraction.device_families):
        layer_roles = {layer.layer_id: layer.role for layer in family.layers}
        absorber_layers = {
            layer_id for layer_id, role in layer_roles.items() if role == "absorber"
        }
        scoped_layers: set[str] = set()
        for absorber_index, absorber in enumerate(family.absorbers):
            path = (
                f"$.device_families[{family_index}].absorbers[{absorber_index}]"
            )
            if absorber.layer_id is None:
                if len(absorber_layers) > 1:
                    issue(
                        f"{path}.layer_id",
                        "absorber is unscoped in a multi-absorber family",
                    )
                continue
            if absorber.layer_id not in layer_roles:
                issue(f"{path}.layer_id", "unknown layer_id")
            elif layer_roles[absorber.layer_id] != "absorber":
                issue(f"{path}.layer_id", "layer_id does not identify an absorber")
            elif absorber.layer_id in scoped_layers:
                issue(f"{path}.layer_id", "more than one absorber uses this layer_id")
            scoped_layers.add(absorber.layer_id)
        if len(absorber_layers) > 1 and absorber_layers - scoped_layers:
            issue(
                f"$.device_families[{family_index}].absorbers",
                "one or more absorber layers lack a scoped composition record",
            )
    for index, device in enumerate(extraction.individual_devices):
        if device.family_id and device.family_id not in families:
            issue(f"$.individual_devices[{index}].family_id", "unknown family_id")
    for index, observation in enumerate(extraction.performance_observations):
        if observation.device_id not in devices:
            issue(
                f"$.performance_observations[{index}].device_id",
                "unknown device_id",
            )
    for index, population in enumerate(extraction.population_statistics):
        if population.family_id and population.family_id not in families:
            issue(f"$.population_statistics[{index}].family_id", "unknown family_id")
    for index, test in enumerate(extraction.stability_tests):
        if test.family_id and test.family_id not in families:
            issue(f"$.stability_tests[{index}].family_id", "unknown family_id")
        if test.device_id and test.device_id not in devices:
            issue(f"$.stability_tests[{index}].device_id", "unknown device_id")

    return {
        "status": "verified" if not issues else "needs_review",
        "issues": issues,
        "counts": {
            "device_families": len(extraction.device_families),
            "individual_devices": len(extraction.individual_devices),
            "performance_observations": len(extraction.performance_observations),
            "population_statistics": len(extraction.population_statistics),
            "stability_tests": len(extraction.stability_tests),
            "reported_values": total_reported_values,
            "source_verified_values": source_verified_values,
            "source_assembled_values": source_assembled_values,
            "issues_by_reason": dict(Counter(item["reason"] for item in issues)),
        },
        "verified_values": verified_values,
    }
