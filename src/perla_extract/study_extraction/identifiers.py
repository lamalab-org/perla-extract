"""Centralize identifier invariants shared by merging and reconciliation."""

from __future__ import annotations

from collections import Counter

from .models import EntityKind, StudyExtraction


def entity_id_lists(study: StudyExtraction) -> dict[EntityKind, list[str]]:
    """Return identifiers without converting to sets and hiding duplicates."""

    return {
        "device_family": [item.family_id for item in study.device_families],
        "individual_device": [item.device_id for item in study.individual_devices],
        "performance_observation": [
            item.observation_id for item in study.performance_observations
        ],
        "population_statistic": [
            item.population_id for item in study.population_statistics
        ],
        "stability_test": [item.test_id for item in study.stability_tests],
    }


def duplicate_entity_ids(study: StudyExtraction) -> dict[EntityKind, list[str]]:
    """Expose ambiguous entity identifiers before reference checks use sets."""

    return {
        kind: sorted(
            identifier for identifier, count in Counter(ids).items() if count > 1
        )
        for kind, ids in entity_id_lists(study).items()
        if len(ids) != len(set(ids))
    }


def window_namespace(identifier: str) -> str | None:
    """Recover the extraction-window prefix added during lossless merging."""

    namespace, separator, _ = identifier.partition(":")
    return namespace if separator and namespace else None
