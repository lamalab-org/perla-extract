"""Combine window-level candidates without silently deleting model output."""

from __future__ import annotations

from collections.abc import Sequence

from .models import Paper, StudyExtraction


def _prefix(identifier: str | None, namespace: str) -> str | None:
    """Make a window-local identifier globally unique while preserving its text."""

    return None if identifier is None else f"{namespace}:{identifier}"


def namespace_candidates(
    extraction: StudyExtraction, namespace: str
) -> StudyExtraction:
    """Prefix identifiers and all internal references for one extraction window."""

    data = extraction.model_dump()
    for family in data["device_families"]:
        family["family_id"] = _prefix(family["family_id"], namespace)
        layer_ids: dict[str, str] = {}
        for layer in family["layers"]:
            old_id = layer["layer_id"]
            layer["layer_id"] = _prefix(old_id, namespace)
            layer_ids[old_id] = layer["layer_id"]
        for step in family["processing_steps"]:
            step["step_id"] = _prefix(step["step_id"], namespace)
            step["target_layer_ids"] = [
                layer_ids.get(layer_id, _prefix(layer_id, namespace))
                for layer_id in step["target_layer_ids"]
            ]
    for device in data["individual_devices"]:
        device["device_id"] = _prefix(device["device_id"], namespace)
        device["family_id"] = _prefix(device["family_id"], namespace)
    for observation in data["performance_observations"]:
        observation["observation_id"] = _prefix(
            observation["observation_id"], namespace
        )
        observation["device_id"] = _prefix(observation["device_id"], namespace)
    for population in data["population_statistics"]:
        population["population_id"] = _prefix(population["population_id"], namespace)
        population["family_id"] = _prefix(population["family_id"], namespace)
    for test in data["stability_tests"]:
        test["test_id"] = _prefix(test["test_id"], namespace)
        test["family_id"] = _prefix(test["family_id"], namespace)
        test["device_id"] = _prefix(test["device_id"], namespace)
        for checkpoint in test["checkpoints"]:
            checkpoint["checkpoint_id"] = _prefix(
                checkpoint["checkpoint_id"], namespace
            )
    return StudyExtraction.model_validate(data)


def merge_candidates(parts: Sequence[tuple[str, StudyExtraction]]) -> StudyExtraction:
    """Return the lossless union of namespaced window-level candidates.

    This function intentionally does not deduplicate or rewrite candidates.
    Reconciliation should return explicit equivalence links between source IDs;
    it should not be allowed to replace this auditable union.
    """

    if not parts:
        raise ValueError("at least one extraction is required")
    namespaced = [
        namespace_candidates(extraction, window_id) for window_id, extraction in parts
    ]
    title = next((part.paper.title for part in namespaced if part.paper.title), None)
    doi = next((part.paper.doi for part in namespaced if part.paper.doi), None)
    notes = [note for part in namespaced for note in part.unresolved_notes]
    if len({part.paper.title for part in namespaced if part.paper.title}) > 1:
        notes.append("Window extractions disagreed on paper title.")
    if len({part.paper.doi for part in namespaced if part.paper.doi}) > 1:
        notes.append("Window extractions disagreed on DOI.")
    return StudyExtraction(
        paper=Paper(title=title, doi=doi),
        device_families=[item for part in namespaced for item in part.device_families],
        individual_devices=[
            item for part in namespaced for item in part.individual_devices
        ],
        performance_observations=[
            item for part in namespaced for item in part.performance_observations
        ],
        population_statistics=[
            item for part in namespaced for item in part.population_statistics
        ],
        stability_tests=[item for part in namespaced for item in part.stability_tests],
        equivalence_groups=[],
        unresolved_notes=notes,
    )
