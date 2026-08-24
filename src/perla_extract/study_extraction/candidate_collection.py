"""Combine records extracted from separate parts of a long paper.

When a paper is too long for one model request, the workflow divides its evidence
blocks into windows and extracts a complete ``StudyExtraction`` from each window.
Those partial extractions are candidates because records mentioned in multiple
windows may or may not describe the same physical device. This module keeps every
candidate and makes its identifiers unique until explicit evidence links identities.
"""

from __future__ import annotations

from collections.abc import Sequence

from .identifiers import duplicate_entity_ids
from .models import PaperMetadata, StudyExtraction


def _namespace_identifier(identifier: str | None, namespace: str) -> str | None:
    """Make a window-local identifier globally unique while preserving its text."""

    return None if identifier is None else f"{namespace}:{identifier}"


def namespace_window_candidates(
    extraction: StudyExtraction, namespace: str
) -> StudyExtraction:
    """Prefix identifiers and all internal references for one extraction window."""

    data = extraction.model_dump()
    for family in data["device_families"]:
        family["family_id"] = _namespace_identifier(family["family_id"], namespace)
        layer_ids: dict[str, str] = {}
        for layer in family["layers"]:
            old_id = layer["layer_id"]
            layer["layer_id"] = _namespace_identifier(old_id, namespace)
            layer_ids[old_id] = layer["layer_id"]
        for step in family["processing_steps"]:
            step["step_id"] = _namespace_identifier(step["step_id"], namespace)
            step["target_layer_ids"] = [
                layer_ids.get(layer_id, _namespace_identifier(layer_id, namespace))
                for layer_id in step["target_layer_ids"]
            ]
    for device in data["individual_devices"]:
        device["device_id"] = _namespace_identifier(device["device_id"], namespace)
        device["family_id"] = _namespace_identifier(device["family_id"], namespace)
    for observation in data["performance_observations"]:
        observation["observation_id"] = _namespace_identifier(
            observation["observation_id"], namespace
        )
        observation["device_id"] = _namespace_identifier(
            observation["device_id"], namespace
        )
    for population in data["population_statistics"]:
        population["population_id"] = _namespace_identifier(
            population["population_id"], namespace
        )
        population["family_id"] = _namespace_identifier(
            population["family_id"], namespace
        )
    for test in data["stability_tests"]:
        test["test_id"] = _namespace_identifier(test["test_id"], namespace)
        test["family_id"] = _namespace_identifier(test["family_id"], namespace)
        test["device_id"] = _namespace_identifier(test["device_id"], namespace)
        for checkpoint in test["checkpoints"]:
            checkpoint["checkpoint_id"] = _namespace_identifier(
                checkpoint["checkpoint_id"], namespace
            )
    return StudyExtraction.model_validate(data)


def combine_window_candidates(
    window_extractions: Sequence[tuple[str, StudyExtraction]],
) -> StudyExtraction:
    """Combine partial extractions produced from different evidence windows.

    Each input contains the device and measurement records found while the model read
    one bounded group of text and table blocks from the paper. The same source device
    can therefore appear in more than one input, but similar labels alone do not prove
    that the records are identical. The function prefixes IDs with the window ID and
    returns every record without deduplicating or rewriting it. A later identity-
    linking step may add evidence-backed links between candidates, but does not remove
    them from this auditable union.
    """

    if not window_extractions:
        raise ValueError("at least one extraction is required")
    window_ids = [window_id for window_id, _ in window_extractions]
    if len(window_ids) != len(set(window_ids)):
        raise ValueError("window IDs must be unique")
    namespaced_extractions = [
        namespace_window_candidates(extraction, window_id)
        for window_id, extraction in window_extractions
    ]
    title = next(
        (
            extraction.paper.title
            for extraction in namespaced_extractions
            if extraction.paper.title
        ),
        None,
    )
    doi = next(
        (
            extraction.paper.doi
            for extraction in namespaced_extractions
            if extraction.paper.doi
        ),
        None,
    )
    notes = [
        note
        for extraction in namespaced_extractions
        for note in extraction.unresolved_notes
    ]
    if (
        len(
            {
                extraction.paper.title
                for extraction in namespaced_extractions
                if extraction.paper.title
            }
        )
        > 1
    ):
        notes.append("Window extractions disagreed on paper title.")
    if (
        len(
            {
                extraction.paper.doi
                for extraction in namespaced_extractions
                if extraction.paper.doi
            }
        )
        > 1
    ):
        notes.append("Window extractions disagreed on DOI.")
    combined = StudyExtraction(
        paper=PaperMetadata(title=title, doi=doi),
        device_families=[
            item
            for extraction in namespaced_extractions
            for item in extraction.device_families
        ],
        individual_devices=[
            item
            for extraction in namespaced_extractions
            for item in extraction.individual_devices
        ],
        performance_observations=[
            item
            for extraction in namespaced_extractions
            for item in extraction.performance_observations
        ],
        population_statistics=[
            item
            for extraction in namespaced_extractions
            for item in extraction.population_statistics
        ],
        stability_tests=[
            item
            for extraction in namespaced_extractions
            for item in extraction.stability_tests
        ],
        identity_links=[],
        unresolved_notes=notes,
    )
    duplicates = duplicate_entity_ids(combined)
    if duplicates:
        raise ValueError(f"entity IDs must be unique after namespacing: {duplicates}")
    return combined
